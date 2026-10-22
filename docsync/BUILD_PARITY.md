# Published pages: what the engine moved, and why

`python3 -m docsync.build_parity` builds every bound report twice — with the
base tree's renderer, content, layout and engine, and with this tree's, the
date pinned — and compares the pages byte for byte. CI runs it against the
commit a push or pull request started from (editor-tests.yml, the `parity`
job, beside `docsync.chart_parity`), and the gate on `main` waits for it.

A page whose own files changed is explained by that diff. A page the ENGINE
moved is explained here: one entry per change, newest first, naming the
reports and what moved in them. This file changing in the same range is
what lets the check pass — so the entry is written with the change, not
after it, and it is the record for whoever next asks why a published page
is not what it was. Charts have `docsync/CHART_PARITY.md` for the same
reason.

## 2026-10-21 — the check begins

Nothing moved. Every bound report built identically from `main` and from the
branch that added this check; this entry records the baseline.
