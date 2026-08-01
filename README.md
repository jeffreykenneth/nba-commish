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
