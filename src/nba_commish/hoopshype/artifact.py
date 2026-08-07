"""Import-artifact construction and atomic publication."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from nba_commish.hoopshype.errors import ArtifactWriteError
from nba_commish.hoopshype.pagination import CollectionResult
from nba_commish.hoopshype.parser import PUBLIC_SOURCE_URL

ARTIFACT_VERSION = "1.0"
SCHEMA_VERSION = "1.0"


def build_artifact(
    collection: CollectionResult,
    *,
    season: str,
    retrieved_at: str,
) -> dict[str, Any]:
    """Build the reviewed public-source JSON envelope."""

    page_counts = [
        {"page_number": page.page_number, "row_count": len(page.rows)}
        for page in collection.pages
    ]
    total = len(collection.rows)
    if total != sum(page["row_count"] for page in page_counts):
        raise ArtifactWriteError(
            "Collected page counts do not reconcile with physical source rows."
        )
    return {
        "metadata": {
            "source_system": "hoopshype",
            "source_locator": PUBLIC_SOURCE_URL,
            "retrieved_at": retrieved_at,
            "generated_at": None,
            "source_event_time": None,
            "season": season,
            "league_scope": None,
            "artifact_version": ARTIFACT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "redaction_status": "public-source-reviewed",
        },
        "collection": {
            "observed_page_count": len(collection.pages),
            "per_page_row_counts": page_counts,
            "total_physical_row_count": total,
        },
        "data": list(collection.rows),
    }


def write_artifact_atomic(path: Path, artifact: dict[str, Any]) -> Path:
    """Publish one complete JSON artifact without clobbering prior evidence."""

    payload = (json.dumps(artifact, ensure_ascii=False, indent=2) + "\n").encode()
    if path.exists():
        try:
            if path.read_bytes() == payload:
                return path
        except OSError as error:
            raise ArtifactWriteError(
                f"Could not inspect existing output artifact: {path}."
            ) from error
        raise ArtifactWriteError(
            f"Output artifact already exists and was not replaced: {path}."
        )

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
    except OSError as error:
        raise ArtifactWriteError(
            f"Could not prepare atomic output publication for: {path}."
        ) from error

    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        if path.exists():
            raise ArtifactWriteError(
                f"Output artifact appeared during publication and was not replaced: {path}."
            )
        os.replace(temporary_path, path)
    except ArtifactWriteError:
        raise
    except OSError as error:
        raise ArtifactWriteError(
            f"Could not publish the output artifact atomically: {path}."
        ) from error
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
    return path
