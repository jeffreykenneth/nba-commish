from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

import nba_commish.hoopshype.csv_fallback as fallback
from nba_commish.hoopshype.artifact import write_artifact_atomic
from nba_commish.hoopshype.csv_fallback import (
    CSV_HEADER,
    CSV_HEADER_FIELDS,
    build_csv_fallback_artifact,
    load_csv_fallback_artifact,
    load_synthetic_csv_fixture_for_tests,
    reconcile_salary_rows,
    run_csv_fallback_import,
)
from nba_commish.hoopshype.deduplication import (
    DeduplicationResult,
    deduplicate_salary_rows,
)
from nba_commish.hoopshype.errors import (
    CsvFallbackArtifactError,
    CsvFallbackReconciliationError,
    CsvFallbackValidationError,
)
from nba_commish.hoopshype_csv_import import main as cli_main

FIXTURE = (
    Path(__file__).parents[1]
    / "data"
    / "fixtures"
    / "commissioner-salary-csv--season-2026-27--league-fixture-01--"
    "20260807t221000z.csv"
)
IMPORTED_AT = "2026-08-07T22:15:00Z"
GENERATED_AT = "2026-08-07T22:16:00Z"


def _cells(
    salary: str = "$1,000",
    marker: str = "",
    *,
    future: bool = False,
) -> str:
    cells = [{"heading": "2026-27", "salary_text": salary, "marker_text": marker}]
    if future:
        cells.append({"heading": "2027-28", "salary_text": "-", "marker_text": ""})
    return json.dumps(cells, ensure_ascii=False, separators=(",", ":"))


def _record(**overrides: str) -> list[str]:
    values = {
        "player_display_text": "Fixture Player",
        "player_url": "/salaries/players/fixture-player/1001/",
        "player_id": "1001",
        "team_display_text": "Fixture Team",
        "team_logo_url": "https://cdn.example.test/nba/logos/9.png?width=30",
        "team_logo_asset_id": "9",
        "target_season": "2026-27",
        "target_season_salary_text": "$1,000",
        "target_season_marker_text": "",
        "source_row_description": "",
        "salary_season_cells_json": _cells(),
        "source_row_reference": "fixture-reference",
    }
    values.update(overrides)
    return [values[field] for field in CSV_HEADER_FIELDS]


def _csv_bytes(
    records: list[list[str]],
    *,
    header: tuple[str, ...] = CSV_HEADER_FIELDS,
    lineterminator: str = "\r\n",
) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(
        stream,
        delimiter=",",
        quotechar='"',
        doublequote=True,
        escapechar=None,
        lineterminator=lineterminator,
    )
    writer.writerow(header)
    writer.writerows(records)
    return stream.getvalue().encode("utf-8")


def _metadata(
    *,
    mode: str = "production",
    **overrides: Any,
) -> bytes:
    if mode == "production":
        value: dict[str, Any] = {
            "source_system": "commissioner_salary_csv",
            "source_locator": "sanitized commissioner salary source",
            "retrieved_at": "2026-08-07T22:00:00Z",
            "generated_at": None,
            "source_event_time": None,
            "season": "2026-27",
            "league_scope": None,
            "artifact_version": "1.0",
            "schema_version": "1.0",
            "redaction_status": "unredacted-local",
        }
    else:
        value = {
            "source_system": "synthetic",
            "source_locator": "synthetic commissioner salary CSV fixture",
            "retrieved_at": None,
            "generated_at": "2026-08-07T22:10:00Z",
            "source_event_time": None,
            "season": "2026-27",
            "league_scope": None,
            "artifact_version": "1.0",
            "schema_version": "1.0",
            "redaction_status": "synthetic",
        }
    value.update(overrides)
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _parse(
    records: list[list[str]],
    *,
    csv_bytes: bytes | None = None,
    metadata_bytes: bytes | None = None,
    metadata_mode: str = "production",
    selected_season: str = "2026-27",
    imported_at: str = IMPORTED_AT,
    initial_diagnostics: tuple[fallback.CsvFallbackDiagnostic, ...] = (),
) -> fallback.CommissionerCsvImport:
    return fallback._parse_csv_evidence(
        csv_bytes if csv_bytes is not None else _csv_bytes(records),
        metadata_bytes if metadata_bytes is not None else _metadata(mode=metadata_mode),
        selected_season=selected_season,
        imported_at=imported_at,
        input_basename="commissioner-salary--season-2026-27--20260807t220000z.csv",
        metadata_basename=(
            "commissioner-salary--season-2026-27--20260807t220000z.metadata.json"
        ),
        metadata_mode=metadata_mode,
        initial_diagnostics=initial_diagnostics,
    )


def _diagnostic_codes(error: CsvFallbackValidationError) -> list[str]:
    return [diagnostic.code for diagnostic in error.diagnostics]


def test_fixed_header_is_exact_and_synthetic_fixture_has_strict_bytes() -> None:
    data = FIXTURE.read_bytes()
    assert CSV_HEADER == (
        "player_display_text,player_url,player_id,team_display_text,"
        "team_logo_url,team_logo_asset_id,target_season,"
        "target_season_salary_text,target_season_marker_text,"
        "source_row_description,salary_season_cells_json,source_row_reference"
    )
    assert data.startswith(CSV_HEADER.encode() + b"\r\n")
    assert data.endswith(b"\r\n")
    assert b"\n" not in data.replace(b"\r\n", b"")
    assert b"\r" not in data.replace(b"\r\n", b"")
    assert b"\x00" not in data


def test_synthetic_fixture_boundary_is_lossless_and_deduplicates() -> None:
    result = load_synthetic_csv_fixture_for_tests(
        FIXTURE,
        "2026-27",
        imported_at=IMPORTED_AT,
    )
    assert len(result.unique_rows) == 2
    assert len(result.occurrences) == 3
    assert [occurrence.input_position for occurrence in result.occurrences] == [1, 2, 3]
    assert [
        occurrence.representative_input_position for occurrence in result.occurrences
    ] == [1, 1, 3]

    first, null_row = result.unique_rows
    assert first["player_display_text"] == "Fixture Player A"
    assert first["player_id"] == 1001
    assert first["player_id_csv_text"] == "001001"
    assert first["team_logo_asset_id"] == 9
    assert first["team_logo_asset_id_csv_text"] == "0009"
    assert first["team_display_text"] == "Fixture Team, A"
    assert first["source_row_reference"] == "reference, A"
    assert first["source_row_description"] == 'commissioner note, with "quotes"'
    assert first["target_season_salary_text"] == "$1,000"
    assert first["target_season_salary_dollars"] == 1_000
    assert len(first["salary_season_cells"]) == 2
    assert first["salary_season_cells_json"].startswith("[")
    assert first["source_system"] == "commissioner_salary_csv"
    assert first["source_page_number"] == 1
    assert first["source_row_position"] == 1
    assert first["rank_text"] == ""
    assert first["source_locator"].endswith("(CSV data record 1)")
    assert first["imported_at"] == IMPORTED_AT

    assert null_row["player_url"] is None
    assert null_row["player_id"] is None
    assert null_row["team_logo_asset_id"] is None
    assert null_row["target_season_salary_text"] == ""
    assert null_row["target_season_salary_dollars"] is None
    assert null_row["target_season_marker_text"] == "TW"
    assert null_row["source_row_description"] == "synthetic review evidence"


def test_production_metadata_and_nullable_fields_are_valid() -> None:
    imported = _parse(
        [
            _record(
                player_url="",
                player_id="",
                team_display_text="",
                team_logo_asset_id="",
                target_season_salary_text="",
                target_season_marker_text="",
                source_row_description="",
                salary_season_cells_json=_cells(""),
                source_row_reference="",
            )
        ]
    )
    row = imported.deduplication.unique_rows[0]
    assert row["player_url"] is None
    assert row["player_id"] is None
    assert row["team_display_text"] is None
    assert row["team_logo_asset_id"] is None
    assert row["source_row_description"] is None
    assert row["source_row_reference"] is None
    assert row["target_season_salary_dollars"] is None


@pytest.mark.parametrize(
    "header",
    [
        CSV_HEADER_FIELDS[:-1],
        (*CSV_HEADER_FIELDS, "extra"),
        tuple(reversed(CSV_HEADER_FIELDS)),
        (*CSV_HEADER_FIELDS[:-1], "renamed"),
        (*CSV_HEADER_FIELDS[:-1], CSV_HEADER_FIELDS[0]),
    ],
)
def test_missing_extra_reordered_renamed_or_duplicate_header_fails(
    header: tuple[str, ...],
) -> None:
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([_record()], csv_bytes=_csv_bytes([_record()], header=header))
    assert _diagnostic_codes(failure.value) == ["header_mismatch"]


def test_quoted_header_is_not_accepted_as_exact_bytes() -> None:
    data = _csv_bytes([_record()]).replace(
        CSV_HEADER.encode(), f'"{CSV_HEADER}"'.encode(), 1
    )
    with pytest.raises(CsvFallbackValidationError, match="header"):
        _parse([_record()], csv_bytes=data)


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (lambda value: b"\xef\xbb\xbf" + value, "bom"),
        (lambda value: value[:-2], "missing_final_crlf"),
        (lambda value: value.replace(b"\r\n", b"\n"), "non_crlf"),
        (lambda value: value.replace(b"\r\n", b"\r"), "non_crlf"),
        (lambda value: value.replace(b"Fixture Player", b"Fixture\x00Player"), "nul"),
        (
            lambda value: value.replace(b"Fixture Player", b"Fixture\tPlayer"),
            "control_character",
        ),
        (
            lambda value: value.replace(b"Fixture Player", b'Fixture"Player'),
            "bare_quote",
        ),
    ],
)
def test_byte_encoding_newline_control_and_quote_families_fail(
    mutator: Any,
    code: str,
) -> None:
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([_record()], csv_bytes=mutator(_csv_bytes([_record()])))
    assert code in _diagnostic_codes(failure.value)


def test_invalid_utf8_and_embedded_or_unclosed_quoted_newline_fail_as_file_errors() -> (
    None
):
    invalid_utf8 = _csv_bytes([_record()]).replace(
        b"Fixture Player", b"Fixture\xffPlayer"
    )
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([_record()], csv_bytes=invalid_utf8)
    assert _diagnostic_codes(failure.value) == ["invalid_utf8"]

    embedded = _csv_bytes([_record(source_row_description="line one\nline two")])
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([], csv_bytes=embedded)
    assert "embedded_newline" in _diagnostic_codes(failure.value)

    unclosed = _csv_bytes([_record()]) + b'"unclosed\r\n'
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([], csv_bytes=unclosed)
    assert "embedded_newline" in _diagnostic_codes(failure.value)


def test_missing_data_and_blank_records_fail_without_guessing() -> None:
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([], csv_bytes=(CSV_HEADER + "\r\n").encode())
    assert _diagnostic_codes(failure.value) == ["missing_data"]

    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([], csv_bytes=(CSV_HEADER + "\r\n\r\n").encode())
    assert _diagnostic_codes(failure.value) == ["missing_data", "blank_record"]


def test_commas_and_double_quotes_round_trip_only_through_csv_quoting() -> None:
    description = 'quoted, evidence says "review"'
    imported = _parse([_record(source_row_description=description)])
    assert (
        imported.deduplication.unique_rows[0]["source_row_description"] == description
    )

    malformed = _csv_bytes([_record(source_row_description=description)]).replace(
        b'"quoted, evidence says ""review"""',
        b'quoted, evidence says "review"',
    )
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([], csv_bytes=malformed)
    assert any(
        code in _diagnostic_codes(failure.value)
        for code in ("bare_quote", "header_mismatch", "invalid_quote_escape")
    )


@pytest.mark.parametrize(
    ("metadata_bytes", "code"),
    [
        (b"\xef\xbb\xbf" + _metadata(), "bom"),
        (b"\xff", "invalid_utf8"),
        (b"[]\n", "wrong_type"),
        (b"{invalid}\n", "invalid_json"),
        (b'{"source_system":"a","source_system":"b"}\n', "invalid_json"),
    ],
)
def test_sidecar_encoding_json_root_and_duplicate_keys_fail(
    metadata_bytes: bytes,
    code: str,
) -> None:
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([_record()], metadata_bytes=metadata_bytes)
    assert code in _diagnostic_codes(failure.value)


def test_missing_sidecar_and_row_errors_accumulate_metadata_then_row() -> None:
    missing = fallback.CsvFallbackDiagnostic(
        location="metadata",
        field="sidecar",
        code="missing",
        message="required same-basename .metadata.json sidecar is missing or unreadable",
    )
    bad = _record(player_display_text="", player_id="-1")
    with pytest.raises(CsvFallbackValidationError) as failure:
        fallback._parse_csv_evidence(
            _csv_bytes([bad]),
            None,
            selected_season="2026-27",
            imported_at=IMPORTED_AT,
            input_basename="safe.csv",
            metadata_basename="safe.metadata.json",
            metadata_mode="production",
            initial_diagnostics=(missing,),
        )
    assert [item.location for item in failure.value.diagnostics] == [
        "metadata",
        "row",
        "row",
        "row",
    ]
    assert [item.field for item in failure.value.diagnostics[1:]] == [
        "player_display_text",
        "player_display_text",
        "player_id",
    ]


def test_missing_extra_and_wrong_production_metadata_fields_accumulate() -> None:
    value = json.loads(_metadata())
    del value["retrieved_at"]
    value["extra"] = "not allowed"
    value["source_system"] = "hoopshype"
    value["generated_at"] = "2026-08-07T22:00:00Z"
    value["source_event_time"] = "invalid"
    value["league_scope"] = "private"
    value["artifact_version"] = "2"
    value["schema_version"] = 1
    value["redaction_status"] = "public-source-reviewed"
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([_record()], metadata_bytes=json.dumps(value).encode())
    codes = _diagnostic_codes(failure.value)
    assert "missing" in codes
    assert "extra" in codes
    assert "invalid_value" in codes
    assert "invalid_timestamp" in codes
    assert "must_be_null" in codes
    assert codes.count("unsupported_version") == 2


@pytest.mark.parametrize(
    "locator",
    [
        "",
        " surrounding ",
        "/private/absolute/path.csv",
        "C:\\private\\input.csv",
        "file:///private/input.csv",
        "https://user:secret@example.test/path",
        "https://example.test/path?token=value",
        "contains cookie value",
        "line\nvalue",
    ],
)
def test_unsafe_metadata_locator_is_rejected_without_echo(locator: str) -> None:
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([_record()], metadata_bytes=_metadata(source_locator=locator))
    message = str(failure.value)
    assert "source locator must be nonempty and sanitized" in message
    if locator:
        assert locator not in message


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_system", "synthetic"),
        ("retrieved_at", None),
        ("retrieved_at", "2026-08-07T22:00:00+00:00"),
        ("generated_at", "2026-08-07T22:00:00Z"),
        ("source_event_time", "2026-99-99T00:00:00Z"),
        ("season", "2026-28"),
        ("season", "2025-26"),
        ("league_scope", "league-private"),
        ("artifact_version", "2.0"),
        ("schema_version", "2.0"),
        ("redaction_status", "synthetic"),
        ("redaction_status", "public-source-reviewed"),
    ],
)
def test_each_production_metadata_rule_fails(field: str, value: Any) -> None:
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([_record()], metadata_bytes=_metadata(**{field: value}))
    assert any(item.field == field for item in failure.value.diagnostics)


def test_synthetic_metadata_is_only_accepted_at_explicit_test_boundary() -> None:
    with pytest.raises(CsvFallbackValidationError):
        _parse([_record()], metadata_bytes=_metadata(mode="synthetic"))
    imported = _parse(
        [_record()],
        metadata_bytes=_metadata(mode="synthetic"),
        metadata_mode="synthetic",
    )
    assert len(imported.deduplication.unique_rows) == 1


@pytest.mark.parametrize(
    "field",
    [
        "player_display_text",
        "team_logo_url",
        "target_season",
        "salary_season_cells_json",
    ],
)
def test_each_nonempty_required_csv_field_is_enforced(field: str) -> None:
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([_record(**{field: ""})])
    assert any(
        item.field == field and item.code == "required"
        for item in failure.value.diagnostics
    )


def test_whitespace_only_player_display_fails_issue21_identity_without_echo() -> None:
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([_record(player_display_text=" PRIVATE\tNAME ")])
    message = str(failure.value)
    assert "control characters" in message
    assert "PRIVATE" not in message

    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([_record(player_display_text="   ")])
    assert "non-whitespace" in str(failure.value)


@pytest.mark.parametrize(
    "value",
    ["-1", "+1", " 1", "1 ", "1,000", "1.0", "true", "False", "１２"],
)
@pytest.mark.parametrize("field", ["player_id", "team_logo_asset_id"])
def test_optional_ids_accept_only_unsigned_ascii_digits(
    field: str,
    value: str,
) -> None:
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([_record(**{field: value})])
    diagnostic = next(item for item in failure.value.diagnostics if item.field == field)
    assert diagnostic.code == "invalid_unsigned_integer"


def test_arbitrarily_long_ids_are_exact_without_single_int_string_conversion() -> None:
    digits = "9" * 4_301
    imported = _parse(
        [
            _record(
                player_id=digits,
                team_logo_asset_id=digits,
                player_url="",
            )
        ]
    )
    row = imported.deduplication.unique_rows[0]
    expected = (10**4_301) - 1
    assert row["player_id"] == expected
    assert row["team_logo_asset_id"] == expected
    assert row["player_id_csv_text"] == digits


@pytest.mark.parametrize(
    "player_url",
    [
        "relative/path",
        "ftp://example.test/player",
        "https:///missing-host",
        "https://user:secret@example.test/player",
        " /player",
        "\\bad\\path",
    ],
)
def test_invalid_player_urls_fail_even_when_id_is_present(player_url: str) -> None:
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([_record(player_url=player_url)])
    assert any(
        item.field == "player_url" and item.code == "invalid_url"
        for item in failure.value.diagnostics
    )
    assert player_url not in str(failure.value)


@pytest.mark.parametrize(
    "player_url",
    [
        "/salaries/players/example/",
        "//players.example.test/example?view=1#bio",
        "https://players.example.test/example?view=1#bio",
    ],
)
def test_issue21_player_url_forms_are_accepted(player_url: str) -> None:
    result = _parse([_record(player_url=player_url, player_id="")])
    assert result.deduplication.unique_rows[0]["player_url"] == player_url


@pytest.mark.parametrize(
    "logo_url",
    [
        "",
        "/relative/logo.png",
        "//cdn.example.test/logo.png",
        "ftp://cdn.example.test/logo.png",
        "https:///missing-host.png",
        "https://user:secret@cdn.example.test/logo.png",
        "https://cdn.example.test/logo with space.png",
    ],
)
def test_team_logo_url_is_always_required_full_public_http_even_with_asset(
    logo_url: str,
) -> None:
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([_record(team_logo_url=logo_url)])
    assert any(item.field == "team_logo_url" for item in failure.value.diagnostics)


def test_team_display_text_is_evidence_only_and_cannot_replace_logo() -> None:
    imported = _parse([_record(team_display_text="Unmapped Fixture Team")])
    row = imported.deduplication.unique_rows[0]
    assert row["team_display_text"] == "Unmapped Fixture Team"
    assert not any(key in row for key in ("team", "team_name", "team_abbreviation"))

    with pytest.raises(CsvFallbackValidationError):
        _parse(
            [
                _record(
                    team_display_text="Unmapped Fixture Team",
                    team_logo_url="",
                )
            ]
        )


@pytest.mark.parametrize(
    "selected_season",
    ["", "2026", "26-27", "2026-28", "٢٠٢٦-٢٧"],
)
def test_cli_selected_season_must_be_consecutive_ascii(selected_season: str) -> None:
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([_record()], selected_season=selected_season)
    assert any(item.field == "selected_season" for item in failure.value.diagnostics)


def test_row_sidecar_and_cli_seasons_must_all_match() -> None:
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse(
            [_record(target_season="2025-26", salary_season_cells_json=_cells())],
            metadata_bytes=_metadata(season="2024-25"),
        )
    assert [
        (item.location, item.field, item.code) for item in failure.value.diagnostics
    ] == [
        ("metadata", "season", "season_mismatch"),
        ("row", "target_season", "season_mismatch"),
        ("row", "salary_season_cells_json", "target_cell_count"),
    ]


@pytest.mark.parametrize("sentinel", ["", "-", "–", "—", "N/A", " n/a "])
def test_issue20_null_salary_sentinels_remain_null_not_zero(sentinel: str) -> None:
    imported = _parse(
        [
            _record(
                target_season_salary_text=sentinel,
                salary_season_cells_json=_cells(sentinel),
            )
        ]
    )
    assert imported.deduplication.unique_rows[0]["target_season_salary_dollars"] is None


@pytest.mark.parametrize(
    "salary",
    ["-1", "$-1", "1.5", "$1.00", "€1000", "USD 1000", "$1,00", "TW$1"],
)
def test_malformed_salary_uses_issue20_and_is_never_coerced(salary: str) -> None:
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse(
            [
                _record(
                    target_season_salary_text=salary,
                    salary_season_cells_json=_cells(salary),
                )
            ]
        )
    assert any(
        item.field == "target_season_salary_text" and item.code == "invalid_salary"
        for item in failure.value.diagnostics
    )
    assert salary not in str(failure.value)


def test_arbitrary_size_salary_is_exact_and_overflow_free() -> None:
    digits = "9" * 4_301
    imported = _parse(
        [
            _record(
                target_season_salary_text=digits,
                salary_season_cells_json=_cells(digits),
            )
        ]
    )
    assert (
        imported.deduplication.unique_rows[0]["target_season_salary_dollars"]
        == (10**4_301) - 1
    )


@pytest.mark.parametrize(
    ("cells_json", "code"),
    [
        ("{}", "wrong_json_shape"),
        ("null", "wrong_json_shape"),
        ("[]", "wrong_json_shape"),
        ("invalid", "invalid_json"),
        (
            (
                '[{"heading":"2026-27","heading":"2026-27",'
                '"salary_text":"$1,000","marker_text":""}]'
            ),
            "invalid_json",
        ),
        (
            '[{"heading":"2026-27","salary_text":"$1,000"}]',
            "invalid_cell_shape",
        ),
        (
            (
                '[{"heading":"2026-27","salary_text":"$1,000",'
                '"marker_text":"","extra":"x"}]'
            ),
            "invalid_cell_shape",
        ),
        (
            '[{"heading":2026,"salary_text":"$1,000","marker_text":""}]',
            "invalid_cell_type",
        ),
        (
            '[{"heading":"2026-28","salary_text":"$1,000","marker_text":""}]',
            "invalid_heading",
        ),
        (
            '[{"heading":"2026-27","salary_text":"bad","marker_text":""}]',
            "invalid_cell_salary",
        ),
        (
            '[{"heading":"2026-27","salary_text":"$1,000","marker_text":"\\u000a"}]',
            "cell_control_character",
        ),
    ],
)
def test_salary_cell_json_validation_families(cells_json: str, code: str) -> None:
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([_record(salary_season_cells_json=cells_json)])
    assert code in _diagnostic_codes(failure.value)


def test_repeated_heading_missing_duplicate_and_mismatched_target_fail() -> None:
    duplicate = json.dumps(
        [
            {"heading": "2026-27", "salary_text": "$1,000", "marker_text": ""},
            {"heading": "2026-27", "salary_text": "$1,000", "marker_text": ""},
        ]
    )
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([_record(salary_season_cells_json=duplicate)])
    assert "duplicate_heading" in _diagnostic_codes(failure.value)
    assert "target_cell_count" in _diagnostic_codes(failure.value)

    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse(
            [
                _record(
                    salary_season_cells_json=json.dumps(
                        [
                            {
                                "heading": "2027-28",
                                "salary_text": "-",
                                "marker_text": "",
                            }
                        ]
                    )
                )
            ]
        )
    assert "target_cell_count" in _diagnostic_codes(failure.value)

    mismatch = _cells("$999", "TW")
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([_record(salary_season_cells_json=mismatch)])
    assert "target_cell_mismatch" in _diagnostic_codes(failure.value)


def test_selected_season_only_cell_is_valid_and_no_future_cell_is_synthesized() -> None:
    imported = _parse([_record(salary_season_cells_json=_cells())])
    cells = imported.deduplication.unique_rows[0]["salary_season_cells"]
    assert cells == [{"heading": "2026-27", "salary_text": "$1,000", "marker_text": ""}]


def test_row_errors_accumulate_in_record_then_header_order_without_values() -> None:
    first = _record(
        player_display_text="",
        player_url="PRIVATE invalid url",
        player_id="PRIVATE-ID",
        team_logo_url="PRIVATE logo",
        team_logo_asset_id="PRIVATE-ASSET",
        target_season="2026-28",
        target_season_salary_text="PRIVATE salary",
        salary_season_cells_json="PRIVATE json",
    )
    second = _record(player_id="-2")
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([first, second])
    row_diagnostics = [
        item for item in failure.value.diagnostics if item.location == "row"
    ]
    assert [item.record_number for item in row_diagnostics] == sorted(
        item.record_number for item in row_diagnostics
    )
    first_fields = [item.field for item in row_diagnostics if item.record_number == 1]
    assert first_fields == sorted(
        first_fields,
        key=lambda field: (
            CSV_HEADER_FIELDS.index(field) if field in CSV_HEADER_FIELDS else -1
        ),
    )
    message = str(failure.value)
    assert "PRIVATE" not in message
    assert "-2" not in message


def test_wrong_record_column_count_reports_record_without_guessing_fields() -> None:
    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse([_record()[:-1]])
    assert [(item.field, item.code) for item in failure.value.diagnostics] == [
        ("record", "column_count")
    ]


def _browser_result_from_commissioner_row(
    source_row: dict[str, Any],
    *,
    cells: list[dict[str, str]] | None = None,
    **updates: Any,
) -> DeduplicationResult:
    row = deepcopy(source_row)
    row.pop("source_row_fingerprint", None)
    for field in (
        "source_system",
        "team_display_text",
        "source_row_reference",
        "salary_season_cells_json",
        "player_id_csv_text",
        "team_logo_asset_id_csv_text",
    ):
        row.pop(field, None)
    row.update(
        {
            "source_page_number": 9,
            "source_row_position": 8,
            "rank_text": "77",
            "source_locator": "https://www.hoopshype.com/salaries/players/#rendered-page-9",
            "imported_at": "2026-08-07T23:00:00Z",
        }
    )
    if cells is not None:
        row["salary_season_cells"] = deepcopy(cells)
    row.update(deepcopy(updates))
    return deduplicate_salary_rows([row])


def test_duplicate_variants_and_repeat_imports_are_idempotent() -> None:
    first = load_synthetic_csv_fixture_for_tests(
        FIXTURE,
        "2026-27",
        imported_at="2026-08-07T22:15:00Z",
    )
    second = load_synthetic_csv_fixture_for_tests(
        FIXTURE,
        "2026-27",
        imported_at="2026-08-08T22:15:00Z",
    )
    assert len(first.unique_rows) == len(second.unique_rows) == 2
    assert len(first.occurrences) == len(second.occurrences) == 3
    assert {row["source_row_fingerprint"] for row in first.unique_rows} == {
        row["source_row_fingerprint"] for row in second.unique_rows
    }
    assert first.occurrences[0].imported_at != second.occurrences[0].imported_at


def test_record_reordering_changes_provenance_not_unique_fingerprint_set() -> None:
    first = _record(player_id="1", team_logo_asset_id="1")
    second = _record(
        player_display_text="Second",
        player_id="2",
        team_logo_asset_id="2",
        team_logo_url="https://cdn.example.test/2.png",
    )
    forward = _parse([first, second]).deduplication
    reverse = _parse([second, first], imported_at="2026-08-08T00:00:00Z").deduplication
    assert {row["source_row_fingerprint"] for row in forward.unique_rows} == {
        row["source_row_fingerprint"] for row in reverse.unique_rows
    }
    assert [row["source_row_position"] for row in forward.unique_rows] == [1, 2]
    assert [row["source_row_position"] for row in reverse.unique_rows] == [1, 2]


def test_reconciliation_uses_canonical_payload_for_browser_equivalence() -> None:
    commissioner = _parse([_record()]).deduplication
    source = commissioner.unique_rows[0]
    browser = _browser_result_from_commissioner_row(
        source,
        player_display_text="Cosmetic Browser Name",
        player_url="/different/player/path?view=browser",
        team_logo_url="https://other.example.test/other-logo.png?width=60",
        target_season_salary_text="1000",
        salary_season_cells=[
            {"heading": "2026-27", "salary_text": "1000", "marker_text": ""}
        ],
    )
    report = reconcile_salary_rows(commissioner, browser)
    assert report.equivalent_fingerprints == (source["source_row_fingerprint"],)
    assert report.commissioner_only_fingerprints == ()
    assert report.browser_only_fingerprints == ()


def test_reconciliation_reports_unmatched_rows_without_merging() -> None:
    commissioner = _parse([_record()]).deduplication
    browser = _browser_result_from_commissioner_row(
        commissioner.unique_rows[0],
        team_logo_asset_id=10,
    )
    report = reconcile_salary_rows(commissioner, browser)
    assert report.equivalent_fingerprints == ()
    assert report.commissioner_only_fingerprints == (
        commissioner.unique_rows[0]["source_row_fingerprint"],
    )
    assert report.browser_only_fingerprints == (
        browser.unique_rows[0]["source_row_fingerprint"],
    )


def test_missing_future_cell_never_reconciles_with_browser_complete_cells() -> None:
    commissioner = _parse([_record(salary_season_cells_json=_cells())]).deduplication
    browser_cells = [
        {"heading": "2026-27", "salary_text": "$1,000", "marker_text": ""},
        {"heading": "2027-28", "salary_text": "-", "marker_text": ""},
    ]
    browser = _browser_result_from_commissioner_row(
        commissioner.unique_rows[0],
        cells=browser_cells,
    )
    report = reconcile_salary_rows(commissioner, browser)
    assert report.equivalent_fingerprints == ()
    assert len(report.commissioner_only_fingerprints) == 1
    assert len(report.browser_only_fingerprints) == 1


def test_fallback_identity_and_logo_urls_reconcile_only_by_issue21_rules() -> None:
    commissioner = _parse(
        [
            _record(
                player_id="",
                player_url="HTTPS://PLAYERS.Example/Path?one=1#bio",
                team_logo_asset_id="",
                team_logo_url="HTTPS://CDN.Example/Logo.png?width=30#one",
            )
        ]
    ).deduplication
    browser = _browser_result_from_commissioner_row(
        commissioner.unique_rows[0],
        player_url="https://players.example/Path?two=2#other",
        team_logo_url="https://cdn.example/Logo.png?width=60#two",
    )
    assert (
        len(reconcile_salary_rows(commissioner, browser).equivalent_fingerprints) == 1
    )

    linkless_browser = _browser_result_from_commissioner_row(
        commissioner.unique_rows[0],
        player_url=None,
    )
    assert (
        reconcile_salary_rows(commissioner, linkless_browser).equivalent_fingerprints
        == ()
    )


def test_reconciliation_detects_forced_digest_collision_across_inputs() -> None:
    digest = lambda _: "0" * 64
    first = _parse([_record()]).deduplication.unique_rows[0]
    first.pop("source_row_fingerprint")
    second = deepcopy(first)
    second["team_logo_asset_id"] = 10
    commissioner = deduplicate_salary_rows([first], hash_function=digest)
    browser = deduplicate_salary_rows([second], hash_function=digest)
    with pytest.raises(CsvFallbackReconciliationError, match="collision"):
        reconcile_salary_rows(commissioner, browser, hash_function=digest)


def test_reconciliation_rejects_malformed_results_and_preserves_inputs() -> None:
    commissioner = _parse([_record()]).deduplication
    browser = _browser_result_from_commissioner_row(commissioner.unique_rows[0])
    before_commissioner = deepcopy(commissioner)
    before_browser = deepcopy(browser)
    report = reconcile_salary_rows(commissioner, browser)
    assert report.equivalent_fingerprints
    assert commissioner == before_commissioner
    assert browser == before_browser

    bad_row = deepcopy(commissioner.unique_rows[0])
    bad_row["source_row_fingerprint"] = "invalid"
    with pytest.raises(CsvFallbackReconciliationError, match="recompute"):
        reconcile_salary_rows(
            DeduplicationResult((bad_row,), commissioner.occurrences),
            browser,
        )
    assert commissioner == before_commissioner
    assert browser == before_browser


def _fixture_import_bundle() -> fallback.CommissionerCsvImport:
    return fallback._load_csv_paths(
        FIXTURE,
        selected_season="2026-27",
        imported_at=IMPORTED_AT,
        metadata_mode="synthetic",
        enforce_private=False,
    )


def test_derived_artifact_shape_lineage_counts_and_round_trip(tmp_path: Path) -> None:
    imported = _fixture_import_bundle()
    artifact = build_csv_fallback_artifact(imported, generated_at=GENERATED_AT)
    assert set(artifact) == {
        "metadata",
        "counts",
        "unique_rows",
        "occurrences",
        "lineage",
    }
    assert artifact["metadata"] == {
        "source_system": "commissioner_salary_csv",
        "source_locator": "synthetic commissioner salary CSV fixture",
        "retrieved_at": None,
        "generated_at": GENERATED_AT,
        "source_event_time": None,
        "season": "2026-27",
        "league_scope": None,
        "artifact_version": "1.0",
        "schema_version": "1.0",
        "redaction_status": "redacted-reviewed",
    }
    assert artifact["counts"] == {
        "physical_csv_row_count": 3,
        "unique_row_count": 2,
        "duplicate_occurrence_count": 1,
    }
    assert artifact["lineage"]["input_csv"] == {
        "basename": FIXTURE.name,
        "sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
    }
    metadata_path = FIXTURE.with_suffix(".metadata.json")
    assert artifact["lineage"]["input_metadata"] == {
        "basename": metadata_path.name,
        "sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
    }
    serialized = json.dumps(artifact)
    assert "commissioner_included" not in serialized
    assert "total_salary_dollars" not in serialized
    assert "applicability" not in serialized

    output = tmp_path / "derived.json"
    write_artifact_atomic(output, artifact)
    assert load_csv_fallback_artifact(output) == imported.deduplication


def test_derived_artifact_round_trips_4301_digit_salary_and_ids(
    tmp_path: Path,
) -> None:
    digits = "9" * 4_301
    imported = _parse(
        [
            _record(
                player_id=digits,
                team_logo_asset_id=digits,
                target_season_salary_text=digits,
                salary_season_cells_json=_cells(digits),
            )
        ]
    )
    artifact = build_csv_fallback_artifact(imported, generated_at=GENERATED_AT)
    encoded_row = artifact["unique_rows"][0]
    assert set(encoded_row["target_season_salary_dollars"]) == {
        "$nba_commish_unsigned_integer_hex"
    }
    output = tmp_path / "large-derived.json"
    write_artifact_atomic(output, artifact)
    loaded = load_csv_fallback_artifact(output)
    assert loaded == imported.deduplication
    assert loaded.unique_rows[0]["target_season_salary_dollars"] == (10**4_301) - 1


@pytest.mark.parametrize(
    "corruption",
    ["root", "metadata", "counts", "lineage", "integer", "occurrence"],
)
def test_corrupted_derived_artifact_is_rejected(
    tmp_path: Path,
    corruption: str,
) -> None:
    artifact = build_csv_fallback_artifact(
        _fixture_import_bundle(), generated_at=GENERATED_AT
    )
    if corruption == "root":
        artifact["extra"] = True
    elif corruption == "metadata":
        artifact["metadata"]["redaction_status"] = "public-source-reviewed"
    elif corruption == "counts":
        artifact["counts"]["unique_row_count"] = 99
    elif corruption == "lineage":
        artifact["lineage"]["input_csv"]["sha256"] = "bad"
    elif corruption == "integer":
        artifact["unique_rows"][0]["player_id"] = {
            "$nba_commish_unsigned_integer_hex": "not-hex"
        }
    else:
        artifact["occurrences"][0]["representative_input_position"] = 2
    output = tmp_path / f"corrupt-{corruption}.json"
    write_artifact_atomic(output, artifact)
    with pytest.raises((CsvFallbackArtifactError, CsvFallbackReconciliationError)):
        load_csv_fallback_artifact(output)


def _prepare_production_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    csv_bytes: bytes | None = None,
    redaction_status: str = "unredacted-local",
) -> tuple[Path, Path, bytes, bytes]:
    monkeypatch.setattr(fallback, "_PRIVATE_ROOT", tmp_path)
    input_path = tmp_path / "commissioner-salary--season-2026-27--20260807t220000z.csv"
    metadata_path = input_path.with_suffix(".metadata.json")
    input_bytes = (
        csv_bytes if csv_bytes is not None else _csv_bytes([_record(), _record()])
    )
    sidecar_bytes = _metadata(redaction_status=redaction_status)
    input_path.write_bytes(input_bytes)
    metadata_path.write_bytes(sidecar_bytes)
    return input_path, metadata_path, input_bytes, sidecar_bytes


def test_run_import_exclusively_publishes_and_reuses_identical_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path, metadata_path, input_bytes, metadata_bytes = _prepare_production_paths(
        tmp_path, monkeypatch
    )
    output = tmp_path / "commissioner-derived--season-2026-27--20260807t221600z.json"
    result = run_csv_fallback_import(
        input_path,
        "2026-27",
        output,
        timestamp_factory=lambda: GENERATED_AT,
    )
    first_bytes = output.read_bytes()
    assert result.physical_row_count == 2
    assert result.unique_row_count == 1
    assert result.duplicate_occurrence_count == 1
    assert result.output_basename == output.name
    assert load_csv_fallback_artifact(output) == result.deduplication

    again = run_csv_fallback_import(
        input_path,
        "2026-27",
        output,
        timestamp_factory=lambda: GENERATED_AT,
    )
    assert again.artifact == result.artifact
    assert output.read_bytes() == first_bytes
    assert input_path.read_bytes() == input_bytes
    assert metadata_path.read_bytes() == metadata_bytes
    assert not list(tmp_path.glob(f".{output.name}.*.tmp"))


def test_existing_different_output_is_never_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path, _, _, _ = _prepare_production_paths(tmp_path, monkeypatch)
    output = tmp_path / "commissioner-derived--season-2026-27--20260807t221600z.json"
    output.write_bytes(b"prior valid artifact")
    with pytest.raises(CsvFallbackArtifactError, match="not replaced"):
        run_csv_fallback_import(
            input_path,
            "2026-27",
            output,
            timestamp_factory=lambda: GENERATED_AT,
        )
    assert output.read_bytes() == b"prior valid artifact"
    assert not list(tmp_path.glob(f".{output.name}.*.tmp"))


def test_destination_creation_race_never_replaces_competing_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path, _, _, _ = _prepare_production_paths(tmp_path, monkeypatch)
    output = tmp_path / "commissioner-derived--season-2026-27--20260807t221600z.json"
    real_link = os.link

    def racing_link(source: os.PathLike[str], destination: os.PathLike[str]) -> None:
        Path(destination).write_bytes(b"competing artifact")
        real_link(source, destination)

    monkeypatch.setattr("nba_commish.hoopshype.artifact.os.link", racing_link)
    with pytest.raises(CsvFallbackArtifactError, match="not replaced"):
        run_csv_fallback_import(
            input_path,
            "2026-27",
            output,
            timestamp_factory=lambda: GENERATED_AT,
        )
    assert output.read_bytes() == b"competing artifact"
    assert not list(tmp_path.glob(f".{output.name}.*.tmp"))


def test_publication_failure_leaves_no_final_or_temporary_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path, _, _, _ = _prepare_production_paths(tmp_path, monkeypatch)
    output = tmp_path / "commissioner-derived--season-2026-27--20260807t221600z.json"

    def fail_link(source: os.PathLike[str], destination: os.PathLike[str]) -> None:
        raise OSError("injected")

    monkeypatch.setattr("nba_commish.hoopshype.artifact.os.link", fail_link)
    with pytest.raises(CsvFallbackArtifactError):
        run_csv_fallback_import(
            input_path,
            "2026-27",
            output,
            timestamp_factory=lambda: GENERATED_AT,
        )
    assert not output.exists()
    assert not list(tmp_path.glob(f".{output.name}.*.tmp"))


def test_invalid_import_publishes_nothing_and_preserves_prior_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = _csv_bytes([_record(player_id="PRIVATE-invalid")])
    input_path, metadata_path, input_bytes, metadata_bytes = _prepare_production_paths(
        tmp_path, monkeypatch, csv_bytes=invalid
    )
    output = tmp_path / "commissioner-derived--season-2026-27--20260807t221600z.json"
    output.write_bytes(b"prior artifact")
    with pytest.raises(CsvFallbackValidationError) as failure:
        run_csv_fallback_import(
            input_path,
            "2026-27",
            output,
            timestamp_factory=lambda: GENERATED_AT,
        )
    assert "PRIVATE" not in str(failure.value)
    assert output.read_bytes() == b"prior artifact"
    assert input_path.read_bytes() == input_bytes
    assert metadata_path.read_bytes() == metadata_bytes
    assert not list(tmp_path.glob(f".{output.name}.*.tmp"))


def test_storage_and_redaction_privacy_gates() -> None:
    outside_input = Path("outside.csv")
    outside_output = Path("outside.json")
    diagnostics = fallback._production_path_diagnostics(outside_input, outside_output)
    assert {item.code for item in diagnostics} == {"outside_private"}

    with pytest.raises(CsvFallbackValidationError) as failure:
        _parse(
            [_record()],
            metadata_bytes=_metadata(redaction_status="public-source-reviewed"),
        )
    assert any(item.field == "redaction_status" for item in failure.value.diagnostics)


def test_output_inherits_redaction_and_never_strengthens_production_input() -> None:
    imported = _parse(
        [_record()], metadata_bytes=_metadata(redaction_status="unredacted-local")
    )
    artifact = build_csv_fallback_artifact(imported, generated_at=GENERATED_AT)
    assert artifact["metadata"]["redaction_status"] == "unredacted-local"
    imported = _parse(
        [_record()], metadata_bytes=_metadata(redaction_status="redacted-reviewed")
    )
    artifact = build_csv_fallback_artifact(imported, generated_at=GENERATED_AT)
    assert artifact["metadata"]["redaction_status"] == "redacted-reviewed"


def test_cli_success_reports_counts_and_safe_output_basename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path, _, _, _ = _prepare_production_paths(tmp_path, monkeypatch)
    output = tmp_path / "commissioner-derived--season-2026-27--20260807t221600z.json"
    assert (
        cli_main(
            [
                "--input",
                str(input_path),
                "--season",
                "2026-27",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Imported 2 physical CSV rows" in captured.out
    assert "1 unique rows" in captured.out
    assert "1 duplicate occurrences" in captured.out
    assert output.name in captured.out
    assert str(tmp_path) not in captured.out


def test_cli_failure_prints_complete_sanitized_report_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path, _, _, _ = _prepare_production_paths(
        tmp_path,
        monkeypatch,
        csv_bytes=_csv_bytes(
            [_record(player_id="PRIVATE-invalid", team_logo_url="bad")]
        ),
    )
    output = tmp_path / "commissioner-derived--season-2026-27--20260807t221600z.json"
    assert (
        cli_main(
            [
                "--input",
                str(input_path),
                "--season",
                "2026-27",
                "--output",
                str(output),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "CSV data record 1, field player_id" in captured.err
    assert "CSV data record 1, field team_logo_url" in captured.err
    assert "PRIVATE" not in captured.err
    assert "Traceback" not in captured.err
    assert not output.exists()


def test_success_and_failure_leave_all_in_memory_inputs_deeply_unchanged() -> None:
    records = [_record()]
    records_before = deepcopy(records)
    csv_bytes = _csv_bytes(records)
    metadata_bytes = _metadata()
    imported = _parse(records, csv_bytes=csv_bytes, metadata_bytes=metadata_bytes)
    imported_before = deepcopy(imported)
    artifact = build_csv_fallback_artifact(imported, generated_at=GENERATED_AT)
    assert records == records_before
    assert imported == imported_before
    assert csv_bytes == _csv_bytes(records_before)
    assert metadata_bytes == _metadata()
    assert artifact["unique_rows"] is not imported.deduplication.unique_rows

    with pytest.raises(CsvFallbackValidationError):
        _parse(
            records,
            csv_bytes=_csv_bytes([_record(player_id="bad")]),
            metadata_bytes=metadata_bytes,
        )
    assert records == records_before
    assert imported == imported_before
