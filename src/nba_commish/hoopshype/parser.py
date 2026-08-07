"""Lossless parsing and structural validation for rendered salary pages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, ClassVar
from urllib.parse import urlsplit

from nba_commish.hoopshype.errors import SourceStructureError
from nba_commish.hoopshype.models import PageSnapshot

PUBLIC_SOURCE_URL = "https://www.hoopshype.com/salaries/players/"

_PAGINATOR_PATTERN = re.compile(r"^(\d+)\s+of\s+(\d+)$")
_PLAYER_ID_PATTERN = re.compile(r"^/salaries/players/[^/]+/(\d+)/?$")
_TEAM_ASSET_PATTERN = re.compile(r"/logos/(\d+)\.png$")


@dataclass(frozen=True)
class ParsedPage:
    """Validated physical rows from one paginator page."""

    page_number: int
    final_page_number: int
    headings: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    content_token: str
    back_disabled: bool
    forward_disabled: bool


def parse_paginator(value: str) -> tuple[int, int]:
    """Parse the adjacent rendered ``N of M`` state."""

    match = _PAGINATOR_PATTERN.fullmatch(value.strip())
    if match is None:
        raise SourceStructureError(
            "Malformed paginator state: expected the adjacent 'N of M' indicator."
        )
    current = int(match.group(1))
    final = int(match.group(2))
    if current < 1 or final < 1 or current > final:
        raise SourceStructureError(
            "Malformed paginator state: page numbers must satisfy 1 <= N <= M."
        )
    return current, final


def parse_page(
    snapshot: PageSnapshot,
    *,
    requested_season: str,
    imported_at: str,
    expected_headings: tuple[str, ...] | None = None,
) -> ParsedPage:
    """Validate a snapshot and retain every physical row in source order."""

    page_number, final_page_number = parse_paginator(snapshot.indicator_text)
    headings = snapshot.headings

    target_count = headings.count(requested_season)
    if target_count == 0:
        raise SourceStructureError(
            f"Requested-season column {requested_season!r} is missing from the table."
        )
    if target_count > 1:
        raise SourceStructureError(
            f"Requested-season heading {requested_season!r} appears more than once."
        )
    if len(headings) < 3 or headings[1] != "Player":
        raise SourceStructureError(
            "Unexpected salary-table header: the second heading must be 'Player'."
        )
    if expected_headings is not None and headings != expected_headings:
        raise SourceStructureError(
            f"Unexpected header change on rendered page {page_number}."
        )

    target_index = headings.index(requested_season) - 2
    salary_headings = headings[2:]
    parsed_rows: list[dict[str, Any]] = []
    for row_position, row in enumerate(snapshot.rows, start=1):
        if not row.player_display_text:
            raise SourceStructureError(
                f"Missing player display text on page {page_number}, row {row_position}."
            )
        if not row.team_logo_url:
            raise SourceStructureError(
                f"Missing team logo on page {page_number}, row {row_position}."
            )
        if len(row.salary_cells) != len(salary_headings):
            raise SourceStructureError(
                f"Unexpected salary-cell count on page {page_number}, row {row_position}."
            )
        cell_headings = tuple(cell.heading for cell in row.salary_cells)
        if cell_headings != salary_headings:
            raise SourceStructureError(
                f"Unexpected salary-cell headings on page {page_number}, row {row_position}."
            )

        target_cell = row.salary_cells[target_index]
        parsed_rows.append(
            {
                "target_season": requested_season,
                "source_page_number": page_number,
                "source_row_position": row_position,
                "rank_text": row.rank_text,
                "player_display_text": row.player_display_text,
                "player_url": row.player_url,
                "player_id": _trailing_player_id(row.player_url),
                "team_logo_url": row.team_logo_url,
                "team_logo_asset_id": _team_logo_asset_id(row.team_logo_url),
                "target_season_salary_text": target_cell.salary_text,
                "target_season_marker_text": target_cell.marker_text,
                "source_row_description": row.source_row_description,
                "source_locator": f"{PUBLIC_SOURCE_URL}#rendered-page-{page_number}",
                "imported_at": imported_at,
                "salary_season_cells": [
                    {
                        "heading": cell.heading,
                        "salary_text": cell.salary_text,
                        "marker_text": cell.marker_text,
                    }
                    for cell in row.salary_cells
                ],
            }
        )

    return ParsedPage(
        page_number=page_number,
        final_page_number=final_page_number,
        headings=headings,
        rows=tuple(parsed_rows),
        content_token=snapshot.content_token,
        back_disabled=snapshot.back_disabled,
        forward_disabled=snapshot.forward_disabled,
    )


def snapshot_from_html(
    html: str,
    *,
    indicator_text: str = "1 of 1",
    back_disabled: bool = True,
    forward_disabled: bool = True,
) -> PageSnapshot:
    """Extract a page snapshot from saved HTML without a browser dependency."""

    parser = _TreeParser()
    parser.feed(html)
    parser.close()
    headings = tuple(node.text().strip() for node in parser.root.find_all("th"))
    salary_headings = headings[2:]
    row_values: list[dict[str, Any]] = []
    for row in parser.root.find_all("tr"):
        if row.has_ancestor("thead"):
            continue
        cells = row.direct_children("td")
        if not cells:
            continue
        if len(cells) < 2:
            row_values.append(
                {
                    "rank_text": cells[0].text().strip() if cells else "",
                    "player_display_text": "",
                    "player_url": None,
                    "team_logo_url": "",
                    "source_row_description": None,
                    "salary_cells": [],
                }
            )
            continue

        identity_cell = cells[1]
        player_link = identity_cell.find_first("a")
        team_logo = identity_cell.find_first("img")
        description = row.attrs.get("data-description")
        if description is None:
            description = row.attrs.get("aria-description")
        salary_cells = []
        for heading, cell in zip(salary_headings, cells[2:], strict=False):
            marker_node = cell.find_first("sup")
            salary_cells.append(
                {
                    "heading": heading,
                    "salary_text": cell.text(excluding={"sup"}).strip(),
                    "marker_text": marker_node.text().strip() if marker_node else "",
                }
            )
        player_node = player_link or identity_cell
        row_values.append(
            {
                "rank_text": cells[0].text().strip(),
                "player_display_text": player_node.text().strip(),
                "player_url": player_link.attrs.get("href") if player_link else None,
                "team_logo_url": team_logo.attrs.get("src") if team_logo else "",
                "source_row_description": description,
                "salary_cells": salary_cells,
            }
        )

    content_token = "\n".join(
        "|".join(
            [
                row["rank_text"],
                row["player_display_text"],
                *(
                    cell["marker_text"] + cell["salary_text"]
                    for cell in row["salary_cells"]
                ),
            ]
        )
        for row in row_values
    )
    return PageSnapshot.from_mapping(
        {
            "indicator_text": indicator_text,
            "back_disabled": back_disabled,
            "forward_disabled": forward_disabled,
            "headings": headings,
            "rows": row_values,
            "content_token": content_token,
        }
    )


def _trailing_player_id(player_url: str | None) -> int | None:
    if player_url is None:
        return None
    path = urlsplit(player_url).path
    match = _PLAYER_ID_PATTERN.search(path)
    return int(match.group(1)) if match else None


def _team_logo_asset_id(team_logo_url: str) -> int | None:
    path = urlsplit(team_logo_url).path
    match = _TEAM_ASSET_PATTERN.search(path)
    return int(match.group(1)) if match else None


class _Node:
    def __init__(
        self,
        tag: str,
        attrs: dict[str, str],
        parent: _Node | None = None,
    ) -> None:
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list[_Node | str] = []

    def text(self, *, excluding: set[str] | None = None) -> str:
        excluded = excluding or set()
        if self.tag in excluded:
            return ""
        return "".join(
            child if isinstance(child, str) else child.text(excluding=excluded)
            for child in self.children
        )

    def find_all(self, tag: str) -> list[_Node]:
        found: list[_Node] = []
        if self.tag == tag:
            found.append(self)
        for child in self.children:
            if isinstance(child, _Node):
                found.extend(child.find_all(tag))
        return found

    def find_first(self, tag: str) -> _Node | None:
        matches = self.find_all(tag)
        return matches[0] if matches else None

    def direct_children(self, tag: str) -> list[_Node]:
        return [
            child
            for child in self.children
            if isinstance(child, _Node) and child.tag == tag
        ]

    def has_ancestor(self, tag: str) -> bool:
        node = self.parent
        while node is not None:
            if node.tag == tag:
                return True
            node = node.parent
        return False


class _TreeParser(HTMLParser):
    _VOID_TAGS: ClassVar[set[str]] = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", {})
        self._current = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(
            tag,
            {name: value or "" for name, value in attrs},
            parent=self._current,
        )
        self._current.children.append(node)
        if tag not in self._VOID_TAGS:
            self._current = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        node = self._current
        while node is not self.root:
            if node.tag == tag:
                self._current = node.parent or self.root
                return
            node = node.parent or self.root

    def handle_data(self, data: str) -> None:
        self._current.children.append(data)
