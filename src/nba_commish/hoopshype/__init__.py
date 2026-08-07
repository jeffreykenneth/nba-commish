"""Hoopshype salary-table collection and lossless source parsing."""

from nba_commish.hoopshype.errors import SalaryParseError
from nba_commish.hoopshype.pipeline import run_import
from nba_commish.hoopshype.salary import (
    normalize_salary_row,
    normalize_salary_rows,
    parse_salary_text,
)

__all__ = [
    "SalaryParseError",
    "normalize_salary_row",
    "normalize_salary_rows",
    "parse_salary_text",
    "run_import",
]
