"""Paginator orchestration independent of Chromium and the live site."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from nba_commish.hoopshype.errors import (
    BrowserCollectionError,
    HoopshypeImportError,
    SourceStructureError,
)
from nba_commish.hoopshype.models import PageSnapshot
from nba_commish.hoopshype.parser import ParsedPage, parse_page


class RenderedPageSession(Protocol):
    """Injectable browser collection boundary."""

    def open(self, season: str) -> None: ...

    def current_snapshot(self) -> PageSnapshot: ...

    def advance(self, previous: PageSnapshot) -> PageSnapshot: ...


@dataclass(frozen=True)
class CollectionResult:
    headings: tuple[str, ...]
    pages: tuple[ParsedPage, ...]
    rows: tuple[dict[str, object], ...]


def collect_all_pages(
    session: RenderedPageSession,
    *,
    season: str,
    imported_at: str,
) -> CollectionResult:
    """Walk each rendered page exactly once and validate completion evidence."""

    try:
        session.open(season)
        snapshot = session.current_snapshot()
        expected_headings: tuple[str, ...] | None = None
        expected_final_page: int | None = None
        pages: list[ParsedPage] = []
        rows: list[dict[str, object]] = []
        seen_pages: set[int] = set()

        while True:
            parsed = parse_page(
                snapshot,
                requested_season=season,
                imported_at=imported_at,
                expected_headings=expected_headings,
            )
            if expected_headings is None:
                expected_headings = parsed.headings
                expected_final_page = parsed.final_page_number
            elif parsed.final_page_number != expected_final_page:
                raise SourceStructureError(
                    "Paginator final-page count changed during collection."
                )

            expected_page = len(pages) + 1
            if parsed.page_number != expected_page or parsed.page_number in seen_pages:
                raise SourceStructureError(
                    f"Repeated or non-advancing page: expected page {expected_page}, "
                    f"observed page {parsed.page_number}."
                )
            if parsed.page_number == 1 and not parsed.back_disabled:
                raise SourceStructureError(
                    "Paginator back control is enabled on the first page."
                )
            if parsed.page_number > 1 and parsed.back_disabled:
                raise SourceStructureError(
                    f"Paginator back control is disabled prematurely on page {parsed.page_number}."
                )
            if parsed.page_number < parsed.final_page_number:
                if parsed.forward_disabled:
                    raise SourceStructureError(
                        f"Paginator forward control is disabled prematurely on page {parsed.page_number}."
                    )
                if not parsed.rows:
                    raise SourceStructureError(
                        f"Zero-row intermediate page observed at page {parsed.page_number}."
                    )
            elif not parsed.forward_disabled:
                raise SourceStructureError(
                    "Final page is not complete because its forward control remains enabled."
                )

            seen_pages.add(parsed.page_number)
            pages.append(parsed)
            rows.extend(parsed.rows)
            if parsed.page_number == parsed.final_page_number:
                break

            previous_snapshot = snapshot
            snapshot = session.advance(previous_snapshot)
            if snapshot.indicator_text == previous_snapshot.indicator_text:
                raise SourceStructureError(
                    "Repeated or non-advancing page: paginator indicator did not change."
                )
            if snapshot.content_token == previous_snapshot.content_token:
                raise SourceStructureError(
                    "Repeated or non-advancing page: rendered row content did not change."
                )

        assert expected_headings is not None
        assert expected_final_page is not None
        expected_pages = set(range(1, expected_final_page + 1))
        if seen_pages != expected_pages:
            raise SourceStructureError(
                "Incomplete paginator walk: not every rendered page was visited exactly once."
            )
        return CollectionResult(
            headings=expected_headings,
            pages=tuple(pages),
            rows=tuple(rows),
        )
    except HoopshypeImportError:
        raise
    except TimeoutError as error:
        raise BrowserCollectionError(
            "Timed out waiting for the rendered Hoopshype page to advance."
        ) from error
    except Exception as error:
        raise BrowserCollectionError(
            "Browser collection failed at the public Hoopshype page."
        ) from error
