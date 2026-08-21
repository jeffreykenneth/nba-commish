"""Salary-season validation."""

import re

from nba_commish.hoopshype.errors import SeasonValidationError

_SEASON_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")


def validate_season(value: str) -> str:
    """Accept only consecutive ``yyyy-yy`` salary seasons."""

    match = _SEASON_PATTERN.fullmatch(value)
    if match is None:
        raise SeasonValidationError(
            "Season must use yyyy-yy form with consecutive years, such as 2026-27."
        )

    start_year = int(match.group(1))
    expected_end = (start_year + 1) % 100
    if int(match.group(2)) != expected_end:
        raise SeasonValidationError(
            "Season must use yyyy-yy form with consecutive years, such as 2026-27."
        )
    return value
