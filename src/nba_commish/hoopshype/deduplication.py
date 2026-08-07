"""Versioned fingerprints and ordered deduplication for normalized salary rows."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

from nba_commish.hoopshype.errors import (
    FingerprintCollisionError,
    FingerprintValidationError,
    SalaryParseError,
)
from nba_commish.hoopshype.salary import parse_salary_text

FINGERPRINT_VERSION = 1
FINGERPRINT_DOMAIN = b"nba-commish/hoopshype-row-fingerprint/v1\x00"
FINGERPRINT_PREFIX = "hoopshype-row-v1:sha256:"

_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SEASON_PATTERN = re.compile(r"^([0-9]{4})-([0-9]{2})$")
_LENGTH_BYTES = 8

HashFunction = Callable[[bytes], str]


@dataclass(frozen=True)
class SourceRowOccurrence:
    """Provenance for one physical input row and its representative group."""

    input_position: int
    fingerprint: str
    representative_input_position: int
    source_locator: str
    imported_at: str
    source_page_number: int
    source_row_position: int
    rank_text: str


@dataclass(frozen=True)
class DeduplicationResult:
    """First-seen canonical rows plus one occurrence per physical input row."""

    unique_rows: tuple[dict[str, Any], ...]
    occurrences: tuple[SourceRowOccurrence, ...]


@dataclass(frozen=True)
class _FingerprintMaterial:
    canonical_payload: bytes
    fingerprint: str


@dataclass(frozen=True)
class _OccurrenceFields:
    source_locator: str
    imported_at: str
    source_page_number: int
    source_row_position: int
    rank_text: str


def fingerprint_salary_row(
    row: Mapping[str, Any],
    *,
    hash_function: HashFunction | None = None,
) -> str:
    """Return the stable version-1 fingerprint for one normalized row."""

    material, _ = _fingerprint_material(
        row,
        input_position=None,
        hash_function=hash_function or _sha256_hexdigest,
    )
    return material.fingerprint


def deduplicate_salary_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    hash_function: HashFunction | None = None,
) -> DeduplicationResult:
    """Collapse exact canonical duplicates while preserving every occurrence."""

    if isinstance(rows, (str, bytes, Mapping)) or not isinstance(rows, Iterable):
        raise FingerprintValidationError(
            "Invalid normalized salary collection: expected an iterable of row mappings."
        )

    digest = hash_function or _sha256_hexdigest
    representatives: dict[str, tuple[bytes, int]] = {}
    unique_rows: list[dict[str, Any]] = []
    occurrences: list[SourceRowOccurrence] = []

    for input_position, row in enumerate(rows, start=1):
        material, occurrence = _fingerprint_material(
            row,
            input_position=input_position,
            hash_function=digest,
        )
        existing = representatives.get(material.fingerprint)
        if existing is None:
            representative_position = input_position
            representatives[material.fingerprint] = (
                material.canonical_payload,
                input_position,
            )
            representative = deepcopy(dict(row))
            representative["source_row_fingerprint"] = material.fingerprint
            unique_rows.append(representative)
        else:
            existing_payload, representative_position = existing
            if existing_payload != material.canonical_payload:
                raise FingerprintCollisionError(
                    "Fingerprint collision between input positions "
                    f"{representative_position} and {input_position} for "
                    f"{material.fingerprint}."
                )

        occurrences.append(
            SourceRowOccurrence(
                input_position=input_position,
                fingerprint=material.fingerprint,
                representative_input_position=representative_position,
                source_locator=occurrence.source_locator,
                imported_at=occurrence.imported_at,
                source_page_number=occurrence.source_page_number,
                source_row_position=occurrence.source_row_position,
                rank_text=occurrence.rank_text,
            )
        )

    return DeduplicationResult(
        unique_rows=tuple(unique_rows),
        occurrences=tuple(occurrences),
    )


def _fingerprint_material(
    row: Mapping[str, Any],
    *,
    input_position: int | None,
    hash_function: HashFunction,
) -> tuple[_FingerprintMaterial, _OccurrenceFields]:
    if not isinstance(row, Mapping):
        raise FingerprintValidationError(
            _with_context(
                "Invalid normalized salary row: expected a mapping",
                row=None,
                input_position=input_position,
            )
        )
    if "source_row_fingerprint" in row:
        raise FingerprintValidationError(
            _with_context(
                "Unexpected source_row_fingerprint on normalized input row",
                row=row,
                input_position=input_position,
            )
        )

    occurrence = _occurrence_fields(row, input_position=input_position)
    context = _context(row, input_position=input_position)

    target_season = _required_string(row, "target_season", context=context)
    _validate_season(target_season, field="target_season", context=context)

    player_id = _nullable_nonnegative_integer(row, "player_id", context=context)
    player_url = _nullable_string(row, "player_url", context=context)
    player_display = _required_string(row, "player_display_text", context=context)
    if not _collapse_whitespace(player_display):
        raise FingerprintValidationError(
            f"player_display_text must not be empty{context}."
        )
    canonical_player_url = None
    if player_url:
        canonical_player_url = _canonical_player_url(player_url, context=context)
    if player_id is not None:
        player_identity = _encode_sequence(
            (_encode_string("player_id"), _encode_integer(player_id))
        )
    elif canonical_player_url is not None:
        player_identity = _encode_sequence(
            (_encode_string("player_url"), _encode_string(canonical_player_url))
        )
    else:
        fallback_player = _collapse_whitespace(
            unicodedata.normalize("NFC", player_display)
        ).casefold()
        if not fallback_player:
            raise FingerprintValidationError(
                f"Linkless player identity must not be empty{context}."
            )
        player_identity = _encode_sequence(
            (_encode_string("player_display_text"), _encode_string(fallback_player))
        )

    logo_asset_id = _nullable_nonnegative_integer(
        row, "team_logo_asset_id", context=context
    )
    logo_url = _required_string(row, "team_logo_url", context=context)
    canonical_logo_url = _canonical_full_http_url(
        logo_url, field="team_logo_url", context=context
    )
    if logo_asset_id is not None:
        team_identity = _encode_sequence(
            (_encode_string("team_logo_asset_id"), _encode_integer(logo_asset_id))
        )
    else:
        team_identity = _encode_sequence(
            (_encode_string("team_logo_url"), _encode_string(canonical_logo_url))
        )

    target_salary_text = _required_string(
        row, "target_season_salary_text", context=context
    )
    target_salary = _nullable_nonnegative_integer(
        row, "target_season_salary_dollars", context=context
    )
    parsed_target_salary = _parse_salary(
        target_salary_text,
        field="target_season_salary_text",
        context=context,
    )
    if parsed_target_salary != target_salary:
        raise FingerprintValidationError(
            "target_season_salary_dollars is inconsistent with "
            f"target_season_salary_text{context}."
        )

    target_marker = _canonical_marker(
        _required_string(row, "target_season_marker_text", context=context)
    )
    description = _canonical_description(
        _nullable_string(row, "source_row_description", context=context)
    )
    retained_cells = _canonical_cells(
        row,
        target_season=target_season,
        target_salary=target_salary,
        target_marker=target_marker,
        context=context,
    )

    canonical_payload = _encode_sequence(
        (
            _encode_field("target_season", _encode_string(target_season)),
            _encode_field("player_identity", player_identity),
            _encode_field("team_identity", team_identity),
            _encode_field(
                "target_season_salary_dollars", _encode_nullable_integer(target_salary)
            ),
            _encode_field("target_season_marker", _encode_string(target_marker)),
            _encode_field(
                "source_row_description", _encode_nullable_string(description)
            ),
            _encode_field("salary_season_cells", _encode_sequence(retained_cells)),
        )
    )
    digest = hash_function(FINGERPRINT_DOMAIN + canonical_payload)
    if not isinstance(digest, str) or _DIGEST_PATTERN.fullmatch(digest) is None:
        raise FingerprintValidationError(
            f"Hash function must return exactly 64 lowercase hexadecimal characters{context}."
        )
    return (
        _FingerprintMaterial(
            canonical_payload=canonical_payload,
            fingerprint=FINGERPRINT_PREFIX + digest,
        ),
        occurrence,
    )


def _canonical_cells(
    row: Mapping[str, Any],
    *,
    target_season: str,
    target_salary: int | None,
    target_marker: str,
    context: str,
) -> tuple[bytes, ...]:
    cells = _required_field(row, "salary_season_cells", context=context)
    if not isinstance(cells, list):
        raise FingerprintValidationError(
            f"salary_season_cells must be a list of mappings{context}."
        )

    canonical: list[tuple[str, int | None, str]] = []
    seen_headings: set[str] = set()
    for cell_position, cell in enumerate(cells, start=1):
        if not isinstance(cell, Mapping):
            raise FingerprintValidationError(
                f"salary_season_cells entry {cell_position} must be a mapping{context}."
            )
        cell_context = f" in salary_season_cells entry {cell_position}{context}"
        heading = _required_string(cell, "heading", context=cell_context)
        _validate_season(heading, field="heading", context=cell_context)
        if heading in seen_headings:
            raise FingerprintValidationError(
                f"Duplicate salary-season heading {heading!r}{context}."
            )
        seen_headings.add(heading)
        salary_text = _required_string(cell, "salary_text", context=cell_context)
        amount = _parse_salary(
            salary_text,
            field="salary_text",
            context=cell_context,
        )
        marker = _canonical_marker(
            _required_string(cell, "marker_text", context=cell_context)
        )
        canonical.append((heading, amount, marker))

    target_cells = [cell for cell in canonical if cell[0] == target_season]
    if len(target_cells) != 1:
        raise FingerprintValidationError(
            "salary_season_cells must contain exactly one entry matching "
            f"target_season{context}."
        )
    _, retained_target_salary, retained_target_marker = target_cells[0]
    if retained_target_salary != target_salary:
        raise FingerprintValidationError(
            "Target retained salary cell is inconsistent with "
            f"target_season_salary_dollars{context}."
        )
    if retained_target_marker != target_marker:
        raise FingerprintValidationError(
            "Target retained salary marker is inconsistent with "
            f"target_season_marker_text{context}."
        )

    canonical.sort(key=lambda cell: cell[0])
    return tuple(
        _encode_sequence(
            (
                _encode_string(heading),
                _encode_nullable_integer(amount),
                _encode_string(marker),
            )
        )
        for heading, amount, marker in canonical
    )


def _occurrence_fields(
    row: Mapping[str, Any], *, input_position: int | None
) -> _OccurrenceFields:
    context = _context(row, input_position=input_position)
    return _OccurrenceFields(
        source_locator=_required_string(row, "source_locator", context=context),
        imported_at=_required_string(row, "imported_at", context=context),
        source_page_number=_positive_integer(
            row, "source_page_number", context=context
        ),
        source_row_position=_positive_integer(
            row, "source_row_position", context=context
        ),
        rank_text=_required_string(row, "rank_text", context=context),
    )


def _parse_salary(value: str, *, field: str, context: str) -> int | None:
    try:
        return parse_salary_text(value)
    except SalaryParseError as error:
        raise FingerprintValidationError(
            f"Malformed {field} for fingerprinting{context}."
        ) from error


def _canonical_description(value: str | None) -> str | None:
    if value is None:
        return None
    canonical = _collapse_whitespace(unicodedata.normalize("NFC", value))
    return canonical or None


def _canonical_marker(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


def _collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


def _canonical_player_url(value: str, *, context: str) -> str:
    parsed = _split_url(value, field="player_url", context=context)
    if parsed.username is not None or parsed.password is not None:
        raise FingerprintValidationError(
            f"player_url must not contain URL user information{context}."
        )
    if parsed.scheme:
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise FingerprintValidationError(
                f"player_url must be HTTP(S), scheme-relative, or root-relative{context}."
            )
        return _url_without_query_fragment(parsed)
    if parsed.netloc:
        if not value.startswith("//") or not parsed.hostname:
            raise FingerprintValidationError(f"Invalid player_url{context}.")
        return _url_without_query_fragment(parsed)
    if not parsed.path.startswith("/") or parsed.path.startswith("//"):
        raise FingerprintValidationError(
            f"player_url must be HTTP(S), scheme-relative, or root-relative{context}."
        )
    return parsed.path


def _canonical_full_http_url(value: str, *, field: str, context: str) -> str:
    parsed = _split_url(value, field=field, context=context)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise FingerprintValidationError(
            f"{field} must be a full public HTTP(S) URL without user information{context}."
        )
    return _url_without_query_fragment(parsed)


def _split_url(value: str, *, field: str, context: str) -> SplitResult:
    if (
        not value
        or value != value.strip()
        or any(character.isspace() for character in value)
        or any(unicodedata.category(character).startswith("C") for character in value)
        or "\\" in value
    ):
        raise FingerprintValidationError(f"Invalid {field}{context}.")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as error:
        raise FingerprintValidationError(f"Invalid {field}{context}.") from error
    return parsed


def _url_without_query_fragment(value: SplitResult) -> str:
    return urlunsplit(
        (
            value.scheme.lower(),
            value.netloc.lower(),
            value.path,
            "",
            "",
        )
    )


def _validate_season(value: str, *, field: str, context: str) -> None:
    match = _SEASON_PATTERN.fullmatch(value)
    if match is None or int(match.group(2)) != (int(match.group(1)) + 1) % 100:
        raise FingerprintValidationError(
            f"{field} must be a consecutive yyyy-yy season{context}."
        )


def _required_field(row: Mapping[str, Any], field: str, *, context: str) -> Any:
    if field not in row:
        raise FingerprintValidationError(f"Missing required {field}{context}.")
    return row[field]


def _required_string(row: Mapping[str, Any], field: str, *, context: str) -> str:
    value = _required_field(row, field, context=context)
    if not isinstance(value, str):
        raise FingerprintValidationError(f"{field} must be a string{context}.")
    return value


def _nullable_string(row: Mapping[str, Any], field: str, *, context: str) -> str | None:
    value = _required_field(row, field, context=context)
    if value is not None and not isinstance(value, str):
        raise FingerprintValidationError(f"{field} must be a string or null{context}.")
    return value


def _positive_integer(row: Mapping[str, Any], field: str, *, context: str) -> int:
    value = _required_field(row, field, context=context)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise FingerprintValidationError(
            f"{field} must be a positive integer{context}."
        )
    return value


def _nullable_nonnegative_integer(
    row: Mapping[str, Any], field: str, *, context: str
) -> int | None:
    value = _required_field(row, field, context=context)
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value < 0
    ):
        raise FingerprintValidationError(
            f"{field} must be a nonnegative integer or null{context}."
        )
    return value


def _encode_field(name: str, value: bytes) -> bytes:
    return _encode_sequence((_encode_string(name), value))


def _encode_nullable_string(value: str | None) -> bytes:
    return _encode_null() if value is None else _encode_string(value)


def _encode_nullable_integer(value: int | None) -> bytes:
    return _encode_null() if value is None else _encode_integer(value)


def _encode_string(value: str) -> bytes:
    return _encode_typed(b"S", value.encode("utf-8"))


def _encode_integer(value: int) -> bytes:
    width = max(1, (value.bit_length() + 7) // 8)
    return _encode_typed(b"I", value.to_bytes(width, byteorder="big", signed=False))


def _encode_null() -> bytes:
    return _encode_typed(b"N", b"")


def _encode_sequence(values: Sequence[bytes]) -> bytes:
    return _encode_typed(b"L", b"".join(values))


def _encode_typed(tag: bytes, payload: bytes) -> bytes:
    return tag + len(payload).to_bytes(_LENGTH_BYTES, byteorder="big") + payload


def _sha256_hexdigest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _context(row: Mapping[str, Any], *, input_position: int | None) -> str:
    parts: list[str] = []
    if input_position is not None:
        parts.append(f"input position {input_position}")
    page = row.get("source_page_number")
    if isinstance(page, int) and not isinstance(page, bool):
        parts.append(f"source page {page}")
    position = row.get("source_row_position")
    if isinstance(position, int) and not isinstance(position, bool):
        parts.append(f"source row {position}")
    player = row.get("player_display_text")
    if isinstance(player, str) and player:
        safe_player = "".join(
            character if character.isprintable() else "?" for character in player
        )[:80]
        parts.append(f"player {safe_player!r}")
    return f" at {', '.join(parts)}" if parts else ""


def _with_context(
    message: str,
    *,
    row: Mapping[str, Any] | None,
    input_position: int | None,
) -> str:
    if row is None:
        if input_position is None:
            return f"{message}."
        return f"{message} at input position {input_position}."
    return f"{message}{_context(row, input_position=input_position)}."
