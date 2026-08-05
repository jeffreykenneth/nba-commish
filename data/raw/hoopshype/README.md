# Hoopshype source captures

This directory contains source captures retrieved from Hoopshype, such as
salary-table HTML or source-row exports. It is for source evidence, not
normalized data or reusable test inputs. Commit a capture only when the task
explicitly requires it and it has been reviewed for private or secret data.

Follow the naming, timestamp, metadata, collision, and redaction rules in
[`data/README.md`](../../README.md). Tests must use a reviewed, minimized copy
under `data/fixtures/`, not a file from this directory.
