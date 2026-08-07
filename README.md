# nba-commish

`nba-commish` uses [uv](https://docs.astral.sh/uv/getting-started/installation/) as its required package and environment manager.

Install uv, then run the quality checks locally:

```bash
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Pre-commit hooks are optional. To install the hooks for local commits, run:

```bash
uv run pre-commit install
```

Run all configured hooks manually with:

```bash
uv run pre-commit run --all-files
```

Remove the hooks with:

```bash
uv run pre-commit uninstall
```

Hook installation is opt-in; contributors may continue to sync the project and
run the manual quality-check commands above without installing the hooks. The
GitHub Actions checks from [#37](https://github.com/jeffreykenneth/nba-commish/issues/37)
remain authoritative.

## Hoopshype salary import

The Hoopshype importer uses a fresh, headless Chromium context to read the
public, unauthenticated players-salary table. Install the uv-managed Python
environment and its matching Chromium binary before the first import:

```bash
uv sync --locked
uv run playwright install chromium
```

Run one explicitly selected salary season and write the unreviewed live output
under the ignored private working directory:

```bash
uv run python -m nba_commish.hoopshype_import --season 2026-27 --output data/private/hoopshype/player-salaries--season-2026-27--20260807t200000z.json
```

The importer verifies the displayed season and `All salaries` controls, visits
every rendered paginator page, preserves every physical row and displayed
salary-season cell in source order, reconciles its page and row totals, and
then publishes one JSON artifact atomically. The output path must be new; an
existing artifact is accepted only when its bytes are already identical and is
otherwise never replaced. A malformed season is rejected before Chromium is
launched. Any navigation, timeout, table-structure, pagination, reconciliation,
or write failure exits non-zero without leaving a new final artifact or
replacing an existing one.

Only the public Hoopshype page is used. The command does not reuse a signed-in
profile, persist cookies or browser storage, bypass access controls, or print
response bodies or session material. Live salary data changes independently of
this repository, so page counts and row counts must be read from each completed
artifact and must never be hard-coded from an earlier capture.

### Salary-text normalization

Raw imports keep `target_season_salary_text` and
`target_season_marker_text` exactly as displayed. Normalization is a separate
in-memory step that copies each row and adds
`target_season_salary_dollars`, whose value is a whole-dollar Python `int` or
`None`. It never rewrites the source text or marker and never assigns contract
meaning to markers such as `P`, `T`, `Q`, or `TW`.

Accepted amounts use an optional leading ASCII dollar sign followed by either
ungrouped ASCII digits (`12500000`) or correctly comma-grouped ASCII digits
(`$12,500,000`). Surrounding Unicode whitespace is ignored, but whitespace
inside the token is invalid. Conversion uses base-10 integers only—salary
amounts are never represented as floating point, so values larger than
JavaScript's exact-integer range remain exact.

After surrounding whitespace is removed, only an empty string, `-`, en dash
`–`, em dash `—`, or ASCII-case-insensitive `N/A` means an explicit no-salary
value and normalizes to `None`. That null result is different from `$0`, which
normalizes to integer zero. Any other syntax, including a marker combined with
an amount such as `TW$678,882`, is malformed and raises a contextual salary
parse error instead of becoming null, zero, or a best-effort number.

### Salary-row fingerprinting and deduplication

Fingerprint version 1 is a persisted compatibility contract. Its public form
is `hoopshype-row-v1:sha256:<64 lowercase hex characters>`. SHA-256 receives
the domain prefix `nba-commish/hoopshype-row-fingerprint/v1\0` followed by a
canonical binary payload in which every value has a type tag and an unsigned
eight-byte length prefix. Integers use exact unsigned big-endian bytes, not
decimal text or floating point. Field names and sequence structure are encoded,
so the payload does not depend on delimiters, mapping insertion order, platform
serialization, locale, page order, or Python's randomized object hash.

The version-1 payload contains exactly:

- `target_season`;
- one player discriminator, using `player_id`, otherwise nonempty
  `player_url`, otherwise `player_display_text`;
- one team discriminator, using `team_logo_asset_id`, otherwise the required
  full `team_logo_url`;
- nullable `target_season_salary_dollars`;
- the canonical target marker;
- the canonical nullable source-row description; and
- every retained salary-season cell, sorted by heading and encoded as its
  heading, normalized nullable dollars, and canonical marker.

Stable numeric player and logo-asset IDs take precedence over presentation
fields. URL fallbacks discard query and fragment and lowercase only scheme and
host; paths remain exact. Display-name fallback uses Unicode NFC, collapses
surrounding/internal Unicode whitespace, and case-folds. Descriptions use NFC
and whitespace collapse but preserve case, punctuation, and diacritics;
`None`, empty, and whitespace-only descriptions share one typed null. Markers
use NFC and surrounding-whitespace removal only. Their case, punctuation,
diacritics, internal whitespace, and unknown content remain evidence rather
than receiving contract meaning.

Salary strings are canonicalized exclusively by the salary parser above, so
currency punctuation is cosmetic, all explicit null sentinels share one typed
null, and zero remains distinct from null. Retained headings must be unique,
consecutive ASCII `yyyy-yy` values. Retained display order is cosmetic, but any
heading, normalized amount, or marker difference changes the fingerprint.
Rank, page/row position, locator, import time, stable-ID player presentation,
and stable-asset logo presentation are intentionally excluded. No other
punctuation, diacritic, path, identity, description, marker, team, contract, or
salary evidence is discarded or inferred.

Ordered deduplication retains the first-seen row for each canonical group and
adds only `source_row_fingerprint` to its deep copy. It also returns one
ordered occurrence for every physical input row with the fingerprint,
representative input position, locator, import time, page, row, and rank. Thus
repeat imports do not increase the unique salary-row count while every source
presentation remains reviewable. Equal digests are compared by canonical
payload; a digest collision between different payloads fails the entire call.

Canonical equality cannot prove that two indistinguishable source rows
represent the same real-world contract. Collapsed presentations must therefore
remain auditable through their occurrence trail; applicability and contract
interpretation belong to later review work.

### Salary-row aggregation and review policy

`aggregate_salary_rows` is a pure in-memory boundary over an issue-#21
`DeduplicationResult`, one selected consecutive ASCII `yyyy-yy` season, and an
optional commissioner-decision mapping. It accepts neither raw/pre-deduplication
rows nor a bare list. Before producing any result it validates every unique
row, recomputes its fingerprint from all remaining canonical fields, requires
exactly one retained selected-season cell, and reconciles every occurrence and
representative relationship. It does no source collection, CSV import, team
mapping, Yahoo matching, fuzzy matching, or caller mutation.

Rows are grouped by the persisted fingerprint-v1 player discriminator:
numeric `player_id`, otherwise the canonical nonempty `player_url`, otherwise
the NFC-normalized, whitespace-collapsed, case-folded `player_display_text`.
A linked or identified row is therefore distinct from a linkless display-name
fallback even when their displayed names match. Team/logo evidence never joins
players. Groups keep first appearance order, dispositions keep first-seen
unique-row order, and each disposition retains a deep copy of its raw row plus
all issue-#21 occurrences in original order.

Every unique row receives exactly one of `included`, `excluded`, or
`review_required`. The Phase 0 default policy is deliberately conservative:

| Condition, in precedence order | Status | Stable reason code | Effect |
| --- | --- | --- | --- |
| Target salary is null | `excluded` | `no_salary_amount` | Retain row and occurrences; contribute nothing. |
| Non-null row has a nonempty target marker or non-null canonical description | `review_required` | `source_evidence_requires_review` | Preserve evidence; infer no applicability. |
| Source-player group has multiple distinct non-null rows | `review_required` | `multiple_non_null_rows` | Review every otherwise ordinary non-null row. |
| Group has one non-null row with empty marker and null description | `included` | `single_ordinary_non_null_row` | Include once under the visible, overrideable default. |

Integer zero is a real non-null amount and follows the ordinary policy. A
group containing one ordinary non-null row and any number of null rows includes
the non-null row and excludes the null rows. Descriptions such as `waived`,
`dead money`, or `partial season` remain unclassified source text and require
review; no substring or regular-expression rule assigns contract purpose.
Exact target marker `TW` is labeled only as Hoopshype evidence for a two-way
contract. It does not automatically include or exclude that salary, and every
other nonempty or unknown marker also requires review.

Commissioner decisions are keyed by an existing fingerprint and contain
exactly a final `status` of `included` or `excluded` plus a nonempty
human-readable `reason`:

```python
decisions = {
    "hoopshype-row-v1:sha256:<64 lowercase hex>": {
        "status": "included",
        "reason": "Reviewed against the source evidence.",
    }
}
```

A decision can override any non-null default disposition, or restate a null
row's exclusion; a null row can never be included. Unknown fingerprints,
duplicate decisions, unsupported statuses, and missing or empty reasons reject
the complete call. Source marker, description, amount, identity, fingerprint,
raw row, and occurrence provenance are never rewritten. Commissioner results
use stable reason codes `commissioner_included` or `commissioner_excluded`,
retain the supplied human reason, and record `decision_source` as
`commissioner`; all other results record `default`.

Each player result exposes all row dispositions and included/excluded/review
subsets, an exact `included_subtotal_dollars`, unresolved-review count,
nullable `total_salary_dollars`, and completion status. The subtotal always
sums included non-null Python integers exactly, without fixed-width or
floating-point conversion. It becomes the final total only when no row remains
`review_required`; otherwise `total_salary_dollars` is `None` and completion is
`review_required`, so a partial subtotal cannot be mistaken for a final salary.

## Yahoo configuration

Yahoo credentials are loaded from the process environment through the typed
`YahooSettings` loader. Use [`.env.example`](.env.example) as a reference and
provide all three required values in the environment of the process that runs
the application. The redirect URI must use `http` with a loopback host such as
`localhost` or `127.0.0.1`, for example:

```bash
export YAHOO_CLIENT_ID="your-client-id"
export YAHOO_CLIENT_SECRET="your-client-secret"
export YAHOO_REDIRECT_URI="http://localhost:8000/auth/yahoo/callback"
```

Register that exact redirect URI in the Yahoo application's OAuth settings.
The scheme, host, port, and path must match `YAHOO_REDIRECT_URI` exactly.

Start initial authorization with:

```bash
uv run python -m nba_commish.yahoo_auth
```

The command listens on the configured loopback host, port, and path, prints the
Yahoo consent URL, and attempts to open it in the default browser. Complete the
consent flow within five minutes. On success, the command atomically saves the
token to the local-only path `data/private/yahoo/oauth-token.json`; on failure
or timeout it exits non-zero without replacing an existing token.

To reauthorize safely, stop any process using the current token, move or delete
`data/private/yahoo/oauth-token.json`, and run the command again. If
authorization fails after moving the token, restore the old file if it is still
needed. Do not edit token JSON by hand.

Application code can load and validate the settings at its boundary:

```python
from nba_commish.config import YahooSettings

settings = YahooSettings.from_env()
```

The application does not read `.env`, `oauth2.json`, or any other credential
file. In particular, `oauth2.json` files created by other Yahoo OAuth tools are
not used. Do not commit a populated `.env` file, an OAuth token file, cookies,
or raw authenticated Yahoo payloads; these local artifacts are ignored by Git.
The initial-authorization command does not refresh expired tokens.

## Dependabot updates

Dependabot monitors the project’s uv dependencies (`pyproject.toml` and
`uv.lock`) and the full-SHA-pinned GitHub Actions used under
`.github/workflows/`. Both ecosystems target `main` and run on the same weekly
schedule: **Monday at 09:00 UTC** (`timezone: Etc/UTC`). Each ecosystem may
have at most **5 open pull requests** at a time.

Dependabot does not auto-approve or auto-merge these pull requests. Maintainers
must review and merge them manually. Python dependency updates keep the
declaration and `uv.lock` in sync; action updates preserve full 40-character
commit-SHA pins and their adjacent release-version comments. The existing
“Quality checks” workflow remains required for these pull requests.

### Request an immediate update check

1. Open the repository on GitHub and select **Insights**.
2. Select **Dependency graph**, then **Dependabot**.
3. In the row for the `uv` or `github-actions` manifest, select **Check for
   updates**. GitHub starts a Dependabot version-update job for that ecosystem;
   it may create a pull request only when an eligible update exists and the
   five-PR limit has not been reached.

### Diagnose a missing, conflicted, or failing update

- On **Insights → Dependency graph → Dependabot**, select **Last checked** (or
  **Recent update jobs**) for the affected ecosystem, then select **View
  logs**. The logs show whether the manifest was found, dependency resolution
  failed, or an update was skipped.
- If no pull request appears, check that the update is eligible, that the
  ecosystem has fewer than five open Dependabot pull requests, and that the
  scheduled job or manual check completed without an error.
- If a pull request is conflicted or its **Quality checks** status fails, open
  the pull request’s **Conversation** and **Checks** tabs, inspect the failing
  logs, and resolve the conflict or dependency/lockfile problem before
  manually reviewing and merging it. Dependabot pull requests remain open
  until a maintainer resolves and merges them.
