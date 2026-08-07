from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from nba_commish.hoopshype.artifact import SCHEMA_VERSION, build_artifact
from nba_commish.hoopshype.errors import SalaryParseError
from nba_commish.hoopshype.pagination import CollectionResult
from nba_commish.hoopshype.parser import parse_page, snapshot_from_html
from nba_commish.hoopshype.salary import (
    normalize_salary_row,
    normalize_salary_rows,
    parse_salary_text,
)

FIXTURE = (
    Path(__file__).parents[1]
    / "data"
    / "fixtures"
    / "hoopshype-salary-table--season-2026-27--20260807t201407z.html"
)
IMPORTED_AT = "2026-08-07T20:00:00Z"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("$12,500,000", 12_500_000),
        ("$850,000", 850_000),
        ("12500000", 12_500_000),
        ("12,500,000", 12_500_000),
        ("  $850,000\t", 850_000),
        ("\u2003$850,000\u00a0", 850_000),
        ("$0", 0),
        ("0", 0),
        ("$00", 0),
        ("$0,000", 0),
        ("000,000", 0),
        ("$9,007,199,254,740,993", 9_007_199_254_740_993),
    ],
)
def test_parse_salary_text_accepts_exact_whole_dollars(
    source: str, expected: int
) -> None:
    result = parse_salary_text(source)
    assert result == expected
    assert isinstance(result, int)
    assert not isinstance(result, bool)


@pytest.mark.parametrize(
    "source",
    ["", " ", "\t\u2003", "-", " – ", "—", "N/A", "n/a", "N/a", "n/A"],
)
def test_parse_salary_text_returns_none_only_for_exact_sentinels(source: str) -> None:
    assert parse_salary_text(source) is None


@pytest.mark.parametrize(
    "source",
    [
        None,
        0,
        1,
        True,
        False,
        1.0,
        [],
        {},
        b"$1",
        "NA",
        "N.A.",
        "--",
        "$",
        "-$1",
        "$-1",
        "+1",
        "$+1",
        "(1,000)",
        "$1.00",
        "1e6",
        "€1,000",
        "USD 1,000",
        "$ 850,000",
        "$850 000",
        "8 50",
        "$850\u00a0000",
        "1,",
        ",1",
        "1,,000",
        "12,34",
        "1,00,000",
        "1_000",
        "1234,567",
        "12,500,0000",
        "TW$678,882",
        "＄1,000",
        "$١٠٠٠",
        "$１２３",
    ],
)
def test_parse_salary_text_rejects_malformed_and_near_sentinel_values(
    source: Any,
) -> None:
    with pytest.raises(SalaryParseError, match="target_season_salary_text"):
        parse_salary_text(source)


def _raw_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "target_season": "2026-27",
        "source_page_number": 25,
        "source_row_position": 20,
        "rank_text": "T462",
        "player_display_text": "Kam Jones",
        "player_url": "/salaries/players/kam-jones/1324173/",
        "player_id": 1324173,
        "team_logo_url": (
            "https://www.gannett-cdn.com/content-pipeline-sports-images/"
            "sports2/nba/logos/15.png?width=30"
        ),
        "team_logo_asset_id": 15,
        "target_season_salary_text": "$678,882",
        "target_season_marker_text": "TW",
        "source_row_description": None,
        "source_locator": (
            "https://www.hoopshype.com/salaries/players/#rendered-page-25"
        ),
        "imported_at": IMPORTED_AT,
        "salary_season_cells": [
            {
                "heading": "2026-27",
                "salary_text": "$678,882",
                "marker_text": "TW",
            },
            {"heading": "2027-28", "salary_text": "-", "marker_text": ""},
        ],
    }
    row.update(overrides)
    return row


def test_row_normalization_preserves_raw_text_and_marker() -> None:
    raw = _raw_row()
    before = deepcopy(raw)

    normalized = normalize_salary_row(raw)

    assert raw == before
    assert normalized["target_season_salary_dollars"] == 678_882
    assert normalized["target_season_salary_text"] == "$678,882"
    assert normalized["target_season_marker_text"] == "TW"
    assert set(normalized) == {*raw, "target_season_salary_dollars"}
    for key, value in raw.items():
        assert normalized[key] == value


def test_marker_is_not_used_to_derive_salary() -> None:
    normalized = normalize_salary_row(
        _raw_row(target_season_marker_text="not-a-contract-meaning")
    )
    assert normalized["target_season_salary_dollars"] == 678_882
    assert normalized["target_season_marker_text"] == "not-a-contract-meaning"


def test_surrounding_whitespace_is_not_removed_from_raw_row_text() -> None:
    source = "\u2003$678,882\t"
    raw = _raw_row(target_season_salary_text=source)
    normalized = normalize_salary_row(raw)

    assert raw["target_season_salary_text"] == source
    assert normalized["target_season_salary_text"] == source
    assert normalized["target_season_salary_dollars"] == 678_882


def test_combined_marker_and_amount_fails_with_complete_safe_row_context() -> None:
    malicious = "TW$678,882\nprivate-payload"
    with pytest.raises(SalaryParseError) as failure:
        normalize_salary_row(_raw_row(target_season_salary_text=malicious))
    message = str(failure.value)
    assert "target_season_salary_text" in message
    assert "source page 25" in message
    assert "source row 20" in message
    assert "Kam Jones" in message
    assert malicious not in message
    assert "private-payload" not in message


def test_ordered_collection_preserves_nulls_duplicates_and_physical_row_count() -> None:
    rows = [
        _raw_row(source_row_position=1, target_season_salary_text="$1"),
        _raw_row(source_row_position=2, target_season_salary_text="-"),
        _raw_row(source_row_position=3, target_season_salary_text="$1"),
        _raw_row(source_row_position=3, target_season_salary_text="$1"),
    ]
    before = deepcopy(rows)

    normalized = normalize_salary_rows(rows)

    assert rows == before
    assert len(normalized) == len(rows)
    assert [row["source_row_position"] for row in normalized] == [1, 2, 3, 3]
    assert [row["target_season_salary_dollars"] for row in normalized] == [
        1,
        None,
        1,
        1,
    ]


def test_collection_returns_no_partial_result_for_a_malformed_row() -> None:
    rows = [
        _raw_row(source_row_position=1, target_season_salary_text="$1"),
        _raw_row(source_row_position=2, target_season_salary_text="bad"),
        _raw_row(source_row_position=3, target_season_salary_text="$3"),
    ]
    before = deepcopy(rows)

    with pytest.raises(SalaryParseError, match="source row 2"):
        normalize_salary_rows(rows)
    assert rows == before
    assert all("target_season_salary_dollars" not in row for row in rows)


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (
            {"source_row_position": 1, "target_season_salary_text": "$1"},
            "Missing required source_page_number",
        ),
        (
            {"source_page_number": 1, "target_season_salary_text": "$1"},
            "Missing required source_row_position",
        ),
        (
            {
                "source_page_number": True,
                "source_row_position": 1,
                "target_season_salary_text": "$1",
            },
            "Invalid source_page_number",
        ),
        (
            {
                "source_page_number": 1,
                "source_row_position": 0,
                "target_season_salary_text": "$1",
            },
            "Invalid source_row_position",
        ),
        (
            {"source_page_number": 1, "source_row_position": 2},
            "Missing required target_season_salary_text",
        ),
        (
            {
                "source_page_number": 1,
                "source_row_position": 2,
                "target_season_salary_text": 1000,
            },
            "Malformed target_season_salary_text",
        ),
        (
            {
                "source_page_number": 1,
                "source_row_position": 2,
                "target_season_salary_text": False,
            },
            "Malformed target_season_salary_text",
        ),
    ],
)
def test_missing_or_invalid_row_fields_fail_actionably(
    row: dict[str, Any], message: str
) -> None:
    with pytest.raises(SalaryParseError, match=message):
        normalize_salary_row(row)


def test_non_mapping_row_fails_without_python_coercion() -> None:
    with pytest.raises(SalaryParseError, match="expected a mapping"):
        normalize_salary_row(["$1"])  # type: ignore[arg-type]


def test_reviewed_fixture_normalizes_kam_jones_without_mutating_raw_artifact() -> None:
    page = parse_page(
        snapshot_from_html(FIXTURE.read_text()),
        requested_season="2026-27",
        imported_at=IMPORTED_AT,
    )
    collection = CollectionResult(
        headings=page.headings,
        pages=(page,),
        rows=page.rows,
    )
    artifact = build_artifact(
        collection,
        season="2026-27",
        retrieved_at=IMPORTED_AT,
    )
    before = deepcopy(artifact)

    normalized = normalize_salary_rows(artifact["data"])

    assert SCHEMA_VERSION == "1.0"
    assert artifact == before
    assert artifact["metadata"]["schema_version"] == "1.0"
    assert all("target_season_salary_dollars" not in row for row in artifact["data"])
    kam_jones = [row for row in normalized if row["player_display_text"] == "Kam Jones"]
    assert [row["target_season_salary_dollars"] for row in kam_jones] == [
        1_075_459,
        678_882,
    ]
    assert kam_jones[1]["target_season_salary_text"] == "$678,882"
    assert kam_jones[1]["target_season_marker_text"] == "TW"
