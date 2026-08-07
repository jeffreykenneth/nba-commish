"""Hoopshype salary-table collection and lossless source parsing."""

from nba_commish.hoopshype.deduplication import (
    DeduplicationResult,
    SourceRowOccurrence,
    deduplicate_salary_rows,
    fingerprint_salary_row,
)
from nba_commish.hoopshype.errors import (
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
    "DeduplicationResult",
    "FingerprintCollisionError",
    "FingerprintError",
    "FingerprintValidationError",
    "SalaryParseError",
    "SourceRowOccurrence",
    "deduplicate_salary_rows",
    "fingerprint_salary_row",
    "normalize_salary_row",
    "normalize_salary_rows",
    "parse_salary_text",
    "run_import",
]
