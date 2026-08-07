"""End-to-end Hoopshype import pipeline with injectable boundaries."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from nba_commish.hoopshype.artifact import build_artifact, write_artifact_atomic
from nba_commish.hoopshype.browser import PlaywrightPageSession
from nba_commish.hoopshype.pagination import (
    RenderedPageSession,
    collect_all_pages,
)
from nba_commish.hoopshype.season import validate_season


def utc_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_import(
    season: str,
    output: Path,
    *,
    session_factory: Callable[[], RenderedPageSession] = PlaywrightPageSession,
    timestamp_factory: Callable[[], str] = utc_timestamp,
) -> dict[str, object]:
    """Collect, validate, reconcile, and atomically publish one salary season."""

    validated_season = validate_season(season)
    retrieved_at = timestamp_factory()
    session = session_factory()
    try:
        collection = collect_all_pages(
            session,
            season=validated_season,
            imported_at=retrieved_at,
        )
    finally:
        close = getattr(session, "close", None)
        if close is not None:
            close()

    artifact = build_artifact(
        collection,
        season=validated_season,
        retrieved_at=retrieved_at,
    )
    write_artifact_atomic(output, artifact)
    return artifact
