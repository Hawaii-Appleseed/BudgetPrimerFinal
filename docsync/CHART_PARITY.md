# Charts: what a designer can still only do in InDesign

Scoped 2026-09-17, against InDesign's native Charts panel (v19.3+) and the
Illustrator graph-tool habits that preceded it.

## Why this file exists

The measure of the gap is not a feature checklist. It is this: **every one of
the Budget Primer's six figures is hand-written SVG in
`report2027/tools/render_report.py`, and none of them goes through
`chart_svg()`.** The engine could not express the report's own charts, so they
were drawn by hand — and the cost is that they are frozen. A user cannot
retype a label, recolour a band, or correct a number in the editor, and
`docsync.check`'s editability pass has no purchase on any of them.

So the ranking below is by "what forced a figure out of the engine", not by
what InDesign happens to put in a panel.

## Done (2026-09-17)

Closed in the Tier 1 + multi-line + tooltips pass:

- **Signed axes.** `_axis_bounds()`. A value below zero used to be clamped by
  `frac = max(0.0, v / vmax)` and drawn at height zero while its own data
  label read the negative number — every deficit and year-over-year chart was
  silently wrong. Bars now grow both ways from a zero rule.
- **Axis scale.** `axisMin`, `axisMax`, `axisTicks`. A tick count also snaps
  the automatic end of the scale to a round step (`_nice_step`).
- **Number format.** `format {prefix, suffix, scale, decimals}` and a
  `labelFormat` override for data labels. `_tick_labels()` raises precision
  when a fixed `decimals` would print two ticks the same.
- **Stacked data labels**, drawn inside each segment and skipped when it is
  too thin to hold type.
- **Multi-line labels.** `\n` becomes `<tspan>`s; `wrapLabels` auto-wraps;
  wrapping breaks after a dash so a money range splits rather than colliding;
  a multi-line label shrinks to fit, floored by `_lfs`.
- **Tooltips.** `tips`, with the runtime injected once per document by
  `Layout._chart_tip_once()` and suppressed under `DOCSYNC_EDIT`.

Every one of these is opt-in or fires only where the old behaviour was wrong:
1,260 old-vs-new renders across every type, dataset and flag are
byte-identical except stacked + `values`, which is the fix. **Keep that
property.** A published report must not move because the engine grew.

## Tier 2 — expressive gaps that still send someone back to InDesign

Roughly in the order they bite.

1. **Per-point colour on bars.** Colour is per series; `colors[]` is
   pie-family only. Highlighting one bar — "this year", "the proposal" — is
   impossible, and it is the single most common ask of a budget chart.
   InDesign direct-selects any individual bar. Smallest useful shape: let
   `colors[]` override by index on the bar family too, and give the data grid
   a per-row swatch when the chart is not a pie.
2. **Reference / target lines and annotations.** "The FY26 level", "the
   inflation-adjusted line". Needs a `rules: [{value, label, color, dash}]`
   on the chart, drawn in the value plane. `fig_obligated` hand-draws its
   endpoint callouts for want of this.
3. **Legend position.** Always bottom, spread evenly across the full width
   (`gap = w / len(keys)`), so long series names collide with no recourse.
   InDesign offers top/bottom/left/right. Add `legendPos`.
4. **Axis titles.** No way to say "Millions of dollars" or "Fiscal year"
   except a floating text box that does not move with the chart.
5. **Area, 100%-stacked, and combo (bar + line) with a secondary axis.**
   `fig_obligated` is a stacked area chart — the reason it is 45 lines of
   hand-written polygon geometry. `CHART_TYPES` has no `area`.
6. **Category label rotation.** Wrapping and shrink-to-fit now cover most
   cases, but at the legibility floor a dense row of long names still has
   nowhere to go. Rotation (or a `chart_scroll`-style escape) is the answer.
7. **Gap width / bar spacing.** Hardcoded at `slot * 0.78`.

## Tier 3 — the InDesign Charts panel proper

8. **Data import, and a linked data file.** The biggest workflow gap by a
   wide margin. Every cell is typed by hand today: pasting a block of TSV
   from Excel lands the whole thing in one cell, where `chartNumber()`
   rejects it. InDesign imports CSV/XLSX *and* links the file so the chart
   follows the data. Two separable pieces — (a) paste a TSV/CSV block into
   the grid and fill it, (b) bind a chart to a data file in the project and
   re-read it on build. (a) is cheap and worth doing on its own.
9. **Chart styles.** No way to save a chart's look and apply it to the next
   one, or redefine across a report. Templates carry palettes, not chart
   looks — so a report's fifteenth chart is styled by hand like the first.
10. **Release to objects.** An escape hatch: turn a chart into ordinary
    editable shapes when the engine genuinely cannot do what is wanted.
    Today the escape hatch is "write the SVG by hand in the renderer", which
    is exactly how the six frozen figures happened.

## The port that is the actual point

Tier 1 exists so the primer's own figures can move onto the engine and become
editable. That port is separate work and has not been done. In rough order of
difficulty:

| Figure | Needs |
|---|---|
| `fig6_chart` (tax rate by quintile) | done — two-line categories, `%` format, fixed `axisMax` |
| `fig3/4/5` (pies) | done — `sliceLabel: "both"`, `$`/`B` format |
| `fig2` (branch/department rows) | per-point colour (1), grouped overlays |
| `fig_obligated` | area type (5), reference lines (2) |
| `fig1_lifecycle` | not a chart — a diagram; belongs in `graphic()` with slots |

`fig6` and the three pies should port today. Doing so would also clear the
`[editability]` findings those pages carry.
