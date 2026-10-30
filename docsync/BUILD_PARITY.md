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

## 2026-10-29 — every report prints in its true colours

Every bound report moved (all 15 that `docsync.build_parity` builds). Each page's first `layer()` now carries one extra
`<style>@media print{*,*::before,*::after{-webkit-print-color-adjust:exact
!important;print-color-adjust:exact !important}}</style>` (`Layout.
_print_color_once`, the rule is `layout.PRINT_EXACT_CSS`, which
`blocks.print_css()` now reuses). Nothing on screen changes; it only affects
print. Why: File > Download > PDF in the hosted editor prints the artboard
through the browser, and with Chrome's "Background graphics" off Chrome
darkens light text (white -> #ABABAB) while card backgrounds still print.
Only pages carrying the PDF button had the rule before.

## 2026-10-21 — the check begins

Nothing moved. Every bound report built identically from `main` and from the
branch that added this check; this entry records the baseline.
