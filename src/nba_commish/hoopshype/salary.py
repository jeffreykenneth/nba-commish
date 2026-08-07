"""Pure, auditable normalization of Hoopshype whole-dollar salary text."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from nba_commish.hoopshype.errors import SalaryParseError

_AMOUNT_PATTERN = re.compile(r"^\$?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)$")
_NO_SALARY_SENTINELS = {"", "-", "–", "—"}
_NA_PATTERN = re.compile(r"^N/A$", flags=re.ASCII | re.IGNORECASE)


def parse_salary_text(value: str) -> int | None:
    """Parse the issue-#19 salary text grammar into exact whole dollars.

    Surrounding Unicode whitespace is ignored. Explicit no-salary sentinels
    return ``None``; every other value must be an optional ASCII dollar sign
    followed by ungrouped ASCII digits or correctly comma-grouped ASCII digits.
    """

    if not isinstance(value, str):
        raise SalaryParseError(
            "Malformed target_season_salary_text: expected a string."
        )

    token = value.strip()
    if token in _NO_SALARY_SENTINELS or _NA_PATTERN.fullmatch(token):
        return None
    if _AMOUNT_PATTERN.fullmatch(token) is None:
        raise SalaryParseError(
            "Malformed target_season_salary_text: expected an optional '$' "
            "and ASCII whole-dollar digits with valid comma grouping, or an "
            "explicit no-salary sentinel."
        )

    number = token.removeprefix("$")
    return int(number.replace(",", ""), 10)


def normalize_salary_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Copy one raw issue-#19 row and add its nullable dollar amount."""

    if not isinstance(row, Mapping):
        raise SalaryParseError(
            "Invalid salary row for target_season_salary_text: expected a mapping."
        )

    page_number = _required_positive_integer(row, "source_page_number")
    row_position = _required_positive_integer(row, "source_row_position")
    context = _row_context(row, page_number, row_position)

    if "target_season_salary_text" not in row:
        raise SalaryParseError(f"Missing required target_season_salary_text{context}.")
    salary_text = row["target_season_salary_text"]
    if not isinstance(salary_text, str):
        raise SalaryParseError(
            f"Malformed target_season_salary_text{context}: expected a string."
        )
    try:
        salary_dollars = parse_salary_text(salary_text)
    except SalaryParseError as error:
        raise SalaryParseError(
            f"Malformed target_season_salary_text{context}: invalid whole-dollar "
            "text or no-salary sentinel."
        ) from error

    normalized = dict(row)
    normalized["target_season_salary_dollars"] = salary_dollars
    return normalized


def normalize_salary_rows(
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize every row in input order without collapsing physical rows."""

    return [normalize_salary_row(row) for row in rows]


def _required_positive_integer(row: Mapping[str, Any], field: str) -> int:
    if field not in row:
        raise SalaryParseError(
            f"Missing required {field} for target_season_salary_text row context."
        )
    value = row[field]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SalaryParseError(
            f"Invalid {field} for target_season_salary_text row context: "
            "expected a positive integer."
        )
    return value


def _row_context(row: Mapping[str, Any], page_number: int, row_position: int) -> str:
    context = f" at source page {page_number}, source row {row_position}"
    player = row.get("player_display_text")
    if isinstance(player, str) and player:
        safe_player = "".join(
            character if character.isprintable() else "?" for character in player
        )[:80]
        context += f" for player {safe_player!r}"
    return context
