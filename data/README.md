# Artifact storage conventions

This directory separates local source captures, generated intermediate data,
reviewed test inputs, and reports. The rules below apply to every artifact in
`data/` and `reports/`.

## Directory classes

| Directory | Classification | Commit policy |
| --- | --- | --- |
| `data/private/` | Local-only working area for unredacted or not-yet-reviewed material from any source. | Never commit; the entire directory is ignored. |
| `data/raw/yahoo/` | Local-only authenticated Yahoo source data. | Never commit payloads; only its `README.md` is tracked. |
| `data/raw/hoopshype/` | Source captures from Hoopshype. | Commit only when a task explicitly requires a reviewed capture and it contains no private or secret data. |
| `data/normalized/` | Generated intermediate data derived from source captures. | Commit only when a task explicitly requires a reviewed output. It is not a test-fixture directory. |
| `data/fixtures/` | Redacted, minimized source examples and synthetic test inputs. | This is the only location for committed redacted or synthetic test inputs. |
| `reports/` | Redacted findings, decision records, and human-review outputs. | Commit reviewed outputs that contain no private or secret data. |

Keep unredacted and not-yet-reviewed captures in `data/private/` or, for
authenticated Yahoo responses, `data/raw/yahoo/`. To create a test input, copy
only the minimum fields needed by the test into `data/fixtures/`, redact and
review it there, and then commit the fixture. A file in a raw or normalized
directory must not be used as a shortcut for a committed test fixture.

## Filenames

Use this lowercase pattern:

```text
<artifact-kind>--<scope>--<capture-or-generation-utc>[-v<collision>].<extension>
```

- Write `artifact-kind` and each scope segment in lowercase ASCII kebab-case.
- Include every relevant scope segment, such as `season-2025-26` and a
  non-sensitive league alias such as `league-fixture-01`. Join multiple scope
  segments with `--`. Never put a real private league or team identifier in a
  committed filename.
- Use `yyyymmddthhmmssz` for the filename timestamp. It is UTC, is always
  second-precision, and sorts chronologically. For example,
  `20260805t143000z` represents 2026-08-05 at 14:30:00 UTC.
- Do not silently overwrite an artifact. A rerun for the same kind, scope, and
  timestamp may reuse the name only when the bytes are identical. Otherwise,
  append the next two-digit collision suffix (`-v02`, then `-v03`, and so on)
  before the extension. This suffix records a filename collision; it does not
  replace the artifact or schema version in metadata.

Examples:

```text
data/raw/hoopshype/nba-salary-source--season-2025-26--20260805t143000z.html
data/normalized/player-salaries--season-2025-26--20260805t143500z.csv
data/fixtures/yahoo-league-settings--season-2025-26--league-fixture-01--20260805t144000z.json
reports/player-match-review--season-2025-26--20260805t150000z.csv
```

The filename time is when the source was retrieved or the derived artifact was
generated, not necessarily when the source event happened. Record source-event
time separately in metadata. For example, a trade completed on July 1 and
retrieved on August 5 uses the August 5 retrieval time in its filename and
`retrieved_at`, while July 1 belongs in `source_event_time`.

## Required metadata

Every artifact must carry these fields:

| Field | Meaning |
| --- | --- |
| `source_system` | Originating system, for example `yahoo_fantasy`, `hoopshype`, or `synthetic`. |
| `source_locator` | Sanitized endpoint, page, query description, or input artifact locator. |
| `retrieved_at` | Retrieval time for a source capture, or `null` for an artifact that was not retrieved. |
| `generated_at` | Generation time for a derived artifact, or `null` for an artifact that was not generated. |
| `source_event_time` | Time represented by the source event, or `null` when it does not apply or is unavailable. |
| `season` | Relevant season in `yyyy-yy` form, or `null` when it does not apply or is unavailable. |
| `league_scope` | A non-sensitive league alias, or `null` when it does not apply or is unavailable. |
| `artifact_version` | Version of the artifact definition or producing workflow. |
| `schema_version` | Version of the data shape. |
| `redaction_status` | One of `unredacted-local`, `redacted-reviewed`, `synthetic`, or `public-source-reviewed`. |

Use ISO 8601 UTC (`YYYY-MM-DDThh:mm:ssZ`) for every non-null metadata
timestamp, for example `2026-08-05T14:30:00Z`. Exactly one of `retrieved_at` or
`generated_at` is normally non-null: source captures use `retrieved_at`, while
normalized data, fixtures assembled from other data, and reports use
`generated_at`. A synthetic fixture uses `generated_at`. Preserve a distinct
`source_event_time` whenever an upstream record supplies one.

Do not infer missing metadata. Represent an unavailable or inapplicable value
as JSON `null` and explain important limitations in the artifact's report when
necessary. Before recording `source_locator`, remove credentials, URL user
information, secrets, tokens, session values, and sensitive query parameters.
Prefer a sanitized endpoint path or a plain-language query description when a
safe URL cannot be retained.

JSON artifacts embed data in this envelope:

```json
{
  "metadata": {
    "source_system": "synthetic",
    "source_locator": null,
    "retrieved_at": null,
    "generated_at": "2026-08-05T14:40:00Z",
    "source_event_time": null,
    "season": "2025-26",
    "league_scope": "league-fixture-01",
    "artifact_version": "1.0",
    "schema_version": "1.0",
    "redaction_status": "synthetic"
  },
  "data": []
}
```

For CSV, HTML, Markdown, and other formats that cannot use the JSON envelope,
place the same metadata object in a same-basename JSON sidecar. For example,
`player-salaries--season-2025-26--20260805t143500z.csv` uses
`player-salaries--season-2025-26--20260805t143500z.metadata.json`. Keep the
artifact and sidecar together, and apply the same collision suffix to both.

## Redaction and privacy

Committed artifacts must not contain credentials, client secrets,
authorization codes, access tokens, refresh tokens, cookies, session
identifiers, passwords, or sensitive URL query values. This prohibition
applies to payloads, metadata, filenames, logs, and source locators.

Before committing an artifact derived from private league data, remove or
replace all private league and manager information. This includes personal
names, email addresses, avatar URLs, league identifiers, team identifiers, and
other account-linked identifiers. Use obvious format-compatible placeholders,
such as `manager_fixture_001`, `league_fixture_001`, or a reserved synthetic
integer when the schema requires a number.

Preserve relationships needed by tests: within one fixture set, every repeat
of a source identifier must map to the same placeholder, and different source
identifiers must map to different placeholders. Keep any mapping from real
values to placeholders only under an ignored local-only path; never commit the
mapping. Review both the artifact and its metadata sidecar before staging them.
