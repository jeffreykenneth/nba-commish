# Hoopshype salary-source findings: 2026-27

## Decision and scope

**Primary acquisition method:** browser rendering. **Next viable fallback:** commissioner-provided CSV.

The investigation ran from `2026-08-07T18:40:47Z` through
`2026-08-07T18:48:48Z`. The target salary season is **2026-27**. The sanitized
source locator is the public, unauthenticated
[Hoopshype NBA Player Salaries page](https://www.hoopshype.com/salaries/players/).
The page itself displayed `Updated Aug. 07, 2026, 4:02 PM GMT+3`; because it did
not display seconds, `source_event_time` remains `null` rather than being
inferred.

The exact scope was the `Players` view with the `2026-27 season` and `All
salaries` controls selected, and every page of its one rendered salary table.
The team-payroll and agent views, individual player pages, salary parsing,
aggregation, applicability decisions, and importer implementation were not
examined. The reviewed source subset is
[`data/raw/hoopshype/nba-salary-source--season-2026-27--20260807t184848z.html`](../data/raw/hoopshype/nba-salary-source--season-2026-27--20260807t184848z.html).

Browser rendering is primary because it was the only attempted method that
produced all **539** observed rows. It is reproducible with the procedure below,
but carries maintenance cost: pagination is client-side, the two arrow buttons
have no accessible name, and the observed CSS classes are generated-looking.
The complete 27-page walk took approximately 49 seconds. Standard HTTP was
faster (about four seconds) and simpler, but exposed only 20 real rows. A
commissioner CSV is the next fallback because it can carry a complete static
season without page-structure coupling; its schema and import behavior belong
to issue #23 and no commissioner file was supplied for this investigation.

## Standard HTTP versus rendered DOM

Both checks used the same public source during the capture window, without
credentials, cookies, or session material.

| Observation | Standard unauthenticated HTTP body | Rendered browser DOM |
| --- | --- | --- |
| Request/result | `https://hoopshype.com/salaries/players/` redirected to canonical `www`; final status `200` | Canonical URL loaded successfully; no authentication or challenge |
| Salary table exists | Yes, one real salary table, plus two loading-skeleton tables | Yes, one salary table after rendering |
| Initial real salary rows | 20 | 20 on page 1 |
| Complete observed rows | 20 available in the response body | 539 across 27 paginator pages |
| Headers on real table | blank rank, `Player`, `2026-27`, `2027-28`, `2028-29`, `2029-30` | blank rank, `Player`, `2026-27`, `2027-28`, `2028-29`, `2029-30` |
| Table identification | Third `<table>`; observed class `bserqJ__bserqJ _2gszxk__2gszxk o6j80t__o6j80t`; first two tables contain `aria-busy="true"` skeletons | The only rendered `table`; `table thead th` for headings and `table tbody tr` for rows |
| Blocking/challenge evidence | None: no `cloudflare`, CAPTCHA, `access denied`, `just a moment`, or challenge-platform text | None observed |

At `2026-08-07T18:41:17Z`, the HTTP response was 145,985 bytes and contained
three `<table>` elements and 65 total `<tr>` elements. Detailed inspection found
two skeleton `<tbody>` elements with 21 placeholder rows each and one real
salary `<tbody>` with 20 rows. Skeleton rows have no player, link, or salary
values and are **not** counted as salary rows.

The HTTP response therefore contains a valid but incomplete first page. The
browser initially agrees at 20 rows, then exposes another 519 only through the
client-side paginator. JavaScript rendering and interaction are required for a
complete table at this source. The 20-versus-539 difference is material and
must not be interpreted as 42 additional salary rows from the HTTP skeletons.

An exploratory historical-looking locator ending in `/2025-2026/` redirected
to `www` and returned `404` with zero tables. It was discarded rather than
treated as evidence or salary rows; the live page's selected `2026-27` season
defines this investigation's target.

## Pagination, lazy loading, and completion

Pagination was present. Immediately after the table were two icon-only arrow
buttons and an `N of 27` indicator. On page 1, the back button was disabled and
the forward button was enabled. Activating the forward button changed the
indicator and rows but did not change the URL. Pages 1 through 26 each contained
20 rows; page 27 contained 19. The calculation `26 * 20 + 19` established the
final row count of **539**. On page 27 the forward button was disabled.

The observed generated selectors were
`button.hd3Vfp__hd3Vfp.WxJiwT__WxJiwT` for back and
`button.hd3Vfp__hd3Vfp._3JhbLM__3JhbLM` for forward. They should be treated as
diagnostic evidence, not a durable contract. A repeatable implementation should
anchor on the table, the adjacent `N of M` text, button direction/order, and
disabled state, verifying that the indicator increments after each action.

Lazy loading was checked and was not observed. Rows changed only after a
paginator action. At `27 of 27`, a full downward scroll followed by a 2.5-second
wait left the row count at 19, the document scroll height at 2,469 pixels, the
indicator at `27 of 27`, and the forward button disabled. Completion was thus
determined by visiting all 27 indicators, summing each page's rows, confirming
the last-page disabled state, and confirming that scrolling did not append
rows.

## Columns and target-season mapping

Every displayed heading is listed below exactly as rendered.

| Physical column | Displayed heading | Normalized meaning | Notes |
| --- | --- | --- | --- |
| 1 | *(blank)* | none | Rank/tie label such as `1`, `83`, or `T462`; non-season |
| 2 | `Player` | none | Player/team presentation; non-season |
| 3 | `2026-27` | `2026-27` | **Selected target-season column** |
| 4 | `2027-28` | `2027-28` | Future season |
| 5 | `2028-29` | `2028-29` | Future season |
| 6 | `2029-30` | `2029-30` | Future season |

The displayed season headings already use normalized `yyyy-yy` form, so the
mapping is identity-preserving. There were no repeated, grouped, or missing
season headings. Each header had `colspan="1"`. The blank rank column and
`Player` are the only non-season columns. A dash (`-`) is a displayed source
value, not an absent column. Source superscripts `P`, `T`, `Q`, and `TW` are
part of the salary-cell presentation and must be retained; interpreting or
parsing them is outside this issue.

## Team and player representation

### Team

All 539 source rows put a 30-pixel team-logo `<img>` before the player identity
inside the second cell. The `src`/`srcset` shape is public CDN content ending in
`/sports2/nba/logos/<numeric-asset-id>.png` plus public rendering parameters.
The image has `alt=""`, is not wrapped in a link, and has no adjacent team text
or abbreviation. Thirty distinct numeric logo asset IDs were observed. Every
row had this same representation; no missing-image or team-link exception was
found in the complete table or reviewed capture.

Consequently, the row visually represents a team with a logo, but it provides
no source team text, abbreviation, or self-describing team link. A later
importer must preserve the source logo URL/asset ID and must not infer a team
name without a separately reviewed mapping. For the duplicate comparison below,
`logo/<id>` is the only row-level team distinction available from the source.

### Player

Every row has display text. In 533 of 539 rows the text is inside a relative
link with the exact shape
`/salaries/players/<lowercase-hyphenated-slug>/<numeric-id>/`. The trailing
numeric segment is a stable player ID derivable without name normalization.
All 533 linked rows matched that shape.

Six rows used a `<span>` instead of an `<a>`, leaving no URL or numeric ID:
`Tarik Biberovic`, `Terrell Brown Jr`, `Alpha Diallo`, `B. Markovic`, `Chris
Manon`, and `Tre Donaldson`. Those rows still retain player display text and the
team-logo asset, but identity cannot be established from a source ID. The
capture includes Tarik Biberovic as the reviewed no-link behavior.

Display text alone is not unique. For example, `J. Champagnie` occurs twice,
but the links resolve to IDs `1176070` (Julian) and `1175260` (Justin). Those are
different identities, not duplicate rows. Both are preserved in the capture.

## Repeated identities and presentation rows

The complete 539-row table was grouped by the trailing numeric player ID. Four
IDs occurred twice, for eight repeated-identity rows total. For rows without an
ID, identity was not inferred from text. Exact presentation comparison used
player text, player link/ID, logo URL, and all four salary-cell texts while
excluding the rank/tie label. It found **zero** repeated presentation-row
groups.

| Page / rank | Season | Player text and source identity | Team representation | Salary text | Source row/contract description | Evidence-limited classification |
| --- | --- | --- | --- | --- | --- | --- |
| 5 / `83` | `2026-27` | Damian Lillard, ID `463121` | `logo/15` | `$22,516,603` | No separate description or marker | Distinct source row |
| 8 / `142` | `2026-27` | Damian Lillard, ID `463121` | `logo/22` | `$13,398,800` | No target-season marker; `P` appears only on 2027-28 | Distinct source row; purpose cannot be classified from this table |
| 6 / `105` | `2026-27` | K. Caldwell-Pope, ID `602730` | `logo/29` | `$17,744,971` | No separate description or marker | Distinct source row |
| 15 / `T299` | `2026-27` | K. Caldwell-Pope, ID `602730` | `logo/20` | `$3,876,529` | No separate description or marker | Distinct source row; purpose cannot be classified from this table |
| 20 / `T396` | `2026-27` | O. Prosper, ID `1232483` | `logo/29` | `$2,497,812` | No separate description or marker | Distinct source row |
| 23 / `459` | `2026-27` | O. Prosper, ID `1232483` | `logo/6` | `$1,002,360` | No separate description or marker | Distinct source row; purpose cannot be classified from this table |
| 23 / `458` | `2026-27` | Kam Jones, ID `1324173` | `logo/4` | `$1,075,459` | No separate description or marker | Distinct source row |
| 25 / `T462` | `2026-27` | Kam Jones, ID `1324173` | `logo/15` | `TW$678,882` | `TW` superscript means Two-Way Contract in the page key | Distinct source row explicitly marked two-way |

The source evidence establishes repeated player identities with different logo
assets and salary texts. Except for Kam Jones's `TW` marker, it does not state
whether the pairs are dead money, waived salary, split payments, or another
contract category. Later deduplication or aggregation must therefore preserve
season, display text, link/ID, full logo URL or asset ID, exact salary text,
rank/tie label, every option/two-way marker, all displayed season values, and
source order. It must not silently collapse rows merely because the player ID
matches. Applicability and aggregation remain issue #22.

## Acquisition methods evaluated in required order

| Order and method | Attempted status | Completeness evidence | Material failure or operational cost | Fallback viability |
| --- | --- | --- | --- | --- |
| 1. Standard HTTP + HTML parsing | Attempted; final `200` after canonical redirect | 20 real target rows versus browser's 539; headers complete but body incomplete | Two 21-row skeleton bodies must be excluded; 519 rows unavailable; about four seconds | Useful only as a low-cost health/first-page check, not a complete acquisition method |
| 2. `pandas.read_html` | Attempted; import failed before a request | No table result | `ModuleNotFoundError: No module named 'pandas'`; pandas is not a declared dependency and dependency changes were forbidden | Not currently reproducible; no evidence it would solve client pagination |
| 3. Browser rendering | Attempted successfully | All 27 pages, 539 rows, stable headers, final button disabled | Approximately 49 seconds; 26 next actions; icon-only controls and generated CSS raise maintenance cost | **Primary** because completeness outweighs runtime/maintenance cost |
| 4. Commissioner-provided CSV | Not attempted; no file supplied | Unknown for this capture | Requires commissioner export/review and the schema work reserved for #23 | **Next viable fallback** when rendering is unavailable or changes |

Exactly one primary method is selected: **browser rendering**. Standard HTTP is
more maintainable and reproducible but materially incomplete; pandas is not
available; and commissioner CSV was not available to validate. Browser rendering
is slower and more structure-sensitive, yet it alone met the completeness
requirement in observed runtime evidence.

## Reproduction

Run commands from the repository root. They use the existing `uv` environment
and Python standard library only; no dependency changes are required.

### 1. Standard HTTP + HTML inspection

```bash
uv run python - <<'PY'
from html import unescape
from urllib.error import HTTPError
from urllib.request import Request, build_opener
import re

for url in (
    "https://hoopshype.com/salaries/players/",
    "https://hoopshype.com/salaries/players/2025-2026/",
):
    request = Request(
        url,
        headers={
            "User-Agent": "nba-commish-feasibility/1.0 (+public-source-review)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        response = build_opener().open(request, timeout=30)
    except HTTPError as error:
        response = error
    body = response.read().decode("utf-8", errors="replace")
    tables = re.findall(r"<table\b.*?</table>", body, re.I | re.S)
    print({"request": url, "status": response.status,
           "final_url": response.geturl(), "table_count": len(tables)})
    for index, table in enumerate(tables, 1):
        headers = [
            re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", value))).strip()
            for value in re.findall(r"<th\b[^>]*>(.*?)</th>", table, re.I | re.S)
        ]
        rows = re.findall(r"<tbody\b.*?<tr\b.*?</tr>.*?</tbody>", table, re.I | re.S)
        tr_count = len(re.findall(r"<tr\b", table, re.I)) - (1 if headers else 0)
        player_links = len(re.findall(r'href="/salaries/players/', table))
        print({"table": index, "headers": headers,
               "body_rows": tr_count, "player_links": player_links})
PY
```

At the investigation time, the live locator finished at `200` with two
21-row skeleton tables and a 20-row real table; the historical-looking locator
finished at `404` with no tables. Future live counts may change and should be
recorded with a new timestamp rather than overwriting this evidence.

### 2. `pandas.read_html`

```bash
uv run python -c "import pandas as pd; print(len(pd.read_html('https://www.hoopshype.com/salaries/players/')))"
```

This is intentionally the exact attempted check. In the current locked
environment it stops with `ModuleNotFoundError: No module named 'pandas'`.
Do not use `uv add` merely to rerun this spike; a dependency change needs
separate approval.

### 3. Rendered browser DOM

Use a fresh public browser context with no sign-in or site cookies:

1. Open `https://www.hoopshype.com/salaries/players/` and wait until the
   skeletons disappear.
2. Confirm the visible controls read `2026-27 season` and `All salaries`.
3. Inspect the single rendered `table`. Read headings from `table thead th`,
   rows from `table tbody tr`, the player from `td:nth-child(2) a` (or the
   fallback `span`), and the team graphic from `td:nth-child(2) img`.
4. Record the `N of 27` indicator and row count. Activate the right-hand arrow,
   wait for `N` to increment, then record the new rows. Repeat until the arrow
   is disabled. Do not count skeleton rows or assume a click succeeded without
   the indicator change.
5. Sum the page counts and group the trailing numeric player-link IDs. Compare
   exact presentation fields without collapsing any row.
6. On the final page, scroll to the bottom, wait at least 2.5 seconds, and
   confirm that neither the row count nor page count changes.

This procedure observed 20 rows on pages 1-26, 19 on page 27, and 539 total.

### 4. Commissioner CSV

No commissioner CSV was provided, so there is no reproducible file-based check
for this investigation. A future check must use the reviewed format produced by
issue #23, retain the original file unchanged with required metadata, and
compare its complete 2026-27 row count and identity fields against this report.

## Evidence and privacy review

The committed HTML is a minimized public-source capture: the rendered `<thead>`
and eight selected `<tr>` elements were serialized from the public DOM and
retained verbatim. Selection reduced the artifact size; the retained elements
were not normalized, deduplicated, aggregated, or converted. They cover an
ordinary linked player/logo row, repeated stable-ID rows, an explicit two-way
row, a same-display-text/different-ID pair, and a no-link/no-ID row.

The HTML, both metadata sidecars, and this report were reviewed. They contain no
credentials, cookies, authorization values, session identifiers, sensitive
query values, private league data, commissioner data, or personal account
material. Public CDN image-rendering parameters are retained because they are
part of the public source elements and contain no session values.
