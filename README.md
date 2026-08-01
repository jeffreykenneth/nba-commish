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
