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
