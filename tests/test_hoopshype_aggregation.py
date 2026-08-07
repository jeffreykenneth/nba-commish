from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from nba_commish.hoopshype.aggregation import (
    AggregationCompletionStatus,
    AggregationReason,
    DecisionSource,
    DispositionStatus,
    SourcePlayerKey,
    aggregate_salary_rows,
)
from nba_commish.hoopshype.deduplication import (
    FINGERPRINT_PREFIX,
    DeduplicationResult,
    deduplicate_salary_rows,
)
from nba_commish.hoopshype.errors import (
    AggregationDecisionError,
    AggregationValidationError,
)
from nba_commish.hoopshype.salary import parse_salary_text

FIXTURE = (
    Path(__file__).parents[1]
    / "data"
    / "fixtures"
    / "hoopshype-repeated-identities--season-2026-27--20260807t213128z.json"
)
IMPORTED_AT = "2026-08-07T21:31:28Z"


def _row(**overrides: Any) -> dict[str, Any]:
    salary_text = overrides.pop("target_season_salary_text", "$1,000")
    marker = overrides.pop("target_season_marker_text", "")
    salary_dollars = overrides.pop(
        "target_season_salary_dollars", parse_salary_text(salary_text)
    )
    row: dict[str, Any] = {
        "target_season": "2026-27",
        "source_page_number": 2,
        "source_row_position": 3,
        "rank_text": "T23",
        "player_display_text": "Example Player",
        "player_url": "/salaries/players/example-player/12345/",
        "player_id": 12345,
        "team_logo_url": "https://cdn.example.test/nba/logos/9.png?width=30",
        "team_logo_asset_id": 9,
        "target_season_salary_text": salary_text,
        "target_season_marker_text": marker,
        "target_season_salary_dollars": salary_dollars,
        "source_row_description": None,
        "source_locator": "https://www.hoopshype.com/salaries/players/#rendered-page-2",
        "imported_at": IMPORTED_AT,
        "salary_season_cells": [
            {
                "heading": "2026-27",
                "salary_text": salary_text,
                "marker_text": marker,
            }
        ],
    }
    row.update(deepcopy(overrides))
    return row


def _duplicate_presentation(row: Mapping[str, Any], position: int) -> dict[str, Any]:
    duplicate = deepcopy(dict(row))
    duplicate["source_page_number"] = 10 + position
    duplicate["source_row_position"] = position
    duplicate["rank_text"] = f"duplicate-{position}"
    duplicate["source_locator"] = (
        f"https://www.hoopshype.com/salaries/players/#rendered-page-{10 + position}"
    )
    duplicate["imported_at"] = f"2026-08-08T00:00:{position:02d}Z"
    return duplicate


def _deduplicated(*rows: Mapping[str, Any]) -> DeduplicationResult:
    return deduplicate_salary_rows(rows)


def _fixture_result() -> tuple[dict[str, Any], DeduplicationResult]:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    normalized_rows: list[dict[str, Any]] = []
    for position, evidence in enumerate(document["data"], start=1):
        asset_id = evidence["team_logo_asset_id"]
        normalized_rows.append(
            _row(
                source_page_number=evidence["source_page_number"],
                source_row_position=position,
                rank_text=evidence["rank_text"],
                player_display_text=evidence["player_display_text"],
                player_url=None,
                player_id=evidence["player_id"],
                team_logo_url=(
                    "https://www.gannett-cdn.com/content-pipeline-sports-images/"
                    f"sports2/nba/logos/{asset_id}.png"
                ),
                team_logo_asset_id=asset_id,
                target_season_salary_text=evidence["target_season_salary_text"],
                target_season_marker_text=evidence["target_season_marker_text"],
                source_locator=(
                    document["metadata"]["source_locator"]
                    + f"; source page {evidence['source_page_number']}"
                ),
            )
        )
    return document, deduplicate_salary_rows(normalized_rows)


def _fingerprints(result: DeduplicationResult) -> list[str]:
    return [row["source_row_fingerprint"] for row in result.unique_rows]


def test_empty_valid_result_produces_empty_aggregation() -> None:
    result = aggregate_salary_rows(DeduplicationResult((), ()), "2026-27")
    assert result.selected_season == "2026-27"
    assert result.players == ()


def test_single_ordinary_non_null_row_is_included_by_default() -> None:
    source = _deduplicated(_row())
    result = aggregate_salary_rows(source, "2026-27")
    player = result.players[0]
    disposition = player.row_dispositions[0]

    assert player.source_player_key == SourcePlayerKey("player_id", 12345)
    assert player.representative_display_text == "Example Player"
    assert player.selected_season == "2026-27"
    assert disposition.status is DispositionStatus.INCLUDED
    assert disposition.reason_code is AggregationReason.SINGLE_ORDINARY_NON_NULL_ROW
    assert disposition.decision_source is DecisionSource.DEFAULT
    assert "Phase 0 default policy" in disposition.reason
    assert disposition.evidence == ()
    assert disposition.raw_row == source.unique_rows[0]
    assert disposition.raw_row is not source.unique_rows[0]
    assert disposition.occurrences == source.occurrences
    assert player.included_rows == (disposition,)
    assert player.excluded_rows == ()
    assert player.review_required_rows == ()
    assert player.included_subtotal_dollars == 1_000
    assert player.unresolved_review_count == 0
    assert player.total_salary_dollars == 1_000
    assert player.completion_status is AggregationCompletionStatus.COMPLETE


def test_null_is_excluded_but_zero_is_an_ordinary_amount() -> None:
    source = _deduplicated(
        _row(
            target_season_salary_text="-",
            team_logo_asset_id=1,
            team_logo_url="https://cdn.example.test/1.png",
        ),
        _row(
            target_season_salary_text="$0",
            team_logo_asset_id=2,
            team_logo_url="https://cdn.example.test/2.png",
        ),
    )
    player = aggregate_salary_rows(source, "2026-27").players[0]

    assert [row.status for row in player.row_dispositions] == [
        DispositionStatus.EXCLUDED,
        DispositionStatus.INCLUDED,
    ]
    assert player.row_dispositions[0].reason_code is AggregationReason.NO_SALARY_AMOUNT
    assert player.row_dispositions[1].target_salary_dollars == 0
    assert player.included_subtotal_dollars == 0
    assert player.total_salary_dollars == 0
    assert player.completion_status is AggregationCompletionStatus.COMPLETE


def test_one_non_null_plus_multiple_null_rows_includes_only_non_null() -> None:
    source = _deduplicated(
        _row(team_logo_asset_id=1, team_logo_url="https://cdn.example.test/1.png"),
        _row(
            target_season_salary_text="-",
            team_logo_asset_id=2,
            team_logo_url="https://cdn.example.test/2.png",
        ),
        _row(
            target_season_salary_text="N/A",
            team_logo_asset_id=3,
            team_logo_url="https://cdn.example.test/3.png",
        ),
    )
    player = aggregate_salary_rows(source, "2026-27").players[0]
    assert [row.status for row in player.row_dispositions] == [
        DispositionStatus.INCLUDED,
        DispositionStatus.EXCLUDED,
        DispositionStatus.EXCLUDED,
    ]
    assert player.included_subtotal_dollars == player.total_salary_dollars == 1_000


@pytest.mark.parametrize("marker", ["TW", "tw", "P", "unknown marker"])
def test_nonempty_target_marker_requires_review(marker: str) -> None:
    source = _deduplicated(_row(target_season_marker_text=marker))
    disposition = (
        aggregate_salary_rows(source, "2026-27").players[0].row_dispositions[0]
    )
    assert disposition.status is DispositionStatus.REVIEW_REQUIRED
    assert disposition.reason_code is AggregationReason.SOURCE_EVIDENCE_REQUIRES_REVIEW
    assert disposition.evidence[0].value == marker
    assert disposition.evidence[0].code == (
        "hoopshype_two_way_contract_marker"
        if marker == "TW"
        else "source_target_marker"
    )


def test_exact_tw_is_labeled_only_as_hoopshype_two_way_evidence() -> None:
    source = _deduplicated(_row(target_season_marker_text="TW"))
    player = aggregate_salary_rows(source, "2026-27").players[0]
    evidence = player.review_required_rows[0].evidence[0]
    assert evidence.code == "hoopshype_two_way_contract_marker"
    assert "two-way contract" in evidence.label
    assert "does not decide salary applicability" in evidence.label
    assert player.total_salary_dollars is None


@pytest.mark.parametrize("description", ["waived", "dead money", "partial season"])
def test_synthetic_description_is_retained_without_classification(
    description: str,
) -> None:
    source = _deduplicated(_row(source_row_description=description))
    disposition = (
        aggregate_salary_rows(source, "2026-27").players[0].row_dispositions[0]
    )
    assert disposition.status is DispositionStatus.REVIEW_REQUIRED
    assert disposition.raw_row["source_row_description"] == description
    assert disposition.evidence[0].code == "source_row_description"
    assert disposition.evidence[0].value == description
    assert "without free-text classification" in disposition.evidence[0].label


def test_whitespace_description_is_canonical_null_for_default_policy() -> None:
    source = _deduplicated(_row(source_row_description=" \t "))
    disposition = (
        aggregate_salary_rows(source, "2026-27").players[0].row_dispositions[0]
    )
    assert disposition.status is DispositionStatus.INCLUDED
    assert disposition.evidence == ()
    assert disposition.raw_row["source_row_description"] == " \t "


def test_whitespace_target_marker_is_canonical_empty_for_default_policy() -> None:
    source = _deduplicated(_row(target_season_marker_text=" \t "))
    disposition = (
        aggregate_salary_rows(source, "2026-27").players[0].row_dispositions[0]
    )
    assert disposition.status is DispositionStatus.INCLUDED
    assert disposition.evidence == ()
    assert disposition.raw_row["target_season_marker_text"] == " \t "


def test_multiple_distinct_non_null_rows_require_review_even_without_evidence() -> None:
    source = _deduplicated(
        _row(team_logo_asset_id=1, team_logo_url="https://cdn.example.test/1.png"),
        _row(
            target_season_salary_text="$2,000",
            team_logo_asset_id=2,
            team_logo_url="https://cdn.example.test/2.png",
        ),
    )
    player = aggregate_salary_rows(source, "2026-27").players[0]
    assert [row.status for row in player.row_dispositions] == [
        DispositionStatus.REVIEW_REQUIRED,
        DispositionStatus.REVIEW_REQUIRED,
    ]
    assert all(
        row.reason_code is AggregationReason.MULTIPLE_NON_NULL_ROWS
        for row in player.row_dispositions
    )
    assert player.included_subtotal_dollars == 0
    assert player.unresolved_review_count == 2
    assert player.total_salary_dollars is None
    assert player.completion_status is AggregationCompletionStatus.REVIEW_REQUIRED


def test_source_evidence_reason_precedes_multiple_row_reason() -> None:
    source = _deduplicated(
        _row(target_season_marker_text="TW"),
        _row(
            target_season_salary_text="$2,000",
            team_logo_asset_id=2,
            team_logo_url="https://cdn.example.test/2.png",
        ),
    )
    player = aggregate_salary_rows(source, "2026-27").players[0]
    assert player.row_dispositions[0].reason_code is (
        AggregationReason.SOURCE_EVIDENCE_REQUIRES_REVIEW
    )
    assert player.row_dispositions[1].reason_code is (
        AggregationReason.MULTIPLE_NON_NULL_ROWS
    )


def test_player_id_precedence_ignores_display_url_and_team_evidence() -> None:
    source = _deduplicated(
        _row(player_display_text="First", player_url="/first", team_logo_asset_id=1),
        _row(
            player_display_text="Second",
            player_url="/second",
            team_logo_asset_id=2,
            team_logo_url="https://cdn.example.test/different.png",
            target_season_salary_text="$2,000",
        ),
    )
    result = aggregate_salary_rows(source, "2026-27")
    assert len(result.players) == 1
    assert result.players[0].source_player_key == SourcePlayerKey("player_id", 12345)
    assert result.players[0].representative_display_text == "First"


def test_url_fallback_reuses_v1_scheme_host_query_fragment_canonicalization() -> None:
    source = _deduplicated(
        _row(
            player_id=None,
            player_url="HTTPS://PLAYERS.Example/Path/Slug?one=1#bio",
            team_logo_asset_id=1,
        ),
        _row(
            player_id=None,
            player_url="https://players.example/Path/Slug?two=2#other",
            player_display_text="Cosmetic Name",
            team_logo_asset_id=2,
            target_season_salary_text="$2,000",
        ),
    )
    player = aggregate_salary_rows(source, "2026-27").players[0]
    assert player.source_player_key == SourcePlayerKey(
        "player_url", "https://players.example/Path/Slug"
    )
    assert len(player.row_dispositions) == 2


def test_linkless_fallback_reuses_v1_nfc_whitespace_casefold() -> None:
    source = _deduplicated(
        _row(
            player_id=None,
            player_url=None,
            player_display_text="  JOSÉ\tPLAYER ",
            team_logo_asset_id=1,
        ),
        _row(
            player_id=None,
            player_url="",
            player_display_text="jose\u0301 player",
            team_logo_asset_id=2,
            target_season_salary_text="$2,000",
        ),
    )
    player = aggregate_salary_rows(source, "2026-27").players[0]
    assert player.source_player_key == SourcePlayerKey(
        "player_display_text", "josé player"
    )
    assert len(player.row_dispositions) == 2


def test_linked_and_linkless_same_display_are_never_merged() -> None:
    source = _deduplicated(
        _row(player_id=None, player_url="/players/example"),
        _row(
            player_id=None,
            player_url=None,
            team_logo_asset_id=2,
            team_logo_url="https://cdn.example.test/2.png",
        ),
        _row(
            player_id=12345,
            player_url=None,
            team_logo_asset_id=3,
            team_logo_url="https://cdn.example.test/3.png",
        ),
    )
    result = aggregate_salary_rows(source, "2026-27")
    assert [player.source_player_key.kind for player in result.players] == [
        "player_url",
        "player_display_text",
        "player_id",
    ]


def test_group_and_row_order_follow_first_seen_unique_rows() -> None:
    source = _deduplicated(
        _row(player_id=2, player_display_text="Second", team_logo_asset_id=1),
        _row(player_id=1, player_display_text="First", team_logo_asset_id=2),
        _row(
            player_id=2,
            player_display_text="Second later",
            team_logo_asset_id=3,
            target_season_salary_text="$2,000",
        ),
    )
    result = aggregate_salary_rows(source, "2026-27")
    assert [player.source_player_key.value for player in result.players] == [2, 1]
    assert [
        row.raw_row["team_logo_asset_id"] for row in result.players[0].row_dispositions
    ] == [1, 3]


def test_duplicate_presentations_preserve_all_ordered_occurrences_once() -> None:
    first = _row()
    source = _deduplicated(
        first,
        _duplicate_presentation(first, 1),
        _duplicate_presentation(first, 2),
    )
    disposition = (
        aggregate_salary_rows(source, "2026-27").players[0].row_dispositions[0]
    )
    assert len(source.unique_rows) == 1
    assert [item.input_position for item in disposition.occurrences] == [1, 2, 3]
    assert [item.rank_text for item in disposition.occurrences] == [
        "T23",
        "duplicate-1",
        "duplicate-2",
    ]
    assert disposition.target_salary_dollars == 1_000
    assert disposition.status is DispositionStatus.INCLUDED


def test_commissioner_can_exclude_default_inclusion() -> None:
    source = _deduplicated(_row())
    fingerprint = _fingerprints(source)[0]
    result = aggregate_salary_rows(
        source,
        "2026-27",
        {fingerprint: {"status": "excluded", "reason": "Not applicable."}},
    )
    disposition = result.players[0].excluded_rows[0]
    assert disposition.reason_code is AggregationReason.COMMISSIONER_EXCLUDED
    assert disposition.reason == "Not applicable."
    assert disposition.decision_source is DecisionSource.COMMISSIONER
    assert result.players[0].included_subtotal_dollars == 0
    assert result.players[0].total_salary_dollars == 0


def test_commissioner_can_include_or_exclude_non_null_review_rows() -> None:
    source = _deduplicated(
        _row(team_logo_asset_id=1),
        _row(
            target_season_salary_text="$2,000",
            target_season_marker_text="TW",
            team_logo_asset_id=2,
            team_logo_url="https://cdn.example.test/2.png",
        ),
    )
    first, second = _fingerprints(source)
    result = aggregate_salary_rows(
        source,
        "2026-27",
        {
            first: {"status": "included", "reason": "Commissioner reviewed A."},
            second: {"status": "excluded", "reason": "Commissioner reviewed B."},
        },
    )
    player = result.players[0]
    assert [row.status for row in player.row_dispositions] == [
        DispositionStatus.INCLUDED,
        DispositionStatus.EXCLUDED,
    ]
    assert player.row_dispositions[1].evidence[0].code == (
        "hoopshype_two_way_contract_marker"
    )
    assert player.included_subtotal_dollars == player.total_salary_dollars == 1_000
    assert player.completion_status is AggregationCompletionStatus.COMPLETE


def test_commissioner_can_restate_null_exclusion_but_not_include_null() -> None:
    source = _deduplicated(_row(target_season_salary_text="-"))
    fingerprint = _fingerprints(source)[0]
    result = aggregate_salary_rows(
        source,
        "2026-27",
        {fingerprint: {"status": "excluded", "reason": "Reviewed empty amount."}},
    )
    disposition = result.players[0].excluded_rows[0]
    assert disposition.decision_source is DecisionSource.COMMISSIONER
    assert disposition.reason_code is AggregationReason.COMMISSIONER_EXCLUDED

    with pytest.raises(AggregationDecisionError, match="null salary row"):
        aggregate_salary_rows(
            source,
            "2026-27",
            {fingerprint: {"status": "included", "reason": "Invalid include."}},
        )


def test_unresolved_review_blocks_final_total_not_included_subtotal() -> None:
    source = _deduplicated(
        _row(player_id=1),
        _row(
            player_id=2,
            target_season_marker_text="P",
            team_logo_asset_id=2,
            team_logo_url="https://cdn.example.test/2.png",
        ),
    )
    result = aggregate_salary_rows(source, "2026-27")
    complete, unresolved = result.players
    assert complete.included_subtotal_dollars == complete.total_salary_dollars == 1_000
    assert unresolved.included_subtotal_dollars == 0
    assert unresolved.total_salary_dollars is None


def test_summation_is_exact_for_multiple_4301_digit_amounts() -> None:
    digits = "9" * 4_301
    amount = (10**4_301) - 1
    source = _deduplicated(
        _row(
            target_season_salary_text=digits,
            target_season_salary_dollars=amount,
            team_logo_asset_id=1,
        ),
        _row(
            target_season_salary_text=digits,
            target_season_salary_dollars=amount,
            team_logo_asset_id=2,
            team_logo_url="https://cdn.example.test/2.png",
        ),
    )
    decisions = {
        fingerprint: {"status": "included", "reason": "Reviewed large amount."}
        for fingerprint in _fingerprints(source)
    }
    player = aggregate_salary_rows(source, "2026-27", decisions).players[0]
    assert player.included_subtotal_dollars == 2 * ((10**4_301) - 1)
    assert player.total_salary_dollars == 2 * ((10**4_301) - 1)
    assert isinstance(player.total_salary_dollars, int)


def test_reviewed_fixture_metadata_and_evidence_are_minimal_and_unclassified() -> None:
    document, source = _fixture_result()
    metadata = document["metadata"]
    assert metadata == {
        "source_system": "hoopshype",
        "source_locator": (
            "reports/hoopshype-salary-findings--season-2026-27--"
            "20260807t185011z.md (issue #18 reviewed repeated-identities report; "
            "identified rows also cite its reviewed raw capture)"
        ),
        "retrieved_at": None,
        "generated_at": "2026-08-07T21:31:28Z",
        "source_event_time": None,
        "season": "2026-27",
        "league_scope": None,
        "artifact_version": "1.0",
        "schema_version": "1.0",
        "redaction_status": "public-source-reviewed",
    }
    rows = document["data"]
    assert len(rows) == len(source.unique_rows) == 8
    assert {row["player_id"] for row in rows} == {463121, 602730, 1232483, 1324173}
    assert all(isinstance(row["team_logo_asset_id"], int) for row in rows)
    assert sum(row["reviewed_capture_locator"] is not None for row in rows) == 4
    classifications = {row["evidence_limited_classification"] for row in rows}
    assert classifications <= {
        "distinct_source_row",
        "distinct_source_row_purpose_unclassified",
        "distinct_source_row_explicit_two_way_marker",
    }
    assert not any(
        term in json.dumps(rows).lower()
        for term in ("waived", "dead money", "partial season")
    )


@pytest.mark.parametrize(
    ("display_text", "expected_total"),
    [
        ("Damian Lillard", 35_915_403),
        ("K. Caldwell-Pope", 21_621_500),
        ("O. Prosper", 3_500_172),
        ("Kam Jones", 1_754_341),
    ],
)
def test_actual_two_row_groups_default_to_review_then_total_exactly_when_included(
    display_text: str,
    expected_total: int,
) -> None:
    _, source = _fixture_result()
    default_result = aggregate_salary_rows(source, "2026-27")
    player = next(
        item
        for item in default_result.players
        if item.representative_display_text == display_text
    )
    assert len(player.row_dispositions) == 2
    assert len({row.fingerprint for row in player.row_dispositions}) == 2
    assert player.unresolved_review_count == 2
    assert player.total_salary_dollars is None

    decisions = {
        row.fingerprint: {
            "status": "included",
            "reason": "Both reviewed public-source rows apply.",
        }
        for row in player.row_dispositions
    }
    resolved = aggregate_salary_rows(source, "2026-27", decisions)
    resolved_player = next(
        item
        for item in resolved.players
        if item.representative_display_text == display_text
    )
    assert resolved_player.included_subtotal_dollars == expected_total
    assert resolved_player.total_salary_dollars == expected_total
    assert resolved_player.unresolved_review_count == 0


def test_actual_kam_jones_tw_row_retains_marker_and_evidence_label() -> None:
    _, source = _fixture_result()
    player = next(
        item
        for item in aggregate_salary_rows(source, "2026-27").players
        if item.representative_display_text == "Kam Jones"
    )
    tw_row = next(
        row for row in player.row_dispositions if row.target_salary_dollars == 678_882
    )
    assert tw_row.raw_row["target_season_marker_text"] == "TW"
    assert tw_row.evidence[0].code == "hoopshype_two_way_contract_marker"
    assert "two-way contract" in tw_row.evidence[0].label


def test_decision_mapping_order_cannot_change_any_result() -> None:
    source = _deduplicated(
        _row(player_id=2, team_logo_asset_id=1),
        _row(
            player_id=2,
            target_season_salary_text="$2,000",
            team_logo_asset_id=2,
            team_logo_url="https://cdn.example.test/2.png",
        ),
        _row(player_id=1, team_logo_asset_id=3),
    )
    first, second, third = _fingerprints(source)
    forward = {
        first: {"status": "included", "reason": "Reviewed first."},
        second: {"status": "excluded", "reason": "Reviewed second."},
        third: {"status": "excluded", "reason": "Reviewed third."},
    }
    reverse = dict(reversed(tuple(forward.items())))
    assert aggregate_salary_rows(source, "2026-27", forward) == (
        aggregate_salary_rows(source, "2026-27", reverse)
    )


@pytest.mark.parametrize(
    "selected_season",
    [None, 2026, "", "2026", "26-27", "2026-28", "٢٠٢٦-٢٧"],
)
def test_malformed_selected_season_fails(selected_season: Any) -> None:
    with pytest.raises(AggregationValidationError, match="selected_season"):
        aggregate_salary_rows(_deduplicated(_row()), selected_season)


def test_bare_rows_and_forged_collection_shapes_fail() -> None:
    source = _deduplicated(_row())
    with pytest.raises(AggregationValidationError, match="DeduplicationResult"):
        aggregate_salary_rows(list(source.unique_rows), "2026-27")
    with pytest.raises(AggregationValidationError, match="unique_rows"):
        aggregate_salary_rows(
            DeduplicationResult(list(source.unique_rows), source.occurrences),
            "2026-27",
        )
    with pytest.raises(AggregationValidationError, match="occurrences"):
        aggregate_salary_rows(
            DeduplicationResult(source.unique_rows, list(source.occurrences)),
            "2026-27",
        )


def test_mixed_target_season_fails_instead_of_filtering() -> None:
    other = _row(
        target_season="2025-26",
        target_season_salary_text="$2,000",
        salary_season_cells=[
            {"heading": "2025-26", "salary_text": "$2,000", "marker_text": ""}
        ],
        target_season_salary_dollars=2_000,
        player_id=2,
    )
    source = _deduplicated(_row(player_id=1), other)
    with pytest.raises(AggregationValidationError, match="must equal selected_season"):
        aggregate_salary_rows(source, "2026-27")


def test_invalid_or_mismatched_row_fingerprint_fails_contextually() -> None:
    source = _deduplicated(_row())
    invalid_row = deepcopy(source.unique_rows[0])
    invalid_row["source_row_fingerprint"] = "invalid"
    with pytest.raises(AggregationValidationError, match="source_row_fingerprint"):
        aggregate_salary_rows(
            DeduplicationResult((invalid_row,), source.occurrences),
            "2026-27",
        )

    mismatched_row = deepcopy(source.unique_rows[0])
    mismatched_row["team_logo_asset_id"] = 10
    with pytest.raises(AggregationValidationError) as failure:
        aggregate_salary_rows(
            DeduplicationResult((mismatched_row,), source.occurrences),
            "2026-27",
        )
    message = str(failure.value)
    assert "fingerprint mismatch" in message
    assert source.unique_rows[0]["source_row_fingerprint"] in message
    assert "source page 2" in message
    assert "source row 3" in message
    assert "Example Player" in message


def test_duplicate_unique_fingerprints_fail() -> None:
    source = _deduplicated(_row())
    with pytest.raises(AggregationValidationError, match="Duplicate"):
        aggregate_salary_rows(
            DeduplicationResult(
                (source.unique_rows[0], deepcopy(source.unique_rows[0])),
                source.occurrences,
            ),
            "2026-27",
        )


@pytest.mark.parametrize(
    "failure_kind", ["malformed_salary", "missing_target", "duplicate_target"]
)
def test_fingerprint_v1_row_validation_and_exact_target_cell_are_enforced(
    failure_kind: str,
) -> None:
    source = _deduplicated(_row())
    row = deepcopy(source.unique_rows[0])
    if failure_kind == "malformed_salary":
        row["target_season_salary_text"] = "not salary"
        row["salary_season_cells"][0]["salary_text"] = "not salary"
    elif failure_kind == "missing_target":
        row["salary_season_cells"][0]["heading"] = "2027-28"
    else:
        row["salary_season_cells"].append(deepcopy(row["salary_season_cells"][0]))
    with pytest.raises(AggregationValidationError, match="fingerprint v1"):
        aggregate_salary_rows(
            DeduplicationResult((row,), source.occurrences),
            "2026-27",
        )


def test_unknown_or_missing_occurrence_fingerprints_fail() -> None:
    source = _deduplicated(_row())
    unknown = replace(
        source.occurrences[0],
        fingerprint=f"{FINGERPRINT_PREFIX}{'0' * 64}",
    )
    with pytest.raises(AggregationValidationError, match="Unknown occurrence"):
        aggregate_salary_rows(
            DeduplicationResult(source.unique_rows, (unknown,)),
            "2026-27",
        )
    with pytest.raises(AggregationValidationError, match="no occurrence"):
        aggregate_salary_rows(
            DeduplicationResult(source.unique_rows, ()),
            "2026-27",
        )


def test_malformed_occurrence_type_order_and_fields_fail() -> None:
    source = _deduplicated(_row())
    with pytest.raises(AggregationValidationError, match="SourceRowOccurrence"):
        aggregate_salary_rows(
            DeduplicationResult(source.unique_rows, ({},)),
            "2026-27",
        )

    duplicate = _deduplicated(_row(), _duplicate_presentation(_row(), 1))
    with pytest.raises(AggregationValidationError, match="original input order"):
        aggregate_salary_rows(
            DeduplicationResult(
                duplicate.unique_rows,
                tuple(reversed(duplicate.occurrences)),
            ),
            "2026-27",
        )

    for field, value in (
        ("input_position", True),
        ("representative_input_position", 0),
        ("fingerprint", "invalid"),
        ("source_locator", None),
        ("imported_at", 1),
        ("rank_text", False),
        ("source_page_number", 0),
        ("source_row_position", True),
    ):
        malformed = replace(source.occurrences[0], **{field: value})
        with pytest.raises(AggregationValidationError, match=field):
            aggregate_salary_rows(
                DeduplicationResult(source.unique_rows, (malformed,)),
                "2026-27",
            )


def test_inconsistent_representative_relationship_and_provenance_fail() -> None:
    first = _row()
    source = _deduplicated(first, _duplicate_presentation(first, 1))
    inconsistent = replace(source.occurrences[1], representative_input_position=2)
    with pytest.raises(AggregationValidationError, match="representative"):
        aggregate_salary_rows(
            DeduplicationResult(
                source.unique_rows,
                (source.occurrences[0], inconsistent),
            ),
            "2026-27",
        )

    wrong_provenance = replace(source.occurrences[0], rank_text="wrong")
    with pytest.raises(AggregationValidationError, match="provenance"):
        aggregate_salary_rows(
            DeduplicationResult(
                source.unique_rows,
                (wrong_provenance, source.occurrences[1]),
            ),
            "2026-27",
        )


def test_unique_rows_must_follow_first_occurrence_order() -> None:
    source = _deduplicated(_row(player_id=1), _row(player_id=2, team_logo_asset_id=2))
    with pytest.raises(AggregationValidationError, match="first-seen"):
        aggregate_salary_rows(
            DeduplicationResult(
                tuple(reversed(source.unique_rows)), source.occurrences
            ),
            "2026-27",
        )


class _DuplicateDecisionMapping(Mapping[str, Mapping[str, str]]):
    def __init__(self, fingerprint: str) -> None:
        self.fingerprint = fingerprint
        self.decision = {"status": "excluded", "reason": "Reviewed."}

    def __getitem__(self, key: str) -> Mapping[str, str]:
        if key != self.fingerprint:
            raise KeyError(key)
        return self.decision

    def __iter__(self) -> Iterator[str]:
        yield self.fingerprint

    def __len__(self) -> int:
        return 1

    def items(self) -> list[tuple[str, Mapping[str, str]]]:
        return [
            (self.fingerprint, self.decision),
            (self.fingerprint, self.decision),
        ]


def test_duplicate_and_unknown_decisions_fail_with_position() -> None:
    source = _deduplicated(_row())
    fingerprint = _fingerprints(source)[0]
    with pytest.raises(AggregationDecisionError) as duplicate_failure:
        aggregate_salary_rows(
            source,
            "2026-27",
            _DuplicateDecisionMapping(fingerprint),
        )
    assert fingerprint in str(duplicate_failure.value)
    assert "decision position 2" in str(duplicate_failure.value)

    unknown = f"{FINGERPRINT_PREFIX}{'0' * 64}"
    with pytest.raises(AggregationDecisionError) as unknown_failure:
        aggregate_salary_rows(
            source,
            "2026-27",
            {unknown: {"status": "excluded", "reason": "Reviewed."}},
        )
    assert unknown in str(unknown_failure.value)
    assert "decision position 1" in str(unknown_failure.value)


@pytest.mark.parametrize(
    "decisions",
    [
        [],
        {1: {"status": "excluded", "reason": "Reviewed."}},
    ],
)
def test_invalid_decision_collection_or_fingerprint_type_fails(decisions: Any) -> None:
    source = _deduplicated(_row())
    with pytest.raises(AggregationDecisionError):
        aggregate_salary_rows(source, "2026-27", decisions)


@pytest.mark.parametrize(
    "decision",
    [
        None,
        {},
        {"status": "included"},
        {"reason": "Reviewed."},
        {"status": "included", "reason": "Reviewed.", "extra": True},
        {"status": "review_required", "reason": "Reviewed."},
        {"status": "other", "reason": "Reviewed."},
        {"status": [], "reason": "Reviewed."},
        {"status": "included", "reason": None},
        {"status": "included", "reason": ""},
        {"status": "included", "reason": " \t "},
        {"status": "included", "reason": "hidden\nline"},
    ],
)
def test_malformed_decision_shapes_statuses_and_reasons_fail(
    decision: Any,
) -> None:
    source = _deduplicated(_row())
    fingerprint = _fingerprints(source)[0]
    with pytest.raises(AggregationDecisionError) as failure:
        aggregate_salary_rows(source, "2026-27", {fingerprint: decision})
    assert fingerprint in str(failure.value)
    assert "position 1" in str(failure.value)


def test_success_leaves_all_inputs_deeply_unchanged_and_outputs_are_copies() -> None:
    first = _row(source_row_description="review evidence")
    source = _deduplicated(first, _duplicate_presentation(first, 1))
    fingerprint = _fingerprints(source)[0]
    decisions = {fingerprint: {"status": "included", "reason": "  Reviewed source.  "}}
    source_before = deepcopy(source)
    decisions_before = deepcopy(decisions)
    result = aggregate_salary_rows(source, "2026-27", decisions)

    assert source == source_before
    assert decisions == decisions_before
    disposition = result.players[0].row_dispositions[0]
    assert disposition.raw_row is not source.unique_rows[0]
    assert (
        disposition.raw_row["salary_season_cells"]
        is not (source.unique_rows[0]["salary_season_cells"])
    )
    assert disposition.reason == "Reviewed source."

    disposition.raw_row["salary_season_cells"][0]["salary_text"] = "changed output"
    assert source == source_before


def test_failure_prevalidation_leaves_rows_occurrences_and_decisions_unchanged() -> (
    None
):
    source = _deduplicated(_row(), _duplicate_presentation(_row(), 1))
    fingerprint = _fingerprints(source)[0]
    decisions = {
        fingerprint: {"status": "included", "reason": "Reviewed."},
        f"{FINGERPRINT_PREFIX}{'0' * 64}": {
            "status": "included",
            "reason": "Unknown.",
        },
    }
    source_before = deepcopy(source)
    decisions_before = deepcopy(decisions)
    with pytest.raises(AggregationDecisionError):
        aggregate_salary_rows(source, "2026-27", decisions)
    assert source == source_before
    assert decisions == decisions_before
