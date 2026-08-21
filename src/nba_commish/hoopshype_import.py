"""Command-line entry point for raw Hoopshype salary collection."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from nba_commish.hoopshype import run_import
from nba_commish.hoopshype.errors import HoopshypeImportError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import physical rows from the public Hoopshype salary table."
    )
    parser.add_argument(
        "--season", required=True, help="Target salary season (yyyy-yy)."
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="New JSON artifact path."
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        artifact = run_import(args.season, args.output)
    except HoopshypeImportError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    collection = artifact["collection"]
    assert isinstance(collection, dict)
    print(
        f"Imported {collection['total_physical_row_count']} physical rows across "
        f"{collection['observed_page_count']} pages to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
