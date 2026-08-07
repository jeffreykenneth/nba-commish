from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from nba_commish.hoopshype.artifact import SCHEMA_VERSION, build_artifact
from nba_commish.hoopshype.deduplication import (
    FINGERPRINT_DOMAIN,
    FINGERPRINT_PREFIX,
    FINGERPRINT_VERSION,
    deduplicate_salary_rows,
    fingerprint_salary_row,
)
from nba_commish.hoopshype.errors import (
    FingerprintCollisionError,
    FingerprintValidationError,
)
from nba_commish.hoopshype.pagination import CollectionResult
from nba_commish.hoopshype.parser import parse_page, snapshot_from_html
from nba_commish.hoopshype.salary import normalize_salary_rows

FIXTURE = (
    Path(__file__).parents[1]
    / "data"
    / "fixtures"
    / "hoopshype-salary-table--season-2026-27--20260807t201407z.html"
)
IMPORTED_AT = "2026-08-07T20:00:00Z"
FINGERPRINT_PATTERN = re.compile(r"^hoopshype-row-v1:sha256:[0-9a-f]{64}$")


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "target_season": "2026-27",
        "source_page_number": 2,
        "source_row_position": 3,
        "rank_text": "T23",
        "player_display_text": "Example Player",
        "player_url": "/salaries/players/example-player/12345/",
        "player_id": 12345,
        "team_logo_url": "https://CDN.EXAMPLE.test/sports2/nba/logos/9.png?width=30",
        "team_logo_asset_id": 9,
        "target_season_salary_text": "$1,000",
        "target_season_marker_text": "",
        "target_season_salary_dollars": 1_000,
        "source_row_description": None,
        "source_locator": "https://www.hoopshype.com/salaries/players/#rendered-page-2",
        "imported_at": IMPORTED_AT,
        "salary_season_cells": [
            {"heading": "2026-27", "salary_text": "$1,000", "marker_text": ""},
            {"heading": "2027-28", "salary_text": "-", "marker_text": "P"},
        ],
    }
    row.update(deepcopy(overrides))
    return row


def _replace_cell(
    row: dict[str, Any],
    heading: str,
    **updates: Any,
) -> dict[str, Any]:
    result = deepcopy(row)
    for cell in result["salary_season_cells"]:
        if cell["heading"] == heading:
            cell.update(updates)
            return result
    raise AssertionError(f"Missing test cell {heading}")


def _assert_same(left: dict[str, Any], right: dict[str, Any]) -> None:
    assert fingerprint_salary_row(left) == fingerprint_salary_row(right)


def _assert_distinct(left: dict[str, Any], right: dict[str, Any]) -> None:
    assert fingerprint_salary_row(left) != fingerprint_salary_row(right)


def test_v1_domain_and_public_fingerprint_format_are_stable() -> None:
    fingerprint = fingerprint_salary_row(_row())
    assert FINGERPRINT_VERSION == 1
    assert FINGERPRINT_DOMAIN == b"nba-commish/hoopshype-row-fingerprint/v1\x00"
    assert FINGERPRINT_PREFIX == "hoopshype-row-v1:sha256:"
    assert FINGERPRINT_PATTERN.fullmatch(fingerprint)


def test_known_v1_fingerprint_is_stable() -> None:
    assert fingerprint_salary_row(_row()) == (
        "hoopshype-row-v1:sha256:"
        "a37216d7a1d912eb68e910a1f25b8bff8d620d1e29f4f0c2f613b4cd068f638d"
    )


def test_hash_input_begins_with_versioned_domain() -> None:
    captured: list[bytes] = []

    def capture(payload: bytes) -> str:
        captured.append(payload)
        return "0" * 64

    assert fingerprint_salary_row(_row(), hash_function=capture) == (
        f"{FINGERPRINT_PREFIX}{'0' * 64}"
    )
    assert len(captured) == 1
    assert captured[0].startswith(FINGERPRINT_DOMAIN)


def test_stable_player_id_ignores_display_and_url_cosmetics() -> None:
    _assert_same(
        _row(),
        _row(
            player_display_text="Cosmetically Different",
            player_url="HTTP://OTHER.example/Different-Slug?query=1#fragment",
        ),
    )


def test_player_url_fallback_normalizes_only_scheme_host_query_and_fragment() -> None:
    _assert_same(
        _row(player_id=None, player_url="HTTP://PLAYERS.Example/Path/Slug?x=1#bio"),
        _row(player_id=None, player_url="http://players.example/Path/Slug?x=2#other"),
    )
    _assert_distinct(
        _row(player_id=None, player_url="http://players.example/Path/Slug"),
        _row(player_id=None, player_url="http://players.example/path/Slug"),
    )


def test_scheme_relative_and_root_relative_player_url_fallbacks_are_stable() -> None:
    _assert_same(
        _row(player_id=None, player_url="//PLAYERS.Example/Path?x=1"),
        _row(player_id=None, player_url="//players.example/Path#fragment"),
    )
    _assert_same(
        _row(player_id=None, player_url="/salaries/free-agent?x=1"),
        _row(player_id=None, player_url="/salaries/free-agent#fragment"),
    )


def test_linkless_player_fallback_uses_nfc_whitespace_and_casefold() -> None:
    _assert_same(
        _row(player_id=None, player_url=None, player_display_text="  JOSÉ\tPLAYER "),
        _row(player_id=None, player_url="", player_display_text="jose\u0301 player"),
    )
    _assert_distinct(
        _row(player_id=None, player_url=None, player_display_text="Jose Player"),
        _row(player_id=None, player_url=None, player_display_text="José Player"),
    )


def test_player_url_precedes_display_fallback() -> None:
    _assert_same(
        _row(player_id=None, player_url="/players/fallback", player_display_text="One"),
        _row(player_id=None, player_url="/players/fallback", player_display_text="Two"),
    )


def test_zero_numeric_discriminators_are_present_and_distinct_from_null() -> None:
    _assert_distinct(_row(player_id=0), _row(player_id=None))
    _assert_distinct(_row(team_logo_asset_id=0), _row(team_logo_asset_id=None))


def test_stable_logo_asset_ignores_cdn_url_cosmetics() -> None:
    _assert_same(
        _row(),
        _row(team_logo_url="http://OTHER.example/different/path.png?quality=99#image"),
    )


def test_logo_url_fallback_normalizes_only_scheme_host_query_and_fragment() -> None:
    _assert_same(
        _row(
            team_logo_asset_id=None,
            team_logo_url="HTTPS://CDN.Example/Logos/Team.png?width=30#one",
        ),
        _row(
            team_logo_asset_id=None,
            team_logo_url="https://cdn.example/Logos/Team.png?width=60#two",
        ),
    )
    _assert_distinct(
        _row(
            team_logo_asset_id=None,
            team_logo_url="https://cdn.example/Logos/Team.png",
        ),
        _row(
            team_logo_asset_id=None,
            team_logo_url="https://cdn.example/logos/Team.png",
        ),
    )


@pytest.mark.parametrize(
    "salary_text",
    ["1000", "  $1,000\t", "001000", "0,001,000"],
)
def test_target_salary_punctuation_is_cosmetic(salary_text: str) -> None:
    variant = _replace_cell(
        _row(target_season_salary_text=salary_text), "2026-27", salary_text=salary_text
    )
    _assert_same(_row(), variant)


@pytest.mark.parametrize("sentinel", ["", "-", "–", "—", "n/a", " N/A "])
def test_null_salary_sentinels_are_canonical(sentinel: str) -> None:
    baseline = _replace_cell(
        _row(target_season_salary_text="-", target_season_salary_dollars=None),
        "2026-27",
        salary_text="-",
    )
    variant = _replace_cell(
        _row(target_season_salary_text=sentinel, target_season_salary_dollars=None),
        "2026-27",
        salary_text=sentinel,
    )
    _assert_same(baseline, variant)


def test_zero_is_distinct_from_null() -> None:
    null_row = _replace_cell(
        _row(target_season_salary_text="-", target_season_salary_dollars=None),
        "2026-27",
        salary_text="-",
    )
    zero_row = _replace_cell(
        _row(target_season_salary_text="$0", target_season_salary_dollars=0),
        "2026-27",
        salary_text="0",
    )
    _assert_distinct(null_row, zero_row)


def test_unbounded_integer_uses_exact_binary_encoding() -> None:
    digits = "9" * 4_301
    amount = (10**4_301) - 1
    grouped = ",".join(
        [digits[:2], *(digits[index : index + 3] for index in range(2, len(digits), 3))]
    )
    plain = _replace_cell(
        _row(
            target_season_salary_text=digits,
            target_season_salary_dollars=amount,
        ),
        "2026-27",
        salary_text=digits,
    )
    punctuated = _replace_cell(
        _row(
            target_season_salary_text=f"${grouped}",
            target_season_salary_dollars=amount,
        ),
        "2026-27",
        salary_text=grouped,
    )
    _assert_same(plain, punctuated)


def test_description_null_and_allowed_whitespace_nfc_canonicalizations() -> None:
    _assert_same(_row(source_row_description=None), _row(source_row_description=" \t "))
    _assert_same(
        _row(source_row_description="  Café\tcontract  "),
        _row(source_row_description="Cafe\u0301 contract"),
    )


def test_description_case_punctuation_and_diacritics_remain_distinct() -> None:
    baseline = _row(source_row_description="Café contract.")
    for description in ("café contract.", "Café contract!", "Cafe contract."):
        _assert_distinct(baseline, _row(source_row_description=description))


def test_marker_nfc_and_surrounding_whitespace_are_cosmetic_only() -> None:
    baseline = _replace_cell(
        _row(target_season_marker_text=" É "), "2026-27", marker_text="E\u0301"
    )
    variant = _replace_cell(
        _row(target_season_marker_text="E\u0301"), "2026-27", marker_text=" É "
    )
    _assert_same(baseline, variant)
    _assert_distinct(
        _replace_cell(
            _row(target_season_marker_text="A  B"), "2026-27", marker_text="A  B"
        ),
        _replace_cell(
            _row(target_season_marker_text="A B"), "2026-27", marker_text="A B"
        ),
    )
    _assert_distinct(
        _replace_cell(
            _row(target_season_marker_text="tw"), "2026-27", marker_text="tw"
        ),
        _replace_cell(
            _row(target_season_marker_text="TW"), "2026-27", marker_text="TW"
        ),
    )


def test_retained_cell_display_order_is_cosmetic() -> None:
    reversed_cells = deepcopy(_row())
    reversed_cells["salary_season_cells"].reverse()
    _assert_same(_row(), reversed_cells)


def test_provenance_and_rank_fields_are_excluded() -> None:
    _assert_same(
        _row(),
        _row(
            rank_text="999",
            source_page_number=99,
            source_row_position=18,
            source_locator="https://www.hoopshype.com/other-public-locator",
            imported_at="2099-12-31T23:59:59Z",
        ),
    )


def test_every_canonical_identity_change_remains_distinct() -> None:
    baseline = _row()
    variants = [
        _row(player_id=54321),
        _row(player_id=None, player_url=None, player_display_text="Different Player"),
        _row(player_id=None, player_url="/players/different"),
        _row(team_logo_asset_id=10),
        _row(team_logo_asset_id=None, team_logo_url="https://cdn.example/logo/10.png"),
        _replace_cell(
            _row(
                target_season="2025-26",
                target_season_salary_text="$1,000",
                target_season_salary_dollars=1_000,
                salary_season_cells=[
                    {"heading": "2025-26", "salary_text": "$1,000", "marker_text": ""},
                    {"heading": "2027-28", "salary_text": "-", "marker_text": "P"},
                ],
            ),
            "2025-26",
            salary_text="$1,000",
        ),
        _replace_cell(
            _row(
                target_season_salary_text="$2,000", target_season_salary_dollars=2_000
            ),
            "2026-27",
            salary_text="$2,000",
        ),
        _replace_cell(
            _row(target_season_marker_text="TW"), "2026-27", marker_text="TW"
        ),
        _row(source_row_description="separate evidence"),
        _row(
            salary_season_cells=[
                {"heading": "2026-27", "salary_text": "$1,000", "marker_text": ""},
                {"heading": "2028-29", "salary_text": "-", "marker_text": "P"},
            ]
        ),
        _replace_cell(baseline, "2027-28", salary_text="$5"),
        _replace_cell(baseline, "2027-28", marker_text="Q"),
    ]
    baseline_fingerprint = fingerprint_salary_row(baseline)
    assert all(
        fingerprint_salary_row(variant) != baseline_fingerprint for variant in variants
    )


def test_repeat_batches_collapse_and_record_every_ordered_occurrence() -> None:
    first = _row()
    distinct = _replace_cell(
        _row(target_season_marker_text="TW"), "2026-27", marker_text="TW"
    )
    first_again = _replace_cell(
        _row(
            rank_text="24",
            source_page_number=5,
            source_row_position=7,
            imported_at="2026-08-08T00:01:00Z",
            source_locator="https://www.hoopshype.com/salaries/players/#rendered-page-5",
            target_season_salary_text="1000",
            player_display_text="Different Cosmetic Name",
            player_url="/cosmetic/slug",
            team_logo_url="https://other.example/logo.png?width=99",
        ),
        "2026-27",
        salary_text="  $1,000 ",
    )
    distinct_again = deepcopy(distinct)
    distinct_again["imported_at"] = "2026-08-08T00:02:00Z"
    result = deduplicate_salary_rows([first, distinct, first_again, distinct_again])

    assert len(result.unique_rows) == 2
    assert [row["target_season_marker_text"] for row in result.unique_rows] == [
        "",
        "TW",
    ]
    assert len(result.occurrences) == 4
    assert [item.input_position for item in result.occurrences] == [1, 2, 3, 4]
    assert [item.representative_input_position for item in result.occurrences] == [
        1,
        2,
        1,
        2,
    ]
    assert result.occurrences[2].source_page_number == 5
    assert result.occurrences[2].source_row_position == 7
    assert result.occurrences[2].rank_text == "24"
    assert result.occurrences[2].source_locator.endswith("rendered-page-5")
    assert result.occurrences[2].imported_at == "2026-08-08T00:01:00Z"
    assert result.occurrences[0].fingerprint == result.occurrences[2].fingerprint


def test_first_seen_representative_is_copied_with_only_fingerprint_added() -> None:
    first = _row(rank_text="first")
    duplicate = _row(rank_text="second")
    result = deduplicate_salary_rows([first, duplicate])

    assert len(result.unique_rows) == 1
    representative = result.unique_rows[0]
    assert representative["rank_text"] == "first"
    assert set(representative) == {*first, "source_row_fingerprint"}
    for key, value in first.items():
        assert representative[key] == value
    assert representative["source_row_fingerprint"] == result.occurrences[0].fingerprint


def test_reordering_cosmetic_duplicates_changes_only_representative_presentation() -> (
    None
):
    first = _row(rank_text="first")
    second = _row(rank_text="second", imported_at="2026-08-08T00:01:00Z")
    forward = deduplicate_salary_rows([first, second])
    reverse = deduplicate_salary_rows([second, first])
    assert forward.unique_rows[0]["rank_text"] == "first"
    assert reverse.unique_rows[0]["rank_text"] == "second"
    assert (
        forward.unique_rows[0]["source_row_fingerprint"]
        == reverse.unique_rows[0]["source_row_fingerprint"]
    )
    assert len(forward.occurrences) == len(reverse.occurrences) == 2


@pytest.mark.parametrize(
    "field",
    [
        "target_season",
        "player_id",
        "player_url",
        "player_display_text",
        "team_logo_asset_id",
        "team_logo_url",
        "target_season_salary_text",
        "target_season_salary_dollars",
        "target_season_marker_text",
        "source_row_description",
        "salary_season_cells",
        "source_locator",
        "imported_at",
        "source_page_number",
        "source_row_position",
        "rank_text",
    ],
)
def test_missing_required_fields_fail_contextually(field: str) -> None:
    row = _row()
    del row[field]
    with pytest.raises(FingerprintValidationError) as failure:
        fingerprint_salary_row(row)
    assert field in str(failure.value)
    if field != "source_page_number":
        assert "source page 2" in str(failure.value)
    if field != "source_row_position":
        assert "source row 3" in str(failure.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target_season", 2026),
        ("player_id", True),
        ("player_id", -1),
        ("player_url", 123),
        ("player_display_text", None),
        ("team_logo_asset_id", False),
        ("team_logo_asset_id", -1),
        ("team_logo_url", None),
        ("target_season_salary_text", 1000),
        ("target_season_salary_dollars", True),
        ("target_season_salary_dollars", -1),
        ("target_season_marker_text", None),
        ("source_row_description", 1),
        ("salary_season_cells", {}),
        ("salary_season_cells", tuple(_row()["salary_season_cells"])),
        ("source_locator", None),
        ("imported_at", 1),
        ("source_page_number", True),
        ("source_row_position", 0),
        ("rank_text", 23),
    ],
)
def test_wrong_field_types_fail_contextually(field: str, value: Any) -> None:
    with pytest.raises(FingerprintValidationError) as failure:
        fingerprint_salary_row(_row(**{field: value}))
    assert field in str(failure.value)
    if field != "player_display_text":
        assert "Example Player" in str(failure.value)


@pytest.mark.parametrize(
    "player_url",
    [
        "relative/path",
        "ftp://players.example/path",
        "https:///missing-host",
        "https://user:secret@players.example/path",
        " /path",
        "\\bad\\path",
        "/path\x00hidden",
    ],
)
def test_invalid_player_urls_fail_even_when_player_id_exists(player_url: str) -> None:
    with pytest.raises(FingerprintValidationError, match="player_url"):
        fingerprint_salary_row(_row(player_url=player_url))


@pytest.mark.parametrize(
    "logo_url",
    [
        "/relative/logo.png",
        "ftp://cdn.example/logo.png",
        "https:///logo.png",
        "https://user:secret@cdn.example/logo.png",
        " https://cdn.example/logo.png",
        "https://[invalid/logo.png",
    ],
)
def test_invalid_full_logo_urls_fail_even_when_asset_exists(logo_url: str) -> None:
    with pytest.raises(FingerprintValidationError, match="team_logo_url"):
        fingerprint_salary_row(_row(team_logo_url=logo_url))


@pytest.mark.parametrize("heading", ["2026", "26-27", "2026-28", "٢٠٢٦-٢٧"])
def test_invalid_retained_headings_fail(heading: str) -> None:
    row = _row()
    row["salary_season_cells"][1]["heading"] = heading
    with pytest.raises(FingerprintValidationError, match="heading"):
        fingerprint_salary_row(row)


@pytest.mark.parametrize("season", ["2026", "26-27", "2026-28", "٢٠٢٦-٢٧"])
def test_invalid_target_season_fails(season: str) -> None:
    with pytest.raises(FingerprintValidationError, match="target_season"):
        fingerprint_salary_row(_row(target_season=season))


def test_duplicate_or_missing_target_retained_heading_fails() -> None:
    duplicate = _row()
    duplicate["salary_season_cells"][1]["heading"] = "2026-27"
    with pytest.raises(FingerprintValidationError, match="Duplicate"):
        fingerprint_salary_row(duplicate)

    missing = _row()
    missing["salary_season_cells"][0]["heading"] = "2025-26"
    with pytest.raises(FingerprintValidationError, match="exactly one"):
        fingerprint_salary_row(missing)


def test_malformed_retained_salary_and_wrong_cell_types_fail() -> None:
    malformed = _replace_cell(_row(), "2027-28", salary_text="bad")
    with pytest.raises(FingerprintValidationError, match="salary_text"):
        fingerprint_salary_row(malformed)

    wrong_marker = _replace_cell(_row(), "2027-28", marker_text=False)
    with pytest.raises(FingerprintValidationError, match="marker_text"):
        fingerprint_salary_row(wrong_marker)

    non_mapping = _row(
        salary_season_cells=[
            {"heading": "2026-27", "salary_text": "$1,000", "marker_text": ""},
            "bad",
        ]
    )
    with pytest.raises(FingerprintValidationError, match="entry 2"):
        fingerprint_salary_row(non_mapping)


@pytest.mark.parametrize("field", ["heading", "salary_text", "marker_text"])
def test_missing_retained_cell_fields_fail_contextually(field: str) -> None:
    row = _row()
    del row["salary_season_cells"][1][field]
    with pytest.raises(FingerprintValidationError) as failure:
        fingerprint_salary_row(row)
    assert field in str(failure.value)
    assert "entry 2" in str(failure.value)
    assert "source page 2" in str(failure.value)


def test_target_salary_and_marker_consistency_is_required() -> None:
    with pytest.raises(FingerprintValidationError, match="inconsistent"):
        fingerprint_salary_row(_row(target_season_salary_dollars=999))
    with pytest.raises(FingerprintValidationError, match="retained salary"):
        fingerprint_salary_row(_replace_cell(_row(), "2026-27", salary_text="$999"))
    with pytest.raises(FingerprintValidationError, match="retained salary marker"):
        fingerprint_salary_row(_replace_cell(_row(), "2026-27", marker_text="TW"))


def test_malformed_target_salary_error_is_safe_and_contextual() -> None:
    malicious = "TW$1,000\nprivate-payload"
    row = _replace_cell(
        _row(target_season_salary_text=malicious),
        "2026-27",
        salary_text=malicious,
    )
    with pytest.raises(FingerprintValidationError) as failure:
        fingerprint_salary_row(row)
    message = str(failure.value)
    assert "target_season_salary_text" in message
    assert "source page 2" in message
    assert "source row 3" in message
    assert "Example Player" in message
    assert "private-payload" not in message


def test_forced_digest_collision_fails_without_partial_result_or_mutation() -> None:
    rows = [_row(), _row(player_id=54321)]
    before = deepcopy(rows)

    with pytest.raises(FingerprintCollisionError) as failure:
        deduplicate_salary_rows(rows, hash_function=lambda payload: "0" * 64)

    message = str(failure.value)
    assert "input positions 1 and 2" in message
    assert f"{FINGERPRINT_PREFIX}{'0' * 64}" in message
    assert rows == before
    assert all("source_row_fingerprint" not in row for row in rows)


def test_validation_failure_returns_no_partial_result_and_does_not_mutate() -> None:
    rows = [_row(), _replace_cell(_row(), "2026-27", marker_text="TW")]
    before = deepcopy(rows)
    with pytest.raises(FingerprintValidationError) as failure:
        deduplicate_salary_rows(rows)
    assert "input position 2" in str(failure.value)
    assert rows == before
    assert all("source_row_fingerprint" not in row for row in rows)


def test_equal_payload_with_injected_digest_is_a_duplicate_not_collision() -> None:
    result = deduplicate_salary_rows(
        [_row(), _row(rank_text="cosmetic")],
        hash_function=lambda payload: "f" * 64,
    )
    assert len(result.unique_rows) == 1
    assert len(result.occurrences) == 2


@pytest.mark.parametrize("digest", ["A" * 64, "0" * 63, "not-hex", b"0" * 64])
def test_invalid_injected_digest_fails(digest: Any) -> None:
    with pytest.raises(FingerprintValidationError, match="64 lowercase"):
        fingerprint_salary_row(_row(), hash_function=lambda payload: digest)


def test_invalid_collection_or_row_type_fails_dedicated_boundary() -> None:
    with pytest.raises(
        FingerprintValidationError, match="expected a mapping"
    ) as failure:
        fingerprint_salary_row("not-a-row")  # type: ignore[arg-type]
    assert "None" not in str(failure.value)
    with pytest.raises(FingerprintValidationError, match="iterable"):
        deduplicate_salary_rows(_row())
    with pytest.raises(FingerprintValidationError, match="mapping"):
        deduplicate_salary_rows(["not-a-row"])


def test_fingerprinting_and_deduplication_are_deeply_non_mutating() -> None:
    rows = [_row(), _row(rank_text="duplicate")]
    before = deepcopy(rows)
    fingerprint_salary_row(rows[0])
    result = deduplicate_salary_rows(rows)

    assert rows == before
    assert all("source_row_fingerprint" not in row for row in rows)
    result.unique_rows[0]["salary_season_cells"][0]["salary_text"] = "changed"
    assert rows == before


def test_reviewed_fixture_raw_artifact_remains_schema_1_and_unmodified() -> None:
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

    result = deduplicate_salary_rows(normalized)

    assert artifact == before
    assert SCHEMA_VERSION == artifact["metadata"]["schema_version"] == "1.0"
    assert all("source_row_fingerprint" not in row for row in artifact["data"])
    assert all("target_season_salary_dollars" not in row for row in artifact["data"])
    assert len(result.occurrences) == len(artifact["data"]) == 8
