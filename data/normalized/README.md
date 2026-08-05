# Normalized outputs

This directory contains generated intermediate data derived from source
captures, such as parsed salary rows, reconciled player records, or calculated
standings. These outputs are reproducible intermediates, not raw evidence,
reports, or reusable test inputs.

Commit an output only when its task explicitly requires it and it has been
reviewed. Follow the naming, timestamp, metadata, collision, and redaction
rules in [`data/README.md`](../README.md). Put committed redacted or synthetic
test inputs in `data/fixtures/` instead.
