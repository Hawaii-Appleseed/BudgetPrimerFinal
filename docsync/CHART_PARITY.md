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

## Meters, stacked totals, and the right-hand tick (2026-09-24)

- **`meter`** — a new type: one bar of parts on a track, to `axisMax` or the
  parts' sum, `values` inside when they fit, `trackColor` / `radius`. New, so
  nothing old can move. (`blocks.meter`, `blocks.meter_spec`.)
- **`totals`** on stacked-bar / stacked-row — each bar's own sum past its end.
  Opt-in: 0 renders moved when it landed.
- **The last tick of a horizontal value axis is no longer cut off.** Its label
  is centred on the plot's right edge, and a horizontal chart reserved no room
  for the half past it — Budget Primer Figure 2 drew "$6B" as "$6". Row,
  stacked-row and column charts with gridlines now reserve half that label's
  width on the right. `chart_parity` shows 3,555 of 24,497 renders moved, every
  one a horizontal chart with a grid: **the fix**. The one other bound report
  with a horizontal engine chart (retitc's burden chart) draws no grid, so no
  published page moved — checked by rebuilding every report.
- **The top tick of a vertical value axis is held inside the plot.** It sat on
  the plot's upper edge with its top third above the drawing, cut off in an
  inline chart's own viewBox (the primer's fixed-costs chart read "$6B" as a
  sliver). `_tick_y()` keeps it just inside; every other tick is unchanged.
  `chart_parity`: 9,415 renders moved — every vertical chart with a grid, the
  top tick label only: **the fix**. No bound report's page moved (rebuilt all).

## Gaps, turned labels on line and area, and two new sweep cases (2026-10-20)

Found by having Claude build complicated charts through every route a report
has (pilot verbs, layout.json, a renderer's `blocks.chart`) and then editing
each one as a person would.

- **A `null` value is a gap.** The schema has always accepted `null` in
  `data`, and `_chart_series()` read it as `0` — so a line or area chart with
  a month not yet reported dived to zero and back, and under a raised
  `axisMin` the dive was drawn below the plot, over whatever sat under the
  chart. `_chart_series()` now records `gaps` per series (the data still
  reads 0, so sums and stacks are unchanged); a line, an unstacked area, a
  combo overlay and a scatter plot break at a gap and leave its axis bounds
  out. A stack has no hole to show, so `stacked-area` and every bar keep
  reading 0.
- **`labelAngle` turns a line or area chart's category names.** The Chart
  panel has offered Labels: Flat / Tilted / Vertical on every chart with an
  axis, and only the bar chart honoured it: 24 months of names on a line
  chart lay flat on top of one another whatever the panel said. Line, area
  and stacked-area now turn them by the bar chart's rule, which moved into
  `_rot_label_band()` (bars draw byte-identically through it — see below).
  The panel no longer offers the control on a scatter plot or a histogram,
  whose axes are numbers.
- **The sweep now carries both.** `DATA["gap"]` (a null in each of two
  series) and `FLAGS` `{"labelAngle": -45}` / `{"labelAngle": -90, "legend":
  True}`, so neither can move unseen again. Against HEAD: 32,385 renders,
  1,424 differ, every one **the fix** — line / area / stacked-area under a
  turned label (672), and line / area / scatter with a gap (752). Not one bar
  chart moved, turned labels included, which is the check on the refactor.
  No bound report draws a line, area or scatter chart or sets `labelAngle`;
  the Budget Primer's one stacked area (fixed costs) has neither, so no
  published page moved.

## Chart polish: round steps across zero, a strip as wide as its labels, words that read on pale fills (2026-10-20)

Found in the same build-out, by drawing complicated charts at report sizes:

- **An axis that crosses zero divides in round steps, zero among them.** Left
  to the default five ticks, its lower end was never rounded, so the axis was
  cut in quarters of an odd span: -60 / 5 / 70 / 135 / 200, or -$0.2B / $2.3B /
  $4.9B / $7.4B / $10B — nothing a reader can add up, and no tick at zero, the
  line every bar grows from. `_axis_bounds()` now picks a round step with zero
  on a tick, at most eight ticks (-100 … 200 in 50s; -2 … 10 in 2s). Only an
  axis that goes below zero with neither end pinned and no `axisTicks`; an axis
  that stays above zero keeps the scale every published chart is drawn
  against. A combo chart's right axis shares the count (`_axis2_bounds(…,
  ticks)`) — two scales on one set of gridlines must divide alike, and a
  right axis left at five would have indexed past its own labels.
- **The strip beside a vertical value axis is as wide as its labels.** It was
  a fixed 2.6 label-heights, so "$15,000M" ran into the axis title and a
  right-hand "$8,000M" was cut by the chart's own edge ("$8,000I").
  `_value_gutter()` widens it only for a label more than a fifth of a
  label-height past the strip (measured 0.50–0.63 em a character across the
  report faces; 0.62 is used), on bar, line, area and scatter charts.
- **Words on a pale funnel band or treemap block are dark**, by the pie's
  rule (`_is_light`), and a treemap name too long for its block shrinks to
  the legibility floor and is left off if it still does not fit, rather than
  printed across the next block.
- **A histogram handed counts already binned** is unchanged (it bins raw
  observations, by design); `addChart` now answers with a note saying to use
  a bar chart with `barGap: 0`. New charts made by the Chart tool or
  `addChart` start in the report's own palette (`dressNewChart`, editor only).

The sweep gained `DATA["skew"]` (9.7, -0.2, 8.3), so the zero-crossing case
is watched from here on. Against HEAD: 37,009 renders, 9,149 differ, every
one **the fix** — zero-crossing axes 2,016 (all the new skew set), the
widened strip 2,672 (long money labels: the `big` set and the formatted
flags), both together 144, funnel and treemap ink and fit 4,313, and their 4
animated twins. **No bound report moved:** all fifteen in docsync.yml, and
the live Budget Primer in ~/BudgetPrimerFinal, were rendered with HEAD's
engine and this one, publish and edit mode, and every file is byte-identical.
