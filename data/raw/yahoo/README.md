# Yahoo source captures

This directory is the local-only location for authenticated Yahoo source
payloads, including league, team, roster, standings, transaction, and draft
responses. Payloads here can contain private league data and must never be
committed. Git ignores every entry except this `README.md`.

Keep captures here only while they are needed for local investigation. Follow
the naming, timestamp, metadata, collision, and redaction rules in
[`data/README.md`](../../README.md). Copy only a reviewed, minimized, redacted
or synthetic test input into `data/fixtures/`; never promote an authenticated
capture directly into version control.
