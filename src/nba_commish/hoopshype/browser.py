"""Public, unauthenticated Chromium boundary for Hoopshype salary pages."""

from __future__ import annotations

import re
from typing import Any

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from nba_commish.hoopshype.errors import (
    BrowserCollectionError,
    SourceStructureError,
)
from nba_commish.hoopshype.models import PageSnapshot
from nba_commish.hoopshype.parser import PUBLIC_SOURCE_URL

_TIMEOUT_MS = 30_000

_TABLE_READY_SCRIPT = r"""
() => [...document.querySelectorAll("table")].some(table => {
  const headings = [...table.querySelectorAll("thead th")]
    .map(cell => (cell.innerText || "").trim());
  return headings.includes("Player") && table.querySelectorAll("tbody tr").length > 0;
})
"""

_EXTRACT_SCRIPT = r"""
() => {
  const clean = value => (value || "").trim();
  const seasonPattern = /^\d{4}-\d{2}$/;
  const pagePattern = /^\d+\s+of\s+\d+$/;
  const tables = [...document.querySelectorAll("table")].filter(table => {
    const headings = [...table.querySelectorAll("thead th")]
      .map(cell => clean(cell.innerText));
    return headings.includes("Player") && headings.some(value => seasonPattern.test(value));
  });
  if (tables.length !== 1) {
    return {error_code: "salary_table_count"};
  }

  const table = tables[0];
  let container = table.parentElement;
  let paginator = null;
  let indicator = null;
  while (container && !paginator) {
    const candidates = [...container.querySelectorAll("*")].filter(element =>
      element.querySelectorAll("button").length === 0 &&
      pagePattern.test(clean(element.innerText))
    );
    const adjacent = candidates.filter(element => {
      const parent = element.parentElement;
      return parent && parent.querySelectorAll("button").length === 2;
    });
    if (adjacent.length === 1) {
      indicator = adjacent[0];
      paginator = indicator.parentElement;
      break;
    }
    container = container.parentElement;
  }
  if (!paginator || !indicator) {
    return {error_code: "paginator_state"};
  }

  const buttons = [...paginator.querySelectorAll("button")];
  if (buttons.length !== 2) {
    return {error_code: "paginator_buttons"};
  }
  if (!(buttons[0].compareDocumentPosition(indicator) & Node.DOCUMENT_POSITION_FOLLOWING) ||
      !(indicator.compareDocumentPosition(buttons[1]) & Node.DOCUMENT_POSITION_FOLLOWING)) {
    return {error_code: "paginator_order"};
  }

  const headings = [...table.querySelectorAll("thead th")]
    .map(cell => clean(cell.innerText));
  const salaryHeadings = headings.slice(2);
  const rows = [...table.querySelectorAll("tbody tr")].map(row => {
    const cells = [...row.querySelectorAll(":scope > td")];
    const identityCell = cells[1] || null;
    const link = identityCell
      ? identityCell.querySelector('a[href*="/salaries/players/"]')
      : null;
    const logo = identityCell ? identityCell.querySelector("img") : null;
    const salaryCells = cells.slice(2).map((cell, index) => {
      const clone = cell.cloneNode(true);
      clone.querySelectorAll("sup").forEach(marker => marker.remove());
      return {
        heading: salaryHeadings[index] || "",
        salary_text: clean(clone.textContent),
        marker_text: clean((cell.querySelector("sup") || {}).textContent),
      };
    });
    return {
      rank_text: clean((cells[0] || {}).innerText),
      player_display_text: clean((link || identityCell || {}).innerText),
      player_url: link ? link.getAttribute("href") : null,
      team_logo_url: logo ? logo.src : "",
      source_row_description: row.hasAttribute("data-description")
        ? row.getAttribute("data-description")
        : row.getAttribute("aria-description"),
      salary_cells: salaryCells,
    };
  });
  const body = table.querySelector("tbody");
  return {
    indicator_text: clean(indicator.innerText),
    back_disabled: buttons[0].disabled,
    forward_disabled: buttons[1].disabled,
    headings,
    rows,
    content_token: body ? body.innerText : "",
  };
}
"""

_CLICK_FORWARD_SCRIPT = r"""
() => {
  const clean = value => (value || "").trim();
  const pagePattern = /^\d+\s+of\s+\d+$/;
  const tables = [...document.querySelectorAll("table")].filter(table =>
    [...table.querySelectorAll("thead th")].some(cell => clean(cell.innerText) === "Player")
  );
  if (tables.length !== 1) return false;
  let container = tables[0].parentElement;
  while (container) {
    const indicator = [...container.querySelectorAll("*")].find(element =>
      element.querySelectorAll("button").length === 0 &&
      pagePattern.test(clean(element.innerText)) &&
      element.parentElement && element.parentElement.querySelectorAll("button").length === 2
    );
    if (indicator) {
      const buttons = [...indicator.parentElement.querySelectorAll("button")];
      if (buttons[1].disabled) return false;
      buttons[1].click();
      return true;
    }
    container = container.parentElement;
  }
  return false;
}
"""

_ADVANCED_SCRIPT = r"""
previous => {
  const clean = value => (value || "").trim();
  const pagePattern = /^\d+\s+of\s+\d+$/;
  const table = [...document.querySelectorAll("table")].find(table =>
    [...table.querySelectorAll("thead th")].some(cell => clean(cell.innerText) === "Player")
  );
  if (!table) return false;
  let container = table.parentElement;
  while (container) {
    const indicator = [...container.querySelectorAll("*")].find(element =>
      element.querySelectorAll("button").length === 0 &&
      pagePattern.test(clean(element.innerText)) &&
      element.parentElement && element.parentElement.querySelectorAll("button").length === 2
    );
    if (indicator) {
      const body = table.querySelector("tbody");
      return clean(indicator.innerText) !== previous.indicator &&
        (body ? body.innerText : "") !== previous.content;
    }
    container = container.parentElement;
  }
  return false;
}
"""

_STRUCTURE_ERRORS = {
    "salary_table_count": "Expected exactly one rendered salary table.",
    "paginator_state": "Malformed or missing paginator state adjacent to the salary table.",
    "paginator_buttons": "Expected exactly two paginator buttons adjacent to the page state.",
    "paginator_order": "Paginator buttons are not ordered back, state, then forward.",
}


class PlaywrightPageSession:
    """Fresh Chromium session that never loads or persists browser profile state."""

    def __init__(self, *, timeout_ms: int = _TIMEOUT_MS) -> None:
        self._timeout_ms = timeout_ms
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    def open(self, season: str) -> None:
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            self._context = self._browser.new_context(service_workers="block")
            self._page = self._context.new_page()
            self._page.set_default_timeout(self._timeout_ms)
            self._page.goto(
                PUBLIC_SOURCE_URL,
                wait_until="domcontentloaded",
                timeout=self._timeout_ms,
            )
            self._page.wait_for_function(_TABLE_READY_SCRIPT, timeout=self._timeout_ms)
            self._select_or_verify_control(f"{season} season", suffix=" season")
            self._select_or_verify_control("All salaries", suffix=" salaries")
            self._page.wait_for_function(
                """season => [...document.querySelectorAll('table thead th')]
                    .some(cell => (cell.innerText || '').trim() === season)""",
                arg=season,
                timeout=self._timeout_ms,
            )
        except PlaywrightTimeoutError as error:
            self.close()
            raise BrowserCollectionError(
                "Timed out waiting for the public Hoopshype salary table or controls."
            ) from error
        except BrowserCollectionError:
            self.close()
            raise
        except Exception as error:
            self.close()
            raise BrowserCollectionError(
                "Could not open the public Hoopshype salary page in Chromium."
            ) from error

    def current_snapshot(self) -> PageSnapshot:
        page = self._require_page()
        try:
            value: dict[str, Any] = page.evaluate(_EXTRACT_SCRIPT)
        except PlaywrightTimeoutError as error:
            raise BrowserCollectionError(
                "Timed out while extracting the rendered Hoopshype salary table."
            ) from error
        except Exception as error:
            raise BrowserCollectionError(
                "Could not extract the rendered public salary table."
            ) from error
        error_code = value.get("error_code")
        if error_code:
            raise SourceStructureError(
                _STRUCTURE_ERRORS.get(
                    str(error_code), "Unexpected rendered salary-table structure."
                )
            )
        return PageSnapshot.from_mapping(value)

    def advance(self, previous: PageSnapshot) -> PageSnapshot:
        page = self._require_page()
        try:
            clicked = page.evaluate(_CLICK_FORWARD_SCRIPT)
            if not clicked:
                raise SourceStructureError(
                    "Paginator forward control could not be activated."
                )
            page.wait_for_function(
                _ADVANCED_SCRIPT,
                arg={
                    "indicator": previous.indicator_text,
                    "content": previous.content_token,
                },
                timeout=self._timeout_ms,
            )
            return self.current_snapshot()
        except PlaywrightTimeoutError as error:
            raise BrowserCollectionError(
                "Timed out waiting for both paginator state and row content to change."
            ) from error

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        self._page = None

    def _select_or_verify_control(self, target: str, *, suffix: str) -> None:
        page = self._require_page()
        pattern = re.compile(rf"^.+{re.escape(suffix)}$")
        controls = page.locator("button").filter(has_text=pattern)
        visible = [
            controls.nth(index)
            for index in range(controls.count())
            if controls.nth(index).is_visible()
        ]
        if not visible:
            raise BrowserCollectionError(
                f"Could not find the public table control for {target!r}."
            )
        current = visible[0]
        if current.inner_text().strip() == target:
            return

        current.click()
        options = page.get_by_text(target, exact=True)
        for index in range(options.count()):
            option = options.nth(index)
            if option.is_visible():
                option.click()
                break
        else:
            raise BrowserCollectionError(
                f"Requested public table option {target!r} is unavailable."
            )
        current.wait_for(state="visible")
        if current.inner_text().strip() != target:
            raise BrowserCollectionError(
                f"Public table control did not select {target!r}."
            )

    def _require_page(self) -> Page:
        if self._page is None:
            raise BrowserCollectionError("Chromium session is not open.")
        return self._page
