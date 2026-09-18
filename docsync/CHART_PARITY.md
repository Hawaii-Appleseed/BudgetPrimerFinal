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
`python3 -m docsync.chart_parity` is that sweep, kept — run it after any
change to `chart_svg()` and account for every render it says moved.

## Inline charts (2026-09-17)

The Chart panel only ever recognised `layout.shapes` with `kind:"chart"` —
pinned, absolutely-positioned. The Budget Primer's `layout.json` has **zero
shapes**: it is a flow document, and a flow document could not have an
editable chart at all. That, not the drawing gaps alone, is why all six
figures were hand-written SVG.

`blocks.chart()` draws a chart INLINE, where the renderer emits it, while its
data lives in `layout.json` under `charts[el_id]` where the panel can edit
it — the relationship `positions[el_id]` already had to a `graphic()`'s
geometry, extended to chart data. The renderer's spec and the user's override
meet in `Layout.chart_spec()`, and `chartWrite()` records **only the keys an
edit actually changed**, so a figure computed from `report_data.json` keeps
following the data after somebody retitles it.

Also closed for the port: pie labels move OUTSIDE the rim for a slice too thin
to hold them, staggered against their neighbours, which is what the
hand-drawn pies did and the engine did not.

## Tier 2 — closed 2026-09-17

All seven, each opt-in. The keys are validated in `_check_chart` and every one
has a control in the Chart panel.

1. **Per-point colour on bars.** `colors[]` now overrides by index on the bar
   family when there is ONE series, which is what a pie has always meant by
   that key; a series can also carry its own `colors[]` to override any single
   point, which is the general form and the one a multi-series chart uses. The
   data grid's row dot becomes a swatch whenever the chart is coloured per
   row. `_point_color()`.
2. **Reference / target lines.** `rules: [{value, label, color, dash, width}]`,
   drawn over the data and under the axis in every chart with a value plane —
   bar, row, line, scatter, area, histogram. `label: true` prints the
   formatted value. A rule off the scale is skipped, not clamped to the edge
   of it. `_rules_svg()`.
3. **Legend position.** `legendPos: bottom | top | left | right`. Left and
   right stack the entries and take their width out of the plot. And the
   bottom legend no longer collides with itself: entries whose ink exceeds
   their even share are packed and centred instead, wrapping to a second row
   only when one packed row cannot hold them. `_legend_svg()`.
4. **Axis titles.** `axisTitle` (the value axis, set on its side) and
   `catTitle` (the category axis). Carved out of the box in `chart_svg()`
   before the plot routine is called, so it cost one strip of the drawing and
   no change to four separate pad calculations. Both are editable by
   double-clicking them on the page, like every other piece of chart text.
5. **Area, 100%-stacked, and combo with a secondary axis.** `area` and
   `stacked-area` are types (`_area_svg`), and their points span the plot edge
   to edge rather than sitting at slot centres — an area chart is a continuous
   quantity, and a gap at each end would say the series began after the axis
   did. `stackPct` makes any stacked chart read as shares of its own total.
   A series may say `type: "line" | "area"` and `axis: "right"` inside a bar
   chart: nominal dollars as columns, the inflation-adjusted series as a line
   on its own scale (`axis2Min`, `axis2Max`, `axis2Format`; the tick COUNT is
   shared, because two scales against one set of gridlines must divide the
   same number of times).
6. **Category label rotation.** `labelAngle`, −90 to 90, on charts whose
   categories run along the foot. A turned label anchors at the end so it
   leans away from its tick, and shrinks — floored — so its lean stays inside
   the drawing.
7. **Gap width.** `barGap`, 0 to 0.9. The default is still the literal
   `slot * 0.78` the multiplier has always been, written that way rather than
   as `1 - 0.22`: those are different floats, and the difference shows in the
   fourth decimal of a bar's width.

Also closed alongside them, because it is the same defect as (6) — a label
with nowhere to go: **a row chart's gutter is now measured from its names.**
It was fixed at `3.4 × label height`, so any name over about six characters
ran off the LEFT of the drawing and lost its first letters, with no wrapping
to save it. The gutter grows to fit, capped at 40% of the width, and past that
the label shrinks.

### What moved

    python3 -m docsync.chart_parity

draws every combination of type, dataset, label set, flag and box size the
engine at HEAD could express, with both engines, and prints what differs —
so the property this file asks for is something you can run rather than
something you have to remember. 20,174 renders:

| renders | what changed | why it is not a regression |
|---:|---|---|
| 17,290 | nothing | byte-identical |
| 2,160 | row-chart gutter widened | those labels ran off the drawing |
| 444 | legend packed / wrapped | those names overlapped each other |
| 280 | `colors[]` honoured on bars | the key was silently ignored |

Nothing else differs. The primer's one engine chart — `fig6_chart`, the only
`chart()` call in any renderer in this repo — renders byte-for-byte as before,
and no project's `layout.json` holds a chart shape or a `charts[]` override,
so nothing published moved.

## Tier 3 — the InDesign Charts panel proper

8. **Data import.** Half done. **(a) Pasting a block of cells now works**:
   paste TSV (or CSV, when the block has no tabs) into any grid cell and the
   table fills from there, growing rows and series to fit, taking a
   non-numeric first row as the series names. That was the half people
   actually use, and until now a pasted block landed in one cell where
   `chartNumber()` refused it. **(b) A chart bound to a data file in the
   project, re-read on build, is still open** — that is the piece that makes a
   chart follow its source the way InDesign's linked data does.
9. **Chart styles.** **Closed.** A chart's look is saved under a name in
   `layout.chartStyles`, picked from the panel's Style section, and "Apply to
   all" dresses every chart in the document — the "redefine across a report"
   half — as ONE history entry and one redraw. `CHART_LOOK` is the list of
   keys a look is made of, mirrored on both sides; `seriesColors` is the
   ordered palette the series take theirs from, so a look survives being
   applied to a chart with a different number of series.

   Two decisions worth keeping. **A style is applied by writing its keys onto
   the chart, never by the renderer looking one up** — so a published page
   cannot depend on a definition still existing, or change meaning because
   someone edited a style. And **a look carries only the look**: not the type,
   the numbers, the title, the axis titles, the axis bounds or the reference
   lines, which are about one particular figure. Wearing a look also REMOVES
   the look keys it does not name, so two charts given the same style end up
   the same rather than the second keeping the first's leftovers.
10. **Release to objects.** An escape hatch: turn a chart into ordinary
    editable shapes when the engine genuinely cannot do what is wanted.
    Today the escape hatch is "write the SVG by hand in the renderer", which
    is exactly how the six frozen figures happened.

## The port that is the actual point

Tier 1 and Tier 2 exist so the primer's own figures can move onto the engine
and become editable. The engine can now express all of them; the port itself
is separate work.

| Figure | Needs |
|---|---|
| `fig6_chart` (tax rate by quintile) | **ported** — inline chart, two-line categories, `%` format, fixed `axisMax` |
| `fig3/4/5` (pies) | **ported** — inline charts, `sliceLabel: "both"`, outside labels for thin slices. Each is TWO charts, FY2026 and FY2027, toggled by the existing `fy_picker` — an engine chart holds one dataset, so the year swap stays the renderer's job |
| `fig2` (branch/department rows) | **unblocked** — per-point colour (1) is in; a row chart's long department names now fit its gutter |
| `fig_obligated` | **unblocked** — `stacked-area` (5) is the shape, `rules` (2) replace its hand-drawn endpoint callouts |
| `fig1_lifecycle` | not a chart — a diagram; belongs in `graphic()` with slots |
