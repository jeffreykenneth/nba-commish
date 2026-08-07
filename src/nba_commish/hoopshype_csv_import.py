"""Command-line entry point for the strict commissioner salary CSV fallback."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from nba_commish.hoopshype.csv_fallback import run_csv_fallback_import
from nba_commish.hoopshype.errors import CsvFallbackError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import a commissioner salary CSV fallback without inference."
    )
    parser.add_argument("--input", required=True, type=Path, help="Input CSV path.")
    parser.add_argument(
        "--season", required=True, help="Selected salary season (yyyy-yy)."
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="New derived JSON artifact path."
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_csv_fallback_import(args.input, args.season, args.output)
    except CsvFallbackError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(
        f"Imported {result.physical_row_count} physical CSV rows; "
        f"{result.unique_row_count} unique rows; "
        f"{result.duplicate_occurrence_count} duplicate occurrences; "
        f"output {result.output_basename}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
