# Test fixtures

This directory is the only location for committed redacted or synthetic test
inputs. Fixtures must be minimal, stable, and reusable, and must contain only
the fields needed to exercise the behavior under test. Do not place generated
sample outputs here.

Copy source-derived inputs here only after redaction and review; otherwise keep
them in `data/private/` or, for authenticated Yahoo data,
`data/raw/yahoo/`. Follow the naming, timestamp, metadata, collision,
placeholder-consistency, and redaction rules in
[`data/README.md`](../README.md).
