"""Pure, reviewable aggregation of deduplicated Hoopshype salary rows."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from nba_commish.hoopshype.deduplication import (
    FINGERPRINT_PREFIX,
    DeduplicationResult,
    SourceRowOccurrence,
    _canonical_description,
    _canonical_marker,
    _canonical_player_url,
    _collapse_whitespace,
    _validate_season,
    fingerprint_salary_row,
)
from nba_commish.hoopshype.errors import (
    AggregationDecisionError,
    AggregationValidationError,
    FingerprintValidationError,
)

_FINGERPRINT_PATTERN = re.compile(rf"^{re.escape(FINGERPRINT_PREFIX)}[0-9a-f]{{64}}$")


class DispositionStatus(StrEnum):
    """Review status assigned to one unique source salary row."""

    INCLUDED = "included"
    EXCLUDED = "excluded"
    REVIEW_REQUIRED = "review_required"


class DecisionSource(StrEnum):
    """Origin of a row disposition."""

    DEFAULT = "default"
    COMMISSIONER = "commissioner"


class AggregationCompletionStatus(StrEnum):
    """Whether a player's salary total is final."""

    COMPLETE = "complete"
    REVIEW_REQUIRED = "review_required"


class AggregationReason(StrEnum):
    """Stable Phase 0 disposition reason codes."""

    NO_SALARY_AMOUNT = "no_salary_amount"
    SOURCE_EVIDENCE_REQUIRES_REVIEW = "source_evidence_requires_review"
    MULTIPLE_NON_NULL_ROWS = "multiple_non_null_rows"
    SINGLE_ORDINARY_NON_NULL_ROW = "single_ordinary_non_null_row"
    COMMISSIONER_INCLUDED = "commissioner_included"
    COMMISSIONER_EXCLUDED = "commissioner_excluded"


@dataclass(frozen=True)
class SourcePlayerKey:
    """Tagged issue-#21 player discriminator used for source grouping."""

    kind: str
    value: int | str


@dataclass(frozen=True)
class DispositionEvidence:
    """One source-observed marker or description retained for review."""

    code: str
    value: str
    label: str


@dataclass(frozen=True)
class SalaryRowDisposition:
    """Review disposition for one unique issue-#21 salary row."""

    fingerprint: str
    raw_row: dict[str, Any]
    occurrences: tuple[SourceRowOccurrence, ...]
    target_salary_dollars: int | None
    status: DispositionStatus
    reason_code: AggregationReason
    reason: str
    evidence: tuple[DispositionEvidence, ...]
    decision_source: DecisionSource


@dataclass(frozen=True)
class PlayerSalaryAggregation:
    """Ordered salary-review model for one source player."""

    source_player_key: SourcePlayerKey
    representative_display_text: str
    selected_season: str
    row_dispositions: tuple[SalaryRowDisposition, ...]
    included_rows: tuple[SalaryRowDisposition, ...]
    excluded_rows: tuple[SalaryRowDisposition, ...]
    review_required_rows: tuple[SalaryRowDisposition, ...]
    included_subtotal_dollars: int
    unresolved_review_count: int
    total_salary_dollars: int | None
    completion_status: AggregationCompletionStatus


@dataclass(frozen=True)
class SalaryAggregationResult:
    """Complete ordered aggregation for one selected season."""

    selected_season: str
    players: tuple[PlayerSalaryAggregation, ...]


@dataclass(frozen=True)
class _ValidatedRow:
    position: int
    fingerprint: str
    raw_row: Mapping[str, Any]
    player_key: SourcePlayerKey
    display_text: str
    target_salary_dollars: int | None
    marker: str
    description: str | None
    occurrences: tuple[SourceRowOccurrence, ...]


@dataclass(frozen=True)
class _CommissionerDecision:
    status: DispositionStatus
    reason: str


def aggregate_salary_rows(
    deduplicated: DeduplicationResult,
    selected_season: str,
    commissioner_decisions: Mapping[str, Mapping[str, Any]] | None = None,
) -> SalaryAggregationResult:
    """Aggregate validated unique rows without guessing unresolved applicability.

    ``commissioner_decisions`` is keyed by ``source_row_fingerprint``. Each
    value must contain exactly ``status`` (``included`` or ``excluded``) and a
    nonempty human-readable ``reason``.
    """

    _validate_selected_season(selected_season)
    validated_rows = _validate_deduplication_result(deduplicated, selected_season)
    decisions = _validate_commissioner_decisions(
        commissioner_decisions,
        validated_rows,
    )

    grouped: dict[SourcePlayerKey, list[_ValidatedRow]] = {}
    for row in validated_rows:
        grouped.setdefault(row.player_key, []).append(row)

    players: list[PlayerSalaryAggregation] = []
    for player_key, rows in grouped.items():
        non_null_count = sum(row.target_salary_dollars is not None for row in rows)
        dispositions = tuple(
            _disposition_for_row(
                row,
                non_null_count=non_null_count,
                decision=decisions.get(row.fingerprint),
            )
            for row in rows
        )
        included = tuple(
            row for row in dispositions if row.status is DispositionStatus.INCLUDED
        )
        excluded = tuple(
            row for row in dispositions if row.status is DispositionStatus.EXCLUDED
        )
        review_required = tuple(
            row
            for row in dispositions
            if row.status is DispositionStatus.REVIEW_REQUIRED
        )
        subtotal = sum(
            row.target_salary_dollars
            for row in included
            if row.target_salary_dollars is not None
        )
        if review_required:
            total = None
            completion = AggregationCompletionStatus.REVIEW_REQUIRED
        else:
            total = subtotal
            completion = AggregationCompletionStatus.COMPLETE
        players.append(
            PlayerSalaryAggregation(
                source_player_key=player_key,
                representative_display_text=rows[0].display_text,
                selected_season=selected_season,
                row_dispositions=dispositions,
                included_rows=included,
                excluded_rows=excluded,
                review_required_rows=review_required,
                included_subtotal_dollars=subtotal,
                unresolved_review_count=len(review_required),
                total_salary_dollars=total,
                completion_status=completion,
            )
        )

    return SalaryAggregationResult(
        selected_season=selected_season,
        players=tuple(players),
    )


def _validate_selected_season(selected_season: str) -> None:
    if not isinstance(selected_season, str):
        raise AggregationValidationError(
            "selected_season must be a consecutive yyyy-yy season."
        )
    try:
        _validate_season(selected_season, field="selected_season", context="")
    except FingerprintValidationError as error:
        raise AggregationValidationError(
            "selected_season must be a consecutive ASCII yyyy-yy season."
        ) from error


def _validate_deduplication_result(
    deduplicated: DeduplicationResult,
    selected_season: str,
) -> tuple[_ValidatedRow, ...]:
    if not isinstance(deduplicated, DeduplicationResult):
        raise AggregationValidationError(
            "Aggregation requires an issue-#21 DeduplicationResult."
        )
    if not isinstance(deduplicated.unique_rows, tuple):
        raise AggregationValidationError(
            "DeduplicationResult.unique_rows must be a tuple."
        )
    if not isinstance(deduplicated.occurrences, tuple):
        raise AggregationValidationError(
            "DeduplicationResult.occurrences must be a tuple."
        )

    partial_rows: list[tuple[int, str, Mapping[str, Any]]] = []
    fingerprints: set[str] = set()
    for position, row in enumerate(deduplicated.unique_rows, start=1):
        if not isinstance(row, Mapping):
            raise AggregationValidationError(
                f"Unique row {position} must be a mapping."
            )
        context = _row_context(row, position=position)
        fingerprint = row.get("source_row_fingerprint")
        if (
            not isinstance(fingerprint, str)
            or _FINGERPRINT_PATTERN.fullmatch(fingerprint) is None
        ):
            raise AggregationValidationError(
                f"Invalid source_row_fingerprint{context}."
            )
        if fingerprint in fingerprints:
            raise AggregationValidationError(
                f"Duplicate source_row_fingerprint {fingerprint}{context}."
            )
        fingerprints.add(fingerprint)

        canonical_input = dict(row)
        canonical_input.pop("source_row_fingerprint")
        try:
            recomputed = fingerprint_salary_row(canonical_input)
        except FingerprintValidationError as error:
            raise AggregationValidationError(
                f"Unique row cannot be validated against fingerprint v1{context}."
            ) from error
        if recomputed != fingerprint:
            raise AggregationValidationError(
                f"source_row_fingerprint mismatch for {fingerprint}{context}."
            )
        if row["target_season"] != selected_season:
            raise AggregationValidationError(
                f"target_season must equal selected_season {selected_season!r}{context}."
            )
        partial_rows.append((position, fingerprint, row))

    occurrences_by_fingerprint = _validate_occurrences(
        deduplicated,
        fingerprints=fingerprints,
        partial_rows=partial_rows,
    )

    validated: list[_ValidatedRow] = []
    for position, fingerprint, row in partial_rows:
        marker = _canonical_marker(row["target_season_marker_text"])
        description = _canonical_description(row["source_row_description"])
        validated.append(
            _ValidatedRow(
                position=position,
                fingerprint=fingerprint,
                raw_row=row,
                player_key=_source_player_key(row, position=position),
                display_text=row["player_display_text"],
                target_salary_dollars=row["target_season_salary_dollars"],
                marker=marker,
                description=description,
                occurrences=tuple(occurrences_by_fingerprint[fingerprint]),
            )
        )
    return tuple(validated)


def _validate_occurrences(
    deduplicated: DeduplicationResult,
    *,
    fingerprints: set[str],
    partial_rows: list[tuple[int, str, Mapping[str, Any]]],
) -> dict[str, list[SourceRowOccurrence]]:
    occurrences_by_fingerprint: dict[str, list[SourceRowOccurrence]] = {
        fingerprint: [] for fingerprint in fingerprints
    }
    occurrences_by_position: dict[int, SourceRowOccurrence] = {}
    for occurrence_position, occurrence in enumerate(deduplicated.occurrences, start=1):
        if not isinstance(occurrence, SourceRowOccurrence):
            raise AggregationValidationError(
                f"Occurrence {occurrence_position} must be a SourceRowOccurrence."
            )
        _validate_occurrence_fields(occurrence, occurrence_position)
        if occurrence.input_position != occurrence_position:
            raise AggregationValidationError(
                "Occurrences must retain consecutive original input order"
                f"{_occurrence_context(occurrence, occurrence_position)}."
            )
        if occurrence.fingerprint not in fingerprints:
            raise AggregationValidationError(
                "Unknown occurrence fingerprint "
                f"{occurrence.fingerprint} at occurrence {occurrence_position}."
            )
        occurrences_by_position[occurrence.input_position] = occurrence
        occurrences_by_fingerprint[occurrence.fingerprint].append(occurrence)

    for fingerprint, occurrences in occurrences_by_fingerprint.items():
        if not occurrences:
            raise AggregationValidationError(
                f"Unique row {fingerprint} has no occurrence provenance."
            )
        representative_position = occurrences[0].representative_input_position
        if representative_position != occurrences[0].input_position:
            raise AggregationValidationError(
                "First occurrence is not its own representative for "
                f"{fingerprint} at occurrence {occurrences[0].input_position}."
            )
        for occurrence in occurrences:
            if occurrence.representative_input_position != representative_position:
                raise AggregationValidationError(
                    "Inconsistent representative relationship for "
                    f"{fingerprint} at occurrence {occurrence.input_position}."
                )
            representative = occurrences_by_position.get(
                occurrence.representative_input_position
            )
            if representative is None or representative.fingerprint != fingerprint:
                raise AggregationValidationError(
                    "Invalid representative occurrence relationship for "
                    f"{fingerprint} at occurrence {occurrence.input_position}."
                )

    expected_order = [
        occurrences_by_fingerprint[fingerprint][0].input_position
        for _, fingerprint, _ in partial_rows
    ]
    if expected_order != sorted(expected_order):
        raise AggregationValidationError(
            "unique_rows must remain in first-seen occurrence order."
        )

    for position, fingerprint, row in partial_rows:
        representative = occurrences_by_fingerprint[fingerprint][0]
        if not _representative_matches_row(representative, row):
            raise AggregationValidationError(
                "Representative occurrence provenance is inconsistent with "
                f"unique row {position} ({fingerprint}){_row_context(row, position=position)}."
            )
    return occurrences_by_fingerprint


def _validate_occurrence_fields(
    occurrence: SourceRowOccurrence,
    occurrence_position: int,
) -> None:
    for field in ("input_position", "representative_input_position"):
        value = getattr(occurrence, field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise AggregationValidationError(
                f"Invalid {field}{_occurrence_context(occurrence, occurrence_position)}."
            )
    if (
        not isinstance(occurrence.fingerprint, str)
        or _FINGERPRINT_PATTERN.fullmatch(occurrence.fingerprint) is None
    ):
        raise AggregationValidationError(
            "Invalid fingerprint"
            f"{_occurrence_context(occurrence, occurrence_position)}."
        )
    for field in ("source_locator", "imported_at", "rank_text"):
        if not isinstance(getattr(occurrence, field), str):
            raise AggregationValidationError(
                f"Invalid {field}{_occurrence_context(occurrence, occurrence_position)}."
            )
    for field in ("source_page_number", "source_row_position"):
        value = getattr(occurrence, field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise AggregationValidationError(
                f"Invalid {field}{_occurrence_context(occurrence, occurrence_position)}."
            )


def _representative_matches_row(
    occurrence: SourceRowOccurrence,
    row: Mapping[str, Any],
) -> bool:
    return (
        occurrence.source_locator == row["source_locator"]
        and occurrence.imported_at == row["imported_at"]
        and occurrence.source_page_number == row["source_page_number"]
        and occurrence.source_row_position == row["source_row_position"]
        and occurrence.rank_text == row["rank_text"]
    )


def _source_player_key(
    row: Mapping[str, Any],
    *,
    position: int,
) -> SourcePlayerKey:
    player_id = row["player_id"]
    if player_id is not None:
        return SourcePlayerKey(kind="player_id", value=player_id)

    player_url = row["player_url"]
    if player_url:
        context = _row_context(row, position=position)
        try:
            canonical_url = _canonical_player_url(player_url, context=context)
        except FingerprintValidationError as error:
            raise AggregationValidationError(
                f"Invalid player_url for source-player grouping{context}."
            ) from error
        return SourcePlayerKey(kind="player_url", value=canonical_url)

    display = _collapse_whitespace(
        unicodedata.normalize("NFC", row["player_display_text"])
    ).casefold()
    return SourcePlayerKey(kind="player_display_text", value=display)


def _validate_commissioner_decisions(
    commissioner_decisions: Mapping[str, Mapping[str, Any]] | None,
    rows: tuple[_ValidatedRow, ...],
) -> dict[str, _CommissionerDecision]:
    if commissioner_decisions is None:
        return {}
    if not isinstance(commissioner_decisions, Mapping):
        raise AggregationDecisionError(
            "commissioner_decisions must be a mapping keyed by fingerprint."
        )

    rows_by_fingerprint = {row.fingerprint: row for row in rows}
    validated: dict[str, _CommissionerDecision] = {}
    for decision_position, item in enumerate(commissioner_decisions.items(), start=1):
        try:
            fingerprint, decision = item
        except (TypeError, ValueError) as error:
            raise AggregationDecisionError(
                f"Malformed commissioner decision at position {decision_position}."
            ) from error
        if not isinstance(fingerprint, str):
            raise AggregationDecisionError(
                f"Decision fingerprint must be a string at position {decision_position}."
            )
        if fingerprint in validated:
            raise AggregationDecisionError(
                "Duplicate commissioner decision for "
                f"{fingerprint} at decision position {decision_position}."
            )
        row = rows_by_fingerprint.get(fingerprint)
        if row is None:
            raise AggregationDecisionError(
                "Unknown source_row_fingerprint "
                f"{fingerprint} at decision position {decision_position}."
            )
        if not isinstance(decision, Mapping):
            raise AggregationDecisionError(
                "Decision must be a mapping for "
                f"{fingerprint} at decision position {decision_position}."
            )
        if set(decision) != {"status", "reason"}:
            raise AggregationDecisionError(
                "Decision must contain exactly status and reason for "
                f"{fingerprint} at decision position {decision_position}."
            )
        status_value = decision["status"]
        if not isinstance(status_value, str) or status_value not in (
            DispositionStatus.INCLUDED,
            DispositionStatus.EXCLUDED,
        ):
            raise AggregationDecisionError(
                "Unsupported decision status for "
                f"{fingerprint} at decision position {decision_position}; "
                "expected included or excluded."
            )
        reason_value = decision["reason"]
        if not isinstance(reason_value, str):
            raise AggregationDecisionError(
                "Decision reason must be a string for "
                f"{fingerprint} at decision position {decision_position}."
            )
        reason = reason_value.strip()
        if not reason or any(not character.isprintable() for character in reason):
            raise AggregationDecisionError(
                "Decision reason must be nonempty human-readable text for "
                f"{fingerprint} at decision position {decision_position}."
            )
        status = DispositionStatus(status_value)
        if status is DispositionStatus.INCLUDED and row.target_salary_dollars is None:
            raise AggregationDecisionError(
                "A null salary row cannot be included for "
                f"{fingerprint} at decision position {decision_position}."
            )
        validated[fingerprint] = _CommissionerDecision(
            status=status,
            reason=reason,
        )
    return validated


def _disposition_for_row(
    row: _ValidatedRow,
    *,
    non_null_count: int,
    decision: _CommissionerDecision | None,
) -> SalaryRowDisposition:
    evidence = _source_evidence(row)
    if decision is not None:
        status = decision.status
        reason_code = (
            AggregationReason.COMMISSIONER_INCLUDED
            if status is DispositionStatus.INCLUDED
            else AggregationReason.COMMISSIONER_EXCLUDED
        )
        reason = decision.reason
        decision_source = DecisionSource.COMMISSIONER
    elif row.target_salary_dollars is None:
        status = DispositionStatus.EXCLUDED
        reason_code = AggregationReason.NO_SALARY_AMOUNT
        reason = "No target-season salary amount is present."
        decision_source = DecisionSource.DEFAULT
    elif evidence:
        status = DispositionStatus.REVIEW_REQUIRED
        reason_code = AggregationReason.SOURCE_EVIDENCE_REQUIRES_REVIEW
        reason = (
            "A source marker or description requires commissioner review; "
            "contract applicability was not inferred."
        )
        decision_source = DecisionSource.DEFAULT
    elif non_null_count > 1:
        status = DispositionStatus.REVIEW_REQUIRED
        reason_code = AggregationReason.MULTIPLE_NON_NULL_ROWS
        reason = (
            "This source player has multiple distinct non-null salary rows; "
            "applicability requires commissioner review."
        )
        decision_source = DecisionSource.DEFAULT
    else:
        status = DispositionStatus.INCLUDED
        reason_code = AggregationReason.SINGLE_ORDINARY_NON_NULL_ROW
        reason = (
            "The only non-null source row has no marker or description and is "
            "included by the Phase 0 default policy."
        )
        decision_source = DecisionSource.DEFAULT

    return SalaryRowDisposition(
        fingerprint=row.fingerprint,
        raw_row=deepcopy(dict(row.raw_row)),
        occurrences=deepcopy(row.occurrences),
        target_salary_dollars=row.target_salary_dollars,
        status=status,
        reason_code=reason_code,
        reason=reason,
        evidence=evidence,
        decision_source=decision_source,
    )


def _source_evidence(row: _ValidatedRow) -> tuple[DispositionEvidence, ...]:
    evidence: list[DispositionEvidence] = []
    if row.marker:
        if row.marker == "TW":
            code = "hoopshype_two_way_contract_marker"
            label = (
                "Hoopshype marker TW is evidence for a two-way contract; "
                "it does not decide salary applicability."
            )
        else:
            code = "source_target_marker"
            label = (
                "Hoopshype target-season marker retained without contract "
                "classification."
            )
        evidence.append(
            DispositionEvidence(
                code=code,
                value=row.raw_row["target_season_marker_text"],
                label=label,
            )
        )
    if row.description is not None:
        evidence.append(
            DispositionEvidence(
                code="source_row_description",
                value=row.raw_row["source_row_description"],
                label=(
                    "Source-row description retained as evidence without "
                    "free-text classification."
                ),
            )
        )
    return tuple(evidence)


def _row_context(row: Mapping[str, Any], *, position: int) -> str:
    parts = [f"unique row {position}"]
    page = row.get("source_page_number")
    source_row = row.get("source_row_position")
    if isinstance(page, int) and not isinstance(page, bool):
        parts.append(f"source page {page}")
    if isinstance(source_row, int) and not isinstance(source_row, bool):
        parts.append(f"source row {source_row}")
    player = row.get("player_display_text")
    if isinstance(player, str) and player:
        safe_player = "".join(
            character if character.isprintable() else "?" for character in player
        )[:80]
        parts.append(f"player {safe_player!r}")
    return f" at {', '.join(parts)}"


def _occurrence_context(
    occurrence: SourceRowOccurrence,
    position: int,
) -> str:
    parts = [f"occurrence {position}"]
    fingerprint = occurrence.fingerprint
    if isinstance(fingerprint, str) and _FINGERPRINT_PATTERN.fullmatch(fingerprint):
        parts.append(fingerprint)
    page = occurrence.source_page_number
    source_row = occurrence.source_row_position
    if isinstance(page, int) and not isinstance(page, bool):
        parts.append(f"source page {page}")
    if isinstance(source_row, int) and not isinstance(source_row, bool):
        parts.append(f"source row {source_row}")
    return f" at {', '.join(parts)}"
