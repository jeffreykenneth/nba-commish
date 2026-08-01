# nba-commish

`nba-commish` uses [uv](https://docs.astral.sh/uv/getting-started/installation/) as its required package and environment manager.

Install uv, then run the quality checks locally:

```bash
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
```
