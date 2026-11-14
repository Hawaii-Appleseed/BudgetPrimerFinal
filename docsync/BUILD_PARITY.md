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

## 2026-11-13 — a chart's type grows with the canvas it sits on

One of the 30 reports `docsync.build_parity` builds moved: `deck-branded`
(16:9, 13.333in wide). Its slide-5 bar chart's type went from 15–16px to
23.5–24.7px: `chart_svg` now takes the page's width (`Layout._shape_plain`
passes `self.page_w`) and multiplies its label size by
`layout.chart_text_scale(page_w)`, which is `page_w / 8.5` for a page wider than
Letter and 1.0 otherwise. Before, a chart's labels were capped at 0.2in
(19px) whatever the canvas, so on a 1280px slide the smallest ones were 15px
against a 21px floor for labels on a slide of that width. The bars moved too
(`x`/`width` in the diff): the value gutter is derived from the label size,
and a bigger label asks for more room. The other 29 pages are byte for byte
what they were — none of them draws a chart shape on a page wider than
Letter. A chart drawn with no page to ask (`blocks.chart`, a bare `chart_svg`
call) keeps its old size, so `docsync.chart_parity` is unchanged (0 of 37,009
differ).

A hub report whose layout holds a chart SHAPE on a page wider than Letter (a
deck made on the hub from `slides`, `slides-widescreen` or a resized page)
will draw that chart's type larger once this ships there, which is the point
of the change; nothing in this repo builds such a page, so no parity run here
can list them.

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
