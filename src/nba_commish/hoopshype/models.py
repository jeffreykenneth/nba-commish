"""Boundary models for rendered Hoopshype salary pages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SalaryCellSnapshot:
    """One rendered salary-season cell before semantic interpretation."""

    heading: str
    salary_text: str
    marker_text: str


@dataclass(frozen=True)
class RowSnapshot:
    """One physical source row extracted from the rendered table."""

    rank_text: str
    player_display_text: str
    player_url: str | None
    team_logo_url: str
    source_row_description: str | None
    salary_cells: tuple[SalaryCellSnapshot, ...]


@dataclass(frozen=True)
class PageSnapshot:
    """A rendered table and its structurally adjacent paginator state."""

    indicator_text: str
    back_disabled: bool
    forward_disabled: bool
    headings: tuple[str, ...]
    rows: tuple[RowSnapshot, ...]
    content_token: str

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> PageSnapshot:
        """Build a typed snapshot from an injected extractor result."""

        headings = tuple(str(heading) for heading in value.get("headings", ()))
        rows = tuple(
            RowSnapshot(
                rank_text=str(row.get("rank_text", "")),
                player_display_text=str(row.get("player_display_text", "")),
                player_url=_nullable_string(row.get("player_url")),
                team_logo_url=str(row.get("team_logo_url", "")),
                source_row_description=_nullable_string(
                    row.get("source_row_description")
                ),
                salary_cells=tuple(
                    SalaryCellSnapshot(
                        heading=str(cell.get("heading", "")),
                        salary_text=str(cell.get("salary_text", "")),
                        marker_text=str(cell.get("marker_text", "")),
                    )
                    for cell in row.get("salary_cells", ())
                ),
            )
            for row in value.get("rows", ())
        )
        return cls(
            indicator_text=str(value.get("indicator_text", "")),
            back_disabled=bool(value.get("back_disabled", False)),
            forward_disabled=bool(value.get("forward_disabled", False)),
            headings=headings,
            rows=rows,
            content_token=str(value.get("content_token", "")),
        )


def _nullable_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
