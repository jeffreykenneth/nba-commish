"""Hoopshype salary-table collection and lossless source parsing."""

from nba_commish.hoopshype.aggregation import (
    AggregationCompletionStatus,
    AggregationReason,
    DecisionSource,
    DispositionEvidence,
    DispositionStatus,
    PlayerSalaryAggregation,
    SalaryAggregationResult,
    SalaryRowDisposition,
    SourcePlayerKey,
    aggregate_salary_rows,
)
from nba_commish.hoopshype.deduplication import (
    DeduplicationResult,
    SourceRowOccurrence,
    deduplicate_salary_rows,
    fingerprint_salary_row,
)
from nba_commish.hoopshype.errors import (
    AggregationDecisionError,
    AggregationError,
    AggregationValidationError,
    FingerprintCollisionError,
    FingerprintError,
    FingerprintValidationError,
    SalaryParseError,
)
from nba_commish.hoopshype.pipeline import run_import
from nba_commish.hoopshype.salary import (
    normalize_salary_row,
    normalize_salary_rows,
    parse_salary_text,
)

__all__ = [
    "AggregationCompletionStatus",
    "AggregationDecisionError",
    "AggregationError",
    "AggregationReason",
    "AggregationValidationError",
    "DecisionSource",
    "DeduplicationResult",
    "DispositionEvidence",
    "DispositionStatus",
    "FingerprintCollisionError",
    "FingerprintError",
    "FingerprintValidationError",
    "PlayerSalaryAggregation",
    "SalaryAggregationResult",
    "SalaryParseError",
    "SalaryRowDisposition",
    "SourcePlayerKey",
    "SourceRowOccurrence",
    "aggregate_salary_rows",
    "deduplicate_salary_rows",
    "fingerprint_salary_row",
    "normalize_salary_row",
    "normalize_salary_rows",
    "parse_salary_text",
    "run_import",
]
