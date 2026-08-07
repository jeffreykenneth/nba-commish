"""Strict commissioner CSV fallback import, artifact, and reconciliation."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from nba_commish.hoopshype.artifact import write_artifact_atomic
from nba_commish.hoopshype.deduplication import (
    DeduplicationResult,
    SourceRowOccurrence,
    _canonical_full_http_url,
    _canonical_player_url,
    _collapse_whitespace,
    _fingerprint_material,
    _sha256_hexdigest,
    deduplicate_salary_rows,
    fingerprint_salary_row,
)
from nba_commish.hoopshype.errors import (
    ArtifactWriteError,
    CsvFallbackArtifactError,
    CsvFallbackReconciliationError,
    CsvFallbackValidationError,
    FingerprintCollisionError,
    FingerprintValidationError,
    SalaryParseError,
    SeasonValidationError,
)
from nba_commish.hoopshype.pipeline import utc_timestamp
from nba_commish.hoopshype.salary import (
    normalize_salary_row,
    normalize_salary_rows,
    parse_salary_text,
)
from nba_commish.hoopshype.season import validate_season

CSV_HEADER_FIELDS = (
    "player_display_text",
    "player_url",
    "player_id",
    "team_display_text",
    "team_logo_url",
    "team_logo_asset_id",
    "target_season",
    "target_season_salary_text",
    "target_season_marker_text",
    "source_row_description",
    "salary_season_cells_json",
    "source_row_reference",
)
CSV_HEADER = ",".join(CSV_HEADER_FIELDS)
CSV_INPUT_ARTIFACT_VERSION = "1.0"
CSV_INPUT_SCHEMA_VERSION = "1.0"
DERIVED_ARTIFACT_VERSION = "1.0"
DERIVED_SCHEMA_VERSION = "1.0"

_METADATA_FIELDS = (
    "source_system",
    "source_locator",
    "retrieved_at",
    "generated_at",
    "source_event_time",
    "season",
    "league_scope",
    "artifact_version",
    "schema_version",
    "redaction_status",
)
_REQUIRED_NONEMPTY_FIELDS = {
    "player_display_text",
    "team_logo_url",
    "target_season",
    "salary_season_cells_json",
}
_NULLABLE_FIELDS = {
    "player_url",
    "player_id",
    "team_display_text",
    "team_logo_asset_id",
    "source_row_description",
    "source_row_reference",
}
_ASCII_INTEGER_PATTERN = re.compile(r"^[0-9]+$")
_ASCII_SEASON_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}$")
_UTC_TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)
_SAFE_BASENAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
_SENSITIVE_LOCATOR_PATTERN = re.compile(
    r"(?:authorization|bearer|cookie|password|secret|session|token)",
    flags=re.IGNORECASE,
)
_WINDOWS_ABSOLUTE_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")
_EXACT_INTEGER_TAG = "$nba_commish_unsigned_integer_hex"
_HEX_PATTERN = re.compile(r"^(?:0|[1-9a-f][0-9a-f]*)$")
_PRIVATE_ROOT = Path(__file__).resolve().parents[3] / "data" / "private"

HashFunction = Callable[[bytes], str]


@dataclass(frozen=True)
class CsvFallbackDiagnostic:
    """One sanitized, deterministically ordered validation diagnostic."""

    location: str
    field: str
    code: str
    message: str
    record_number: int | None = None

    def __str__(self) -> str:
        if self.location == "row":
            return (
                f"CSV data record {self.record_number}, field {self.field}: "
                f"{self.message}"
            )
        return f"{self.location} {self.field}: {self.message}"


@dataclass(frozen=True)
class CommissionerCsvImport:
    """Validated source evidence and its issue-#21 deduplication boundary."""

    deduplication: DeduplicationResult
    input_metadata: dict[str, Any]
    physical_row_count: int
    input_csv_sha256: str
    input_metadata_sha256: str
    input_csv_basename: str
    input_metadata_basename: str


@dataclass(frozen=True)
class CsvFallbackRunResult:
    """Published fallback artifact summary and in-memory deduplication result."""

    deduplication: DeduplicationResult
    physical_row_count: int
    unique_row_count: int
    duplicate_occurrence_count: int
    output_basename: str
    artifact: dict[str, Any]


@dataclass(frozen=True)
class ReconciliationResult:
    """Ordered canonical equivalence and unmatched fingerprint report."""

    equivalent_fingerprints: tuple[str, ...]
    commissioner_only_fingerprints: tuple[str, ...]
    browser_only_fingerprints: tuple[str, ...]


@dataclass(frozen=True)
class _CanonicalRow:
    fingerprint: str
    payload: bytes


class _DuplicateJsonKey(ValueError):
    pass


def import_commissioner_csv(
    input_path: Path,
    selected_season: str,
    *,
    imported_at: str | None = None,
) -> DeduplicationResult:
    """Load a production commissioner CSV and return issue-#21 output exactly."""

    timestamp = imported_at or utc_timestamp()
    imported = _load_csv_paths(
        input_path,
        selected_season=selected_season,
        imported_at=timestamp,
        metadata_mode="production",
        enforce_private=True,
    )
    return imported.deduplication


def load_synthetic_csv_fixture_for_tests(
    input_path: Path,
    selected_season: str,
    *,
    imported_at: str,
) -> DeduplicationResult:
    """Explicit test-only boundary for a reviewed synthetic fixture."""

    imported = _load_csv_paths(
        input_path,
        selected_season=selected_season,
        imported_at=imported_at,
        metadata_mode="synthetic",
        enforce_private=False,
    )
    return imported.deduplication


def run_csv_fallback_import(
    input_path: Path,
    selected_season: str,
    output_path: Path,
    *,
    timestamp_factory: Callable[[], str] = utc_timestamp,
) -> CsvFallbackRunResult:
    """Validate, normalize, deduplicate, and exclusively publish one fallback."""

    timestamp = timestamp_factory()
    path_diagnostics = _production_path_diagnostics(input_path, output_path)
    imported = _load_csv_paths(
        input_path,
        selected_season=selected_season,
        imported_at=timestamp,
        metadata_mode="production",
        enforce_private=False,
        initial_diagnostics=path_diagnostics,
    )
    artifact = build_csv_fallback_artifact(imported, generated_at=timestamp)
    try:
        write_artifact_atomic(output_path, artifact)
    except ArtifactWriteError as error:
        raise CsvFallbackArtifactError(
            "Could not exclusively publish derived fallback artifact "
            f"{_display_basename(output_path)}; existing output was not replaced."
        ) from error

    unique_count = len(imported.deduplication.unique_rows)
    return CsvFallbackRunResult(
        deduplication=imported.deduplication,
        physical_row_count=imported.physical_row_count,
        unique_row_count=unique_count,
        duplicate_occurrence_count=imported.physical_row_count - unique_count,
        output_basename=_display_basename(output_path),
        artifact=artifact,
    )


def build_csv_fallback_artifact(
    imported: CommissionerCsvImport,
    *,
    generated_at: str,
) -> dict[str, Any]:
    """Build a versioned, exact-integer-safe derived JSON artifact."""

    if not _is_utc_timestamp(generated_at):
        raise CsvFallbackArtifactError(
            "Derived artifact generated_at must be an ISO 8601 UTC timestamp."
        )
    result = imported.deduplication
    unique_count = len(result.unique_rows)
    occurrence_count = len(result.occurrences)
    if occurrence_count != imported.physical_row_count:
        raise CsvFallbackArtifactError(
            "Derived artifact counts do not reconcile with occurrence provenance."
        )
    metadata = imported.input_metadata
    return {
        "metadata": {
            "source_system": "commissioner_salary_csv",
            "source_locator": metadata["source_locator"],
            "retrieved_at": None,
            "generated_at": generated_at,
            "source_event_time": metadata["source_event_time"],
            "season": metadata["season"],
            "league_scope": None,
            "artifact_version": DERIVED_ARTIFACT_VERSION,
            "schema_version": DERIVED_SCHEMA_VERSION,
            "redaction_status": (
                "redacted-reviewed"
                if metadata["redaction_status"] == "synthetic"
                else metadata["redaction_status"]
            ),
        },
        "counts": {
            "physical_csv_row_count": imported.physical_row_count,
            "unique_row_count": unique_count,
            "duplicate_occurrence_count": occurrence_count - unique_count,
        },
        "unique_rows": [_encode_row(row) for row in result.unique_rows],
        "occurrences": [asdict(occurrence) for occurrence in result.occurrences],
        "lineage": {
            "input_csv": {
                "basename": imported.input_csv_basename,
                "sha256": imported.input_csv_sha256,
            },
            "input_metadata": {
                "basename": imported.input_metadata_basename,
                "sha256": imported.input_metadata_sha256,
            },
        },
    }


def load_csv_fallback_artifact(path: Path) -> DeduplicationResult:
    """Load and validate a derived artifact back into an equal result."""

    try:
        payload = path.read_bytes()
    except OSError as error:
        raise CsvFallbackArtifactError(
            f"Could not read derived fallback artifact {_display_basename(path)}."
        ) from error
    try:
        document = _json_loads_strict(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKey) as error:
        raise CsvFallbackArtifactError(
            f"Derived fallback artifact {_display_basename(path)} is not valid UTF-8 JSON."
        ) from error
    if not isinstance(document, dict) or set(document) != {
        "metadata",
        "counts",
        "unique_rows",
        "occurrences",
        "lineage",
    }:
        raise CsvFallbackArtifactError(
            "Derived fallback artifact has an invalid root shape."
        )
    metadata = document["metadata"]
    if not isinstance(metadata, dict) or set(metadata) != set(_METADATA_FIELDS):
        raise CsvFallbackArtifactError("Derived fallback artifact metadata is invalid.")
    if (
        metadata.get("source_system") != "commissioner_salary_csv"
        or not _is_safe_locator(metadata.get("source_locator"))
        or metadata.get("retrieved_at") is not None
        or not _is_utc_timestamp(metadata.get("generated_at"))
        or (
            metadata.get("source_event_time") is not None
            and not _is_utc_timestamp(metadata.get("source_event_time"))
        )
        or metadata.get("league_scope") is not None
        or metadata.get("redaction_status")
        not in {"unredacted-local", "redacted-reviewed"}
    ):
        raise CsvFallbackArtifactError("Derived fallback artifact metadata is invalid.")
    artifact_season = metadata.get("season")
    try:
        if (
            not isinstance(artifact_season, str)
            or _ASCII_SEASON_PATTERN.fullmatch(artifact_season) is None
        ):
            raise SeasonValidationError("invalid")
        validate_season(artifact_season)
    except SeasonValidationError as error:
        raise CsvFallbackArtifactError(
            "Derived fallback artifact metadata season is invalid."
        ) from error
    if (
        metadata.get("artifact_version") != DERIVED_ARTIFACT_VERSION
        or metadata.get("schema_version") != DERIVED_SCHEMA_VERSION
    ):
        raise CsvFallbackArtifactError(
            "Derived fallback artifact version is unsupported."
        )
    if not isinstance(document["unique_rows"], list) or not isinstance(
        document["occurrences"], list
    ):
        raise CsvFallbackArtifactError(
            "Derived fallback rows or occurrences are invalid."
        )
    try:
        unique_rows = tuple(_decode_row(row) for row in document["unique_rows"])
        occurrences = tuple(
            SourceRowOccurrence(**occurrence) for occurrence in document["occurrences"]
        )
    except (KeyError, TypeError, ValueError) as error:
        raise CsvFallbackArtifactError(
            "Derived fallback row or occurrence encoding is invalid."
        ) from error
    result = DeduplicationResult(unique_rows=unique_rows, occurrences=occurrences)
    try:
        _canonical_rows(result, label="derived artifact")
        _validate_occurrence_trail(result, label="derived artifact")
    except CsvFallbackReconciliationError as error:
        raise CsvFallbackArtifactError(
            "Derived fallback artifact row provenance is invalid."
        ) from error

    counts = document["counts"]
    if not isinstance(counts, dict) or counts != {
        "physical_csv_row_count": len(occurrences),
        "unique_row_count": len(unique_rows),
        "duplicate_occurrence_count": len(occurrences) - len(unique_rows),
    }:
        raise CsvFallbackArtifactError(
            "Derived fallback artifact counts are inconsistent."
        )
    _validate_lineage(document["lineage"])
    return result


def reconcile_salary_rows(
    commissioner: DeduplicationResult,
    browser: DeduplicationResult,
    *,
    hash_function: HashFunction | None = None,
) -> ReconciliationResult:
    """Compare canonical payloads without merging or trusting digest text alone."""

    digest = hash_function or _sha256_hexdigest
    commissioner_rows = _canonical_rows(
        commissioner,
        label="commissioner",
        hash_function=digest,
    )
    browser_rows = _canonical_rows(
        browser,
        label="browser",
        hash_function=digest,
    )
    _validate_occurrence_trail(commissioner, label="commissioner")
    _validate_occurrence_trail(browser, label="browser")

    payload_by_fingerprint: dict[str, bytes] = {}
    for row in (*commissioner_rows, *browser_rows):
        prior = payload_by_fingerprint.get(row.fingerprint)
        if prior is not None and prior != row.payload:
            raise CsvFallbackReconciliationError(
                "Canonical digest collision across reconciliation inputs for "
                f"{row.fingerprint}; no rows were matched."
            )
        payload_by_fingerprint[row.fingerprint] = row.payload

    browser_by_payload = {row.payload: row for row in browser_rows}
    commissioner_payloads = {row.payload for row in commissioner_rows}
    equivalent = tuple(
        row.fingerprint
        for row in commissioner_rows
        if row.payload in browser_by_payload
    )
    commissioner_only = tuple(
        row.fingerprint
        for row in commissioner_rows
        if row.payload not in browser_by_payload
    )
    browser_only = tuple(
        row.fingerprint
        for row in browser_rows
        if row.payload not in commissioner_payloads
    )
    return ReconciliationResult(
        equivalent_fingerprints=equivalent,
        commissioner_only_fingerprints=commissioner_only,
        browser_only_fingerprints=browser_only,
    )


def _load_csv_paths(
    input_path: Path,
    *,
    selected_season: str,
    imported_at: str,
    metadata_mode: str,
    enforce_private: bool,
    initial_diagnostics: Sequence[CsvFallbackDiagnostic] = (),
) -> CommissionerCsvImport:
    diagnostics = list(initial_diagnostics)
    if enforce_private:
        diagnostics.extend(_production_path_diagnostics(input_path, None))
    metadata_path = input_path.with_suffix(".metadata.json")
    csv_bytes: bytes | None = None
    metadata_bytes: bytes | None = None
    try:
        csv_bytes = input_path.read_bytes()
    except OSError:
        diagnostics.append(
            _diagnostic(
                "file", "input", "unreadable", "input CSV is missing or unreadable"
            )
        )
    try:
        metadata_bytes = metadata_path.read_bytes()
    except OSError:
        diagnostics.append(
            _diagnostic(
                "metadata",
                "sidecar",
                "missing",
                "required same-basename .metadata.json sidecar is missing or unreadable",
            )
        )
    return _parse_csv_evidence(
        csv_bytes,
        metadata_bytes,
        selected_season=selected_season,
        imported_at=imported_at,
        input_basename=input_path.name,
        metadata_basename=metadata_path.name,
        metadata_mode=metadata_mode,
        initial_diagnostics=diagnostics,
    )


def _parse_csv_evidence(
    csv_bytes: bytes | None,
    metadata_bytes: bytes | None,
    *,
    selected_season: str,
    imported_at: str,
    input_basename: str,
    metadata_basename: str,
    metadata_mode: str,
    initial_diagnostics: Sequence[CsvFallbackDiagnostic] = (),
) -> CommissionerCsvImport:
    diagnostics = list(initial_diagnostics)
    selected_valid = _validate_selected_season(selected_season, diagnostics)
    if not _is_utc_timestamp(imported_at):
        diagnostics.append(
            _diagnostic(
                "file",
                "imported_at",
                "invalid_timestamp",
                "import timestamp must be ISO 8601 UTC",
            )
        )
    metadata = _validate_metadata(
        metadata_bytes,
        selected_season=selected_season if selected_valid else None,
        mode=metadata_mode,
        diagnostics=diagnostics,
    )
    records = _validate_csv_file(csv_bytes, diagnostics)

    normalized_rows: list[dict[str, Any]] = []
    if records is not None and selected_valid and _is_utc_timestamp(imported_at):
        source_locator = (
            metadata.get("source_locator")
            if metadata is not None and _is_safe_locator(metadata.get("source_locator"))
            else "commissioner salary CSV"
        )
        for record_number, record in enumerate(records, start=1):
            normalized = _validate_record(
                record,
                record_number=record_number,
                selected_season=selected_season,
                source_locator=source_locator,
                imported_at=imported_at,
                diagnostics=diagnostics,
            )
            if normalized is not None:
                normalized_rows.append(normalized)

    if diagnostics:
        raise CsvFallbackValidationError(
            tuple(sorted(diagnostics, key=_diagnostic_key))
        )

    try:
        normalized_batch = normalize_salary_rows(
            [deepcopy(row) for row in normalized_rows]
        )
        deduplicated = deduplicate_salary_rows(normalized_batch)
    except (
        SalaryParseError,
        FingerprintValidationError,
        FingerprintCollisionError,
    ) as error:
        raise CsvFallbackValidationError(
            (
                _diagnostic(
                    "file",
                    "rows",
                    "boundary_validation",
                    "accepted rows failed issue-#20/#21 boundary validation",
                ),
            )
        ) from error
    assert csv_bytes is not None
    assert metadata_bytes is not None
    assert metadata is not None
    return CommissionerCsvImport(
        deduplication=deduplicated,
        input_metadata=deepcopy(metadata),
        physical_row_count=len(normalized_rows),
        input_csv_sha256=hashlib.sha256(csv_bytes).hexdigest(),
        input_metadata_sha256=hashlib.sha256(metadata_bytes).hexdigest(),
        input_csv_basename=input_basename,
        input_metadata_basename=metadata_basename,
    )


def _validate_selected_season(
    selected_season: str,
    diagnostics: list[CsvFallbackDiagnostic],
) -> bool:
    if (
        not isinstance(selected_season, str)
        or _ASCII_SEASON_PATTERN.fullmatch(selected_season) is None
    ):
        diagnostics.append(
            _diagnostic(
                "metadata",
                "selected_season",
                "invalid_season",
                "CLI season must be a consecutive ASCII yyyy-yy value",
            )
        )
        return False
    try:
        validate_season(selected_season)
    except SeasonValidationError:
        diagnostics.append(
            _diagnostic(
                "metadata",
                "selected_season",
                "invalid_season",
                "CLI season must be a consecutive ASCII yyyy-yy value",
            )
        )
        return False
    return True


def _validate_metadata(
    metadata_bytes: bytes | None,
    *,
    selected_season: str | None,
    mode: str,
    diagnostics: list[CsvFallbackDiagnostic],
) -> dict[str, Any] | None:
    if metadata_bytes is None:
        return None
    if metadata_bytes.startswith(b"\xef\xbb\xbf"):
        diagnostics.append(
            _diagnostic(
                "metadata",
                "encoding",
                "bom",
                "sidecar must be UTF-8 without a BOM",
            )
        )
        return None
    try:
        text = metadata_bytes.decode("utf-8")
    except UnicodeDecodeError:
        diagnostics.append(
            _diagnostic(
                "metadata",
                "encoding",
                "invalid_utf8",
                "sidecar must be valid UTF-8",
            )
        )
        return None
    try:
        value = _json_loads_strict(text)
    except (_DuplicateJsonKey, json.JSONDecodeError):
        diagnostics.append(
            _diagnostic(
                "metadata",
                "json",
                "invalid_json",
                "sidecar must contain one JSON object with unique keys",
            )
        )
        return None
    if not isinstance(value, dict):
        diagnostics.append(
            _diagnostic(
                "metadata",
                "root",
                "wrong_type",
                "sidecar root must be a JSON object",
            )
        )
        return None

    missing = [field for field in _METADATA_FIELDS if field not in value]
    extra = sorted(set(value) - set(_METADATA_FIELDS))
    for field in missing:
        diagnostics.append(
            _diagnostic(
                "metadata",
                field,
                "missing",
                "required metadata field is missing",
            )
        )
    for field in extra:
        diagnostics.append(
            _diagnostic(
                "metadata",
                field,
                "extra",
                "unexpected metadata field is not allowed",
            )
        )
    if mode == "production":
        if value.get("source_system") != "commissioner_salary_csv":
            diagnostics.append(
                _diagnostic(
                    "metadata",
                    "source_system",
                    "invalid_value",
                    "production source_system must be commissioner_salary_csv",
                )
            )
        allowed_redaction = {"unredacted-local", "redacted-reviewed"}
        if value.get("retrieved_at") is None or not _is_utc_timestamp(
            value.get("retrieved_at")
        ):
            diagnostics.append(
                _diagnostic(
                    "metadata",
                    "retrieved_at",
                    "invalid_timestamp",
                    "production retrieved_at must be a non-null ISO 8601 UTC timestamp",
                )
            )
        if value.get("generated_at") is not None:
            diagnostics.append(
                _diagnostic(
                    "metadata",
                    "generated_at",
                    "must_be_null",
                    "production generated_at must be null",
                )
            )
    elif mode == "synthetic":
        if value.get("source_system") != "synthetic":
            diagnostics.append(
                _diagnostic(
                    "metadata",
                    "source_system",
                    "invalid_value",
                    "test fixture source_system must be synthetic",
                )
            )
        allowed_redaction = {"synthetic"}
        if value.get("retrieved_at") is not None:
            diagnostics.append(
                _diagnostic(
                    "metadata",
                    "retrieved_at",
                    "must_be_null",
                    "synthetic retrieved_at must be null",
                )
            )
        if not _is_utc_timestamp(value.get("generated_at")):
            diagnostics.append(
                _diagnostic(
                    "metadata",
                    "generated_at",
                    "invalid_timestamp",
                    "synthetic generated_at must be an ISO 8601 UTC timestamp",
                )
            )
    else:
        raise ValueError("Unsupported metadata validation mode.")

    locator = value.get("source_locator")
    if not _is_safe_locator(locator):
        diagnostics.append(
            _diagnostic(
                "metadata",
                "source_locator",
                "unsafe",
                "source locator must be nonempty and sanitized",
            )
        )
    if value.get("source_event_time") is not None and not _is_utc_timestamp(
        value.get("source_event_time")
    ):
        diagnostics.append(
            _diagnostic(
                "metadata",
                "source_event_time",
                "invalid_timestamp",
                "source_event_time must be ISO 8601 UTC or null",
            )
        )
    season = value.get("season")
    season_valid = (
        isinstance(season, str) and _ASCII_SEASON_PATTERN.fullmatch(season) is not None
    )
    if season_valid:
        try:
            validate_season(season)
        except SeasonValidationError:
            season_valid = False
    if not season_valid:
        diagnostics.append(
            _diagnostic(
                "metadata",
                "season",
                "invalid_season",
                "sidecar season must be a consecutive ASCII yyyy-yy value",
            )
        )
    elif selected_season is not None and season != selected_season:
        diagnostics.append(
            _diagnostic(
                "metadata",
                "season",
                "season_mismatch",
                "sidecar season must equal the CLI-selected season",
            )
        )
    if value.get("league_scope") is not None:
        diagnostics.append(
            _diagnostic(
                "metadata",
                "league_scope",
                "must_be_null",
                "commissioner salary CSV league_scope must be null",
            )
        )
    for field, expected in (
        ("artifact_version", CSV_INPUT_ARTIFACT_VERSION),
        ("schema_version", CSV_INPUT_SCHEMA_VERSION),
    ):
        if value.get(field) != expected:
            diagnostics.append(
                _diagnostic(
                    "metadata",
                    field,
                    "unsupported_version",
                    f"{field} must be {expected}",
                )
            )
    if value.get("redaction_status") not in allowed_redaction:
        diagnostics.append(
            _diagnostic(
                "metadata",
                "redaction_status",
                "invalid_value",
                "redaction_status is not allowed for this input boundary",
            )
        )
    return value


def _validate_csv_file(
    csv_bytes: bytes | None,
    diagnostics: list[CsvFallbackDiagnostic],
) -> list[list[str]] | None:
    if csv_bytes is None:
        return None
    structural_error = False
    if csv_bytes.startswith(b"\xef\xbb\xbf"):
        diagnostics.append(
            _diagnostic(
                "file",
                "encoding",
                "bom",
                "CSV must be UTF-8 without a BOM",
            )
        )
        structural_error = True
    try:
        text = csv_bytes.decode("utf-8")
    except UnicodeDecodeError:
        diagnostics.append(
            _diagnostic(
                "file",
                "encoding",
                "invalid_utf8",
                "CSV must be valid UTF-8",
            )
        )
        return None
    if "\x00" in text:
        diagnostics.append(
            _diagnostic(
                "file",
                "bytes",
                "nul",
                "CSV must not contain NUL bytes",
            )
        )
        structural_error = True
    newline_errors = _newline_diagnostics(text)
    diagnostics.extend(newline_errors)
    structural_error = structural_error or bool(newline_errors)
    structure_error = _csv_structure_diagnostic(text)
    if structure_error is not None:
        diagnostics.append(structure_error)
        structural_error = True
    if structural_error:
        return None
    if not text.startswith(CSV_HEADER + "\r\n"):
        diagnostics.append(
            _diagnostic(
                "file",
                "header",
                "header_mismatch",
                "header must exactly match the fixed 12-column schema and order",
            )
        )
        return None
    try:
        rows = list(
            csv.reader(
                io.StringIO(text, newline=""),
                delimiter=",",
                quotechar='"',
                doublequote=True,
                escapechar=None,
                strict=True,
            )
        )
    except csv.Error:
        diagnostics.append(
            _diagnostic(
                "file",
                "quoting",
                "invalid_csv",
                "CSV quoting is not valid RFC 4180",
            )
        )
        return None
    if not rows or rows[0] != list(CSV_HEADER_FIELDS):
        diagnostics.append(
            _diagnostic(
                "file",
                "header",
                "header_mismatch",
                "header must exactly match the fixed 12-column schema and order",
            )
        )
        return None
    records = rows[1:]
    nonblank = [row for row in records if row]
    if not nonblank:
        diagnostics.append(
            _diagnostic(
                "file",
                "records",
                "missing_data",
                "CSV must contain at least one nonblank data record",
            )
        )
    return records


def _newline_diagnostics(text: str) -> list[CsvFallbackDiagnostic]:
    errors: list[CsvFallbackDiagnostic] = []
    if not text.endswith("\r\n"):
        errors.append(
            _diagnostic(
                "file",
                "newlines",
                "missing_final_crlf",
                "every record, including the final record, must end with CRLF",
            )
        )
    if any(
        character == "\n" and (index == 0 or text[index - 1] != "\r")
        for index, character in enumerate(text)
    ) or any(
        character == "\r" and (index + 1 == len(text) or text[index + 1] != "\n")
        for index, character in enumerate(text)
    ):
        errors.append(
            _diagnostic(
                "file",
                "newlines",
                "non_crlf",
                "record separators must use CRLF only",
            )
        )
    return errors


def _csv_structure_diagnostic(text: str) -> CsvFallbackDiagnostic | None:
    state = "field_start"
    index = 0
    while index < len(text):
        character = text[index]
        if character == "\x00":
            index += 1
            continue
        if character not in {"\r", "\n"} and unicodedata.category(character).startswith(
            "C"
        ):
            return _diagnostic(
                "file",
                "fields",
                "control_character",
                "CSV fields must not contain control characters",
            )
        if state == "quoted":
            if character in {"\r", "\n"}:
                return _diagnostic(
                    "file",
                    "fields",
                    "embedded_newline",
                    "CSV fields must not contain embedded CR or LF",
                )
            if character == '"':
                if index + 1 < len(text) and text[index + 1] == '"':
                    index += 2
                    continue
                state = "after_quote"
            index += 1
            continue
        if state == "after_quote":
            if character == ",":
                state = "field_start"
            elif (
                character == "\r" and index + 1 < len(text) and text[index + 1] == "\n"
            ):
                state = "field_start"
                index += 1
            else:
                return _diagnostic(
                    "file",
                    "quoting",
                    "invalid_quote_escape",
                    "quoted fields must use RFC 4180 double-quote escaping",
                )
            index += 1
            continue
        if state == "field_start":
            if character == '"':
                state = "quoted"
            elif character == ",":
                pass
            elif (
                character == "\r" and index + 1 < len(text) and text[index + 1] == "\n"
            ):
                index += 1
            elif character == "\n":
                pass
            else:
                state = "unquoted"
            index += 1
            continue
        if character == '"':
            return _diagnostic(
                "file",
                "quoting",
                "bare_quote",
                "double quotes are allowed only as RFC 4180 field quoting",
            )
        if character == ",":
            state = "field_start"
        elif character == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
            state = "field_start"
            index += 1
        index += 1
    if state == "quoted":
        return _diagnostic(
            "file",
            "quoting",
            "unclosed_quote",
            "quoted field is not closed",
        )
    return None


def _validate_record(
    record: list[str],
    *,
    record_number: int,
    selected_season: str,
    source_locator: str,
    imported_at: str,
    diagnostics: list[CsvFallbackDiagnostic],
) -> dict[str, Any] | None:
    initial_error_count = len(diagnostics)
    if not record:
        diagnostics.append(
            _diagnostic(
                "row",
                "record",
                "blank_record",
                "blank records are not allowed",
                record_number=record_number,
            )
        )
        return None
    if len(record) != len(CSV_HEADER_FIELDS):
        diagnostics.append(
            _diagnostic(
                "row",
                "record",
                "column_count",
                "record must contain exactly 12 fields",
                record_number=record_number,
            )
        )
        return None
    fields = dict(zip(CSV_HEADER_FIELDS, record, strict=True))
    for field in _REQUIRED_NONEMPTY_FIELDS:
        if fields[field] == "":
            diagnostics.append(
                _diagnostic(
                    "row",
                    field,
                    "required",
                    "required field must not be empty",
                    record_number=record_number,
                )
            )

    if not _collapse_whitespace(fields["player_display_text"]):
        diagnostics.append(
            _diagnostic(
                "row",
                "player_display_text",
                "empty_identity",
                "player display text must contain non-whitespace characters",
                record_number=record_number,
            )
        )

    player_id = _validate_optional_id(
        fields["player_id"],
        field="player_id",
        record_number=record_number,
        diagnostics=diagnostics,
    )
    logo_asset_id = _validate_optional_id(
        fields["team_logo_asset_id"],
        field="team_logo_asset_id",
        record_number=record_number,
        diagnostics=diagnostics,
    )
    player_url = fields["player_url"] or None
    if player_url is not None:
        try:
            _canonical_player_url(player_url, context="")
        except FingerprintValidationError:
            diagnostics.append(
                _diagnostic(
                    "row",
                    "player_url",
                    "invalid_url",
                    "player URL must be issue-#21 HTTP(S), scheme-relative, or root-relative form",
                    record_number=record_number,
                )
            )
    if fields["team_logo_url"]:
        try:
            _canonical_full_http_url(
                fields["team_logo_url"], field="team_logo_url", context=""
            )
        except FingerprintValidationError:
            diagnostics.append(
                _diagnostic(
                    "row",
                    "team_logo_url",
                    "invalid_url",
                    "team logo URL must be a full public issue-#21 HTTP(S) URL",
                    record_number=record_number,
                )
            )

    row_season = fields["target_season"]
    row_season_valid = (
        isinstance(row_season, str)
        and _ASCII_SEASON_PATTERN.fullmatch(row_season) is not None
    )
    if row_season_valid:
        try:
            validate_season(row_season)
        except SeasonValidationError:
            row_season_valid = False
    if not row_season_valid:
        diagnostics.append(
            _diagnostic(
                "row",
                "target_season",
                "invalid_season",
                "target season must be a consecutive ASCII yyyy-yy value",
                record_number=record_number,
            )
        )
    elif row_season != selected_season:
        diagnostics.append(
            _diagnostic(
                "row",
                "target_season",
                "season_mismatch",
                "target season must equal CLI and sidecar season",
                record_number=record_number,
            )
        )

    try:
        parse_salary_text(fields["target_season_salary_text"])
    except SalaryParseError:
        diagnostics.append(
            _diagnostic(
                "row",
                "target_season_salary_text",
                "invalid_salary",
                "salary text does not match issue-#20 whole-dollar/null grammar",
                record_number=record_number,
            )
        )

    salary_cells = _validate_salary_cells(
        fields["salary_season_cells_json"],
        target_season=row_season,
        target_salary_text=fields["target_season_salary_text"],
        target_marker_text=fields["target_season_marker_text"],
        record_number=record_number,
        diagnostics=diagnostics,
    )
    if len(diagnostics) != initial_error_count:
        return None
    assert salary_cells is not None

    raw_row: dict[str, Any] = {
        "target_season": row_season,
        "source_page_number": 1,
        "source_row_position": record_number,
        "rank_text": "",
        "player_display_text": fields["player_display_text"],
        "player_url": player_url,
        "player_id": player_id,
        "team_logo_url": fields["team_logo_url"],
        "team_logo_asset_id": logo_asset_id,
        "target_season_salary_text": fields["target_season_salary_text"],
        "target_season_marker_text": fields["target_season_marker_text"],
        "source_row_description": fields["source_row_description"] or None,
        "source_locator": f"{source_locator} (CSV data record {record_number})",
        "imported_at": imported_at,
        "salary_season_cells": salary_cells,
        "source_system": "commissioner_salary_csv",
        "team_display_text": fields["team_display_text"] or None,
        "source_row_reference": fields["source_row_reference"] or None,
        "salary_season_cells_json": fields["salary_season_cells_json"],
        "player_id_csv_text": fields["player_id"] or None,
        "team_logo_asset_id_csv_text": fields["team_logo_asset_id"] or None,
    }
    try:
        normalized = normalize_salary_row(raw_row)
        fingerprint_salary_row(normalized)
    except SalaryParseError:
        diagnostics.append(
            _diagnostic(
                "row",
                "target_season_salary_text",
                "normalization_failure",
                "row failed issue-#20 normalization",
                record_number=record_number,
            )
        )
        return None
    except FingerprintValidationError:
        diagnostics.append(
            _diagnostic(
                "row",
                "row",
                "fingerprint_validation",
                "row failed issue-#21 fingerprint validation",
                record_number=record_number,
            )
        )
        return None
    return normalized


def _validate_optional_id(
    value: str,
    *,
    field: str,
    record_number: int,
    diagnostics: list[CsvFallbackDiagnostic],
) -> int | None:
    if value == "":
        return None
    if _ASCII_INTEGER_PATTERN.fullmatch(value) is None:
        diagnostics.append(
            _diagnostic(
                "row",
                field,
                "invalid_unsigned_integer",
                "value must contain unsigned ASCII base-10 digits only",
                record_number=record_number,
            )
        )
        return None
    return _ascii_digits_to_int(value)


def _validate_salary_cells(
    value: str,
    *,
    target_season: str,
    target_salary_text: str,
    target_marker_text: str,
    record_number: int,
    diagnostics: list[CsvFallbackDiagnostic],
) -> list[dict[str, str]] | None:
    field = "salary_season_cells_json"
    if value == "":
        return None
    try:
        parsed = _json_loads_strict(value)
    except (_DuplicateJsonKey, json.JSONDecodeError):
        diagnostics.append(
            _diagnostic(
                "row",
                field,
                "invalid_json",
                "value must be a JSON array with unique object keys",
                record_number=record_number,
            )
        )
        return None
    if not isinstance(parsed, list) or not parsed:
        diagnostics.append(
            _diagnostic(
                "row",
                field,
                "wrong_json_shape",
                "value must be a nonempty JSON array",
                record_number=record_number,
            )
        )
        return None
    cells: list[dict[str, str]] = []
    seen_headings: set[str] = set()
    target_cells: list[dict[str, str]] = []
    for cell_position, cell in enumerate(parsed, start=1):
        if not isinstance(cell, dict) or set(cell) != {
            "heading",
            "salary_text",
            "marker_text",
        }:
            diagnostics.append(
                _diagnostic(
                    "row",
                    field,
                    "invalid_cell_shape",
                    f"cell {cell_position} must contain exactly heading, salary_text, and marker_text",
                    record_number=record_number,
                )
            )
            continue
        if any(not isinstance(cell[key], str) for key in cell):
            diagnostics.append(
                _diagnostic(
                    "row",
                    field,
                    "invalid_cell_type",
                    f"cell {cell_position} values must all be strings",
                    record_number=record_number,
                )
            )
            continue
        if any(_contains_control(cell[key]) for key in cell):
            diagnostics.append(
                _diagnostic(
                    "row",
                    field,
                    "cell_control_character",
                    f"cell {cell_position} must not contain control characters",
                    record_number=record_number,
                )
            )
            continue
        heading = cell["heading"]
        heading_valid = _ASCII_SEASON_PATTERN.fullmatch(heading) is not None
        if heading_valid:
            try:
                validate_season(heading)
            except SeasonValidationError:
                heading_valid = False
        if not heading_valid:
            diagnostics.append(
                _diagnostic(
                    "row",
                    field,
                    "invalid_heading",
                    f"cell {cell_position} heading must be a consecutive ASCII yyyy-yy season",
                    record_number=record_number,
                )
            )
        elif heading in seen_headings:
            diagnostics.append(
                _diagnostic(
                    "row",
                    field,
                    "duplicate_heading",
                    f"cell {cell_position} repeats a salary-season heading",
                    record_number=record_number,
                )
            )
        seen_headings.add(heading)
        try:
            parse_salary_text(cell["salary_text"])
        except SalaryParseError:
            diagnostics.append(
                _diagnostic(
                    "row",
                    field,
                    "invalid_cell_salary",
                    f"cell {cell_position} salary_text fails issue-#20 grammar",
                    record_number=record_number,
                )
            )
        copied = {
            "heading": heading,
            "salary_text": cell["salary_text"],
            "marker_text": cell["marker_text"],
        }
        cells.append(copied)
        if heading == target_season:
            target_cells.append(copied)
    if len(target_cells) != 1:
        diagnostics.append(
            _diagnostic(
                "row",
                field,
                "target_cell_count",
                "exactly one cell must match target_season",
                record_number=record_number,
            )
        )
    elif (
        target_cells[0]["salary_text"] != target_salary_text
        or target_cells[0]["marker_text"] != target_marker_text
    ):
        diagnostics.append(
            _diagnostic(
                "row",
                field,
                "target_cell_mismatch",
                "target cell salary_text and marker_text must exactly equal top-level fields",
                record_number=record_number,
            )
        )
    return cells


def _canonical_rows(
    result: DeduplicationResult,
    *,
    label: str,
    hash_function: HashFunction = _sha256_hexdigest,
) -> tuple[_CanonicalRow, ...]:
    if not isinstance(result, DeduplicationResult):
        raise CsvFallbackReconciliationError(
            f"{label} input must be a DeduplicationResult."
        )
    rows: list[_CanonicalRow] = []
    seen_fingerprints: dict[str, bytes] = {}
    seen_payloads: set[bytes] = set()
    for position, row in enumerate(result.unique_rows, start=1):
        if not isinstance(row, Mapping):
            raise CsvFallbackReconciliationError(
                f"{label} unique row {position} must be a mapping."
            )
        claimed = row.get("source_row_fingerprint")
        if not isinstance(claimed, str):
            raise CsvFallbackReconciliationError(
                f"{label} unique row {position} has no valid fingerprint."
            )
        canonical_input = dict(row)
        canonical_input.pop("source_row_fingerprint")
        try:
            material, _ = _fingerprint_material(
                canonical_input,
                input_position=position,
                hash_function=hash_function,
            )
        except FingerprintValidationError as error:
            raise CsvFallbackReconciliationError(
                f"{label} unique row {position} cannot be canonically fingerprinted."
            ) from error
        if material.fingerprint != claimed:
            raise CsvFallbackReconciliationError(
                f"{label} unique row {position} fingerprint does not recompute."
            )
        prior = seen_fingerprints.get(claimed)
        if prior is not None:
            if prior != material.canonical_payload:
                raise CsvFallbackReconciliationError(
                    f"Canonical digest collision inside {label} input for {claimed}."
                )
            raise CsvFallbackReconciliationError(
                f"Duplicate unique fingerprint inside {label} input for {claimed}."
            )
        if material.canonical_payload in seen_payloads:
            raise CsvFallbackReconciliationError(
                f"Duplicate canonical unique row inside {label} input."
            )
        seen_fingerprints[claimed] = material.canonical_payload
        seen_payloads.add(material.canonical_payload)
        rows.append(
            _CanonicalRow(
                fingerprint=claimed,
                payload=material.canonical_payload,
            )
        )
    return tuple(rows)


def _validate_occurrence_trail(result: DeduplicationResult, *, label: str) -> None:
    rows_by_fingerprint = {
        row["source_row_fingerprint"]: row
        for row in result.unique_rows
        if isinstance(row, Mapping)
        and isinstance(row.get("source_row_fingerprint"), str)
    }
    fingerprints = set(rows_by_fingerprint)
    occurrences_by_fingerprint = {fingerprint: [] for fingerprint in fingerprints}
    occurrences_by_position: dict[int, SourceRowOccurrence] = {}
    for position, occurrence in enumerate(result.occurrences, start=1):
        if not isinstance(occurrence, SourceRowOccurrence):
            raise CsvFallbackReconciliationError(
                f"{label} occurrence {position} has an invalid type."
            )
        if (
            isinstance(occurrence.input_position, bool)
            or not isinstance(occurrence.input_position, int)
            or occurrence.input_position != position
        ):
            raise CsvFallbackReconciliationError(
                f"{label} occurrence order is inconsistent at position {position}."
            )
        if occurrence.fingerprint not in fingerprints:
            raise CsvFallbackReconciliationError(
                f"{label} occurrence {position} has an unknown fingerprint."
            )
        if (
            isinstance(occurrence.representative_input_position, bool)
            or not isinstance(occurrence.representative_input_position, int)
            or occurrence.representative_input_position < 1
            or any(
                not isinstance(getattr(occurrence, field), str)
                for field in ("source_locator", "imported_at", "rank_text")
            )
            or any(
                isinstance(getattr(occurrence, field), bool)
                or not isinstance(getattr(occurrence, field), int)
                or getattr(occurrence, field) < 1
                for field in ("source_page_number", "source_row_position")
            )
        ):
            raise CsvFallbackReconciliationError(
                f"{label} occurrence {position} has malformed provenance."
            )
        occurrences_by_position[position] = occurrence
        occurrences_by_fingerprint[occurrence.fingerprint].append(occurrence)
    for fingerprint, occurrences in occurrences_by_fingerprint.items():
        if not occurrences:
            raise CsvFallbackReconciliationError(
                f"{label} unique fingerprint {fingerprint} has no occurrence."
            )
        representative_position = occurrences[0].input_position
        for occurrence in occurrences:
            representative = occurrences_by_position.get(
                occurrence.representative_input_position
            )
            if (
                occurrence.representative_input_position != representative_position
                or representative is None
                or representative.fingerprint != fingerprint
            ):
                raise CsvFallbackReconciliationError(
                    f"{label} occurrence {occurrence.input_position} has inconsistent provenance."
                )
        representative = occurrences[0]
        row = rows_by_fingerprint[fingerprint]
        if not (
            representative.representative_input_position
            == representative.input_position
            and representative.source_locator == row.get("source_locator")
            and representative.imported_at == row.get("imported_at")
            and representative.source_page_number == row.get("source_page_number")
            and representative.source_row_position == row.get("source_row_position")
            and representative.rank_text == row.get("rank_text")
        ):
            raise CsvFallbackReconciliationError(
                f"{label} representative occurrence is inconsistent for {fingerprint}."
            )
    representative_order = [
        occurrences_by_fingerprint[row["source_row_fingerprint"]][0].input_position
        for row in result.unique_rows
    ]
    if representative_order != sorted(representative_order):
        raise CsvFallbackReconciliationError(
            f"{label} unique rows are not in first-seen occurrence order."
        )


def _encode_row(row: Mapping[str, Any]) -> dict[str, Any]:
    encoded = deepcopy(dict(row))
    for field in (
        "player_id",
        "team_logo_asset_id",
        "target_season_salary_dollars",
    ):
        value = encoded[field]
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CsvFallbackArtifactError(
                    f"Cannot encode invalid exact integer field {field}."
                )
            encoded[field] = {_EXACT_INTEGER_TAG: format(value, "x")}
    return encoded


def _decode_row(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("row must be an object")
    decoded = deepcopy(value)
    for field in (
        "player_id",
        "team_logo_asset_id",
        "target_season_salary_dollars",
    ):
        encoded = decoded.get(field)
        if encoded is None:
            continue
        if (
            not isinstance(encoded, dict)
            or set(encoded) != {_EXACT_INTEGER_TAG}
            or not isinstance(encoded[_EXACT_INTEGER_TAG], str)
            or _HEX_PATTERN.fullmatch(encoded[_EXACT_INTEGER_TAG]) is None
        ):
            raise ValueError(f"invalid exact integer encoding for {field}")
        decoded[field] = int(encoded[_EXACT_INTEGER_TAG], 16)
    return decoded


def _validate_lineage(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {
        "input_csv",
        "input_metadata",
    }:
        raise CsvFallbackArtifactError("Derived fallback lineage is invalid.")
    for field in ("input_csv", "input_metadata"):
        item = value[field]
        if (
            not isinstance(item, dict)
            or set(item) != {"basename", "sha256"}
            or not _is_safe_basename(item["basename"])
            or not isinstance(item["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None
        ):
            raise CsvFallbackArtifactError("Derived fallback lineage is invalid.")


def _production_path_diagnostics(
    input_path: Path,
    output_path: Path | None,
) -> list[CsvFallbackDiagnostic]:
    diagnostics: list[CsvFallbackDiagnostic] = []
    if not _is_within_private(input_path):
        diagnostics.append(
            _diagnostic(
                "file",
                "input_storage",
                "outside_private",
                "production commissioner CSV must stay under ignored data/private",
            )
        )
    if input_path.suffix != ".csv" or not _is_safe_basename(input_path.name):
        diagnostics.append(
            _diagnostic(
                "file",
                "input_basename",
                "unsafe_basename",
                "input CSV basename must be lowercase, sanitized, and end in .csv",
            )
        )
    if output_path is not None:
        if not _is_within_private(output_path):
            diagnostics.append(
                _diagnostic(
                    "file",
                    "output_storage",
                    "outside_private",
                    "derived commissioner output must stay under ignored data/private",
                )
            )
        if output_path.suffix != ".json" or not _is_safe_basename(output_path.name):
            diagnostics.append(
                _diagnostic(
                    "file",
                    "output_basename",
                    "unsafe_basename",
                    "output basename must be lowercase, sanitized, and end in .json",
                )
            )
    return diagnostics


def _is_within_private(path: Path) -> bool:
    try:
        path.resolve().relative_to(_PRIVATE_ROOT.resolve())
    except (OSError, ValueError):
        return False
    return True


def _is_safe_basename(value: Any) -> bool:
    return (
        isinstance(value, str)
        and _SAFE_BASENAME_PATTERN.fullmatch(value) is not None
        and _SENSITIVE_LOCATOR_PATTERN.search(value) is None
    )


def _display_basename(path: Path) -> str:
    return path.name if _is_safe_basename(path.name) else "fallback-artifact"


def _is_safe_locator(value: Any) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or _contains_control(value)
        or _SENSITIVE_LOCATOR_PATTERN.search(value) is not None
        or value.startswith(("/", "\\", "~"))
        or _WINDOWS_ABSOLUTE_PATTERN.match(value) is not None
        or "file://" in value.lower()
    ):
        return False
    parsed = urlsplit(value)
    if parsed.scheme:
        if parsed.scheme.lower() not in {"http", "https"}:
            return False
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
        ):
            return False
    return True


def _is_utc_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or _UTC_TIMESTAMP_PATTERN.fullmatch(value) is None:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return False
    return True


def _contains_control(value: str) -> bool:
    return any(unicodedata.category(character).startswith("C") for character in value)


def _json_loads_strict(value: str) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise _DuplicateJsonKey(key)
            result[key] = item
        return result

    def reject_constant(value: str) -> None:
        raise json.JSONDecodeError("non-finite number", value, 0)

    return json.loads(
        value,
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
    )


def _ascii_digits_to_int(value: str) -> int:
    chunk_digits = 9
    chunk_base = 1_000_000_000
    first_length = len(value) % chunk_digits or chunk_digits
    result = int(value[:first_length], 10)
    for start in range(first_length, len(value), chunk_digits):
        result = result * chunk_base + int(value[start : start + chunk_digits], 10)
    return result


def _diagnostic(
    location: str,
    field: str,
    code: str,
    message: str,
    *,
    record_number: int | None = None,
) -> CsvFallbackDiagnostic:
    return CsvFallbackDiagnostic(
        location=location,
        field=field,
        code=code,
        message=message,
        record_number=record_number,
    )


def _diagnostic_key(diagnostic: CsvFallbackDiagnostic) -> tuple[int, int, int, str]:
    location_order = {"metadata": 0, "file": 1, "row": 2}
    if diagnostic.location == "metadata":
        field_order = (
            _METADATA_FIELDS.index(diagnostic.field)
            if diagnostic.field in _METADATA_FIELDS
            else -1
        )
    elif diagnostic.location == "row":
        field_order = (
            CSV_HEADER_FIELDS.index(diagnostic.field)
            if diagnostic.field in CSV_HEADER_FIELDS
            else -1
        )
    else:
        field_order = 0
    return (
        location_order[diagnostic.location],
        diagnostic.record_number or 0,
        field_order,
        diagnostic.code,
    )
