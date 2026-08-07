from __future__ import annotations

import json
from os import PathLike, link
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from nba_commish.hoopshype.artifact import (
    build_artifact,
    write_artifact_atomic,
)
from nba_commish.hoopshype.errors import (
    ArtifactWriteError,
    BrowserCollectionError,
    SeasonValidationError,
    SourceStructureError,
)
from nba_commish.hoopshype.models import PageSnapshot
from nba_commish.hoopshype.pagination import collect_all_pages
from nba_commish.hoopshype.parser import parse_page, snapshot_from_html
from nba_commish.hoopshype.pipeline import run_import
from nba_commish.hoopshype.season import validate_season

FIXTURE = (
    Path(__file__).parents[1]
    / "data"
    / "fixtures"
    / "hoopshype-salary-table--season-2026-27--20260807t201407z.html"
)
IMPORTED_AT = "2026-08-07T20:00:00Z"
HEADINGS = ("", "Player", "2026-27", "2027-28", "2028-29", "2029-30")


def _row(
    name: str,
    *,
    player_id: int | None = 100,
    logo_id: int | None = 9,
    salary: str = "$1,000,000",
    marker: str = "",
    rank: str = "1",
    description: str | None = None,
    headings: tuple[str, ...] = HEADINGS,
) -> dict[str, Any]:
    player_url = (
        f"/salaries/players/source-player/{player_id}/"
        if player_id is not None
        else None
    )
    logo_url = (
        "https://www.gannett-cdn.com/content-pipeline-sports-images/"
        f"sports2/nba/logos/{logo_id}.png?format=png8&width=30"
        if logo_id is not None
        else ""
    )
    return {
        "rank_text": rank,
        "player_display_text": name,
        "player_url": player_url,
        "team_logo_url": logo_url,
        "source_row_description": description,
        "salary_cells": [
            {
                "heading": heading,
                "salary_text": salary if index == 0 else "-",
                "marker_text": marker if index == 0 else "",
            }
            for index, heading in enumerate(headings[2:])
        ],
    }


def _snapshot(
    page: int,
    final: int,
    *,
    rows: list[dict[str, Any]] | None = None,
    headings: tuple[str, ...] = HEADINGS,
    indicator: str | None = None,
    back_disabled: bool | None = None,
    forward_disabled: bool | None = None,
    content_token: str | None = None,
) -> PageSnapshot:
    values = rows if rows is not None else [_row(f"Player {page}", player_id=page)]
    return PageSnapshot.from_mapping(
        {
            "indicator_text": indicator or f"{page} of {final}",
            "back_disabled": page == 1 if back_disabled is None else back_disabled,
            "forward_disabled": page == final
            if forward_disabled is None
            else forward_disabled,
            "headings": headings,
            "rows": values,
            "content_token": content_token or f"content-{page}",
        }
    )


class FakeSession:
    def __init__(self, states: list[PageSnapshot]) -> None:
        self.states = states
        self.index = 0
        self.opened_with: str | None = None
        self.closed = False

    def open(self, season: str) -> None:
        self.opened_with = season

    def current_snapshot(self) -> PageSnapshot:
        return self.states[self.index]

    def advance(self, previous: PageSnapshot) -> PageSnapshot:
        self.index += 1
        return self.states[self.index]

    def close(self) -> None:
        self.closed = True


class TimeoutSession(FakeSession):
    def advance(self, previous: PageSnapshot) -> PageSnapshot:
        raise TimeoutError


def _collect(*states: PageSnapshot):
    return collect_all_pages(
        FakeSession(list(states)), season="2026-27", imported_at=IMPORTED_AT
    )


def test_reviewed_fixture_preserves_all_eight_physical_rows() -> None:
    snapshot = snapshot_from_html(FIXTURE.read_text())
    page = parse_page(snapshot, requested_season="2026-27", imported_at=IMPORTED_AT)

    assert len(page.rows) == 8
    assert [row["player_display_text"] for row in page.rows].count(
        "Damian Lillard"
    ) == 2
    lillard = [
        row for row in page.rows if row["player_display_text"] == "Damian Lillard"
    ]
    assert [row["player_id"] for row in lillard] == [463121, 463121]

    champagnies = [
        row for row in page.rows if row["player_display_text"] == "J. Champagnie"
    ]
    assert [row["player_id"] for row in champagnies] == [1176070, 1175260]

    tarik = next(
        row for row in page.rows if row["player_display_text"] == "Tarik Biberovic"
    )
    assert tarik["player_url"] is None
    assert tarik["player_id"] is None

    kam_jones = [row for row in page.rows if row["player_display_text"] == "Kam Jones"]
    assert len(kam_jones) == 2
    assert kam_jones[1]["target_season_marker_text"] == "TW"
    assert kam_jones[1]["target_season_salary_text"] == "$678,882"
    assert kam_jones[1]["source_row_description"] is None
    assert "source_row_fingerprint" not in kam_jones[1]


def test_reviewed_fixture_metadata_is_complete_and_reviewed() -> None:
    metadata_path = FIXTURE.with_suffix(".metadata.json")
    metadata = json.loads(metadata_path.read_text())
    assert set(metadata) == {
        "source_system",
        "source_locator",
        "retrieved_at",
        "generated_at",
        "source_event_time",
        "season",
        "league_scope",
        "artifact_version",
        "schema_version",
        "redaction_status",
    }
    assert metadata["source_system"] == "hoopshype"
    assert metadata["season"] == "2026-27"
    assert metadata["redaction_status"] == "public-source-reviewed"


def test_synthetic_multi_page_success_including_19_row_final_page() -> None:
    first_rows = [_row("First A", player_id=1), _row("First B", player_id=2)]
    final_rows = [
        _row(f"Final {index}", player_id=100 + index, rank=str(index))
        for index in range(1, 20)
    ]
    result = _collect(
        _snapshot(1, 2, rows=first_rows),
        _snapshot(2, 2, rows=final_rows),
    )

    assert [page.page_number for page in result.pages] == [1, 2]
    assert [len(page.rows) for page in result.pages] == [2, 19]
    assert len(result.rows) == 21
    assert result.rows[-1]["source_page_number"] == 2
    assert result.rows[-1]["source_row_position"] == 19


def test_no_link_player_is_valid_and_team_evidence_is_not_mapped() -> None:
    result = _collect(_snapshot(1, 1, rows=[_row("No Link", player_id=None)]))
    row = result.rows[0]
    assert row["player_display_text"] == "No Link"
    assert row["player_url"] is None
    assert row["player_id"] is None
    assert row["team_logo_asset_id"] == 9
    assert not any(key in row for key in ("team", "team_name", "team_abbreviation"))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "/content-pipeline-sports-images/sports2/nba/logos/9.png",
            (
                "https://www.hoopshype.com/content-pipeline-sports-images/"
                "sports2/nba/logos/9.png"
            ),
        ),
        (
            "//cdn.example.test/sports2/nba/logos/9.png?width=30",
            "https://cdn.example.test/sports2/nba/logos/9.png?width=30",
        ),
    ],
)
def test_relative_team_logo_urls_are_resolved_before_emission(
    source: str, expected: str
) -> None:
    row = _row("Relative Logo")
    row["team_logo_url"] = source
    parsed = _collect(_snapshot(1, 1, rows=[row])).rows[0]

    assert parsed["team_logo_url"] == expected
    parts = urlsplit(parsed["team_logo_url"])
    assert parts.scheme in {"http", "https"}
    assert parts.netloc
    assert parsed["team_logo_asset_id"] == 9


def test_saved_html_relative_team_logo_url_is_resolved() -> None:
    html = """
    <thead><tr><th></th><th>Player</th><th>2026-27</th></tr></thead>
    <tr><td>1</td><td><img src="/sports2/nba/logos/12.png"><span>Player</span></td>
    <td><sup>TW</sup>$1</td></tr>
    """
    page = parse_page(
        snapshot_from_html(html),
        requested_season="2026-27",
        imported_at=IMPORTED_AT,
    )
    assert page.rows[0]["team_logo_url"] == (
        "https://www.hoopshype.com/sports2/nba/logos/12.png"
    )
    assert page.rows[0]["team_logo_asset_id"] == 12


@pytest.mark.parametrize(
    "source",
    [
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        "ftp://cdn.example.test/sports2/nba/logos/9.png",
        "https:///sports2/nba/logos/9.png",
        "//",
        "https://user:secret@cdn.example.test/sports2/nba/logos/9.png",
        "https://cdn.example.test/sports2/nba/logos/logo with space.png",
        "\\\\cdn.example.test\\sports2\\nba\\logos\\9.png",
        "https://[invalid/sports2/nba/logos/9.png",
    ],
)
def test_unsafe_or_nonpublic_team_logo_urls_fail_actionably(source: str) -> None:
    row = _row("Invalid Logo")
    row["team_logo_url"] = source
    with pytest.raises(SourceStructureError, match="Invalid team logo URL.*row 1"):
        _collect(_snapshot(1, 1, rows=[row]))


def test_repeated_identities_names_values_logos_and_markers_remain_distinct() -> None:
    rows = [
        _row("Repeated", player_id=77, logo_id=1, salary="$5"),
        _row("Repeated", player_id=77, logo_id=1, salary="$5"),
        _row("Repeated", player_id=77, logo_id=2, salary="$5"),
        _row("Repeated", player_id=77, logo_id=2, salary="$5", marker="TW"),
    ]
    result = _collect(_snapshot(1, 1, rows=rows))
    assert len(result.rows) == 4
    assert [row["source_row_position"] for row in result.rows] == [1, 2, 3, 4]
    assert [row["team_logo_asset_id"] for row in result.rows] == [1, 1, 2, 2]
    assert [row["target_season_marker_text"] for row in result.rows] == [
        "",
        "",
        "",
        "TW",
    ]


@pytest.mark.parametrize(
    "value", ["2026", "26-27", "2026/27", "2026-2027", " 2026-27", "2026-27 ", ""]
)
def test_malformed_seasons_are_rejected(value: str) -> None:
    with pytest.raises(SeasonValidationError, match="consecutive years"):
        validate_season(value)


@pytest.mark.parametrize("value", ["2026-28", "2026-26", "2099-01"])
def test_nonconsecutive_seasons_are_rejected(value: str) -> None:
    with pytest.raises(SeasonValidationError, match="consecutive years"):
        validate_season(value)


def test_consecutive_century_boundary_is_valid() -> None:
    assert validate_season("2099-00") == "2099-00"


def test_invalid_season_does_not_launch_browser_or_touch_output(tmp_path: Path) -> None:
    output = tmp_path / "existing.json"
    output.write_text("valid prior artifact")
    launched = False

    def session_factory() -> FakeSession:
        nonlocal launched
        launched = True
        return FakeSession([])

    with pytest.raises(SeasonValidationError):
        run_import("2026-28", output, session_factory=session_factory)
    assert launched is False
    assert output.read_text() == "valid prior artifact"


def test_missing_player_display_text_fails_actionably() -> None:
    with pytest.raises(
        SourceStructureError, match="Missing player display text.*row 1"
    ):
        _collect(_snapshot(1, 1, rows=[_row("")]))


def test_missing_team_logo_fails_actionably() -> None:
    with pytest.raises(SourceStructureError, match="Missing team logo.*row 1"):
        _collect(_snapshot(1, 1, rows=[_row("Player", logo_id=None)]))


def test_missing_requested_season_column_fails_actionably() -> None:
    headings = ("", "Player", "2027-28")
    with pytest.raises(SourceStructureError, match="Requested-season column.*missing"):
        _collect(
            _snapshot(1, 1, headings=headings, rows=[_row("Player", headings=headings)])
        )


def test_repeated_requested_season_heading_fails_actionably() -> None:
    headings = ("", "Player", "2026-27", "2026-27")
    with pytest.raises(SourceStructureError, match="appears more than once"):
        _collect(
            _snapshot(1, 1, headings=headings, rows=[_row("Player", headings=headings)])
        )


def test_unexpected_header_change_between_pages_fails() -> None:
    changed = ("", "Player", "2026-27", "2028-29")
    with pytest.raises(SourceStructureError, match="Unexpected header change.*page 2"):
        _collect(
            _snapshot(1, 2),
            _snapshot(2, 2, headings=changed, rows=[_row("Player", headings=changed)]),
        )


@pytest.mark.parametrize("indicator", ["1/2", "page 1 of 2", "0 of 2", "3 of 2", ""])
def test_malformed_paginator_state_fails(indicator: str) -> None:
    snapshot = _snapshot(1, 2, indicator=indicator or "invalid")
    with pytest.raises(SourceStructureError, match="Malformed paginator state"):
        _collect(snapshot)


def test_repeated_indicator_is_rejected() -> None:
    with pytest.raises(SourceStructureError, match="indicator did not change"):
        _collect(_snapshot(1, 2), _snapshot(2, 2, indicator="1 of 2"))


def test_advanced_indicator_with_unchanged_rows_is_rejected() -> None:
    with pytest.raises(SourceStructureError, match="row content did not change"):
        _collect(
            _snapshot(1, 2, content_token="same"),
            _snapshot(2, 2, content_token="same"),
        )


def test_skipped_page_is_rejected() -> None:
    with pytest.raises(SourceStructureError, match="expected page 2, observed page 3"):
        _collect(_snapshot(1, 3), _snapshot(3, 3))


def test_zero_row_intermediate_page_fails() -> None:
    with pytest.raises(SourceStructureError, match="Zero-row intermediate page"):
        _collect(_snapshot(1, 2, rows=[]))


def test_timeout_is_sanitized() -> None:
    session = TimeoutSession([_snapshot(1, 2)])
    with pytest.raises(BrowserCollectionError, match="Timed out waiting") as failure:
        collect_all_pages(session, season="2026-27", imported_at=IMPORTED_AT)
    assert "cookie" not in str(failure.value).lower()


@pytest.mark.parametrize(
    ("snapshot", "message"),
    [
        (_snapshot(1, 1, back_disabled=False), "back control is enabled"),
        (_snapshot(1, 2, forward_disabled=True), "disabled prematurely"),
        (_snapshot(1, 1, forward_disabled=False), "forward control remains enabled"),
    ],
)
def test_premature_paginator_states_fail(snapshot: PageSnapshot, message: str) -> None:
    with pytest.raises(SourceStructureError, match=message):
        _collect(snapshot)


def test_back_control_disabled_after_first_page_fails() -> None:
    with pytest.raises(
        SourceStructureError, match="back control is disabled prematurely"
    ):
        _collect(_snapshot(1, 2), _snapshot(2, 2, back_disabled=True))


def test_artifact_embeds_required_metadata_counts_and_all_salary_cells() -> None:
    result = _collect(_snapshot(1, 1, rows=[_row("Player", marker="P")]))
    artifact = build_artifact(result, season="2026-27", retrieved_at=IMPORTED_AT)
    assert artifact["metadata"] == {
        "source_system": "hoopshype",
        "source_locator": "https://www.hoopshype.com/salaries/players/",
        "retrieved_at": IMPORTED_AT,
        "generated_at": None,
        "source_event_time": None,
        "season": "2026-27",
        "league_scope": None,
        "artifact_version": "1.0",
        "schema_version": "1.0",
        "redaction_status": "public-source-reviewed",
    }
    assert artifact["collection"] == {
        "observed_page_count": 1,
        "per_page_row_counts": [{"page_number": 1, "row_count": 1}],
        "total_physical_row_count": 1,
    }
    row = artifact["data"][0]
    assert row["target_season_salary_text"] == "$1,000,000"
    assert row["target_season_marker_text"] == "P"
    assert [cell["salary_text"] for cell in row["salary_season_cells"]] == [
        "$1,000,000",
        "-",
        "-",
        "-",
    ]


def test_successful_pipeline_publishes_json_and_closes_session(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "artifact.json"
    session = FakeSession([_snapshot(1, 1)])
    artifact = run_import(
        "2026-27",
        output,
        session_factory=lambda: session,
        timestamp_factory=lambda: IMPORTED_AT,
    )
    assert session.opened_with == "2026-27"
    assert session.closed is True
    assert json.loads(output.read_text()) == artifact


def test_incomplete_collection_leaves_no_new_final_artifact(tmp_path: Path) -> None:
    output = tmp_path / "artifact.json"
    with pytest.raises(SourceStructureError):
        run_import(
            "2026-27",
            output,
            session_factory=lambda: FakeSession([_snapshot(1, 2, rows=[])]),
            timestamp_factory=lambda: IMPORTED_AT,
        )
    assert not output.exists()


def test_incomplete_collection_preserves_existing_artifact(tmp_path: Path) -> None:
    output = tmp_path / "artifact.json"
    output.write_text("prior valid artifact")
    with pytest.raises(SourceStructureError):
        run_import(
            "2026-27",
            output,
            session_factory=lambda: FakeSession([_snapshot(1, 2, rows=[])]),
            timestamp_factory=lambda: IMPORTED_AT,
        )
    assert output.read_text() == "prior valid artifact"


def test_exclusive_writer_failure_leaves_no_final_or_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "artifact.json"

    def fail_link(source: PathLike[str], destination: PathLike[str]) -> None:
        raise OSError("injected")

    monkeypatch.setattr("nba_commish.hoopshype.artifact.os.link", fail_link)
    with pytest.raises(ArtifactWriteError, match="atomically"):
        write_artifact_atomic(output, {"metadata": {}, "data": []})
    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


def test_existing_artifact_is_never_replaced(tmp_path: Path) -> None:
    output = tmp_path / "artifact.json"
    output.write_text("prior valid artifact")
    with pytest.raises(ArtifactWriteError, match="already exists"):
        write_artifact_atomic(output, {"metadata": {}, "data": []})
    assert output.read_text() == "prior valid artifact"


def test_destination_created_at_publication_boundary_is_never_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "artifact.json"
    real_link = link

    def racing_link(source: PathLike[str], destination: PathLike[str]) -> None:
        Path(destination).write_text("prior valid artifact")
        real_link(source, destination)

    monkeypatch.setattr("nba_commish.hoopshype.artifact.os.link", racing_link)
    with pytest.raises(ArtifactWriteError, match="appeared.*not replaced"):
        write_artifact_atomic(output, {"metadata": {}, "data": []})

    assert output.read_text() == "prior valid artifact"
    assert list(tmp_path.iterdir()) == [output]
