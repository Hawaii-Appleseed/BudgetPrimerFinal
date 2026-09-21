"""Layout overrides: positions and shapes, as data.

The report's design lives in the renderer, and that is what keeps it a designed
artifact rather than a blank canvas — twelve pages that stay twelve pages
because only text varies. This module does not change that. It adds a thin
layer on top: where an override exists the renderer honours it, and everywhere
else the code's own design stands.

So a dragged box is one line of JSON, reviewable in a diff and revertable by
deleting it, instead of a hand-edit to layout code. With no overrides the file
is empty and the published HTML is byte-for-byte what it always was.

Coordinates are inches from the page's top-left corner, because `.page` is
`position: relative` and absolutely positioned children resolve against it.

    {
      "positions": { "callout.obligated": {"x": 1.2, "y": 3.4, "w": 5.0,
                                            "reserve": 2.7, "z": 1} },
      "shapes": [ {"id":"s1","page":3,"kind":"rect","x":1,"y":2,"w":3,"h":1,
                   "fill":"#6B9E78","z":"back"} ]
    }
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

# One direction only: content.py imports nothing from this package (it takes its
# styler duck-typed), so there is no cycle. A text box is a markdown block that
# happens to be positioned — block_html already renders exactly that for the
# overflow slots, and a second renderer for the same thing would drift.
from .content import Footnotes, block_html, md_inline, paragraph

# Letter portrait, because that is what the Budget Primer is. Any report with a
# different page passes its own size in — this is a default, not a law. It is
# the only thing in this package that ever knew about one particular report.
PAGE_W_IN, PAGE_H_IN = 8.5, 11.0
# A pageless document has no bottom. It still needs SOME number for the rules
# that keep content on the page (clamping a drag, an align-to-page reference),
# so it gets one no report will reach rather than a special case in every
# caller.
PAGELESS_H = 200.0

# Stamped on everything this module takes out of the flow and pins by inch —
# placed designed elements, boxes, buttons, tables. It is what mobile_css()
# releases on a screen too narrow to hold the sheet, and it has to be an
# explicit opt-in rather than a match on the inline style, because ONE
# absolutely positioned thing here must never be released: the shape layer,
# which is the page's background and detaches from the page the moment it
# joins the flow. Forgetting the stamp on something new leaves it pinned on a
# phone — visible, and caught by test_docsync's placed-elements-are-stamped
# check. Guessing from the style attribute instead would silently unpin a
# background, which is neither.
PLACED = " data-placed"

# A hidden element is GONE in the editor too, exactly as it is on the published
# page. It was first drawn as a translucent dashed ghost that kept its box, so
# the deletion would be visibly reversible — but a half-faded element reads as a
# broken delete, not an undoable one, and the box it kept meant the page never
# closed the gap the way publishing would. Reversibility lives in Undo and in
# the editor's "Restore deleted" list instead, where it belongs.
# What a page may be. Wide enough for A3 and long enough for a US Legal sheet,
# with room either side; narrow enough that a typo in a hand-edited layout.json
# cannot ask the renderer for a page a mile across.
PAGE_MIN_IN, PAGE_MAX_IN = 1.0, 100.0


def _check_page(v, where: str):
    """A page-size override from layout.json, or None.

    A report is BUILT at a size — docsync.yml's editor.page, which the manifest
    carries — and this overrides it. It belongs in layout.json rather than the
    report's stylesheet because every coordinate in this file is inches
    measured against the page: the geometry and the CSS have to come from ONE
    value, or a resize moves everything that was placed before it.

    {"w": 8.5, "h": 11} — or "h": null for pageless, a fixed width with no
    bottom."""
    if v is None:
        return None
    if not isinstance(v, dict):
        raise LayoutError(f"{where}: page must be an object like "
                          f'{{"w": 8.5, "h": 11}}, not {type(v).__name__}')
    w = v.get("w")
    if not isinstance(w, (int, float)) or isinstance(w, bool):
        raise LayoutError(f"{where}: page width {w!r} is not a number")
    if not PAGE_MIN_IN <= w <= PAGE_MAX_IN:
        raise LayoutError(f"{where}: page width {w}in is outside "
                          f"{PAGE_MIN_IN}–{PAGE_MAX_IN}in")
    h = v.get("h")
    if h is None:
        return (float(w), None)                       # pageless
    if not isinstance(h, (int, float)) or isinstance(h, bool):
        raise LayoutError(f"{where}: page height {h!r} is not a number "
                          f"(use null for a pageless document)")
    if not PAGE_MIN_IN <= h <= PAGE_MAX_IN:
        raise LayoutError(f"{where}: page height {h}in is outside "
                          f"{PAGE_MIN_IN}–{PAGE_MAX_IN}in")
    return (float(w), float(h))
KINDS = ("rect", "ellipse", "line", "triangle", "arrow", "icon", "chart")
LINE_ENDS = ("none", "start", "end", "both")

# --- charts ---------------------------------------------------------------
# A chart is a SHAPE, not a fourth kind of placed object. That is the whole
# design: it inherits x/y/w/h in inches, z-order, rotation, opacity, drag,
# resize, duplicate and delete from the shape pipeline, and it renders inside
# the same per-page <svg> layer every report renderer already emits — so no
# report has to add a call to show one. It is drawn as plain SVG, with no
# library, which is what lets the same markup serve the browser preview and
# the headless-Chrome PDF (which has no network) from one code path.
# "column" is the old name for what is now "row" (horizontal bars); kept so
# every layout.json written before the rename still validates and renders.
# Deliberately ABSENT: an animated bar-chart race, which has no meaning in a
# report that is printed — every type here has to survive being a PDF.
CHART_TYPES = ("bar", "stacked-bar", "row", "stacked-row", "column",
               "pie", "donut", "line", "area", "stacked-area", "scatter",
               "histogram", "radar", "funnel", "packed", "treemap")
# Which types read a value PER LABEL (one series) rather than per series, and
# so name their key by label the way a pie does.
CHART_BY_ITEM = ("pie", "donut", "funnel", "packed", "treemap", "histogram")
# Which types are drawn in a value plane, and so have an axis to title, a
# scale to pin and reference lines to hang off. Mirrored in the editor as
# CHART_WITH_AXES — a treemap offered an axis colour would be a lie.
CHART_AXIS_TYPES = ("bar", "stacked-bar", "row", "stacked-row", "column",
                    "line", "area", "stacked-area", "scatter", "histogram")
# Where the legend may sit. Absent is "bottom", and bottom is what every
# chart drawn before this got — see _legend_svg for why that matters.
CHART_LEGEND_POS = ("bottom", "top", "left", "right")
# What ONE series may be drawn as inside a bar chart. This is the combo
# chart: a line of "inflation-adjusted" over columns of nominal dollars,
# which is the shape half the budget figures actually want.
CHART_SERIES_KINDS = ("bar", "line", "area")
# A chart STYLE is the look, saved under a name and applied to the next chart.
# These are the keys that make up a look — everything that is not this
# figure's own data or its own subject. `type`, `labels`, `series` data,
# `title`, the axis titles, `axisMin`/`axisMax` and `rules` are all about one
# particular figure and are deliberately absent; `seriesColors` is not a chart
# key at all but the ORDERED palette a style hands to `series[i].color`.
#
# A style is applied by WRITING these keys onto the chart, never by the
# renderer looking one up: a published report must not depend on a style
# definition still existing, or say something different once someone edits it.
CHART_LOOK = ("colors", "titleColor", "labelColor", "axisColor", "gridColor",
              "legend", "legendPos", "values", "grid", "tips", "sliceLabel",
              "format", "labelFormat", "axis2Format", "barGap", "labelAngle",
              "wrapLabels", "axisTicks")
CHART_COLORS = ("#6B9E78", "#52796F", "#95B7A2", "#354F52",
                "#CAD2C5", "#2F3E46", "#A8C4B0", "#7A8E92")
# What a number label may be scaled by, and what a pie slice may say about
# itself. Both are data the editor's pickers read, so the two halves cannot
# offer different sets.
CHART_NUM_SCALES = ("none", "auto", "K", "M", "B")
CHART_SLICE_LABELS = ("percent", "value", "both")
# Below this sweep a slice cannot hold its own label, and the label goes
# outside the rim instead. 55 degrees is where the hand-drawn pies in the
# published primer put the line, and they were tuned against real figures.
_PIE_INSIDE_SWEEP = math.radians(55)


def _is_light(hexc) -> bool:
    """Whether a fill needs dark text on it. blocks.is_light_bg is the same
    test, but blocks imports THIS module, so it cannot be imported back."""
    h = str(hexc).lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if len(h) < 6:
        return False
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return False
    if len(h) == 8:
        a = int(h[6:8], 16) / 255
        r, g, b = (v * a + 255 * (1 - a) for v in (r, g, b))
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) > 130


def _nice_max(v: float) -> float:
    """A round number at or above v, so an axis reads 0 / 25 / 50 rather than
    0 / 23.7 / 47.4."""
    if v <= 0:
        return 1.0
    exp = math.floor(math.log10(v))
    base = 10.0 ** exp
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if v <= m * base:
            return m * base
    return 10 * base


def _nice_min(v: float) -> float:
    """The negative counterpart of _nice_max: a round number at or BELOW v."""
    return -_nice_max(-v) if v < 0 else 0.0


def _axis_bounds(c: dict, vals) -> tuple:
    """The value axis a chart is drawn against: (lo, hi, ticks).

    Two things this fixes at once. A chart whose data goes below zero used to
    be drawn against `0 … _nice_max(max)` with every bar clamped by
    `max(0.0, v / vmax)` — so a -45 drew as a bar of height zero while its own
    data label read "-45". The axis now reaches down to a round number below
    the lowest value whenever one is negative, and the bar grows from the zero
    line instead of from the floor.

    And the scale is no longer always automatic: `axisMin` / `axisMax` pin
    either end, `axisTicks` says how many divisions to draw. A chart that sets
    none of the three gets exactly the bounds it got before — which is what
    keeps every published report byte-identical.
    """
    vals = [v for v in vals if v is not None]
    hi_data = max(vals, default=0.0)
    lo_data = min(vals, default=0.0)
    lo = c.get("axisMin")
    hi = c.get("axisMax")
    if hi is None:
        hi = _nice_max(hi_data)
    if lo is None:
        lo = _nice_min(lo_data)
    # An authored pair that is inside-out or empty would divide by zero below.
    if hi <= lo:
        hi = lo + max(1.0, abs(lo) * 0.1)
    want = c.get("axisTicks")
    ticks = 5 if not isinstance(want, int) or isinstance(want, bool) \
        else max(2, min(11, want))
    # Asking for a tick COUNT is also asking for ticks worth reading, so the
    # automatic end of the scale is snapped out to a round step. Not done for
    # the default 5: the existing scale (0 … 1.5x10^k in quarters) is what
    # every published report is drawn against, and moving it would redraw
    # them all.
    if isinstance(want, int) and not isinstance(want, bool):
        step = _nice_step((hi - lo) / max(1, ticks - 1))
        if c.get("axisMin") is None:
            lo = math.floor(lo / step) * step
        if c.get("axisMax") is None:
            hi = lo + step * (ticks - 1)
            while hi < hi_data:
                step = _nice_step(step * 1.0001 + step * 0.25)
                hi = lo + step * (ticks - 1)
    return float(lo), float(hi), ticks


def _nice_step(raw: float) -> float:
    """A round tick interval at or above raw — 1, 2, 2.5 or 5 times a power of
    ten, the intervals a reader can add up in their head."""
    if raw <= 0:
        return 1.0
    exp = math.floor(math.log10(raw))
    base = 10.0 ** exp
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * base:
            return m * base
    return 10 * base


def _tick_labels(vmin: float, vmax: float, nticks: int, fmt: dict) -> list:
    """Every tick on one axis, formatted together rather than one at a time.

    Formatted together because a fixed `decimals` can collapse neighbouring
    ticks into the same string — 0, 0.25, 0.5, 0.75, 1 scaled to billions at
    zero decimals is "0 0 1 1 1", an axis that says nothing. When that
    happens the precision is raised until the ticks are distinct again, which
    is what the author meant by asking for that scale.
    """
    span = vmax - vmin
    vals = [vmin + span * (i / (nticks - 1)) for i in range(nticks)]
    out = [_fmt_val(v, fmt) for v in vals]
    if not fmt or not isinstance(fmt.get("decimals"), int):
        return out
    for extra in range(1, 4):
        if len(set(out)) == len(out):
            break
        bumped = dict(fmt, decimals=fmt["decimals"] + extra)
        out = [_fmt_val(v, bumped) for v in vals]
    return out


# Scale suffixes, largest first — "auto" picks the first one the number clears.
_NUM_SCALES = (("B", 1e9), ("M", 1e6), ("K", 1e3))


def _num_format(c: dict, which: str = "format") -> dict:
    """The number format for one role. `format` covers the value axis and the
    data labels; `labelFormat` overrides it for the data labels alone, which is
    what lets an axis read "$0B … $5B" while the point labels read "$2.44B"."""
    fmt = c.get("format")
    fmt = dict(fmt) if isinstance(fmt, dict) else {}
    if which != "format":
        extra = c.get(which)
        if isinstance(extra, dict):
            fmt.update(extra)
    return fmt


def _wrap_lines(c: dict) -> int:
    """How many lines a category label may take. 1 (the default) is the old
    single-line behaviour, and an explicit "\\n" in a label still breaks it
    either way — this only governs AUTOMATIC wrapping of a long name."""
    v = c.get("wrapLabels")
    if v is True:
        return 2
    if isinstance(v, int) and not isinstance(v, bool):
        return max(1, min(4, v))
    return 1


def _fmt_val(v: float, fmt: dict = None) -> str:
    """A number as the chart should show it. With no format this is exactly
    _fmt_num — thousands separated, no trailing .0 — so an unformatted chart
    is unchanged."""
    if not fmt:
        return _fmt_num(v)
    scale = fmt.get("scale") or "none"
    suffix = fmt.get("suffix") or ""
    unit = ""
    if scale == "auto":
        for name, div in _NUM_SCALES:
            if abs(v) >= div:
                v, unit = v / div, name
                break
    elif scale in ("K", "M", "B"):
        div = dict(_NUM_SCALES)[scale]
        v, unit = v / div, scale
    # The scale's letter can be overridden, or dropped with "". A figure whose
    # caption already says "($Millions)" wants "$2,262", not "$2,262M" — the
    # unit is stated once, in prose, which is how the published primer says it.
    if fmt.get("unit") is not None:
        unit = fmt["unit"]
    dec = fmt.get("decimals")
    if isinstance(dec, int):
        body = f"{v:,.{max(0, min(6, dec))}f}"
    else:
        body = _fmt_num(v)
    neg = body.startswith("-")
    if neg:
        body = body[1:]
    return f"{'-' if neg else ''}{fmt.get('prefix') or ''}{body}{unit}{suffix}"


def _fmt_num(v: float) -> str:
    """Axis and value labels: no trailing .0, thousands separated."""
    if v == int(v):
        return f"{int(v):,}"
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def _xml(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# Estimated width of one character, in ems. The width of a glyph run cannot
# be measured here — this renders under Pyodide and in headless Chrome,
# neither with a text-metrics API at build time. 0.58 is set by the strings
# these labels actually carry: digits, "$" and "%" are among the widest
# glyphs in a grotesque, and a category label under a budget chart is mostly
# those. Erring wide breaks a line early, which is recoverable; erring narrow
# runs two labels into each other, which is not.
_EM_W = 0.58


def _wrap_label(s: str, budget: float, size: float, max_lines: int) -> list:
    """Break one label to fit `budget` inches at `size`, up to max_lines.

    Splits on spaces first. A single "word" still too wide for the line is
    split after a dash — "$21,900-$44,200" under a bar is one word to
    str.split and one and a half inches to a reader, and money ranges are
    most of what these labels say. The last line keeps whatever is left
    rather than being truncated: a clipped category name is worse than one
    slightly over budget.
    """
    if budget <= 0:
        return [str(s)]
    per = max(1, int(budget / (size * _EM_W)))
    words = []
    for w in str(s).split():
        while len(w) > per:
            # Prefer a dash inside the over-long run; only then a hard cut.
            cut = max(w.rfind(d, 1, per + 1) for d in ("-", "–", "—", "/"))
            if cut <= 0:
                break
            words.append(w[:cut + 1])
            w = w[cut + 1:]
        words.append(w)
    if not words:
        return [str(s)]
    lines, cur = [], ""
    for wd in words:
        trial = f"{cur} {wd}".strip()
        if len(trial) <= per or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = wd
            if len(lines) == max_lines - 1:
                break
    rest = words[sum(len(l.split()) for l in lines):]
    lines.append(" ".join(rest) if rest else cur)
    return [l for l in lines if l] or [str(s)]


def _label_lines(labels) -> int:
    """The most lines any category label asks for by carrying its own breaks.
    1 when none of them does, which is every chart written before this."""
    return max((str(l).count("\n") + 1 for l in labels), default=1)


def _fit_size(s, budget: float, size: float) -> float:
    """Shrink a multi-line label until its widest line fits the space it has,
    but never below the legibility floor — a label too small to read is not a
    fix for one that overlaps. Same 0.52em estimate as _wrap_label."""
    widest = max((len(line) for line in str(s).split("\n")), default=0)
    if widest <= 0 or budget <= 0:
        return size
    need = widest * size * _EM_W
    return _lfs(size * budget / need) if need > budget else size


def _label_parts(s, size: float, wrap: float = 0.0, max_lines: int = 2) -> list:
    """The lines a chart label will actually be drawn on: its own breaks
    first, then each of those wrapped to the space available.

    Split out so the code that RESERVES room for the category band and the
    code that draws it cannot disagree. They did: the band was sized from the
    breaks the author typed, wrapping then added a third line to every label,
    and that line was drawn through the paragraph under the chart.
    """
    parts = str(s).split("\n")
    if not wrap:
        return parts
    out = []
    for piece in parts:
        out.extend(_wrap_label(piece, wrap, size, max_lines))
    return out


def _cat_label(name, budget: float, base: float, wrap_lines: int,
               fit_single: bool = False) -> tuple:
    """A category label's final size and lines. One function so the measuring
    pass and the drawing pass ask exactly the same question.

    `fit_single` shrinks a ONE-LINE label to its budget too. A row chart's
    names sit in a fixed gutter and are anchored at its right edge, so a long
    one ran off the left of the drawing and lost its first letters — there is
    no wrapping to save it and nothing else to give. Off by default: under a
    vertical bar a single line has always kept its size, and every chart drawn
    that way has to keep rendering the same.
    """
    multi = "\n" in str(name) or wrap_lines > 1
    if not multi:
        return (_fit_size(name, budget, base) if fit_single else base), [str(name)]
    size = _fit_size(name, budget, base)
    lmax = max(wrap_lines, str(name).count("\n") + 2)
    return size, _label_parts(name, size, wrap=budget, max_lines=lmax)


def _lines_from(parts: list, x: float, size: float, center: bool = False) -> str:
    """Already-split lines as the inside of a <text>. One line is the bare
    string, so a chart that never wrapped renders byte-for-byte as before."""
    if len(parts) == 1:
        return _xml(parts[0])
    lead = size * 1.15
    first = -lead * (len(parts) - 1) / 2 if center else 0.0
    out = []
    for i, line in enumerate(parts):
        dy = first if i == 0 else lead
        out.append(f'<tspan x="{x:.4f}" dy="{dy:.4f}">{_xml(line)}</tspan>')
    return "".join(out)


def _lines_inner(s, x: float, size: float, *, center: bool = False,
                 wrap: float = 0.0, max_lines: int = 2) -> str:
    """The inside of a chart's <text>, when the text may be several lines.

    A single line returns just the escaped string, so every chart that has
    never carried a break renders byte-for-byte as it always did — the reason
    this is an inner-content helper rather than a whole-element one, which
    would have had to re-order every caller's attributes.

    Several lines become one <tspan> each, re-anchored at the same x because
    SVG does not wrap, stepping down by the leading. `center` lifts the block
    by half its height so the run is centred on the baseline it was given —
    what a slice label or a label inside a bar wants; a label UNDER a bar
    wants the default, where the first line keeps that baseline.
    """
    text = str(s)
    parts = text.split("\n")
    if wrap:
        # Each authored line is wrapped in its own right. A label that already
        # carries a break can still hold a line too wide for its slot — the
        # income range under "Second 20%" is exactly that — and leaving those
        # alone was what let two neighbouring labels overlap.
        out = []
        for piece in parts:
            out.extend(_wrap_label(piece, wrap, size, max_lines))
        parts = out
    if len(parts) == 1:
        return _xml(parts[0])
    lead = size * 1.15
    first = -lead * (len(parts) - 1) / 2 if center else 0.0
    out = []
    for i, line in enumerate(parts):
        dy = first if i == 0 else lead
        out.append(f'<tspan x="{x:.4f}" dy="{dy:.4f}">{_xml(line)}</tspan>')
    return "".join(out)


def _tip(c: dict, text: str) -> str:
    """A hover tooltip on one drawn part, when the chart asked for them.

    Opt-in (`tips`), because the attribute is also what docsync.text reads a
    chart's numbers out of — emitting it unasked would change what content
    search indexes for every chart already published.
    """
    if not c.get("tips"):
        return ""
    # data-tip ALONE, and the runtime selects on it. A class here would be a
    # SECOND class attribute on a bar that already carries ds-cbar, and a
    # parser keeps only the first — so the hook silently vanished on exactly
    # the elements that most needed it.
    #
    # pointer-events has to be asked for as well: a page's shape layer is
    # pointer-events:none so that a chart drawn over the prose does not eat
    # clicks meant for it, which also meant no bar ever saw the mouse and no
    # tooltip could fire. Only outside the editor — in it, a bar that took
    # the pointer would be a bar you could not drag the chart by.
    pe = "" if os.environ.get("DOCSYNC_EDIT") else ' pointer-events="auto"'
    return f'{pe} data-tip="{_xml(text)}"'

# --- icons ---------------------------------------------------------------
# An icon is picked from an open-source set (Iconoir, Lucide, Heroicons,
# Bootstrap Icons… via the Iconify API) and its GEOMETRY is copied into
# layout.json — not a reference to it. That is deliberate: the PDF is printed
# by headless Chrome from built HTML, and the preview renders under Pyodide,
# neither of which may assume a network. A stored icon renders offline
# forever, and cannot change under the report when an upstream set is
# revised.
#
# The flip side is that markup from the internet ends up inside the rendered
# page, so it is checked here as well as in the editor: layout.json can be
# hand-edited, and this is the only gate the renderer itself controls.
# Whitelist, and a hard failure — never a silent strip, which would leave a
# half-drawn icon nobody can explain.
ICON_TAGS = {
    "g", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon",
    "defs", "clipPath", "mask", "use", "title", "desc", "symbol",
    "linearGradient", "radialGradient", "stop",
}
ICON_BANNED = re.compile(
    r"<\s*(script|foreignObject|image|iframe|a|animate|animateTransform|set|handler)\b"
    r"|\bon[a-z]+\s*=|javascript:|<!ENTITY|<!DOCTYPE", re.I)
_ICON_TAG_RE = re.compile(r"<\s*/?\s*([A-Za-z][A-Za-z0-9]*)")
_VIEWBOX_RE = re.compile(r"^\s*-?[\d.]+(\s+-?[\d.]+){3}\s*$")


def check_icon_svg(body: str, where: str) -> str:
    """The icon's inner markup, or a hard failure explaining what was wrong."""
    if not isinstance(body, str) or not body.strip():
        raise LayoutError(f"{where}: an icon needs its 'svg' markup")
    if len(body) > 64_000:
        raise LayoutError(f"{where}: icon markup is {len(body)} bytes — far past "
                          f"anything an icon needs; refusing it")
    m = ICON_BANNED.search(body)
    if m:
        raise LayoutError(f"{where}: icon markup contains {m.group(0)!r}, which is "
                          f"not allowed in an icon")
    for tag in _ICON_TAG_RE.findall(body):
        if tag not in ICON_TAGS:
            raise LayoutError(f"{where}: icon markup uses <{tag}>, which is not one "
                              f"of the allowed SVG shape tags")
    return body


def icon_color(fill) -> str:
    """The colour an icon's `currentColor` resolves to.

    Icon sets draw in `currentColor` precisely so one CSS property recolours
    the whole glyph — that is what makes them palette-aware here. A gradient
    cannot be a colour, so a gradient fill contributes its first stop rather
    than failing: the icon still lands in the report's palette.
    """
    if isinstance(fill, dict):
        stops = fill.get("stops") or []
        return stops[0].get("color", "#2F3E46") if stops else "#2F3E46"
    if isinstance(fill, str) and fill != "none":
        return fill
    return "#2F3E46"

# Fonts a report may ask for, with the weights Google will actually serve. An
# allowlist, not free text: a typo'd family falls back to sans-serif in the PDF
# with nothing to catch it, and an unchecked family name would land inside a
# style="…" attribute, where a stray quote ends the attribute. Canva's picker is
# a fixed list too — this is parity, not a compromise.
FONTS = {
    "Barlow":         [400, 500, 600, 700, 800, 900],
    "Source Sans 3":  [300, 400, 600, 700],
    "Playfair Display": [400, 500, 600, 700, 800, 900],
    # High-contrast display serif, and one of the few with a true ʻokina
    # (U+02BB) — Bodoni Moda, Libre Bodoni, DM Serif and Prata all lack it,
    # which rules them out for any Hawaiian-language title.
    "Noto Serif Display": [400, 500, 600, 700, 800, 900],
    "Merriweather":   [300, 400, 700, 900],
    "Lora":           [400, 500, 600, 700],
    "Libre Baskerville": [400, 700],
    "Inter":          [300, 400, 500, 600, 700, 800, 900],
    "Roboto":         [300, 400, 500, 700, 900],
    "Open Sans":      [300, 400, 600, 700, 800],
    "Lato":           [300, 400, 700, 900],
    "Montserrat":     [300, 400, 500, 600, 700, 800, 900],
    "Oswald":         [300, 400, 500, 600, 700],
    "Raleway":        [300, 400, 500, 600, 700, 800, 900],
    "Nunito":         [300, 400, 600, 700, 800, 900],
    "Work Sans":      [300, 400, 500, 600, 700, 800],
    "IBM Plex Sans":  [300, 400, 500, 600, 700],
    "IBM Plex Serif": [300, 400, 500, 600, 700],
    "Space Grotesk":  [300, 400, 500, 600, 700],
    "Bebas Neue":     [400],
    "Anton":          [400],
    "Archivo":        [300, 400, 500, 600, 700, 800, 900],
    "Karla":          [300, 400, 500, 600, 700, 800],
    "Rubik":          [300, 400, 500, 600, 700, 800, 900],
    "Cormorant Garamond": [300, 400, 500, 600, 700],
    "Crimson Text":   [400, 600, 700],
    # The Hawaiʻi Appleseed brand pair (per the 2026 brand guide): Manrope for
    # headings and display, Poppins for body. Manrope has no true italic on
    # Google Fonts — the brand's italic accents are set in Poppins italic.
    "Manrope":        [400, 500, 600, 700, 800],
    "Poppins":        [300, 400, 500, 600, 700, 800],
}

# What primer.css already asks Google for. The report needs these whether or not
# anything is overridden, and font_link must reproduce the old hardcoded literal
# from exactly this when nothing is.
BRAND_FONTS = {"Barlow": [800, 900], "Source Sans 3": [300, 400, 600, 700]}
BRAND_ITALICS = {"Source Sans 3": [400]}

# Effects. Each is a function from its parameters to CSS, so validation can
# name the ones that exist and the editor can ask which parameters to show.
#
# Two conventions worth stating once:
#  * 0 degrees is 12 o'clock and it goes clockwise — the same convention
#    arc_path() already uses in the renderer. A second convention for the same
#    idea in one repo is a bug waiting to happen.
#  * Offsets and blurs are in em, so a shadow stays proportional when the type
#    is resized instead of detaching from it.
#  * We store ALPHA, not Canva's "transparency". Storing the inverted quantity
#    invites a 1-x slip at every read; the slider can show whatever it likes.
def _rgba(hexc: str, a: float) -> str:
    h = hexc.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{a:g})"


def _xy(offset: float, deg: float) -> tuple[float, float]:
    rad = math.radians(deg)
    return round(offset * math.sin(rad), 3), round(-offset * math.cos(rad), 3)


def _fx_shadow(e: dict) -> str:
    dx, dy = _xy(e.get("offset", 0.06), e.get("direction", 135))
    c = _rgba(e.get("color", "#2F3E46"), e.get("alpha", 0.45))
    return f'text-shadow:{dx}em {dy}em {e.get("blur", 0.04)}em {c}'


def _fx_lift(e: dict) -> str:
    k = e.get("intensity", 0.5)
    return f'text-shadow:0 {round(k * .5, 3)}em {round(k * 1.2, 3)}em rgba(0,0,0,{round(k * .5, 3)})'


def _fx_hollow(e: dict) -> str:
    return (f'color:transparent;-webkit-text-stroke:{e.get("width", 0.02)}em '
            f'{e.get("color", "#52796F")}')


def _fx_splice(e: dict) -> str:
    dx, dy = _xy(e.get("offset", 0.06), e.get("direction", 135))
    return (f'color:transparent;-webkit-text-stroke:{e.get("width", 0.02)}em '
            f'{e.get("color", "#52796F")};'
            f'text-shadow:{dx}em {dy}em 0 {e.get("shadow", "#95B7A2")}')


def _fx_echo(e: dict) -> str:
    dx, dy = _xy(e.get("offset", 0.06), e.get("direction", 135))
    c = e.get("color", "#52796F")
    return (f'text-shadow:{dx}em {dy}em 0 {_rgba(c, .5)},'
            f'{round(dx * 2, 3)}em {round(dy * 2, 3)}em 0 {_rgba(c, .3)}')


def _fx_glitch(e: dict) -> str:
    dx, dy = _xy(e.get("offset", 0.04), e.get("direction", 90))
    return (f'text-shadow:{round(-dx, 3)}em {round(-dy, 3)}em 0 {e.get("color", "#00E5FF")},'
            f'{dx}em {dy}em 0 {e.get("shadow", "#FF00A0")}')


def _fx_neon(e: dict) -> str:
    c = e.get("color", "#6B9E78")
    k = e.get("intensity", 1.0)
    return (f'color:{c};text-shadow:0 0 {round(.08 * k, 3)}em {c},'
            f'0 0 {round(.25 * k, 3)}em {c},0 0 {round(.6 * k, 3)}em {_rgba(c, .7)}')


EFFECTS = {"shadow": _fx_shadow, "lift": _fx_lift, "hollow": _fx_hollow,
           "splice": _fx_splice, "echo": _fx_echo, "glitch": _fx_glitch,
           "neon": _fx_neon}

# Which knobs each effect actually uses — the editor shows only these, so no
# control is ever offered that does nothing.
EFFECT_PARAMS = {
    "shadow": ["offset", "direction", "blur", "alpha", "color"],
    "lift":   ["intensity"],
    "hollow": ["width", "color"],
    "splice": ["width", "offset", "direction", "color", "shadow"],
    "echo":   ["offset", "direction", "color"],
    "glitch": ["offset", "direction", "color", "shadow"],
    "neon":   ["intensity", "color"],
}

# --- legibility floors -------------------------------------------------------
#
# Text that is too small to read is the failure this engine kept shipping, and
# it kept shipping because the three places a size can be set are measured in
# three different units and only ONE of them is what a reader actually sees:
#
#   * CSS px on a page whose inches are real inches -> 1px = 0.75pt on paper.
#   * SVG user units in the page's INCH coordinate system (every chart) ->
#     1 unit = 72pt. A 0.07in floor is 5pt type. It read as "a small number".
#   * A screen zoom (.page{zoom:1.25}) that flatters everything on a desktop
#     and applies to neither the PDF nor a phone.
#
# So the floors are stated once, in POINTS — the unit the reader's eye is in —
# and every unit conversion hangs off them. Raising a floor here raises it for
# charts, for placed text, for the editor's stepper and for docsync.check.
PT_PER_IN = 72.0
PX_PER_PT = 96.0 / PT_PER_IN

# The hard floor is written in px because that is the unit the person setting
# it works in — 7.875pt rather than a round 8 so it lands on a step the
# editor's half-px stepper can actually reach. Enforcing a round 8pt would
# mean 10.67px, which fails documents already sitting on the smallest size the
# UI offers, for a twentieth of a point.
MIN_TEXT_PX = 10.5
MIN_TEXT_PT = MIN_TEXT_PX / PX_PER_PT     # nothing, anywhere, ever, below this
MIN_LABEL_PT = 10.0          # a chart's primary labels: axis ticks, legend
MIN_SUBLABEL_PT = 9.0        # labels DERIVED from those (bar values, slices)

# The chart floors in the inches a chart is actually drawn in.
MIN_LABEL_IN = MIN_LABEL_PT / PT_PER_IN            # 0.1389in
MIN_SUBLABEL_IN = MIN_SUBLABEL_PT / PT_PER_IN      # 0.125in


def _lfs(size_in: float) -> float:
    """A chart label's font-size in inches, floored at the legibility minimum.

    Every derived label in this module used to be a bare multiple of `fs`
    (x0.75, x0.78, x0.8...). Those multipliers are still what sets the visual
    hierarchy — they just cannot take a label below the point where the reader
    stops being able to read it. Wrapping the multiplication rather than
    shrinking the multipliers keeps a large chart looking exactly as it did
    and only bites on the small ones, which are the ones that were broken.
    """
    return max(float(size_in), MIN_SUBLABEL_IN)


ALIGNS = ("left", "center", "right", "justify")
CASES = ("none", "upper", "lower", "title")


def _hex(v, where: str) -> str:
    if not isinstance(v, str) or not re.fullmatch(r"#[0-9a-fA-F]{3,8}", v):
        raise LayoutError(f"{where}: {v!r} is not a hex colour")
    return v


def text_css(st: dict) -> str:
    """One text style -> the CSS declarations it means.

    A module function, not a method, because the editor calls it through Pyodide
    to preview a slider without a full re-render. One implementation of what a
    style means; a JavaScript twin would drift from this one silently and
    forever.

    Returns "" for an empty style, which is what keeps an unstyled report
    byte-identical to the one that shipped before any of this existed.
    """
    if not st:
        return ""
    out = []
    if st.get("font"):
        # Single quotes: this lands inside style="…", so a double quote here
        # would end the attribute and the rest of the style would become stray
        # markup. Family names are allowlisted, so no apostrophe can appear.
        out.append(f"font-family:'{st['font']}'")
    if st.get("size"):
        # validate_style() already refuses anything below the floor, so this
        # only catches a style that reached CSS without being validated —
        # a Pyodide live-preview of a slider mid-drag, say. Clamping (rather
        # than raising) is right HERE: a preview must not throw.
        out.append(f'font-size:{max(float(st["size"]), MIN_TEXT_PX):g}px')
    if st.get("weight"):
        out.append(f'font-weight:{int(st["weight"])}')
    if st.get("italic"):
        out.append("font-style:italic")
    if st.get("underline"):
        out.append("text-decoration:underline")
    if st.get("color"):
        out.append(f'color:{st["color"]}')
    if st.get("tracking") is not None:
        out.append(f'letter-spacing:{st["tracking"]}px')
    if st.get("leading") is not None:
        out.append(f'line-height:{st["leading"]}')
    case = st.get("case")
    if case and case != "none":
        out.append("text-transform:" + {"upper": "uppercase", "lower": "lowercase",
                                        "title": "capitalize"}[case])
    fx = st.get("effect")
    if fx and fx.get("kind"):
        # After colour, deliberately: hollow and splice hollow the glyph out, so
        # they must win over a colour the same style also set.
        out.append(EFFECTS[fx["kind"]](fx))
    if st.get("align"):
        out.append(f'text-align:{st["align"]}')
        # text-align does nothing to an inline box, and the inline slots are
        # spans. Give it a box to align within — but only when alignment was
        # actually asked for, so nothing else grows a width it never had.
        out.append("display:inline-block;width:100%")
    return ";".join(out)


# Everything a named style may hold beyond the style keys themselves.
TEXT_STYLE_META = ("from",)
# Object styles — the same idea for the things that are not text. A style
# names a `kind`, because a shape, a text box and a table are three key sets
# and not one: "the same machinery pointed at a different map" turned out to
# be three maps, and a style that could be worn by any of them would be a
# style that could say nothing checkable. `kind` and `from` are bookkeeping,
# never CSS, so they are stripped when a style is resolved.
OBJECT_STYLE_META = ("from", "kind")
OBJECT_KINDS = ("shape", "box", "table")
# What a style of each kind may set — and, by omission, what it may not: never
# geometry (x/y/w/h/rot/z), never identity (id, page), never content (md, rows,
# a chart's data). A style is a look. Everything here is opt-in and validated
# by the same checker the object itself goes through, so wearing a style can
# never smuggle in a value the object could not have carried directly.
OBJECT_STYLE_KEYS = {
    "shape": ("fill", "stroke", "sw", "dash", "r", "ends", "alpha", "shadow",
              "blend"),
    "box": ("fill", "pad", "radius", "border", "alpha", "shadow", "style",
            "blend", "cols", "gap"),
    "table": ("border", "fill", "band", "headerFill", "headerColor", "header",
              "alpha", "style", "blend"),
}
# mix-blend-mode, the six people reach for plus the two that matter for
# type over a photograph. "normal" is accepted so a style can UNDO a blend
# an object inherited from the style it comes from.
BLENDS = ("normal", "multiply", "screen", "overlay", "darken", "lighten",
          "difference", "luminosity")
# A text box's border: one rule on every edge, or a single rule on one edge —
# the left rule of a pull quote is the whole reason `sides` exists here.
BOX_BORDER_SIDES = ("all", "left", "right", "top", "bottom")


def resolve_style(st: dict, styles: dict, _seen=None,
                  meta=TEXT_STYLE_META) -> dict:
    """One style, with whatever it inherits already folded in.

    The rule, and it is the only one: **the nearer the author, the stronger.**
    A slot's own keys beat the style it uses; a style's own keys beat the one
    it comes `from`. So `use` is a starting point and never a cage — the thing
    that makes people actually adopt a stylesheet instead of working around it.

    A style that cannot be found, or a `from` chain that loops, resolves to
    what is left rather than raising: this runs in the render path and under
    Pyodide for a live preview, where throwing would blank the page. Load-time
    validation is where a bad name is refused (see _check_style_names).
    """
    if not st:
        return {}
    name = st.get("use")
    base = {}
    if name and isinstance(styles, dict) and name in styles:
        seen = set(_seen or ())
        if name not in seen:
            seen.add(name)
            base = resolve_style(dict(styles[name], use=styles[name].get("from")),
                                 styles, seen, meta)
    out = dict(base)
    for k, v in st.items():
        if k in ("use",) + tuple(meta):
            continue
        out[k] = v
    return out


def resolve_object(obj: dict, styles: dict) -> dict:
    """A shape, box or table with the object style it wears folded in.

    Same rule as text — the nearer the author, the stronger — with one more
    line: a box's `style` (its TEXT style) is itself a dict, and an object
    style that sets `style: {use: "Body"}` under a box that says
    `style: {size: 14}` should give a 14px Body, not lose Body. So that one
    key merges one level down; every other key is whole.

    An object wearing nothing comes back AS IT IS — the same dict, not a copy
    — so an untouched layout renders through exactly the object it always
    did and the byte-for-byte promise costs nothing to keep.
    """
    if not obj or not obj.get("use"):
        return obj
    base = resolve_style({"use": obj["use"]}, styles, meta=OBJECT_STYLE_META)
    out = dict(base)
    for k, v in obj.items():
        if k == "use":
            continue
        if k == "style" and isinstance(v, dict) and isinstance(base.get("style"), dict):
            out[k] = {**base["style"], **v}
        else:
            out[k] = v
    return out


def _check_text(st: dict, where: str) -> None:
    """A bad style must fail here, at load, like a bad layer does — not reach
    the page as a silently ignored declaration."""
    fam = st.get("font")
    if fam is not None:
        if fam not in FONTS:
            raise LayoutError(
                f"{where}: {fam!r} is not a font this report can load. "
                f"One of: {', '.join(sorted(FONTS))}")
        w = st.get("weight")
        if w is not None and int(w) not in FONTS[fam]:
            raise LayoutError(
                f"{where}: {fam} has no weight {w} — it would be faked by the "
                f"browser. One of: {FONTS[fam]}")
    if st.get("align") and st["align"] not in ALIGNS:
        raise LayoutError(f"{where}: align {st['align']!r} must be one of "
                          f"{', '.join(ALIGNS)}")
    if st.get("case") and st["case"] not in CASES:
        raise LayoutError(f"{where}: case {st['case']!r} must be one of "
                          f"{', '.join(CASES)}")
    if st.get("color"):
        _hex(st["color"], where + ".color")
    for k in ("size", "tracking", "leading"):
        if st.get(k) is not None:
            _num(st[k], f"{where}.{k}")
    # Refused, not clamped. A style is authored by a person — through the
    # editor's stepper or a pilot verb — and silently enlarging what they
    # asked for teaches them nothing, while silently HONOURING it ships type
    # no reader can read. Saying no names the floor and the unit it is in.
    if st.get("size") is not None and float(st["size"]) < MIN_TEXT_PX:
        raise LayoutError(
            f"{where}.size: {float(st['size']):g}px is "
            f"{float(st['size']) * 0.75:.1f}pt in print — below the {MIN_TEXT_PT:g}pt "
            f"legibility floor. The smallest size this engine will set is "
            f"{MIN_TEXT_PX:g}px.")
    # `is not None`, not truthiness: an empty effect object is falsy, so a bare
    # "effect": {} would skip every check below and pass as a no-op rather than
    # as the malformed thing it is.
    fx = st.get("effect")
    if fx is not None:
        if not isinstance(fx, dict) or not fx.get("kind"):
            raise LayoutError(f"{where}: effect needs a 'kind'")
        if fx["kind"] not in EFFECTS:
            raise LayoutError(f"{where}: effect {fx['kind']!r} must be one of "
                              f"{', '.join(sorted(EFFECTS))}")
        for c in ("color", "shadow"):
            if fx.get(c):
                _hex(fx[c], f"{where}.effect.{c}")
        a = fx.get("alpha")
        if a is not None and not (0 <= float(a) <= 1):
            raise LayoutError(f"{where}: effect alpha {a} is not a fraction "
                              f"between 0 and 1")
        for k in ("offset", "direction", "blur", "intensity", "width"):
            if fx.get(k) is not None:
                _num(fx[k], f"{where}.effect.{k}")


# Polygon kinds, as point lists inside the x/y/w/h frame every shape shares.
# The editor mirrors these two functions in JavaScript for live drag preview
# (a Pyodide call per mousemove is not a 60fps neighbour) — change one, change
# both, or the preview will visibly disagree with the committed page.
def triangle_points(x, y, w, h):
    return [(x + w / 2, y), (x + w, y + h), (x, y + h)]


def arrow_points(x, y, w, h):
    xs, hh = x + w * 0.62, h / 2
    return [(x, y + h * 0.28), (xs, y + h * 0.28), (xs, y),
            (x + w, y + hh), (xs, y + h), (xs, y + h * 0.72), (x, y + h * 0.72)]


def _pts(pts):
    return " ".join(f"{round(px, 4):g},{round(py, 4):g}" for px, py in pts)


class LayoutError(RuntimeError):
    """Raised when an override could not produce a sane page."""


def _z(s: dict) -> int:
    """Layer of a shape. Accepts the old back/front words so existing files
    keep working, but everything speaks integers now."""
    z = s.get("z", -1)
    if z == "back":
        return -1
    if z == "front":
        return 2
    try:
        return int(z)
    except (TypeError, ValueError):
        raise LayoutError(f"shape '{s.get('id')}': z {z!r} is not a layer number")


# Entrance animations. An allowlist like `act`: a kind lands in the published
# page as behaviour, so an unknown one must be a loud error here rather than
# an element that quietly never appears.
#
# The vocabulary follows AOS (github.com/michalsnik/aos, MIT) — fade / fade-up
# / fade-down / slide / zoom — because it is the settled naming for exactly
# this data-attribute + observer + keyframes shape. The keyframes themselves
# are written here, in translate/scale form (see _anim_block for why).
# Animate.css was considered and rejected: it relicensed from MIT to the
# Hippocratic License, which is not open source and travels with anything
# vendored from it.
ANIM_KINDS = ("fade", "rise", "drop", "slide-left", "slide-right", "grow", "pop",
              "bars")

# Kinds that animate an element's PARTS rather than the element. The whole
# chart stays put and visible — its bars grow out of the axis, one after the
# next — so these deliberately do NOT take the opacity:0 wait state the others
# use, and they only mean anything on the thing that has those parts.
ANIM_PART_KINDS = ("bars",)
# What each part kind needs to be drawn on, for the refusal message.
ANIM_PART_HOSTS = {"bars": "a chart"}


def _anim_check(a, where: str, host: str = "") -> None:
    if not isinstance(a, dict):
        raise LayoutError(f"{where}: anim must be an object like "
                          '{"kind": "fade", "duration": 0.6, "delay": 0}')
    kind = a.get("kind")
    if kind not in ANIM_KINDS:
        raise LayoutError(f"{where}: anim kind {kind!r} — one of "
                          + ", ".join(ANIM_KINDS))
    # A part animation on something with no such parts would validate, render,
    # and then simply never happen — the reader waits for a reveal that has
    # nothing to reveal. Refuse it here, where the message can say what it
    # needed instead.
    if kind in ANIM_PART_KINDS and host != kind:
        raise LayoutError(f"{where}: anim kind {kind!r} only works on "
                          f"{ANIM_PART_HOSTS[kind]}")
    d = a.get("duration", 0.6)
    w = a.get("delay", 0)
    if not isinstance(d, (int, float)) or not 0.05 <= d <= 10:
        raise LayoutError(f"{where}: anim duration {d!r} — seconds, 0.05 to 10")
    if not isinstance(w, (int, float)) or not 0 <= w <= 10:
        raise LayoutError(f"{where}: anim delay {w!r} — seconds, 0 to 10")


def anim_attrs(a) -> str:
    """The data attributes an animated element carries — in BOTH modes.

    The editor needs them too: presentation mode replays a slide's entrances
    from exactly these, and stamping them everywhere costs the published page
    three data attributes. What differs by mode is the SCRIPT (publish only,
    where it applies the initial hidden state) — never the markup.
    """
    if not a:
        return ""
    return (f' data-ds-anim="{a["kind"]}"'
            f' data-ds-ad="{a.get("duration", 0.6):g}"'
            f' data-ds-aw="{a.get("delay", 0):g}"')


def _num(v, where: str) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        raise LayoutError(f"{where}: {v!r} is not a number")


def _alpha(v, where: str) -> float:
    a = _num(v, where)
    if not 0 <= a <= 1:
        raise LayoutError(f"{where}: {v} is not a fraction between 0 and 1")
    return a


# ---- fills: a solid hex, or a gradient ----------------------------------
# A fill value is EITHER a hex string (unchanged — the byte-identity case) or a
# gradient {type:"linear"|"radial", angle, stops:[{color, at}]}. Three helpers
# turn that one value into the three forms the report needs — a CSS background,
# an SVG paint (with its <defs>), and one representative colour for the contrast
# test. All are module functions so the renderer, the tests, and the editor
# (through Pyodide) share ONE definition; a JavaScript twin would drift.

def _split_hex(hexc: str) -> tuple[str, float]:
    """A #hex (3/4/6/8 digits) -> ('#rrggbb', alpha 0..1). SVG stops carry colour
    and opacity separately, so an 8-digit fill has to be split."""
    h = hexc.lstrip("#")
    if len(h) in (3, 4):
        h = "".join(c * 2 for c in h)
    a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
    return "#" + h[:6], round(a, 4)


def _fill(v, where: str):
    """Validate a fill value — a hex or a gradient — and return it unchanged."""
    if isinstance(v, str):
        return _hex(v, where)
    if isinstance(v, dict):
        if v.get("type") not in ("linear", "radial"):
            raise LayoutError(f"{where}: gradient 'type' must be 'linear' or 'radial'")
        if v["type"] == "linear" and v.get("angle") is not None:
            _num(v["angle"], f"{where}.angle")
        stops = v.get("stops")
        if not isinstance(stops, list) or len(stops) < 2:
            raise LayoutError(f"{where}: a gradient needs two or more stops")
        for j, st in enumerate(stops):
            if not isinstance(st, dict):
                raise LayoutError(f"{where}.stops[{j}]: expected a {{color, at}} object")
            _hex(st.get("color"), f"{where}.stops[{j}].color")
            _alpha(st.get("at"), f"{where}.stops[{j}].at")
        return v
    raise LayoutError(f"{where}: {v!r} is not a hex colour or a gradient")


def _grad_vector(angle: float) -> tuple:
    """CSS angle (0deg = up, clockwise) -> SVG objectBoundingBox x1,y1,x2,y2, the
    last stop sitting toward the angle. y runs downward in SVG, hence -cos."""
    rad = math.radians(angle)
    dx, dy = math.sin(rad), -math.cos(rad)
    return (round(.5 - .5 * dx, 4), round(.5 - .5 * dy, 4),
            round(.5 + .5 * dx, 4), round(.5 + .5 * dy, 4))


def fill_css(v) -> str:
    """A fill value -> a CSS background value. A hex passes through verbatim, so a
    solid fill emits exactly the bytes it did before."""
    if not isinstance(v, dict):
        return v
    stops = ", ".join(f'{s["color"]} {round(s["at"] * 100, 3):g}%' for s in v["stops"])
    if v["type"] == "radial":
        return f"radial-gradient(circle, {stops})"
    return f'linear-gradient({_num(v.get("angle", 0), "angle"):g}deg, {stops})'


def fill_svg_paint(v, defid: str) -> tuple:
    """A fill value -> (SVG paint, defs). A hex/None is (itself, '') — no defs,
    so a solid shape is byte-identical."""
    if not isinstance(v, dict):
        return (v if v is not None else "none"), ""
    body = ""
    for s in v["stops"]:
        six, a = _split_hex(s["color"])
        body += (f'<stop offset="{round(s["at"] * 100, 3):g}%" stop-color="{six}"'
                 f' stop-opacity="{a:g}"/>')
    if v["type"] == "radial":
        defs = f'<radialGradient id="{defid}" cx="0.5" cy="0.5" r="0.5">{body}</radialGradient>'
    else:
        x1, y1, x2, y2 = _grad_vector(v.get("angle", 0))
        defs = (f'<linearGradient id="{defid}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}">'
                f'{body}</linearGradient>')
    return f"url(#{defid})", defs


def fill_repr(v) -> str:
    """A fill value -> one #rrggbb for the contrast test. A gradient averages its
    stops, each composited over white by its own alpha so a translucent stop reads
    like it looks. A hex passes straight to is_light_bg, which composites itself."""
    if not isinstance(v, dict):
        return v
    rs = gs = bs = 0.0
    for s in v["stops"]:
        six, a = _split_hex(s["color"])
        r, g, b = (int(six[i:i + 2], 16) for i in (1, 3, 5))
        rs += r * a + 255 * (1 - a)
        gs += g * a + 255 * (1 - a)
        bs += b * a + 255 * (1 - a)
    n = len(v["stops"])
    return "#" + "".join(f"{round(c / n):02x}" for c in (rs, gs, bs))


def _chart_series(c: dict) -> list:
    """Normalised series: every one has a name, a data list and a colour —
    and, since the combo pass, what it is DRAWN as and which axis it is drawn
    against. Both default to what every series was before ("bar" for the bar
    family, the left axis), so a spec that names neither is unchanged."""
    out = []
    for i, s in enumerate(c.get("series") or []):
        kind = s.get("type")
        out.append({
            "name": s.get("name") or f"Series {i + 1}",
            "data": [float(v or 0) for v in (s.get("data") or [])],
            "color": s.get("color") or CHART_COLORS[i % len(CHART_COLORS)],
            # Per-point colour. A series colour says "this measure"; a point
            # colour says "this one bar" — the highlight that used to mean
            # leaving the engine and drawing the figure by hand.
            "colors": list(s.get("colors") or []),
            "kind": kind if kind in CHART_SERIES_KINDS else "bar",
            "axis": "right" if s.get("axis") == "right" else "left",
        })
    return out


def _point_color(c: dict, s: dict, gi: int, nseries: int) -> str:
    """The fill for ONE bar. A series' own `colors[]` wins; a single-series
    chart also reads the chart-level `colors[]`, which is what a pie has
    always meant by that key — one colour per item. Everything else is the
    series colour, so a chart that sets neither draws exactly as before."""
    cols = s.get("colors") or []
    if gi < len(cols) and cols[gi]:
        return cols[gi]
    if nseries == 1:
        top = c.get("colors") or []
        if gi < len(top) and top[gi]:
            return top[gi]
    return s["color"]


def _ch_hook(c: dict, what: str) -> str:
    """In edit mode every piece of chart TEXT carries what it stands for, so
    the editor can open the right field when one is double-clicked on the page
    — the labels are edited where they are read, not only in a side panel."""
    if not os.environ.get("DOCSYNC_EDIT"):
        return ""
    return f' data-ch="{what}" style="cursor:text"'


def bar_anim_attrs(anim, i: int) -> str:
    """Per-bar timing for the 'bars' entrance, baked at render time.

    The reveal script only toggles a class on the CHART; the bars take their
    own duration and delay from here, so each one starts a beat after the last
    and the row reads left to right instead of arriving all at once. Baked
    rather than set from script because the stagger is per-bar and the script
    has one element to talk to.
    """
    if not anim or anim.get("kind") != "bars":
        return ""
    dur = anim.get("duration", 0.6)
    # The whole row still finishes in about `duration`: the step shrinks as
    # bars multiply, so a 20-bar chart does not take twenty times as long.
    step = min(0.09, dur / 8)
    return (f' style="animation-duration:{dur:g}s;'
            f'animation-delay:{anim.get("delay", 0) + i * step:.3f}s"')


def chart_svg(c: dict, x: float, y: float, w: float, h: float,
              anim=None) -> str:
    """A chart as SVG, in the page's inch coordinates. Every length here is an
    inch, so the drawing scales with the box the way any other shape does.

    `anim` is the SHAPE's entrance, passed in because a part animation ('bars')
    is drawn INTO the parts — the chart data has no idea it is being animated.
    """
    kind = c.get("type", "bar")
    labels = [str(v) for v in (c.get("labels") or [])]
    series = _chart_series(c)
    title = c.get("title") or ""
    # Every ink colour is overridable; these are the defaults the report has
    # always drawn with, so a chart that sets none looks exactly as before.
    c_title = c.get("titleColor") or "#2F3E46"
    c_label = c.get("labelColor") or "#52796F"
    c_axis = c.get("axisColor") or "#7A8E92"
    c_grid = c.get("gridColor") or "#E4EBE6"
    # Label size tracks the box, but never below the point where a reader
    # stops being able to read it. The old floor was 0.07in — five-point type
    # on paper, and the sub-label multipliers took it to under four. The cap
    # rises with the floor: 0.13in sat BELOW MIN_LABEL_IN, so a floored size
    # would have been clamped straight back down by its own ceiling.
    fs = max(MIN_LABEL_IN, min(0.2, h * 0.055))
    parts = []
    top = y
    if title:
        parts.append(f'<text x="{x + w / 2:.4f}" y="{y + fs * 1.1:.4f}" '
                     f'text-anchor="middle" font-size="{_lfs(fs * 1.25):.4f}" '
                     f'font-weight="700" fill="{c_title}"'
                     f'{_ch_hook(c, "title")}>{_xml(title)}</text>')
        top = y + fs * 2.0
    legend = bool(c.get("legend")) and len(series) > 0
    pos = _legend_pos(c)
    keys = _legend_keys(c, kind, labels, series) if legend else []
    legend = legend and bool(keys)   # a series with no data names nothing
    lg_w = lg_h = 0.0
    if legend:
        if pos in ("left", "right"):
            lg_w = min(w * 0.36, max(_legend_w(k, fs) for k in keys))
        else:
            lg_h = len(_legend_rows(keys, w, fs)[0]) * fs * 1.9

    # Axis titles are carved out HERE rather than inside each plot routine:
    # the body is simply handed a smaller box, so "Millions of dollars" costs
    # one strip of the drawing and no change to four different pad
    # calculations. A chart that names neither is handed the box it always was.
    horizontal = kind in ("row", "column", "stacked-row")
    axis_t = str(c.get("axisTitle") or "") if kind in CHART_AXIS_TYPES else ""
    cat_t = str(c.get("catTitle") or "") if kind in CHART_AXIS_TYPES else ""
    t_left = fs * 1.35 if (cat_t if horizontal else axis_t) else 0.0
    t_bot = fs * 1.35 if (axis_t if horizontal else cat_t) else 0.0

    body_x = x + lg_w if pos == "left" else x
    body_w = max(0.2, w - lg_w)
    body_top = top + (lg_h if pos == "top" else 0.0)
    body_h = max(0.2, (y + h) - body_top - (lg_h if pos == "bottom" else 0.0))

    ink = {"label": c_label, "axis": c_axis, "grid": c_grid}
    args = (c, kind, labels, series, body_x + t_left, body_top,
            body_w - t_left, body_h - t_bot, fs, ink)
    # Only the bar family has bars to grow; every other chart type takes a
    # whole-element entrance instead, which needs nothing drawn into it.
    bar_anim = anim if kind not in ("pie", "donut", "line", "area",
                                    "stacked-area", "scatter", "radar",
                                    "funnel", "packed", "treemap") else None
    if kind in ("pie", "donut"):
        parts.append(_pie_svg(*args))
    elif kind in ("area", "stacked-area"):
        parts.append(_area_svg(*args))
    elif kind in ("line", "scatter"):
        parts.append(_xy_svg(*args))
    elif kind == "radar":
        parts.append(_radar_svg(*args))
    elif kind == "funnel":
        parts.append(_funnel_svg(*args))
    elif kind == "packed":
        parts.append(_packed_svg(*args))
    elif kind == "treemap":
        parts.append(_treemap_svg(*args))
    elif kind == "histogram":
        parts.append(_histogram_svg(*args, anim=bar_anim))
    else:
        parts.append(_bars_svg(*args, anim=bar_anim))

    if axis_t or cat_t:
        parts.append(_axis_titles_svg(
            c, axis_t, cat_t, horizontal, body_x, body_top,
            body_w, body_h, t_left, t_bot, fs, c_label))
    if legend:
        parts.append(_legend_svg(c, keys, pos, x, y, w, h, top, lg_w, lg_h,
                                 body_top, body_h, fs, c_label))
    return "".join(parts)


def _axis_titles_svg(c, axis_t, cat_t, horizontal, bx, by, bw, bh,
                     t_left, t_bot, fs, col) -> str:
    """"Millions of dollars" down the side, "Fiscal year" along the foot.

    Until now the only way to say either was a floating text box, which did
    not move when the chart moved and did not rotate at all.
    """
    parts = []
    sz = _lfs(fs * 0.85)
    left_t = cat_t if horizontal else axis_t
    bot_t = axis_t if horizontal else cat_t
    if left_t:
        # Rotated about its own centre, reading upwards, which is how every
        # value axis in print is set.
        cx, cy = bx + fs * 0.75, by + (bh - t_bot) / 2
        parts.append(f'<text x="{cx:.4f}" y="{cy:.4f}" text-anchor="middle" '
                     f'font-size="{sz:.4f}" fill="{col}" font-weight="600" '
                     f'transform="rotate(-90 {cx:.4f} {cy:.4f})"'
                     f'{_ch_hook(c, "axisTitle" if not horizontal else "catTitle")}'
                     f'>{_xml(left_t)}</text>')
    if bot_t:
        cx = bx + t_left + (bw - t_left) / 2
        parts.append(f'<text x="{cx:.4f}" y="{by + bh - fs * 0.25:.4f}" '
                     f'text-anchor="middle" font-size="{sz:.4f}" fill="{col}" '
                     f'font-weight="600"'
                     f'{_ch_hook(c, "catTitle" if not horizontal else "axisTitle")}'
                     f'>{_xml(bot_t)}</text>')
    return "".join(parts)


def _slice_color(c: dict, i: int) -> str:
    cols = c.get("colors") or []
    if i < len(cols) and cols[i]:
        return cols[i]
    return CHART_COLORS[i % len(CHART_COLORS)]


def _rules_svg(c, px, py, pw, ph, vmin, vmax, horizontal, fs, ink,
               fmt=None) -> str:
    """Reference lines across the value plane — "the FY26 level", "the
    inflation-adjusted line", a target.

    Drawn OVER the data and under the axis, because the point of one is to be
    read against the bars it crosses. A rule whose value falls off the scale
    is skipped rather than clamped: a line drawn at the top of the plot that
    does not mean the top of the plot is worse than no line.
    """
    rules = c.get("rules")
    if not rules:
        return ""
    span = (vmax - vmin) or 1.0
    out = []
    for r in rules:
        if not isinstance(r, dict):
            continue
        v = r.get("value")
        if v is None:
            continue
        t = (float(v) - vmin) / span
        if t < -0.0001 or t > 1.0001:
            continue
        col = r.get("color") or "#2F3E46"
        wdt = r.get("width")
        wdt = 0.012 if not isinstance(wdt, (int, float)) or isinstance(wdt, bool) \
            else max(0.002, min(0.08, float(wdt)))
        dash = "" if r.get("dash") is False else \
            f' stroke-dasharray="{fs * 0.34:.4f} {fs * 0.22:.4f}"'
        lab = r.get("label")
        if lab is True:
            lab = _fmt_val(float(v), fmt)
        lab = "" if lab in (None, False) else str(lab)
        lsz = _lfs(fs * 0.74)
        if horizontal:
            gx = px + pw * t
            out.append(f'<line x1="{gx:.4f}" y1="{py:.4f}" x2="{gx:.4f}" '
                       f'y2="{py + ph:.4f}" stroke="{col}" '
                       f'stroke-width="{wdt:.4f}"{dash}/>')
            if lab:
                # Inside the plot, hugging the line, at the top — the one
                # place on a horizontal chart that is never a bar's own row.
                out.append(f'<text x="{gx + fs * 0.16:.4f}" y="{py + lsz:.4f}" '
                           f'font-size="{lsz:.4f}" fill="{col}" '
                           f'font-weight="600">{_xml(lab)}</text>')
        else:
            gy = py + ph - ph * t
            out.append(f'<line x1="{px:.4f}" y1="{gy:.4f}" x2="{px + pw:.4f}" '
                       f'y2="{gy:.4f}" stroke="{col}" '
                       f'stroke-width="{wdt:.4f}"{dash}/>')
            if lab:
                out.append(f'<text x="{px + pw:.4f}" y="{gy - fs * 0.18:.4f}" '
                           f'text-anchor="end" font-size="{lsz:.4f}" '
                           f'fill="{col}" font-weight="600">{_xml(lab)}</text>')
    return "".join(out)


def _legend_keys(c, kind, labels, series) -> list:
    """What the legend names. A by-item chart's legend names the ITEMS
    (slices, bins, blocks); a bar or line legend names the series. Either way
    each entry is editable in place — a legend IS the label."""
    if kind in CHART_BY_ITEM and series:
        return [{"name": labels[i] if i < len(labels) else f"#{i + 1}",
                 "color": _slice_color(c, i), "hook": f"label:{i}"}
                for i in range(len(series[0]["data"]))]
    return [{"name": s["name"], "color": s["color"], "hook": f"series:{i}"}
            for i, s in enumerate(series)]


def _legend_pos(c) -> str:
    p = c.get("legendPos")
    return p if p in CHART_LEGEND_POS else "bottom"


def _legend_w(k, fs: float) -> float:
    """One entry's ink: the swatch, the gap the text starts after, the text
    itself at the 0.58em estimate, and a little air before the next one."""
    return fs + len(str(k["name"])) * _EM_W * _lfs(fs * 0.85) + fs * 0.4


def _legend_rows(keys, w: float, fs: float) -> list:
    """Entries packed into rows that fit `w`, or a single evenly-spread row
    when they already do.

    Returned as `(rows, spread)`: `spread` True means every entry fits in its
    even share of the width, which is how the legend has ALWAYS been laid out
    — and so is what has to keep being emitted, character for character, for
    every chart already in a published report. The packed layout fires only
    where the even spread would have run two names into each other, which is
    a collision, not a style.
    """
    gap = w / max(1, len(keys))
    if all(fs + len(str(k["name"])) * _EM_W * _lfs(fs * 0.85) <= gap
           for k in keys):
        return [list(keys)], True
    rows, cur, used = [], [], 0.0
    for k in keys:
        kw = _legend_w(k, fs)
        if cur and used + kw > w and len(rows) < 2:
            rows.append(cur)
            cur, used = [], 0.0
        cur.append(k)
        used += kw
    if cur:
        rows.append(cur)
    return rows, False


def _legend_svg(c, keys, pos, x, y, w, h, top, lg_w, lg_h, body_top, body_h,
                fs, c_label) -> str:
    """The legend, wherever it was asked to sit."""
    parts = []
    sw = fs * 0.72
    if pos in ("left", "right"):
        lx = x if pos == "left" else x + w - lg_w
        step = fs * 1.6
        ly = body_top + max(0.0, (body_h - step * len(keys)) / 2) + fs * 0.9
        for i, k in enumerate(keys):
            ky = ly + i * step
            parts.append(f'<rect x="{lx:.4f}" y="{ky - sw:.4f}" '
                         f'width="{sw:.4f}" height="{sw:.4f}" rx="{fs * 0.16:.4f}" '
                         f'fill="{k["color"]}"/>')
            parts.append(f'<text x="{lx + fs:.4f}" y="{ky - fs * 0.12:.4f}" '
                         f'font-size="{_lfs(fs * 0.85):.4f}" fill="{c_label}"'
                         f'{_ch_hook(c, k["hook"])}>{_xml(k["name"])}</text>')
        return "".join(parts)
    rows, spread = _legend_rows(keys, w, fs)
    # The LAST row keeps the baseline the single row has always had — written
    # as that expression, not as one derived from lg_h, so the arithmetic
    # cannot drift by a float bit and move every published legend.
    base = (top + fs * 1.4) if pos == "top" \
        else (y + h - fs * 0.5 - (len(rows) - 1) * fs * 1.9)
    for ri, row in enumerate(rows):
        ly = base + ri * fs * 1.9
        if spread:
            gap = w / max(1, len(row))
            xs = [x + i * gap for i in range(len(row))]
        else:
            total = sum(_legend_w(k, fs) for k in row)
            run = x + max(0.0, (w - total) / 2)
            xs = []
            for k in row:
                xs.append(run)
                run += _legend_w(k, fs)
        for kx, k in zip(xs, row):
            parts.append(f'<rect x="{kx:.4f}" y="{ly - fs * 0.75:.4f}" '
                         f'width="{sw:.4f}" height="{sw:.4f}" rx="{fs * 0.16:.4f}" '
                         f'fill="{k["color"]}"/>')
            parts.append(f'<text x="{kx + fs:.4f}" y="{ly - fs * 0.12:.4f}" '
                         f'font-size="{_lfs(fs * 0.85):.4f}" fill="{c_label}"'
                         f'{_ch_hook(c, k["hook"])}>{_xml(k["name"])}</text>')
    return "".join(parts)


def _stack_pct(series, n):
    """Each category's values rewritten as a share of that category's total.

    The 100%-stacked chart: the question is composition, not size, and every
    column reaching the same height is what makes the shares comparable
    across categories of wildly different totals. Copies the data rather than
    scaling it in place — the caller's spec is the document, and a render must
    not rewrite it."""
    out = [dict(s, data=list(s["data"])) for s in series]
    for i in range(n):
        tot = sum(abs(s["data"][i]) for s in out if i < len(s["data"]))
        if not tot:
            continue
        for s in out:
            if i < len(s["data"]):
                s["data"][i] = s["data"][i] / tot * 100.0
    return out


def _stack_totals(group, n):
    """The two running totals a stack needs: how far the positive half of
    each category reaches, and how far the negative half does."""
    tot = []
    for i in range(n):
        vals = [s["data"][i] if i < len(s["data"]) else 0 for s in group]
        tot.append(sum(v for v in vals if v > 0))
        tot.append(sum(v for v in vals if v < 0))
    return tot


def _axis2_bounds(c, vals):
    """The secondary axis. Reads its own min and max but shares the tick
    COUNT, because two scales drawn against one set of gridlines have to
    have the same number of divisions or neither can be read off them."""
    shim = {"axisMin": c.get("axis2Min"), "axisMax": c.get("axis2Max"),
            "axisTicks": c.get("axisTicks")}
    return _axis_bounds(shim, vals)


def _bar_gap(c) -> float:
    """The share of each category's slot the bars FILL — `barGap` is the share
    left empty, and this is its complement.

    Written so the default is the literal 0.78 the multiplier has always
    been, not 1 - 0.22: those are different floats, and the difference shows
    up in the fourth decimal of a bar's width."""
    g = c.get("barGap")
    if not isinstance(g, (int, float)) or isinstance(g, bool):
        return 0.78
    return 1.0 - max(0.0, min(0.9, float(g)))


def _label_angle(c) -> float:
    a = c.get("labelAngle")
    if not isinstance(a, (int, float)) or isinstance(a, bool):
        return 0.0
    return max(-90.0, min(90.0, float(a)))


def _rotated_label(name, cx, cy, size, angle, col, hook) -> str:
    """One category label turned on its side. A rotated label anchors at the
    END for a left lean and the START for a right one, so the text runs away
    from the tick it belongs to rather than across its neighbours."""
    anchor = "end" if angle < 0 else "start" if angle > 0 else "middle"
    return (f'<text x="{cx:.4f}" y="{cy:.4f}" text-anchor="{anchor}" '
            f'font-size="{size:.4f}" fill="{col}" '
            f'transform="rotate({angle:g} {cx:.4f} {cy:.4f})"{hook}>'
            f'{_xml(str(name).replace(chr(10), " "))}</text>')


def _bars_svg(c, kind, labels, series, x, y, w, h, fs, ink, anim=None) -> str:
    """'bar' = vertical columns, 'row' = horizontal bars ('column' is the old
    name for row), each also available STACKED. Stacking is the same geometry
    with the bars laid end to end instead of side by side, so it shares every
    axis, gridline and label decision rather than forking the whole routine.

    A series may also say it is drawn as a `line` (or an `area`) and read
    against a `right` axis — the combo chart, where nominal dollars are
    columns and the inflation-adjusted series is a line over them.
    """
    if not series:
        return ""
    n = max(len(labels), max((len(s["data"]) for s in series), default=0))
    if not n:
        return ""
    stacked = kind.startswith("stacked")
    horizontal = kind in ("row", "column", "stacked-row")
    if stacked and c.get("stackPct"):
        series = _stack_pct(series, n)
    # A horizontal chart's value axis runs sideways; a line drawn across it is
    # not a chart anyone draws, so a row chart keeps every series as bars.
    if horizontal:
        bars, over = list(series), []
    else:
        bars = [s for s in series if s["kind"] == "bar"]
        over = [s for s in series if s["kind"] != "bar"]
    two_ax = any(s["axis"] == "right" for s in series) and not horizontal

    bar_ids = {id(s) for s in bars}

    def _bounds(side):
        grp = [s for s in series if s["axis"] == side]
        if not grp:
            return None
        bs = [s for s in grp if id(s) in bar_ids]
        vals = _stack_totals(bs, n) if (stacked and bs) else \
            [v for s in bs for v in s["data"]]
        vals += [v for s in grp if id(s) not in bar_ids for v in s["data"]]
        return grp, vals

    left = _bounds("left")
    vmin, vmax, nticks = _axis_bounds(c, left[1] if left else [0])
    vmin2 = vmax2 = None
    if two_ax:
        right = _bounds("right")
        vmin2, vmax2, _ = _axis2_bounds(c, right[1] if right else [0])
    grid = c.get("grid") is not False
    show_vals = bool(c.get("values"))
    afmt = _num_format(c)
    lfmt = _num_format(c, "labelFormat")
    wrap_lines = _wrap_lines(c)
    angle = _label_angle(c)
    parts = []
    # Room for the tick labels along the value axis and the category names.
    pad_l = (fs * 2.6) if not horizontal else (fs * 3.4)
    pad_r = (fs * 2.6) if two_ax else (fs * 0.4)
    if horizontal and labels:
        # A row chart's names live in this gutter, anchored at its right edge,
        # and nothing wraps them: a name wider than 3.4 label-heights ran off
        # the LEFT of the drawing and lost its first letters. So the gutter is
        # measured from the names, up to a cap — past which the plot would be
        # all margin and the label shrinks to fit instead.
        _sz = _lfs(fs * 0.82)
        _widest = max((len(ln) for nm in labels
                       for ln in str(nm).split("\n")), default=0)
        pad_l = max(pad_l, min(w * 0.4, _widest * _EM_W * _sz / 0.9))
    # How deep the category band has to be — measured, not guessed. The
    # horizontal plot's width does not depend on pad_b, so the labels can be
    # laid out first and the band sized from the lines they ACTUALLY take:
    # counting only the breaks the author typed missed the ones wrapping adds,
    # and the extra line was drawn straight through the prose below.
    band = max(_label_lines(labels), wrap_lines)
    if not horizontal:
        probe_slot = max(0.1, w - pad_l - pad_r) / n
        base_sz = _lfs(fs * 0.82)
        band = max(band, max(
            (len(_cat_label(nm, probe_slot * 0.95, base_sz, wrap_lines)[1])
             for nm in labels if nm), default=1))
    pad_b = fs * (1.6 + 0.95 * (band - 1))
    rot_sz = _lfs(fs * 0.82)
    if angle and not horizontal:
        # A turned label is as deep as its own length leaning over, so the
        # band is measured off the longest name rather than off a line count.
        rad = math.radians(abs(angle))
        chars = max((len(str(nm).replace("\n", " ")) for nm in labels),
                    default=0)
        # The leftmost label leans back past its own tick, and the only room
        # it has to lean into is the tick-label gutter. Shrink until it fits,
        # never below the legibility floor — at which point it overflows,
        # which is still better than a label nobody can read.
        room = pad_l + max(0.1, w - pad_l - pad_r) / n / 2
        if chars and math.cos(rad) > 0.01:
            fit = room / (chars * _EM_W * math.cos(rad))
            rot_sz = _lfs(min(rot_sz, rot_sz * fit))
        longest = chars * _EM_W * rot_sz
        pad_b = fs * 0.8 + longest * math.sin(rad) + rot_sz * math.cos(rad)
    # A bar below zero hangs its value label under itself, which is exactly
    # where the category names are. Give that row its own line.
    if show_vals and not horizontal and not stacked and min(
            (v for s in series for v in s["data"]), default=0) < 0:
        pad_b += fs * 0.9
    px, py = x + pad_l, y
    pw, ph = max(0.1, w - pad_l - pad_r), max(0.1, h - pad_b)
    span = vmax - vmin
    # Where zero sits in the plot. With no negatives this IS the axis line, so
    # every all-positive chart draws exactly where it used to.
    zero_t = (0.0 - vmin) / span if span else 0.0
    zero_t = min(1.0, max(0.0, zero_t))
    zy = py + ph - ph * zero_t                 # vertical charts: the zero line
    zx = px + pw * zero_t                      # horizontal charts

    # gridlines + ticks
    if grid:
        ticklab = _tick_labels(vmin, vmax, nticks, afmt)
        lab2 = _tick_labels(vmin2, vmax2, nticks, _num_format(c, "axis2Format")) \
            if two_ax else None
        for i in range(nticks):
            t = i / (nticks - 1)
            if horizontal:
                gx = px + pw * t
                parts.append(f'<line x1="{gx:.4f}" y1="{py:.4f}" x2="{gx:.4f}" '
                             f'y2="{py + ph:.4f}" stroke="{ink["grid"]}" stroke-width="0.006"/>')
                parts.append(f'<text x="{gx:.4f}" y="{py + ph + fs:.4f}" text-anchor="middle" '
                             f'font-size="{_lfs(fs * 0.8):.4f}" fill="{ink["axis"]}">{ticklab[i]}</text>')
            else:
                gy = py + ph - ph * t
                parts.append(f'<line x1="{px:.4f}" y1="{gy:.4f}" x2="{px + pw:.4f}" '
                             f'y2="{gy:.4f}" stroke="{ink["grid"]}" stroke-width="0.006"/>')
                parts.append(f'<text x="{px - fs * 0.3:.4f}" y="{gy + fs * 0.3:.4f}" '
                             f'text-anchor="end" font-size="{_lfs(fs * 0.8):.4f}" '
                             f'fill="{ink["axis"]}">{ticklab[i]}</text>')
                if lab2 is not None:
                    parts.append(f'<text x="{px + pw + fs * 0.3:.4f}" '
                                 f'y="{gy + fs * 0.3:.4f}" '
                                 f'font-size="{_lfs(fs * 0.8):.4f}" '
                                 f'fill="{ink["axis"]}">{lab2[i]}</text>')

    slot = (ph if horizontal else pw) / n
    inner = slot * _bar_gap(c)
    bw = inner if stacked else inner / max(1, len(bars))
    nser = len(series)
    for gi in range(n):
        base = (py if horizontal else px) + gi * slot + (slot - inner) / 2
        name = labels[gi] if gi < len(labels) else ""
        up = 0.0                # how far the positive half of a stack has got
        down = 0.0              # and the negative half, in the other direction
        for si, s in enumerate(bars):
            v = s["data"][gi] if gi < len(s["data"]) else 0
            # The bar spans zero -> v, measured against the axis it is drawn
            # on. A negative value therefore grows the other way instead of
            # being clamped to nothing, which is what it used to do.
            frac = 0.0 if not span else abs(v) / span
            col = _point_color(c, s, gi, nser)
            tip = ""
            if c.get("tips"):
                who = name or f"#{gi + 1}"
                if s.get("name") and nser > 1:
                    who = f"{who} — {s['name']}"
                tip = _tip(c, f"{who}: {_fmt_val(v, lfmt)}")
            if horizontal:
                blen = pw * frac
                by = base if stacked else base + si * bw
                if stacked:
                    run = up if v >= 0 else down
                    bx0 = zx + (pw * run if v >= 0 else -pw * run - blen)
                else:
                    bx0 = zx if v >= 0 else zx - blen
                parts.append(f'<rect class="ds-cbar ds-cbar-x" x="{bx0:.4f}" '
                             f'y="{by:.4f}" width="{blen:.4f}" '
                             f'height="{bw * 0.86:.4f}" fill="{col}" '
                             f'rx="{min(0.02, bw * 0.2):.4f}"'
                             f'{bar_anim_attrs(anim, gi)}{tip}/>')
                if stacked:
                    if v >= 0:
                        up += frac
                    else:
                        down += frac
                if show_vals:
                    if stacked:
                        # Inside the segment, which is the only place a
                        # stacked label can go without colliding with the next
                        # one — and skipped when the segment is too thin to
                        # hold the type, rather than drawn over its neighbour.
                        if v and blen > fs * 1.8:
                            parts.append(f'<text x="{bx0 + blen / 2:.4f}" '
                                         f'y="{by + bw * 0.62:.4f}" text-anchor="middle" '
                                         f'font-size="{_lfs(fs * 0.7):.4f}" '
                                         f'fill="#fff">{_fmt_val(v, lfmt)}</text>')
                    else:
                        lx = (bx0 + blen + fs * 0.22) if v >= 0 else (bx0 - fs * 0.22)
                        anchor = ' text-anchor="end"' if v < 0 else ''
                        parts.append(f'<text x="{lx:.4f}" '
                                     f'y="{by + bw * 0.62:.4f}" font-size="{_lfs(fs * 0.78):.4f}"'
                                     f'{anchor} '
                                     f'fill="{ink["label"]}">{_fmt_val(v, lfmt)}</text>')
            else:
                bh = ph * frac
                bx = base if stacked else base + si * bw
                if stacked:
                    run = up if v >= 0 else down
                    by0 = (zy - bh - ph * run) if v >= 0 else (zy + ph * run)
                else:
                    by0 = (zy - bh) if v >= 0 else zy
                parts.append(f'<rect class="ds-cbar" x="{bx:.4f}" y="{by0:.4f}" '
                             f'width="{bw * 0.86:.4f}" height="{bh:.4f}" '
                             f'fill="{col}" rx="{min(0.02, bw * 0.2):.4f}"'
                             f'{bar_anim_attrs(anim, gi)}{tip}/>')
                if stacked:
                    if v >= 0:
                        up += frac
                    else:
                        down += frac
                if show_vals:
                    if stacked:
                        if v and bh > fs * 1.1:
                            parts.append(f'<text x="{bx + bw * 0.43:.4f}" '
                                         f'y="{by0 + bh / 2 + fs * 0.25:.4f}" text-anchor="middle" '
                                         f'font-size="{_lfs(fs * 0.7):.4f}" '
                                         f'fill="#fff">{_fmt_val(v, lfmt)}</text>')
                    else:
                        # A negative bar labels itself underneath — but a bar
                        # that reaches most of the way to the floor leaves no
                        # room there, and the label used to land on the
                        # category names. Held inside the plot instead: the
                        # one place it is always legible and never someone
                        # else's row.
                        ly = (by0 - fs * 0.22) if v >= 0 else min(
                            by0 + bh + fs * 0.78, py + ph - fs * 0.12)
                        parts.append(f'<text x="{bx + bw * 0.43:.4f}" '
                                     f'y="{ly:.4f}" text-anchor="middle" '
                                     f'font-size="{_lfs(fs * 0.78):.4f}" fill="{ink["label"]}">{_fmt_val(v, lfmt)}</text>')
        if name:
            # A label only asks for a different size once it is more than one
            # line — a single-line one keeps the size it always had, which is
            # what leaves every existing chart byte-for-byte unchanged.
            budget = (pad_l * 0.9) if horizontal else (slot * 0.95)
            # Same question the band measurement asked, same answer.
            csize, cparts = _cat_label(name, budget, _lfs(fs * 0.82),
                                       wrap_lines, fit_single=horizontal)
            if horizontal:
                parts.append(f'<text x="{px - fs * 0.3:.4f}" '
                             f'y="{base + inner / 2 + fs * 0.3:.4f}" text-anchor="end" '
                             f'font-size="{csize:.4f}" fill="{ink["label"]}"'
                             f'{_ch_hook(c, f"label:{gi}")}>'
                             f'{_lines_from(cparts, px - fs * 0.3, csize, center=True)}</text>')
            elif angle:
                parts.append(_rotated_label(
                    name, base + inner / 2, py + ph + fs * 0.72,
                    rot_sz, angle, ink["label"],
                    _ch_hook(c, f"label:{gi}")))
            else:
                parts.append(f'<text x="{base + inner / 2:.4f}" y="{py + ph + fs:.4f}" '
                             f'text-anchor="middle" font-size="{csize:.4f}" '
                             f'fill="{ink["label"]}"{_ch_hook(c, f"label:{gi}")}'
                             f'>{_lines_from(cparts, base + inner / 2, csize)}</text>')
    # Combo overlays, drawn over the bars they are read against. Each takes
    # the scale of the axis it named, which is the whole point of a secondary
    # axis: a line in percent over columns in dollars.
    for s in over:
        lo, hi = (vmin2, vmax2) if (two_ax and s["axis"] == "right") else (vmin, vmax)
        sp = (hi - lo) or 1.0
        pts = []
        for gi in range(n):
            v = s["data"][gi] if gi < len(s["data"]) else 0
            cx = px + slot * gi + slot / 2
            cy = py + ph - ph * ((v - lo) / sp)
            pts.append((cx, cy, v, gi))
        pl = " ".join(f"{a:.4f},{b:.4f}" for a, b, _, _ in pts)
        if s["kind"] == "area":
            zt = min(1.0, max(0.0, (0.0 - lo) / sp))
            fy = py + ph - ph * zt
            parts.append(f'<polygon points="{pts[0][0]:.4f},{fy:.4f} {pl} '
                         f'{pts[-1][0]:.4f},{fy:.4f}" fill="{s["color"]}" '
                         f'fill-opacity="0.35"/>')
        parts.append(f'<polyline points="{pl}" fill="none" '
                     f'stroke="{s["color"]}" stroke-width="0.022" '
                     f'stroke-linejoin="round" stroke-linecap="round"/>')
        for cx, cy, v, gi in pts:
            tip = ""
            if c.get("tips"):
                who = labels[gi] if gi < len(labels) and labels[gi] else f"#{gi + 1}"
                tip = _tip(c, f"{who} — {s['name']}: {_fmt_val(v, lfmt)}")
            parts.append(f'<circle cx="{cx:.4f}" cy="{cy:.4f}" '
                         f'r="{fs * 0.26:.4f}" fill="{s["color"]}"{tip}/>')
            if show_vals:
                parts.append(f'<text x="{cx:.4f}" y="{cy - fs * 0.45:.4f}" '
                             f'text-anchor="middle" font-size="{_lfs(fs * 0.75):.4f}" '
                             f'fill="{ink["label"]}">{_fmt_val(v, lfmt)}</text>')
    parts.append(_rules_svg(c, px, py, pw, ph, vmin, vmax, horizontal, fs,
                            ink, lfmt))
    # the axis itself, last so it sits over the gridlines. With negatives in
    # play it is the ZERO line, not the floor — a rule under the bars would be
    # drawn somewhere no bar starts from.
    if horizontal:
        parts.append(f'<line x1="{zx:.4f}" y1="{py:.4f}" x2="{zx:.4f}" y2="{py + ph:.4f}" '
                     f'stroke="{ink["axis"]}" stroke-width="0.008"/>')
    else:
        parts.append(f'<line x1="{px:.4f}" y1="{zy:.4f}" x2="{px + pw:.4f}" '
                     f'y2="{zy:.4f}" stroke="{ink["axis"]}" stroke-width="0.008"/>')
    return "".join(parts)


def _plot_frame(px, py, pw, ph, vmin, vmax, ink, fs, grid, xlabels=None,
                xmin=None, xmax=None, nticks=5, fmt=None, wrap_lines=1,
                hook_chart=None, xfmt=None, xspread=False) -> str:
    """Gridlines, ticks and the two axis rules — shared by every chart drawn in
    a value plane (line, scatter, histogram), so they cannot drift apart.

    The horizontal rule is drawn at ZERO rather than at the foot of the plot,
    which is the same thing whenever the data is all positive and the right
    thing when it is not."""
    parts = []
    span = (vmax - vmin) or 1.0
    zero_t = min(1.0, max(0.0, (0.0 - vmin) / span))
    zy = py + ph - ph * zero_t
    if grid:
        ticklab = _tick_labels(vmin, vmax, nticks, fmt)
        for i in range(nticks):
            t = i / (nticks - 1)
            gy = py + ph - ph * t
            parts.append(f'<line x1="{px:.4f}" y1="{gy:.4f}" x2="{px + pw:.4f}" '
                         f'y2="{gy:.4f}" stroke="{ink["grid"]}" stroke-width="0.006"/>')
            parts.append(f'<text x="{px - fs * 0.3:.4f}" y="{gy + fs * 0.3:.4f}" '
                         f'text-anchor="end" font-size="{_lfs(fs * 0.8):.4f}" '
                         f'fill="{ink["axis"]}">{ticklab[i]}</text>')
    if xlabels is not None:
        for i, name in enumerate(xlabels):
            if not name:
                continue
            # A bar's label sits over the middle of its slot; an area's sits
            # over the point it belongs to, which is on the plot's own edge at
            # each end. Same labels, two different things to line up with.
            anchor = "middle"
            if xspread and len(xlabels) > 1:
                gx = px + pw * i / (len(xlabels) - 1)
                slot = pw / (len(xlabels) - 1)
                # The first and last points ARE the plot's edges, so a label
                # centred on one hangs half its width outside the drawing and
                # loses that half. Tuck the two ends in instead.
                anchor = "start" if i == 0 else \
                    "end" if i == len(xlabels) - 1 else "middle"
            else:
                gx = px + (pw * (i + 0.5) / len(xlabels) if len(xlabels) else 0)
                slot = pw / max(1, len(xlabels))
            parts.append(f'<text x="{gx:.4f}" y="{py + ph + fs:.4f}" text-anchor="{anchor}" '
                         f'font-size="{_lfs(fs * 0.82):.4f}" fill="{ink["label"]}"'
                         f'{_ch_hook(hook_chart if hook_chart is not None else {}, f"label:{i}")}>'
                         f'{_lines_inner(name, gx, _lfs(fs * 0.82), wrap=slot * 0.95 if wrap_lines > 1 else 0, max_lines=wrap_lines)}</text>')
    elif xmin is not None:
        xlab = _tick_labels(xmin, xmax, 5, fmt if xfmt is None else xfmt)
        for i in range(5):
            t = i / 4
            gx = px + pw * t
            parts.append(f'<text x="{gx:.4f}" y="{py + ph + fs:.4f}" text-anchor="middle" '
                         f'font-size="{_lfs(fs * 0.8):.4f}" '
                         f'fill="{ink["axis"]}">{xlab[i]}</text>')
    parts.append(f'<line x1="{px:.4f}" y1="{zy:.4f}" x2="{px + pw:.4f}" '
                 f'y2="{zy:.4f}" stroke="{ink["axis"]}" stroke-width="0.008"/>')
    parts.append(f'<line x1="{px:.4f}" y1="{py:.4f}" x2="{px:.4f}" y2="{py + ph:.4f}" '
                 f'stroke="{ink["axis"]}" stroke-width="0.008"/>')
    return "".join(parts)


def _area_svg(c, kind, labels, series, x, y, w, h, fs, ink) -> str:
    """'area' fills each series down to the zero line; 'stacked-area' lays the
    series on top of one another so the top edge is the total.

    This is the shape `fig_obligated` is — and the reason that figure is
    forty-five lines of hand-written polygon geometry in the primer's
    renderer instead of data. The points span the plot edge to edge rather
    than sitting at slot centres, because an area chart is a continuous
    quantity over time and a gap at each end would say the series began after
    the axis did.
    """
    if not series:
        return ""
    n = max(len(labels), max((len(s["data"]) for s in series), default=0))
    if not n:
        return ""
    stacked = kind == "stacked-area"
    if stacked and c.get("stackPct"):
        series = _stack_pct(series, n)
    pad_l, pad_b = fs * 2.6, fs * 1.6
    px, py = x + pad_l, y
    pw, ph = max(0.1, w - pad_l - fs * 0.4), max(0.1, h - pad_b)
    afmt = _num_format(c)
    lfmt = _num_format(c, "labelFormat")
    vals = _stack_totals(series, n) if stacked else \
        [v for s in series for v in s["data"]]
    vmin, vmax, nticks = _axis_bounds(c, vals)
    span = (vmax - vmin) or 1.0
    parts = [_plot_frame(px, py, pw, ph, vmin, vmax, ink, fs,
                         c.get("grid") is not False,
                         xlabels=[labels[i] if i < len(labels) else ""
                                  for i in range(n)],
                         nticks=nticks, fmt=afmt, wrap_lines=_wrap_lines(c),
                         hook_chart=c, xspread=True)]
    xs = [px + (pw * i / (n - 1) if n > 1 else pw / 2) for i in range(n)]
    zy = py + ph - ph * min(1.0, max(0.0, (0.0 - vmin) / span))
    show_vals = bool(c.get("values"))
    run = [0.0] * n                      # the top of the stack so far
    for s in series:
        lower = list(run)
        tops = []
        for i in range(n):
            v = s["data"][i] if i < len(s["data"]) else 0
            run[i] = run[i] + v if stacked else v
            tops.append(run[i])
        ty = [py + ph - ph * ((v - vmin) / span) for v in tops]
        by = [py + ph - ph * ((v - vmin) / span) for v in lower] if stacked \
            else [zy] * n
        fwd = " ".join(f"{xs[i]:.4f},{ty[i]:.4f}" for i in range(n))
        back = " ".join(f"{xs[i]:.4f},{by[i]:.4f}" for i in range(n - 1, -1, -1))
        parts.append(f'<polygon points="{fwd} {back}" fill="{s["color"]}" '
                     f'fill-opacity="{0.9 if stacked else 0.35:.2f}"/>')
        parts.append(f'<polyline points="{fwd}" fill="none" '
                     f'stroke="{s["color"]}" stroke-width="0.022" '
                     f'stroke-linejoin="round" stroke-linecap="round"/>')
        for i in range(n):
            v = s["data"][i] if i < len(s["data"]) else 0
            if c.get("tips"):
                who = labels[i] if i < len(labels) and labels[i] else f"#{i + 1}"
                if len(series) > 1:
                    who = f"{who} — {s['name']}"
                # The band, not a point: an area chart has nothing small
                # enough to aim at, so the whole segment answers.
                parts.append(f'<rect x="{xs[i] - pw / max(1, n * 2):.4f}" '
                             f'y="{min(ty[i], by[i]):.4f}" '
                             f'width="{pw / max(1, n):.4f}" '
                             f'height="{abs(by[i] - ty[i]):.4f}" fill="transparent"'
                             f'{_tip(c, f"{who}: {_fmt_val(v, lfmt)}")}/>')
            if show_vals:
                parts.append(f'<text x="{xs[i]:.4f}" y="{ty[i] - fs * 0.35:.4f}" '
                             f'text-anchor="middle" '
                             f'font-size="{_lfs(fs * 0.72):.4f}" '
                             f'fill="{ink["label"]}">{_fmt_val(v, lfmt)}</text>')
    parts.append(_rules_svg(c, px, py, pw, ph, vmin, vmax, False, fs, ink, lfmt))
    return "".join(parts)


def _xy_svg(c, kind, labels, series, x, y, w, h, fs, ink) -> str:
    """'line' joins each series across the categories; 'scatter' plots points
    in a numeric plane. Scatter reads the FIRST TWO series as x and y — that
    is what a scatter plot is — and falls back to index-vs-value when only one
    was given, so switching type from a bar chart still shows something."""
    if not series:
        return ""
    pad_l, pad_b = fs * 2.6, fs * 1.6
    px, py = x + pad_l, y
    pw, ph = max(0.1, w - pad_l - fs * 0.4), max(0.1, h - pad_b)
    grid = c.get("grid") is not False
    parts = []

    afmt = _num_format(c)
    lfmt = _num_format(c, "labelFormat")
    wrap_lines = _wrap_lines(c)

    if kind == "scatter":
        if len(series) >= 2:
            xs, ys = series[0]["data"], series[1]["data"]
            col = series[1]["color"]
        else:
            xs = list(range(len(series[0]["data"])))
            ys = series[0]["data"]
            col = series[0]["color"]
        n = min(len(xs), len(ys))
        if not n:
            return ""
        xmin, xmax = min(xs[:n]), max(xs[:n])
        if xmax == xmin:
            xmax = xmin + 1
        ymin, ymax, nticks = _axis_bounds(c, ys[:n])
        yspan = (ymax - ymin) or 1.0
        parts.append(_plot_frame(px, py, pw, ph, ymin, ymax, ink, fs, grid,
                                 xmin=xmin, xmax=xmax, nticks=nticks, fmt=afmt))
        for i in range(n):
            cx = px + pw * (xs[i] - xmin) / (xmax - xmin)
            cy = py + ph - ph * ((ys[i] - ymin) / yspan)
            tip = _tip(c, f"{_fmt_val(xs[i], afmt)}, {_fmt_val(ys[i], lfmt)}") if c.get("tips") else ""
            parts.append(f'<circle cx="{cx:.4f}" cy="{cy:.4f}" r="{fs * 0.34:.4f}" '
                         f'fill="{col}" fill-opacity="0.85"{tip}/>')
        parts.append(_rules_svg(c, px, py, pw, ph, ymin, ymax, False, fs, ink,
                                lfmt))
        return "".join(parts)

    n = max(len(labels), max((len(s["data"]) for s in series), default=0))
    if not n:
        return ""
    vmin, vmax, nticks = _axis_bounds(c, [v for s in series for v in s["data"]])
    span = (vmax - vmin) or 1.0
    parts.append(_plot_frame(px, py, pw, ph, vmin, vmax, ink, fs, grid,
                             xlabels=[labels[i] if i < len(labels) else "" for i in range(n)],
                             nticks=nticks, fmt=afmt, wrap_lines=wrap_lines,
                             hook_chart=c))
    show_vals = bool(c.get("values"))
    for s in series:
        pts = []
        for i in range(n):
            v = s["data"][i] if i < len(s["data"]) else 0
            cx = px + pw * (i + 0.5) / n
            cy = py + ph - ph * ((v - vmin) / span)
            pts.append((cx, cy, v, i))
        parts.append(f'<polyline points="{" ".join(f"{a:.4f},{b:.4f}" for a, b, _, _ in pts)}" '
                     f'fill="none" stroke="{s["color"]}" stroke-width="0.022" '
                     f'stroke-linejoin="round" stroke-linecap="round"/>')
        for cx, cy, v, i in pts:
            tip = ""
            if c.get("tips"):
                who = labels[i] if i < len(labels) and labels[i] else f"#{i + 1}"
                if s.get("name") and len(series) > 1:
                    who = f"{who} — {s['name']}"
                tip = _tip(c, f"{who}: {_fmt_val(v, lfmt)}")
            parts.append(f'<circle cx="{cx:.4f}" cy="{cy:.4f}" r="{fs * 0.26:.4f}" '
                         f'fill="{s["color"]}"{tip}/>')
            if show_vals:
                parts.append(f'<text x="{cx:.4f}" y="{cy - fs * 0.45:.4f}" '
                             f'text-anchor="middle" font-size="{_lfs(fs * 0.75):.4f}" '
                             f'fill="{ink["label"]}">{_fmt_val(v, lfmt)}</text>')
    parts.append(_rules_svg(c, px, py, pw, ph, vmin, vmax, False, fs, ink, lfmt))
    return "".join(parts)


def _histogram_svg(c, kind, labels, series, x, y, w, h, fs, ink, anim=None) -> str:
    """Bins the FIRST series' raw numbers — a histogram's input is a list of
    observations, not one value per category, which is what separates it from
    a bar chart."""
    if not series or not series[0]["data"]:
        return ""
    vals = series[0]["data"]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi = lo + 1
    nb = max(3, min(10, int(len(vals) ** 0.5 + 0.5) or 3))
    step = (hi - lo) / nb
    counts = [0] * nb
    for v in vals:
        k = min(nb - 1, int((v - lo) / step))
        counts[k] += 1
    cmax = _nice_max(max(counts))
    # A histogram's value axis counts observations, so it is never negative
    # and never carries the chart's money format — that belongs to the BIN
    # axis, which is where the data's own numbers are shown.
    nticks = c.get("axisTicks")
    nticks = 5 if not isinstance(nticks, int) or isinstance(nticks, bool) \
        else max(2, min(11, nticks))
    afmt = _num_format(c)
    pad_l, pad_b = fs * 2.6, fs * 1.6
    px, py = x + pad_l, y
    pw, ph = max(0.1, w - pad_l - fs * 0.4), max(0.1, h - pad_b)
    parts = [_plot_frame(px, py, pw, ph, 0, cmax, ink, fs,
                         c.get("grid") is not False, xmin=lo, xmax=hi,
                         nticks=nticks, xfmt=afmt)]
    bw = pw / nb
    for i, ct in enumerate(counts):
        bh = ph * (ct / cmax if cmax else 0)
        tip = ""
        if c.get("tips"):
            tip = _tip(c, f"{_fmt_val(lo + i * step, afmt)}–"
                          f"{_fmt_val(lo + (i + 1) * step, afmt)}: {ct}")
        parts.append(f'<rect class="ds-cbar" x="{px + i * bw + bw * 0.06:.4f}" '
                     f'y="{py + ph - bh:.4f}" '
                     f'width="{bw * 0.88:.4f}" height="{bh:.4f}" '
                     f'fill="{_slice_color(c, i)}"{bar_anim_attrs(anim, i)}{tip}/>')
        if c.get("values") and ct:
            parts.append(f'<text x="{px + i * bw + bw / 2:.4f}" y="{py + ph - bh - fs * 0.22:.4f}" '
                         f'text-anchor="middle" font-size="{_lfs(fs * 0.75):.4f}" '
                         f'fill="{ink["label"]}">{ct}</text>')
    # A histogram's value axis counts observations, so a rule on it is drawn
    # against that count — not against the bin axis the money format belongs to.
    parts.append(_rules_svg(c, px, py, pw, ph, 0, cmax, False, fs, ink, None))
    return "".join(parts)


def _radar_svg(c, kind, labels, series, x, y, w, h, fs, ink) -> str:
    """One spoke per label, one closed polygon per series."""
    n = max(len(labels), max((len(s["data"]) for s in series), default=0))
    if n < 3 or not series:
        return ""
    cx, cy = x + w / 2, y + h / 2
    r = max(0.05, min(w, h) / 2 - fs * 1.5)
    vmax = _nice_max(max((v for s in series for v in s["data"]), default=0))
    ang = lambda i: -math.pi / 2 + 2 * math.pi * i / n
    parts = []
    if c.get("grid") is not False:
        for ring in range(1, 5):
            rr = r * ring / 4
            pts = " ".join(f"{cx + rr * math.cos(ang(i)):.4f},{cy + rr * math.sin(ang(i)):.4f}"
                           for i in range(n))
            parts.append(f'<polygon points="{pts}" fill="none" '
                         f'stroke="{ink["grid"]}" stroke-width="0.006"/>')
    for i in range(n):
        parts.append(f'<line x1="{cx:.4f}" y1="{cy:.4f}" '
                     f'x2="{cx + r * math.cos(ang(i)):.4f}" y2="{cy + r * math.sin(ang(i)):.4f}" '
                     f'stroke="{ink["grid"]}" stroke-width="0.006"/>')
    for s in series:
        pts = []
        for i in range(n):
            v = s["data"][i] if i < len(s["data"]) else 0
            rr = r * (v / vmax if vmax else 0)
            pts.append(f"{cx + rr * math.cos(ang(i)):.4f},{cy + rr * math.sin(ang(i)):.4f}")
        parts.append(f'<polygon points="{" ".join(pts)}" fill="{s["color"]}" '
                     f'fill-opacity="0.28" stroke="{s["color"]}" stroke-width="0.018"/>')
    for i in range(n):
        if i >= len(labels) or not labels[i]:
            continue
        lx, ly = cx + (r + fs * 0.7) * math.cos(ang(i)), cy + (r + fs * 0.7) * math.sin(ang(i))
        anchor = "middle" if abs(math.cos(ang(i))) < 0.3 else ("start" if math.cos(ang(i)) > 0 else "end")
        parts.append(f'<text x="{lx:.4f}" y="{ly + fs * 0.3:.4f}" text-anchor="{anchor}" '
                     f'font-size="{_lfs(fs * 0.8):.4f}" fill="{ink["label"]}"'
                     f'{_ch_hook(c, f"label:{i}")}>{_xml(labels[i])}</text>')
    return "".join(parts)


def _funnel_svg(c, kind, labels, series, x, y, w, h, fs, ink) -> str:
    """Stages narrowing top to bottom, each band's width its share of the
    largest — the first series only, since a funnel is one flow."""
    data = series[0]["data"] if series else []
    if not data:
        return ""
    top = max(data) or 1
    band = h / len(data)
    parts = []
    for i, v in enumerate(data):
        nxt = data[i + 1] if i + 1 < len(data) else v
        w0 = w * 0.86 * (v / top)
        w1 = w * 0.86 * (nxt / top)
        cx = x + w / 2
        y0, y1 = y + i * band, y + (i + 1) * band - band * 0.08
        parts.append(f'<polygon points="{cx - w0 / 2:.4f},{y0:.4f} {cx + w0 / 2:.4f},{y0:.4f} '
                     f'{cx + w1 / 2:.4f},{y1:.4f} {cx - w1 / 2:.4f},{y1:.4f}" '
                     f'fill="{_slice_color(c, i)}"/>')
        name = labels[i] if i < len(labels) else ""
        txt = f"{_xml(name)}" + (f"  {_fmt_num(v)}" if c.get("values") else "")
        if name or c.get("values"):
            parts.append(f'<text x="{cx:.4f}" y="{(y0 + y1) / 2 + fs * 0.3:.4f}" '
                         f'text-anchor="middle" font-size="{_lfs(fs * 0.8):.4f}" fill="#fff" '
                         f'font-weight="600"{_ch_hook(c, f"label:{i}")}>{txt}</text>')
    return "".join(parts)


def _packed_svg(c, kind, labels, series, x, y, w, h, fs, ink) -> str:
    """Circles sized by value. Laid out largest-first in rows rather than by a
    physics solver: deterministic output matters more here than perfect
    packing, because the same layout.json must render identically in the
    browser preview and the PDF."""
    data = series[0]["data"] if series else []
    if not data:
        return ""
    order = sorted(range(len(data)), key=lambda i: -data[i])
    total = sum(v for v in data if v > 0) or 1
    # AREA proportional to value: with r = k*sqrt(v), the total area is
    # pi*k^2*total, so k falls straight out of the share of the box to fill.
    # (An earlier version multiplied k by sqrt(pi) as well, which put the pi
    # back in and made the largest circle wider than the box — the loop then
    # broke on the first item and the chart rendered empty.)
    k = math.sqrt(w * h * 0.45 / (math.pi * total))
    # ...and no circle may exceed the box, however lopsided the data.
    cap = min(w, h) / 2
    circles = [(i, min(cap, max(fs * 0.4, math.sqrt(max(data[i], 0)) * k)))
               for i in order]
    parts = []
    cx, cy, row_h = x, y, 0.0
    for i, r in circles:
        if cx + 2 * r > x + w and cx > x:
            cx, cy, row_h = x, cy + row_h, 0.0
        if cy + 2 * r > y + h:
            break                                   # out of room; drop the tail
        parts.append(f'<circle cx="{cx + r:.4f}" cy="{cy + r:.4f}" r="{r:.4f}" '
                     f'fill="{_slice_color(c, i)}"/>')
        name = labels[i] if i < len(labels) else ""
        # Sized by the floor, not by the circle: a bubble too small to hold
        # legible type now goes UNLABELLED rather than carrying four-point
        # text nobody can read. `min(fs * 0.8, r * 0.5)` used to shrink the
        # label to fit, which is the same bug in a more considerate voice.
        lab = _lfs(fs * 0.8)
        if name and r > lab * 1.4:
            parts.append(f'<text x="{cx + r:.4f}" y="{cy + r + fs * 0.28:.4f}" '
                         f'text-anchor="middle" font-size="{lab:.4f}" '
                         f'fill="#fff" font-weight="600"'
                         f'{_ch_hook(c, f"label:{i}")}>{_xml(name)}</text>')
        cx += 2 * r
        row_h = max(row_h, 2 * r)
    return "".join(parts)


def _treemap_svg(c, kind, labels, series, x, y, w, h, fs, ink) -> str:
    """Squarified treemap: split the remaining strip along its shorter side so
    the blocks stay near-square, which is what makes areas comparable."""
    data = [(i, v) for i, v in enumerate(series[0]["data"] if series else []) if v > 0]
    if not data:
        return ""
    data.sort(key=lambda t: -t[1])
    total = sum(v for _, v in data)
    parts = []
    rx, ry, rw, rh = x, y, w, h
    rest = total
    idx = 0
    while idx < len(data) and rw > 0.01 and rh > 0.01:
        i, v = data[idx]
        share = v / rest if rest else 0
        if rw >= rh:                                 # cut a column off the left
            bw = rw * share
            bx, by, bh2 = rx, ry, rh
            bw2 = bw
            rx, rw = rx + bw, rw - bw
        else:                                        # cut a row off the top
            bh = rh * share
            bx, by, bw2 = rx, ry, rw
            bh2 = bh
            ry, rh = ry + bh, rh - bh
        parts.append(f'<rect x="{bx:.4f}" y="{by:.4f}" width="{max(0, bw2 - 0.012):.4f}" '
                     f'height="{max(0, bh2 - 0.012):.4f}" fill="{_slice_color(c, i)}" rx="0.02"/>')
        name = labels[i] if i < len(labels) else ""
        if name and bw2 > fs * 2 and bh2 > fs * 1.2:
            parts.append(f'<text x="{bx + fs * 0.35:.4f}" y="{by + fs * 1.0:.4f}" '
                         f'font-size="{_lfs(fs * 0.78):.4f}" fill="#fff" font-weight="600"'
                         f'{_ch_hook(c, f"label:{i}")}>{_xml(name)}</text>')
        rest -= v
        idx += 1
    return "".join(parts)


def _pie_svg(c, kind, labels, series, x, y, w, h, fs, ink) -> str:
    """One ring of slices from the FIRST series — a pie has no second series to
    show, so extra ones are ignored rather than silently overlaid."""
    data = series[0]["data"] if series else []
    total = sum(v for v in data if v > 0)
    if total <= 0:
        return ""
    cx, cy = x + w / 2, y + h / 2
    show_vals = bool(c.get("values"))
    lfmt = _num_format(c, "labelFormat")
    slice_label = c.get("sliceLabel") or "percent"
    # A pie with any thin slice hangs labels outside the rim, so the disc has
    # to give that ring back. Only then — a pie whose slices are all fat draws
    # exactly as large as it always did.
    thin = show_vals and any(
        0 < v and 2 * math.pi * (v / total) <= _PIE_INSIDE_SWEEP for v in data)
    margin = fs * 3.2 if thin else fs * 0.4
    r = max(0.05, min(w, h) / 2 - margin)
    inner = r * 0.58 if kind == "donut" else 0.0
    outside_n = 0
    parts = []
    ang = -math.pi / 2                                    # 12 o'clock, clockwise
    for i, v in enumerate(data):
        if v <= 0:
            continue
        sweep = 2 * math.pi * (v / total)
        a2 = ang + sweep
        big = 1 if sweep > math.pi else 0
        x1, y1 = cx + r * math.cos(ang), cy + r * math.sin(ang)
        x2, y2 = cx + r * math.cos(a2), cy + r * math.sin(a2)
        col_hex = _slice_color(c, i)
        if inner:
            i1x, i1y = cx + inner * math.cos(a2), cy + inner * math.sin(a2)
            i2x, i2y = cx + inner * math.cos(ang), cy + inner * math.sin(ang)
            d = (f'M{x1:.4f},{y1:.4f} A{r:.4f},{r:.4f} 0 {big} 1 {x2:.4f},{y2:.4f} '
                 f'L{i1x:.4f},{i1y:.4f} A{inner:.4f},{inner:.4f} 0 {big} 0 {i2x:.4f},{i2y:.4f} Z')
        else:
            d = (f'M{cx:.4f},{cy:.4f} L{x1:.4f},{y1:.4f} '
                 f'A{r:.4f},{r:.4f} 0 {big} 1 {x2:.4f},{y2:.4f} Z')
        name = labels[i] if i < len(labels) else f"#{i + 1}"
        pct = v / total * 100
        tip = _tip(c, f"{name}: {_fmt_val(v, lfmt)} ({pct:.1f}%)") if c.get("tips") else ""
        parts.append(f'<path d="{d}" fill="{col_hex}" stroke="#fff" stroke-width="0.01"{tip}/>')
        if show_vals:
            mid = ang + sweep / 2
            # A wedge wide enough to hold its label keeps it inside; a thin one
            # puts it beyond the rim, anchored away from the circle and
            # staggered against its neighbour. Cramming "$0.9B (9.9%)" into a
            # 36-degree slice is what made the primer's three pies hand-drawn:
            # the labels overlapped each other and ran off their own wedges.
            # What a slice says about itself. Percent alone is the default and
            # what every pie drew before; "value" and "both" exist because a
            # money pie that cannot show the money has to be redrawn by hand —
            # which is exactly what the primer's own figures did.
            if slice_label == "value":
                txt = _fmt_val(v, lfmt)
            elif slice_label == "both":
                txt = f"{_fmt_val(v, lfmt)}\n({pct:.1f}%)"
            else:
                txt = f"{pct:.0f}%"
            big = sweep > _PIE_INSIDE_SWEEP
            if big:
                lr = (inner + r) / 2 if inner else r * 0.62
            else:
                # Pushed out by the label's own half-height as well as the
                # stagger: a two-line label centred on the anchor hangs its
                # second line back over the rim otherwise.
                half = fs * 0.46 * txt.count("\n")
                lr = r + fs * (0.8 + 0.9 * (outside_n % 2)) + half
                outside_n += 1
            tx, ty = cx + lr * math.cos(mid), cy + lr * math.sin(mid)
            # Inside a slice the label is reversed out — unless the slice is
            # pale, where white on it cannot be read. Outside, it is ordinary
            # label ink on the page.
            if big:
                anchor, col = "middle", ("#2F3E46" if _is_light(col_hex) else "#fff")
            else:
                ct = math.cos(mid)
                anchor = "start" if ct > 0.25 else "end" if ct < -0.25 else "middle"
                col = ink["label"]
                tx = min(max(tx, x + fs * 0.2), x + w - fs * 0.2)
            lsz = _lfs(fs * 0.8)
            parts.append(f'<text x="{tx:.4f}" y="{ty + fs * 0.3:.4f}" '
                         f'text-anchor="{anchor}" '
                         f'font-size="{lsz:.4f}" fill="{col}" font-weight="600">'
                         f'{_lines_inner(txt, tx, lsz, center=True)}</text>')
        ang = a2
    return "".join(parts)


def _check_chart(c, where: str) -> None:
    if not isinstance(c, dict):
        raise LayoutError(f"{where}: expected a chart object")
    if c.get("type") not in CHART_TYPES:
        raise LayoutError(f"{where}.type: expected one of {', '.join(CHART_TYPES)}")
    labels = c.get("labels")
    if labels is not None and not isinstance(labels, list):
        raise LayoutError(f"{where}.labels: expected a list")
    series = c.get("series")
    if not isinstance(series, list) or not series:
        raise LayoutError(f"{where}.series: needs at least one series")
    for i, s in enumerate(series):
        w2 = f"{where}.series[{i}]"
        if not isinstance(s, dict):
            raise LayoutError(f"{w2}: expected an object")
        if not isinstance(s.get("data"), list):
            raise LayoutError(f"{w2}.data: expected a list of numbers")
        for j, v in enumerate(s["data"]):
            if v is not None and not isinstance(v, (int, float)) or isinstance(v, bool):
                raise LayoutError(f"{w2}.data[{j}]: {v!r} is not a number")
        if s.get("color"):
            _hex(s["color"], f"{w2}.color")
        for j, col in enumerate(s.get("colors") or []):
            if col:
                _hex(col, f"{w2}.colors[{j}]")
        if s.get("type") is not None and s["type"] not in CHART_SERIES_KINDS:
            raise LayoutError(f"{w2}.type: expected one of "
                              f"{', '.join(CHART_SERIES_KINDS)}")
        if s.get("axis") is not None and s["axis"] not in ("left", "right"):
            raise LayoutError(f"{w2}.axis: expected left or right")
    for j, col in enumerate(c.get("colors") or []):
        if col:
            _hex(col, f"{where}.colors[{j}]")
    for flag in ("legend", "values", "grid", "tips", "stackPct"):
        if c.get(flag) is not None and not isinstance(c[flag], bool):
            raise LayoutError(f"{where}.{flag}: expected true or false")
    for k in ("titleColor", "labelColor", "axisColor", "gridColor"):
        if c.get(k):
            _hex(c[k], f"{where}.{k}")
    for k in ("axisMin", "axisMax", "axis2Min", "axis2Max"):
        if c.get(k) is not None:
            _num(c[k], f"{where}.{k}")
    for lo, hi in (("axisMin", "axisMax"), ("axis2Min", "axis2Max")):
        if c.get(lo) is not None and c.get(hi) is not None \
                and c[hi] <= c[lo]:
            raise LayoutError(f"{where}.{hi}: must be above {lo}")
    if c.get("legendPos") is not None and c["legendPos"] not in CHART_LEGEND_POS:
        raise LayoutError(f"{where}.legendPos: expected one of "
                          f"{', '.join(CHART_LEGEND_POS)}")
    for k in ("axisTitle", "catTitle"):
        if c.get(k) is not None and not isinstance(c[k], str):
            raise LayoutError(f"{where}.{k}: expected text")
    g = c.get("barGap")
    if g is not None:
        _num(g, f"{where}.barGap")
        if not 0 <= g <= 0.9:
            raise LayoutError(f"{where}.barGap: expected 0-0.9")
    a = c.get("labelAngle")
    if a is not None:
        _num(a, f"{where}.labelAngle")
        if not -90 <= a <= 90:
            raise LayoutError(f"{where}.labelAngle: expected -90 to 90")
    rules = c.get("rules")
    if rules is not None:
        if not isinstance(rules, list):
            raise LayoutError(f"{where}.rules: expected a list")
        for i, r in enumerate(rules):
            w3 = f"{where}.rules[{i}]"
            if not isinstance(r, dict):
                raise LayoutError(f"{w3}: expected an object")
            for bad in set(r) - {"value", "label", "color", "dash", "width"}:
                raise LayoutError(f"{w3}.{bad}: not a reference-line field")
            if r.get("value") is None:
                raise LayoutError(f"{w3}.value: a reference line needs a value")
            _num(r["value"], f"{w3}.value")
            if r.get("color"):
                _hex(r["color"], f"{w3}.color")
            if r.get("width") is not None:
                _num(r["width"], f"{w3}.width")
            if r.get("dash") is not None and not isinstance(r["dash"], bool):
                raise LayoutError(f"{w3}.dash: expected true or false")
            if r.get("label") is not None \
                    and not isinstance(r["label"], (str, bool)):
                raise LayoutError(f"{w3}.label: expected text, or true for "
                                  f"the value itself")
    t = c.get("axisTicks")
    if t is not None:
        if not isinstance(t, int) or isinstance(t, bool) or not 2 <= t <= 11:
            raise LayoutError(f"{where}.axisTicks: expected a whole number 2-11")
    wl = c.get("wrapLabels")
    if wl is not None and not isinstance(wl, bool):
        if not isinstance(wl, int) or not 1 <= wl <= 4:
            raise LayoutError(f"{where}.wrapLabels: expected true/false or 1-4")
    if c.get("sliceLabel") is not None and c["sliceLabel"] not in CHART_SLICE_LABELS:
        raise LayoutError(f"{where}.sliceLabel: expected one of "
                          f"{', '.join(CHART_SLICE_LABELS)}")
    for k in ("format", "labelFormat", "axis2Format"):
        f = c.get(k)
        if f is None:
            continue
        if not isinstance(f, dict):
            raise LayoutError(f"{where}.{k}: expected an object")
        for bad in set(f) - {"prefix", "suffix", "scale", "decimals", "unit"}:
            raise LayoutError(f"{where}.{k}.{bad}: not a number-format field")
        for s in ("prefix", "suffix", "unit"):
            if f.get(s) is not None and not isinstance(f[s], str):
                raise LayoutError(f"{where}.{k}.{s}: expected text")
        if f.get("scale") is not None and f["scale"] not in CHART_NUM_SCALES:
            raise LayoutError(f"{where}.{k}.scale: expected one of "
                              f"{', '.join(CHART_NUM_SCALES)}")
        d = f.get("decimals")
        if d is not None and (not isinstance(d, int) or isinstance(d, bool)
                              or not 0 <= d <= 6):
            raise LayoutError(f"{where}.{k}.decimals: expected a whole number 0-6")


def _check_chart_style(st, where: str) -> None:
    """One saved look. Checked by running the real chart validator over a
    throwaway chart wearing it — so a style cannot hold a colour, a format or
    a legend position the renderer would refuse, and there is exactly one
    place that knows what any of those mean."""
    if not isinstance(st, dict):
        raise LayoutError(f"{where}: expected an object")
    for bad in set(st) - set(CHART_LOOK) - {"seriesColors"}:
        raise LayoutError(f"{where}.{bad}: not part of a chart's look")
    for i, col in enumerate(st.get("seriesColors") or []):
        if col:
            _hex(col, f"{where}.seriesColors[{i}]")
    probe = {k: v for k, v in st.items() if k != "seriesColors"}
    probe.update(type="bar", labels=["A"], series=[{"name": "s", "data": [1]}])
    _check_chart(probe, where)


def _check_uses(st: dict, styles: dict, where: str,
                what: str = "text style") -> None:
    """A `use` has to name a style that exists. Refused at load rather than
    resolving to nothing: a slot silently wearing no style looks exactly like
    a slot someone forgot to style, and the two want different fixes."""
    name = st.get("use")
    if name is None:
        return
    if not isinstance(name, str) or not name:
        raise LayoutError(f"{where}.use: expected the name of a {what}")
    if name not in styles:
        known = ", ".join(sorted(styles)) or "none are defined"
        raise LayoutError(f"{where}.use: no {what} called {name!r} — {known}")


def _check_style_chain(styles: dict, what: str = "textStyle") -> None:
    """Every `from` names a real style, and no chain eats itself. A loop
    resolves to something harmless at render time, but it is always a mistake
    and the person who typed it should hear about it here.

    An object style may only come from one of its own kind: a box that
    inherits a shape's stroke width would be a box carrying a key it cannot
    have, which the key check below would refuse anyway — but "inherits from
    a shape style" is the message that says what went wrong."""
    noun = "text style" if what == "textStyle" else "object style"
    for name, st in styles.items():
        seen, cur = [name], st.get("from")
        while cur is not None:
            if not isinstance(cur, str) or cur not in styles:
                raise LayoutError(f"{what} '{name}': inherits from "
                                  f"{cur!r}, which is not a {noun}")
            if cur in seen:
                raise LayoutError(f"{what} '{name}': inherits from itself "
                                  f"through {' -> '.join(seen + [cur])}")
            if what == "objectStyle" and styles[cur].get("kind") != st.get("kind"):
                raise LayoutError(f"{what} '{name}' is a {st.get('kind')} style "
                                  f"and cannot inherit from '{cur}', a "
                                  f"{styles[cur].get('kind')} style")
            seen.append(cur)
            cur = styles[cur].get("from")


def _check_object_use(obj: dict, kind: str, styles: dict, where: str) -> None:
    """An object's `use` names an object style that exists AND is for this
    kind of object — a table wearing a box style has nothing to wear."""
    _check_uses(obj, styles, where, "object style")
    name = obj.get("use")
    if name is not None and styles[name].get("kind") != kind:
        raise LayoutError(f"{where}.use: '{name}' is a {styles[name].get('kind')} "
                          f"style, and this is a {kind}")


def _check_border(b, where: str, sides: tuple) -> None:
    """One border spec — weight in px, a colour, a line style, which edges.
    Shared by tables (all/outer/inner/none) and text boxes (all or one edge),
    so the two cannot disagree about what a border is."""
    if not isinstance(b, dict):
        raise LayoutError(f"{where}: expected an object")
    if b.get("w") is not None:
        w = _num(b["w"], f"{where}.w")
        if not 0 <= w <= 12:
            raise LayoutError(f"{where}.w: {w} is outside 0–12px")
    if b.get("color"):
        _hex(b["color"], f"{where}.color")
    if b.get("style") and b["style"] not in TABLE_BORDER_STYLES:
        raise LayoutError(f"{where}.style: expected one of "
                          f"{', '.join(TABLE_BORDER_STYLES)}")
    if b.get("sides") and b["sides"] not in sides:
        raise LayoutError(f"{where}.sides: expected one of "
                          f"{', '.join(sides)}")


def _check_blend(v, where: str) -> None:
    if v is not None and v not in BLENDS:
        raise LayoutError(f"{where}.blend: {v!r} must be one of {', '.join(BLENDS)}")


def _check_shape_look(s: dict, where: str) -> None:
    """The presentation half of a shape — everything an object style may set.
    Run on the RESOLVED shape, so a style cannot hand a shape a value the
    shape could not have carried itself; and on a style's own keys, so a bad
    style fails where it is defined and not on the first shape to wear it."""
    # These land verbatim inside SVG attributes: a malformed value does not
    # error, it renders an invisible shape. Fill may be a gradient; stroke
    # stays a solid hex.
    if s.get("fill") not in (None, "none"):
        _fill(s["fill"], f"{where}.fill")
    if s.get("stroke") not in (None, "none"):
        _hex(s["stroke"], f"{where}.stroke")
    if s.get("sw") is not None:
        _num(s["sw"], f"{where}.sw")
    if s.get("alpha") is not None:
        _alpha(s["alpha"], f"{where}.alpha")
    if s.get("shadow") is not None:
        _check_shadow(s["shadow"], f"{where}.shadow")
    if s.get("r") is not None and _num(s["r"], f"{where}.r") < 0:
        raise LayoutError(f"{where}: corner radius cannot be negative")
    if s.get("dash") is not None:
        d = s["dash"]
        if not isinstance(d, list) or not 1 <= len(d) <= 2 or any(
                _num(v, f"{where}.dash") <= 0 for v in d):
            raise LayoutError(f"{where}: dash must be one or two positive "
                              f"lengths, like [0.08, 0.05]")
    if s.get("ends") is not None and s["ends"] not in LINE_ENDS:
        raise LayoutError(f"{where}: ends {s['ends']!r} must be one of "
                          f"{', '.join(LINE_ENDS)}")
    _check_blend(s.get("blend"), where)


def _check_box_look(b: dict, where: str) -> None:
    """The presentation half of a text box. `pad`, `radius`, `border`, `cols`
    and `gap` are new with object styles, because a pull-quote style with no
    padding and no rule is not a pull-quote style; all opt-in, and a box that
    names none of them renders exactly as it did."""
    if b.get("fill"):
        _fill(b["fill"], f"{where}.fill")
    if b.get("alpha") is not None:
        _alpha(b["alpha"], f"{where}.alpha")
    if b.get("shadow") is not None:
        _check_shadow(b["shadow"], f"{where}.shadow")
    if b.get("style"):
        _check_text(b["style"], f"{where}.style")
    pad = b.get("pad")
    if pad is not None:
        vals = pad if isinstance(pad, list) else [pad]
        if len(vals) not in (1, 2, 4):
            raise LayoutError(f"{where}.pad: one, two or four inches, like "
                              f"0.1 or [0.08, 0.12]")
        for v in vals:
            if not 0 <= _num(v, f"{where}.pad") <= 2:
                raise LayoutError(f"{where}.pad: {v} is outside 0–2in")
    if b.get("radius") is not None:
        r = _num(b["radius"], f"{where}.radius")
        if not 0 <= r <= 200:
            raise LayoutError(f"{where}.radius: {r:g} is outside 0–200px")
    if b.get("border") is not None:
        _check_border(b["border"], f"{where}.border", BOX_BORDER_SIDES)
    if b.get("cols") is not None:
        c = b["cols"]
        if not isinstance(c, int) or isinstance(c, bool) or not 1 <= c <= 4:
            raise LayoutError(f"{where}.cols: {c!r} — a whole number of "
                              f"columns, 1 to 4")
    if b.get("gap") is not None and not 0 <= _num(b["gap"], f"{where}.gap") <= 2:
        raise LayoutError(f"{where}.gap: {b['gap']} is outside 0–2in")
    _check_blend(b.get("blend"), where)


ANCHOR_EDGES = ("top", "bottom")
WRAP_SIDES = ("left", "right")


def _check_wrap(obj: dict, where: str) -> None:
    """Text wraps around an object only if the object belongs to a paragraph
    — wrap without an anchor has no text to wrap."""
    w = obj.get("wrap")
    if w is None:
        return
    if w not in WRAP_SIDES:
        raise LayoutError(f"{where}.wrap: {w!r} must be one of {', '.join(WRAP_SIDES)}")
    if not isinstance(obj.get("anchor"), dict):
        raise LayoutError(f"{where}.wrap: needs an anchor — say which paragraph "
                          f"the text flows around it in")
    if obj.get("wrapPad") is not None and not 0 <= _num(obj["wrapPad"], f"{where}.wrapPad") <= 1:
        raise LayoutError(f"{where}.wrapPad: {obj['wrapPad']} is outside 0–1in")


def _check_anchor(a, where: str) -> None:
    """An anchor: the slot this object follows, the offset from it, which edge.
    The slot's existence is not checked here — a Layout knows nothing of
    content.md — and at render the object simply stays at its stored y when
    the host is missing, which is also what a page without JavaScript shows."""
    if not isinstance(a, dict):
        raise LayoutError(f"{where}.anchor: expected an object like "
                          f'{{"to": "<slot>", "dy": 0.25}}')
    to = a.get("to")
    if not isinstance(to, str) or not to.strip():
        raise LayoutError(f"{where}.anchor.to: the slot this follows, by key")
    if not re.match(r"^[A-Za-z0-9_.:-]+$", to):
        raise LayoutError(f"{where}.anchor.to: {to!r} is not a slot key")
    if a.get("dy") is not None:
        _num(a["dy"], f"{where}.anchor.dy")
    if a.get("edge") is not None and a["edge"] not in ANCHOR_EDGES:
        raise LayoutError(f"{where}.anchor.edge: {a['edge']!r} must be one of "
                          f"{', '.join(ANCHOR_EDGES)}")


def anchor_attrs(obj: dict) -> str:
    """The data the runtime reads: which slot, how far below its top (or
    bottom), in inches — and, for text wrap, which side. Emitted in BOTH
    modes: the editor positions anchored objects through the same runtime the
    published page runs."""
    a = obj.get("anchor") if obj else None
    if not a:
        return ""
    out = f' data-anc="{a["to"]}" data-anc-dy="{float(a.get("dy") or 0):g}"'
    if a.get("edge") == "bottom":
        out += ' data-anc-edge="bottom"'
    if obj.get("wrap"):
        out += f' data-wrap="{obj["wrap"]}"'
        if obj.get("wrapPad") is not None:
            out += f' data-wrap-pad="{float(obj["wrapPad"]):g}"'
    return out


# The anchor runtime. Every pinned object has a y in inches; an ANCHORED one
# has, instead of a place, a paragraph it belongs to and a distance from it,
# and this puts it there once the browser has set the type. Measured, not
# computed: the engine hands text to CSS and cannot know where a paragraph
# ends, so the one thing that can — the layout that just happened — is asked.
#
# `top` is written against the object's offsetParent, not the page: an
# element inside a positioned ancestor resolves its top there, and the
# editor's placer stores such elements in that same local frame. Page inches
# per px come from the sheet's own rendered width over its width in inches, so
# zoom, a scaled export and a narrowed phone all measure the same.
#
# Runs at parse, at load, when fonts arrive and on resize. On a phone the
# release rule (mobile_css) puts every pinned thing back in the flow with
# `top:auto !important`, which beats the inline value this writes — so an
# anchored object reads in order there, exactly as an unanchored one does.
# One source: the editor evaluates this same string after an incremental
# render (imported nodes do not run their scripts), so the two cannot drift.
ANCHOR_JS = (
    "(function(){var W=%(w)s;function go(){"
    # Struts first: an element that reserves flow space and whose page holds
    # no spacer for it gets one, exactly where it stands in the DOM — which is
    # the slot it left, since going absolute moves nothing in the tree. The
    # same markup spacer() emits, so the editor and the anchors below cannot
    # tell the two apart.
    "var rs=document.querySelectorAll('[data-reserve-for]');"
    "for(var j=0;j<rs.length;j++){var e=rs[j],rid=e.getAttribute('data-reserve-for');"
    "var rp=e.closest('.page');if(!rp||!e.parentNode)continue;"
    "if(rp.querySelector('.ds-spacer[data-spacer-for=\"'+rid+'\"]'))continue;"
    "var sp=document.createElement('div');sp.className='ds-spacer';"
    "sp.setAttribute('data-spacer-for',rid);sp.setAttribute('data-anc-host','spacer:'+rid);"
    "sp.setAttribute('aria-hidden','true');var rw=e.getAttribute('data-reserve-w');"
    "sp.style.cssText=(rw?'width:'+rw+'in;':'')+'height:'+e.getAttribute('data-reserve')+'in;flex:0 0 auto';"
    "e.parentNode.insertBefore(sp,e);}"
    "var els=document.querySelectorAll('[data-anc]');"
    "for(var i=0;i<els.length;i++){var el=els[i],pg=el.closest('.page');"
    "if(!pg)continue;var key=el.getAttribute('data-anc');"
    # A slot of several paragraphs is several hosts: its TOP is the first
    # one's, its BOTTOM the last one's, so "under this text" means under all
    # of it — and a paragraph added to the slot moves what hangs below it.
    # THIS page only. The sums below are page-local — the host's edge minus
    # this page's top — so a host found on another sheet yields a `top` of
    # however many inches apart the two pages are, and the object leaves the
    # page entirely. A document-wide fallback used to do exactly that when a
    # slot was renamed (a section moved to another page) or the object was
    # dragged off its host's page. No host here means no measurement: the
    # object keeps the y it was left at, which is what a page without
    # JavaScript shows and what the editor's own anchorSet already enforces
    # (it refuses to anchor to a paragraph that is not on this page).
    "var sel='[data-anc-host=\"'+key+'\"]';var hs=pg.querySelectorAll(sel);"
    "if(!hs.length)continue;"
    "var bottom=el.getAttribute('data-anc-edge')==='bottom';"
    "var host=bottom?hs[hs.length-1]:hs[0];"
    "if(host===el||el.contains(host)||host.contains(el))continue;"
    # Text wrap: the object becomes a FLOAT just before its paragraph, in the
    # same flow, so the paragraph's lines shorten around it — CSS's own wrap,
    # no measuring. A previous sibling, never a child: the paragraph's own
    # markup stays exactly what content.md says, so editing it in place can
    # never swallow the figure. Pinned geometry is undone inline (position,
    # top, left); the width in inches stays, which is what a figure keeps.
    "var wrap=el.getAttribute('data-wrap');"
    "if(wrap){var par=host.parentNode;"
    "if(el.parentNode!==par||el.nextSibling!==host)par.insertBefore(el,host);"
    "var pad=(el.getAttribute('data-wrap-pad')||'0.12')+'in';"
    "el.style.position='static';el.style.top='';el.style.left='';"
    "el.style.cssFloat=wrap;el.style.zIndex='';"
    "el.style.margin=wrap==='right'?'0 0 '+pad+' '+pad:'0 '+pad+' '+pad+' 0';"
    "continue;}"
    "var pr=pg.getBoundingClientRect();if(!pr.width)continue;"
    "var op=el.offsetParent;var ar=(op&&op!==pg&&pg.contains(op))?op.getBoundingClientRect():pr;"
    "var hr=host.getBoundingClientRect();var ppi=pr.width/W;"
    "var edge=bottom?hr.bottom:hr.top;"
    "var top=(edge-ar.top)/ppi+parseFloat(el.getAttribute('data-anc-dy')||'0');"
    "el.style.top=top.toFixed(3)+'in';}}"
    "window.__dsAnchor=go;go();addEventListener('load',go);addEventListener('resize',go);"
    "if(document.fonts&&document.fonts.ready)document.fonts.ready.then(go);})();"
)


def box_pad_css(pad) -> str:
    vals = pad if isinstance(pad, list) else [pad]
    return "padding:" + " ".join(f"{float(v):g}in" for v in vals)


def box_border_css(b: dict) -> str:
    """A text box's border as CSS: every edge, or the one edge `sides` names.
    Same spec as a table's, drawn by the box's own declaration."""
    style = b.get("style", "solid")
    w = b.get("w", 1)
    if style == "none" or not w:
        return ""
    side = b.get("sides", "all")
    prop = "border" if side == "all" else f"border-{side}"
    return f'{prop}:{float(w):g}px {style} {b.get("color", "#C9D6CD")}'


def _check_shadow(sh, where: str) -> None:
    if not isinstance(sh, dict):
        raise LayoutError(f"{where}: expected a shadow object")
    for k in ("offset", "direction", "blur"):
        if sh.get(k) is not None:
            _num(sh[k], f"{where}.{k}")
    if sh.get("alpha") is not None:
        _alpha(sh["alpha"], f"{where}.alpha")
    if sh.get("color"):
        _hex(sh["color"], f"{where}.color")


TABLE_BORDER_STYLES = ("solid", "dashed", "dotted", "none")
TABLE_ALIGNS = ("left", "center", "right")
# Which edges the border is drawn on. "all" is every edge, "outer" the frame
# only, "inner" the gridlines only, "none" nothing — Canva's own four.
TABLE_BORDER_SIDES = ("all", "outer", "inner", "none")


def _check_table_look(t: dict, where: str, nrows: int, ncols: int) -> None:
    """The presentation half of a table: border, fills, column widths and the
    per-cell overrides. All optional — a table with none of it renders exactly
    as it did before any of this existed, which is what keeps every committed
    layout.json valid."""
    b = t.get("border")
    if b is not None:
        _check_border(b, f"{where}.border", TABLE_BORDER_SIDES)
    if t.get("alpha") is not None:
        _alpha(t["alpha"], f"{where}.alpha")
    if t.get("style"):
        _check_text(t["style"], f"{where}.style")
    if t.get("header") is not None and not isinstance(t["header"], bool):
        raise LayoutError(f"{where}.header: expected true or false")
    _check_blend(t.get("blend"), where)
    # band: tints every OTHER body row — the zebra half of a table style, as
    # one property rather than a fill override per cell, so it survives rows
    # being inserted and deleted underneath it.
    for k in ("fill", "headerFill", "headerColor", "band"):
        if t.get(k):
            _hex(t[k], f"{where}.{k}")
    cw = t.get("colw")
    if cw is not None:
        if not isinstance(cw, list) or len(cw) != ncols:
            raise LayoutError(f"{where}.colw: expected {ncols} widths, "
                              f"one per column")
        for j, v in enumerate(cw):
            n = _num(v, f"{where}.colw[{j}]")
            if n <= 0:
                raise LayoutError(f"{where}.colw[{j}]: a width must be positive")
    cells = t.get("cells")
    if cells is not None:
        if not isinstance(cells, dict):
            raise LayoutError(f"{where}.cells: expected an object keyed 'row,col'")
        for key, ov in cells.items():
            w2 = f"{where}.cells['{key}']"
            try:
                r, c = (int(p) for p in str(key).split(","))
            except ValueError:
                raise LayoutError(f"{w2}: key must be 'row,col', zero-based")
            if not (0 <= r < nrows and 0 <= c < ncols):
                raise LayoutError(f"{w2}: no such cell in a {nrows}x{ncols} table")
            if not isinstance(ov, dict):
                raise LayoutError(f"{w2}: expected an object")
            if ov.get("fill"):
                _hex(ov["fill"], f"{w2}.fill")
            if ov.get("color"):
                _hex(ov["color"], f"{w2}.color")
            if ov.get("align") and ov["align"] not in TABLE_ALIGNS:
                raise LayoutError(f"{w2}.align: expected one of "
                                  f"{', '.join(TABLE_ALIGNS)}")
            for flag in ("bold", "italic"):
                if ov.get(flag) is not None and not isinstance(ov[flag], bool):
                    raise LayoutError(f"{w2}.{flag}: expected true or false")


def table_border_css(b: dict, side: str) -> str:
    """One edge's CSS for a table border spec. `side` is 'outer' or 'inner' —
    which the `sides` setting turns on or off independently."""
    if not b:
        return ""
    style = b.get("style", "solid")
    on = b.get("sides", "all")
    if style == "none" or on == "none" or (on == "outer" and side == "inner") \
            or (on == "inner" and side == "outer"):
        return "0"
    w = b.get("w", 1)
    if not w:
        return "0"
    return f'{w}px {style} {b.get("color", "#C9D6CD")}'


def shadow_css(sh: dict) -> str:
    """An element shadow -> its box-shadow value. Inches, not em: a box's
    shadow belongs to the page's geometry, not to a font size it does not
    have. Direction shares the clock convention every other angle here uses.
    Module-level so the editor can preview a slider through the same code
    that will render the committed page."""
    dx, dy = _xy(sh.get("offset", 0.04), sh.get("direction", 135))
    c = _rgba(sh.get("color", "#2F3E46"), sh.get("alpha", 0.35))
    return f'{dx}in {dy}in {sh.get("blur", 0.06)}in {c}'


def shape_shadow_css(sh: dict) -> str:
    """The same shadow as a drop-shadow() filter, for SVG shapes — box-shadow
    follows the box, and a shape is not its bounding box.

    The units are the trap. The shape layer's viewBox is in INCHES (1 user
    unit = 1in), and Chrome resolves CSS filter lengths on SVG children as
    user units at 1px = 1 unit — so "0.06in" becomes 96 units: a
    five-and-a-half-INCH blur that swallowed half a page in testing. The inch
    values are therefore written with a px suffix, which lands them as the
    inches they mean."""
    dx, dy = _xy(sh.get("offset", 0.04), sh.get("direction", 135))
    c = _rgba(sh.get("color", "#2F3E46"), sh.get("alpha", 0.35))
    return f'filter:drop-shadow({dx}px {dy}px {sh.get("blur", 0.04)}px {c})'


class Layout:
    def __init__(self, path: Path, page: tuple[float, float] = (PAGE_W_IN, PAGE_H_IN)):
        self.path = path
        self.page_w, self.page_h = page
        # Set by Content's constructor (bind_footnotes) when one is built
        # against this Layout. None means nobody did — a Layout used on its
        # own, in a test or a tool — and an endnotes box then renders its
        # heading with no list rather than raising.
        self._fn = None
        raw = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text() or "{}")
            except json.JSONDecodeError as e:
                raise LayoutError(f"{path.name}: not valid JSON — {e}")
        # The page this layout is drawn on. The constructor default is what the
        # report was BUILT at; layout.json may override it, which is what
        # File > Resize writes. Read before anything else, because every
        # geometry rule below measures against it.
        self.page = _check_page(raw.get("page"), path.name)
        if self.page:
            self.page_w = self.page[0]
            self.page_h = self.page[1] if self.page[1] is not None else PAGELESS_H
        self._page_style_sent = False
        self._mobile_css_sent = False
        self._chart_tip_sent = False
        self.positions = raw.get("positions") or {}
        # Inline charts, keyed by element id. A chart SHAPE is pinned by inch
        # in the page's SVG layer; an inline chart is drawn where the renderer
        # emits it, in the flow, the way graphic() draws an SVG — and this is
        # the half of it the editor may change. Exactly the relationship
        # `positions` already has to a graphic: the renderer says what the
        # thing is, layout.json holds what the user did to it.
        #
        # It exists because a flow document could not have an editable chart
        # at all. The Budget Primer has none of the former and six figures
        # that wanted to be the latter, so all six were written as frozen SVG
        # by hand — see docsync/CHART_PARITY.md.
        self.charts = raw.get("charts") or {}
        # Named looks, saved by the editor and applied by it. Nothing in the
        # render path reads these: applying a style writes its keys onto the
        # chart, so a built page never depends on the definition surviving.
        self.chart_styles = raw.get("chartStyles") or {}
        self.shapes = raw.get("shapes") or []
        self.text = raw.get("text") or {}
        # Named, reusable, redefinable text styles — a stylesheet, which is
        # the organising idea of InDesign and the thing self.text alone could
        # never be. A slot, box or table says `use: "<name>"` and may still
        # carry its own keys on top; those win, so a style is a starting point
        # and not a cage. A style may itself say `from: "<name>"`, which is
        # how "Body small" is Body at a smaller size and stays Body when Body
        # changes. Absent, every one of those is exactly what it was.
        self.text_styles = raw.get("textStyles") or {}
        # Named object styles — the same for shapes, text boxes and tables.
        # Each says which `kind` it dresses; an object says `use: "<name>"`
        # and its own keys still win. See resolve_object().
        self.object_styles = raw.get("objectStyles") or {}
        self.boxes = raw.get("boxes") or []
        self.tables = raw.get("tables") or []
        # The slots some object follows. content.py stamps these with
        # data-anc-host in BOTH modes, which is the one hook the published page
        # has for the anchor runtime to measure against (data-slot is edit-only).
        self.anchor_hosts = {
            a["to"] for a in (o.get("anchor") for o in
                              list(self.boxes) + list(self.tables)
                              + [p for p in self.positions.values() if isinstance(p, dict)])
            if isinstance(a, dict) and isinstance(a.get("to"), str) and a.get("to")}
        self._anchor_sent = False
        # Ids whose vacated flow slot is held. The runtime materialises the
        # strut for any of these whose renderer never called spacer() — see
        # attr() and ANCHOR_JS — so it ships whenever one exists.
        self.reserved = {k for k, p in self.positions.items()
                         if isinstance(p, dict) and p.get("reserve")}
        self.fills = raw.get("fill") or {}
        # Editor affordances only: ids the editor refuses to drag, and groups
        # that select-and-move as one. The renderer reads neither, so they
        # cannot move a byte of the published page — validated so a hand-edit
        # cannot quietly disable a lock or dangle a group.
        self.locked = raw.get("locked") or []
        self.groups = raw.get("groups") or []
        # Designed elements the user deleted. Unlike `locked` this DOES move
        # published bytes: a hidden element renders display:none (attr()/
        # style()) and its spacer is suppressed, so the flow closes over the
        # gap. Reversible by design — the markup still ships, and the editor
        # renders it as a selectable ghost so Delete can restore it. This is
        # the only way to "remove" a renderer-emitted element: it is
        # regenerated on every build, so absence has to be an override here,
        # not an edit to the output.
        self.hidden = raw.get("hidden") or []
        # Every element some toggle button reveals, flattened: target may be
        # one id or a list, and boxes, shapes and tables all consult this to
        # stamp their publish-mode hook. Built once, here, so the three
        # renderers cannot disagree about who is toggleable.
        self.toggle_targets = set()
        # Each target's OWN transition duration, keyed by target id — the
        # button that reveals it is the natural place to set the speed (one
        # button, its content), but the CSS transition lives on the target,
        # so this is the id-indexed reverse of `toggle_targets`. Two buttons
        # naming the same target is an edge case not worth solving — the
        # last one scanned wins, same as any other last-write map build.
        self.toggle_speed = {}
        for _b in (raw.get("boxes") or []):
            if _b.get("act") == "toggle":
                _t = _b.get("target")
                _spd = _b.get("tglSpeed", 0.3)
                for _x in (_t if isinstance(_t, list) else [_t]):
                    if _x:
                        self.toggle_targets.add(str(_x))
                        self.toggle_speed[str(_x)] = _spd
        self.imgs = raw.get("img") or {}
        # Ruler guides the editor snaps to, as {x:[in…], y:[in…]}. Editor-only,
        # like `locked`: the renderer never emits a guide, so it cannot move a
        # published byte — validated so a hand-edit cannot smuggle in junk.
        self.guides = raw.get("guides") or {}
        # Page identity vs order. Designed pages are IDS (their born ordinals);
        # "pages" reorders, hides (by omission) and interleaves blank pages.
        # Everything page-keyed — shapes, boxes, fills, layers — stays keyed by
        # identity, so reordering never re-homes anyone's work.
        self.pages = raw.get("pages") or {}
        # Master pages: a named set of boxes and shapes a page `use`s, so the
        # running footer or the section hairline changes in one place. A page
        # names its master in pageMasters (keys are page ids as strings, the
        # way JSON keys are); master_items() expands them per page.
        self.masters = raw.get("masters") or {}
        self.page_masters = raw.get("pageMasters") or {}
        # An explicit endnote order, by source id. Endnotes are numbered by
        # first appearance in the prose; dragging one past another on the
        # Endnotes page records an override here instead of rewriting the
        # refs in the text. Partial by design — ids listed here lead, in this
        # order, and anything else keeps its first-appearance place after
        # them. An id that is no longer cited is simply ignored.
        self.endnotes = raw.get("endnotes") or []
        # Height overrides for colored background sections, {id: {"h": in}}.
        # A section keeps its background glued to itself and its text in flow;
        # the override only stretches (or trims toward natural height) the
        # band via min-height — the web-native half of the Canva model, see
        # STAGE2_AUTOMATION.md. Emitted by sec().
        self.sections = raw.get("sections") or {}
        self._validate()

    def endnote_order(self) -> list:
        """The editor's endnote order override (ids), or [] for none."""
        return [e for e in self.endnotes if isinstance(e, str)]

    def _validate(self):
        for el, s in self.sections.items():
            if not isinstance(s, dict) or _num(s.get("h"), f"section '{el}'.h") <= 0:
                raise LayoutError(f"section '{el}': needs a positive 'h'")
        # An inline chart's override is a partial chart: the renderer supplies
        # the type and the data, this supplies whatever the user changed. So
        # it is checked as a whole chart only once the two are merged
        # (chart_spec does that); here only the shape of the container.
        if not isinstance(self.charts, dict):
            raise LayoutError("charts: expected an object keyed by element id")
        for el, c in self.charts.items():
            if not isinstance(c, dict):
                raise LayoutError(f"chart '{el}': expected an object")
        if not isinstance(self.chart_styles, dict):
            raise LayoutError("chartStyles: expected an object keyed by name")
        for name, st in self.chart_styles.items():
            _check_chart_style(st, f"chartStyle '{name}'")
        for el, p in self.positions.items():
            for k in ("x", "y"):
                if k not in p:
                    raise LayoutError(f"position '{el}' has no '{k}'")
                _num(p[k], f"position '{el}'.{k}")
            if p.get("rot") is not None:
                _num(p["rot"], f"position '{el}'.rot")
            if p.get("scale") is not None and _num(p["scale"], f"position '{el}'.scale") <= 0:
                raise LayoutError(f"position '{el}': scale must be positive")
            if p.get("alpha") is not None:
                _alpha(p["alpha"], f"position '{el}'.alpha")
            if p.get("flip") is not None and p["flip"] not in ("h", "v", "hv"):
                raise LayoutError(f"position '{el}': flip {p['flip']!r} must be "
                                  f"h, v or hv")
            if p.get("anim") is not None:
                _anim_check(p["anim"], f"position '{el}'")
            if p.get("anchor") is not None:
                _check_anchor(p["anchor"], f"position '{el}'")
            _check_wrap(p, f"position '{el}'")
        if not isinstance(self.masters, dict):
            raise LayoutError("masters: expected an object keyed by name")
        for name, m in self.masters.items():
            if not isinstance(m, dict):
                raise LayoutError(f"master '{name}': expected an object with boxes/shapes")
            for k in m:
                if k not in ("boxes", "shapes"):
                    raise LayoutError(f"master '{name}': only 'boxes' and 'shapes' — "
                                      f"not {k!r}")
                if not isinstance(m[k], list):
                    raise LayoutError(f"master '{name}'.{k}: expected a list")
        if not isinstance(self.page_masters, dict):
            raise LayoutError("pageMasters: expected an object keyed by page id")
        for pid, name in self.page_masters.items():
            if name not in self.masters:
                known = ", ".join(sorted(self.masters)) or "none are defined"
                raise LayoutError(f"pageMasters['{pid}']: no master called {name!r} — {known}")
        seen = set()
        for i, s in enumerate(self.shapes):
            self._check_shape(s, f"shape #{i + 1}", seen)
        # A master's items go through the same checks as a page's own, with
        # a page they never have supplied for the check that wants one.
        for name, m in self.masters.items():
            for i, ms in enumerate(m.get("shapes") or []):
                self._check_master_item(ms, f"master '{name}' shape #{i + 1}")
                self._check_shape(dict(ms, page=0), f"master '{name}' shape #{i + 1}", seen)
        for el, p in self.positions.items():
            if "z" in p and not isinstance(p["z"], int):
                raise LayoutError(f"position '{el}': z {p['z']!r} is not a layer number")
        if not isinstance(self.text_styles, dict):
            raise LayoutError("textStyles: expected an object keyed by name")
        for name, st in self.text_styles.items():
            if not isinstance(st, dict):
                raise LayoutError(f"textStyle '{name}': expected a style object")
            if st.get("use") is not None:
                raise LayoutError(f"textStyle '{name}': a style inherits with "
                                  f"'from', not 'use'")
            _check_text(st, f"textStyle '{name}'")
        _check_style_chain(self.text_styles)
        if not isinstance(self.object_styles, dict):
            raise LayoutError("objectStyles: expected an object keyed by name")
        for name, st in self.object_styles.items():
            w = f"objectStyle '{name}'"
            if not isinstance(st, dict):
                raise LayoutError(f"{w}: expected a style object")
            kind = st.get("kind")
            if kind not in OBJECT_KINDS:
                raise LayoutError(f"{w}: kind {kind!r} must be one of "
                                  f"{', '.join(OBJECT_KINDS)} — a style dresses "
                                  f"one kind of thing")
            if st.get("use") is not None:
                raise LayoutError(f"{w}: a style inherits with 'from', not 'use'")
            bad = [k for k in st if k not in OBJECT_STYLE_META
                   and k not in OBJECT_STYLE_KEYS[kind]]
            if bad:
                raise LayoutError(
                    f"{w}: {bad[0]!r} is not something a {kind} style can set — "
                    f"a style is a look, not a place or a content. One of: "
                    f"{', '.join(OBJECT_STYLE_KEYS[kind])}")
            look = {k: v for k, v in st.items() if k not in OBJECT_STYLE_META}
            if kind == "shape":
                _check_shape_look(look, w)
            elif kind == "box":
                _check_box_look(look, w)
            else:
                _check_table_look(look, w, 0, 0)
        _check_style_chain(self.object_styles, "objectStyle")
        for key, st in self.text.items():
            if not isinstance(st, dict):
                raise LayoutError(f"text '{key}': expected a style object")
            _check_uses(st, self.text_styles, f"text '{key}'")
            # The RESOLVED style is what reaches the page, so that is what has
            # to clear the legibility floor: a slot wearing a style is not
            # excused a size the style set for it.
            _check_text(self.styled_as(st), f"text '{key}'")
        for el, c in self.fills.items():
            _fill(c, f"fill '{el}'")
        if not isinstance(self.locked, list) or any(
                not isinstance(x, str) or not x for x in self.locked):
            raise LayoutError("locked: expected a list of element ids")
        if not isinstance(self.hidden, list) or any(
                not isinstance(x, str) or not x for x in self.hidden):
            raise LayoutError("hidden: expected a list of element ids")
        if not isinstance(self.groups, list):
            raise LayoutError("groups: expected a list of groups")
        _grouped = set()
        for i, g in enumerate(self.groups):
            if not isinstance(g, list) or len(g) < 2 or any(
                    not isinstance(m, str) or not m for m in g):
                raise LayoutError(f"groups #{i + 1}: a group is two or more "
                                  f"element ids")
            for m in g:
                if m in _grouped:
                    raise LayoutError(f"groups: '{m}' is in two groups — an "
                                      f"element belongs to at most one")
                _grouped.add(m)
        if self.guides:
            if not isinstance(self.guides, dict):
                raise LayoutError("guides: expected {x:[…], y:[…]}")
            for axis, span in (("x", self.page_w), ("y", self.page_h)):
                vals = self.guides.get(axis)
                if vals is None:
                    continue
                if not isinstance(vals, list):
                    raise LayoutError(f"guides.{axis}: expected a list of inches")
                for v in vals:
                    if not 0 <= _num(v, f"guides.{axis}") <= span:
                        raise LayoutError(f"guides.{axis}: {v} is off the "
                                          f"{span}in page")
        if self.pages:
            if not isinstance(self.pages, dict):
                raise LayoutError("pages: expected an object with order/blanks")
            blanks = self.pages.get("blanks") or []
            bids = set()
            for i, b in enumerate(blanks):
                if not isinstance(b, dict) or not isinstance(b.get("id"), str) \
                        or not b["id"]:
                    raise LayoutError(f"pages.blanks #{i + 1}: needs a string id")
                if b["id"] in bids:
                    raise LayoutError(f"pages.blanks: duplicate id '{b['id']}'")
                bids.add(b["id"])
                # A blank page may carry a COPY of a designed page's markup
                # (the editor's Duplicate page; see pagecopy.py).
                cp = b.get("copy")
                if cp is not None and (not isinstance(cp, dict)
                                       or not isinstance(cp.get("html"), str)):
                    raise LayoutError(f"pages.blanks '{b['id']}': copy must be "
                                      f"an object with the page's html")
            order = self.pages.get("order")
            if order is not None:
                if not isinstance(order, list) or not order:
                    raise LayoutError("pages.order: expected a non-empty list")
                seen_o = set()
                for pid in order:
                    if isinstance(pid, bool) or not isinstance(pid, (int, str)):
                        raise LayoutError(f"pages.order: {pid!r} is neither a "
                                          f"designed page number nor a blank id")
                    if isinstance(pid, str) and pid not in bids:
                        raise LayoutError(f"pages.order: '{pid}' is not a blank "
                                          f"this file declares")
                    if pid in seen_o:
                        raise LayoutError(f"pages.order: '{pid}' appears twice")
                    seen_o.add(pid)
        for el, g in self.imgs.items():
            where = f"img '{el}'"
            if not isinstance(g, dict):
                raise LayoutError(f"{where}: expected an image-override object")
            if g.get("radius") is not None and _num(g["radius"], f"{where}.radius") < 0:
                raise LayoutError(f"{where}: radius cannot be negative")
            if g.get("src") is not None and (
                    not isinstance(g["src"], str) or not g["src"].strip()):
                raise LayoutError(f"{where}: src must be a path")
            if g.get("filter") is not None:
                f = g["filter"]
                if not isinstance(f, dict):
                    raise LayoutError(f"{where}: filter must be an object")
                for k in ("bright", "contrast", "sat"):
                    if f.get(k) is not None and _num(f[k], f"{where}.filter.{k}") < 0:
                        raise LayoutError(f"{where}.filter.{k} cannot be negative")
                if f.get("gray") is not None:
                    _alpha(f["gray"], f"{where}.filter.gray")
            if g.get("crop") is not None:
                c = g["crop"]
                if not isinstance(c, dict) or any(k not in c for k in ("imgW", "dx", "dy")):
                    raise LayoutError(f"{where}: crop needs imgW, dx and dy")
                for k in ("imgW", "dx", "dy"):
                    if _num(c[k], f"{where}.crop.{k}") < 0:
                        raise LayoutError(f"{where}.crop.{k} cannot be negative")
        for i, b in enumerate(self.boxes):
            self._check_box(b, f"box #{i + 1}", seen)
        for name, m in self.masters.items():
            for i, mb in enumerate(m.get("boxes") or []):
                self._check_master_item(mb, f"master '{name}' box #{i + 1}")
                self._check_box(dict(mb, page=0), f"master '{name}' box #{i + 1}", seen)
        for i, t in enumerate(self.tables):
            where = f"table #{i + 1}"
            if t.get("anim") is not None:
                _anim_check(t["anim"], where)
            tid = t.get("id")
            if not tid:
                raise LayoutError(f"{where}: needs an 'id'")
            if tid in seen:
                raise LayoutError(f"{where}: duplicate id '{tid}' — already a shape or box")
            seen.add(tid)
            if not isinstance(t.get("page"), (int, str)) or isinstance(t.get("page"), bool):
                raise LayoutError(f"{where}: 'page' must be a page number or blank-page id")
            for k in ("x", "y", "w"):
                _num(t.get(k), f"{where}.{k}")
            rows = t.get("rows")
            if not isinstance(rows, list) or not rows:
                raise LayoutError(f"{where}: 'rows' must be a non-empty grid")
            width = None
            for ri, row in enumerate(rows):
                if not isinstance(row, list) or not row:
                    raise LayoutError(f"{where}: row #{ri + 1} must be a non-empty list of cells")
                if width is None:
                    width = len(row)
                elif len(row) != width:
                    raise LayoutError(f"{where}: row #{ri + 1} has {len(row)} cells, expected {width}")
                for c in row:
                    if not isinstance(c, str):
                        raise LayoutError(f"{where}: every cell must be text (markdown)")
            if "z" in t and not isinstance(t["z"], int):
                raise LayoutError(f"{where}: z {t['z']!r} is not a layer number")
            if t.get("rot") is not None:
                _num(t["rot"], f"{where}.rot")
            if t.get("anchor") is not None:
                _check_anchor(t["anchor"], where)
            _check_object_use(t, "table", self.object_styles, where)
            _check_table_look(self.dressed(t), where, len(rows), width)
        # The DRESSED box or table: a text style named by the object style it
        # wears has to exist too, and the resolved size has to clear the floor.
        for i, b in enumerate(self.boxes):
            st = self.dressed(b).get("style")
            if isinstance(st, dict):
                _check_uses(st, self.text_styles, f"box #{i + 1}.style")
                _check_text(self.styled_as(st), f"box #{i + 1}.style")
        for i, t in enumerate(self.tables):
            st = self.dressed(t).get("style")
            if isinstance(st, dict):
                _check_uses(st, self.text_styles, f"table #{i + 1}.style")
                _check_text(self.styled_as(st), f"table #{i + 1}.style")
        for name, st in self.object_styles.items():
            if isinstance(st.get("style"), dict):
                _check_uses(st["style"], self.text_styles,
                            f"objectStyle '{name}'.style")

    # ---- positions -------------------------------------------------------

    def _style(self, p: dict) -> str:
        # margin:0 FIRST, and it is load-bearing. A margin on an absolutely
        # positioned element is ADDED to its left/top, so the element renders
        # somewhere other than the coordinate stored here — and because the
        # editor re-measures the RENDERED box on the next drag, the discrepancy
        # is written back as the new coordinate and compounds on every save. An
        # rxkids callout with margin-top:60px walked off the bottom of an 84in
        # page that way, and three call sites there had grown hand-written
        # margin-top:0 patches before the pattern was spotted.
        # It comes first so a deliberate margin passed through attr()'s `extra`
        # still wins: attr() joins css then extra, and the later declaration in
        # an inline style is the one that applies.
        s = f'margin:0;position:absolute;left:{p["x"]}in;top:{p["y"]}in'
        if p.get("w") or p.get("h"):
            # Same reason as the margin above, and the same compounding: the
            # inch a drag stored is the MEASURED rect, padding and border
            # included. A designed piece that carries either (a callout, a
            # tile) read that number as content width and painted wider than
            # it was dropped, and the next drag wrote the wider number back.
            s += ";box-sizing:border-box"
        if p.get("w"):
            s += f';width:{p["w"]}in'
        # Height is opt-in. A text box with a fixed height either clips its
        # words or leaves a hole when the prose changes, so only things whose
        # size is their content — images, shapes — should carry one.
        #
        # "hmin" makes it a FLOOR instead: the element is at least this tall
        # and grows if its words need more. That is what lets a designed text
        # slot (a section heading) take the same top/bottom handles a text box
        # has without the drag becoming a way to clip your own heading.
        if p.get("h"):
            s += f';{"min-height" if p.get("hmin") else "height"}:{p["h"]}in'
        # z is an integer layer: below 0 sits under the text, above 0 over it.
        s += f';z-index:{int(p.get("z", 1))}'
        # Rotation on its OWN property, not inside `transform`. A designed
        # piece can wear a class transform — .lc-right's translateY(-50%) —
        # and one inline `transform` REPLACES it: rotating such a callout by a
        # hundredth of a degree dropped its translate and the piece jumped
        # half its own height, both live and on the page. `rotate` composes
        # with whatever the stylesheet said instead. Same reasoning as the
        # entrance keyframes, which animate translate/scale for exactly this
        # reason (see anim_css) — and the same reason scale and flip STAY in
        # transform: those keyframes own the `scale` property, and a grown
        # entrance would otherwise erase a graphic's own scale mid-flight.
        if p.get("rot"):
            s += f';rotate:{p["rot"]}deg'
        # One transform declaration for the rest of it: a second would silently
        # replace the first, which is exactly how a flip would eat a scale.
        # Default (centre) origin, so scale grows a graphic from its middle and
        # rotate/flip pivot in place — one origin that suits every operation.
        tf = []
        if p.get("scale") is not None and float(p["scale"]) != 1:
            tf.append(f'scale({p["scale"]})')
        if p.get("flip"):
            f = p["flip"]
            tf.append(f'scale({-1 if "h" in f else 1},{-1 if "v" in f else 1})')
        if tf:
            s += f';transform:{" ".join(tf)}'
        if p.get("alpha") is not None:
            s += f';opacity:{p["alpha"]:g}'
        return s

    def tgl_arrow(self, box_id: str, edit: bool) -> str:
        """The expand button's chevron: down when the section is shut, up when
        it is open (the .ds-tgl-on rule turns it).

        A real inline SVG rather than the ▾ glyph it used to be, for two
        reasons. It is drawn art, so it takes a colour of its own — keyed
        under `tglarrow.<box>` in the same `fill` map every other recolourable
        piece of artwork uses, which is what makes it restylable by clicking
        it. And it renders in the EDITOR too, where the button used to have no
        arrow at all: the affordance a reader will see should be the one the
        person placing the button is looking at.

        Its data-el makes it selectable, never movable — see edit.html's
        dragify. Pinning it into positions{} would take it out of the flow and
        out of its own button.
        """
        aid = f"tglarrow.{box_id}"
        ink = self.fill(aid) or "currentColor"
        tag = f' data-el="{aid}"' if edit else ""
        return (f'<svg class="ds-tgl-i ds-tgl-svg" viewBox="0 0 16 16"'
                f' width="1em" height="1em" aria-hidden="true"{tag}>'
                f'<path d="M3.5 6L8 10.5L12.5 6" fill="none" stroke="{ink}"'
                f' stroke-width="2" stroke-linecap="round"'
                f' stroke-linejoin="round"/></svg>')

    def _anchor_once(self) -> str:
        """The anchor runtime, once per document and only when something is
        anchored — a layout with no anchors emits the bytes it always did."""
        if self._anchor_sent or not (self.anchor_hosts or self.reserved):
            return ""
        self._anchor_sent = True
        return f"<script>{ANCHOR_JS % {'w': f'{float(self.page_w):g}'}}</script>"

    def _anim_block(self) -> str:
        """Keyframes for every animated element, and — published only — the
        observer that triggers them on scroll-in. Emitted once, the first
        time any emitter renders while the layout holds an animation.

        The initial hidden state is applied BY THE SCRIPT, never by static
        CSS: a page whose JavaScript never runs (noscript, a blocked file,
        an ancient browser) must show everything, and so must print and a
        reader who asked for reduced motion. The keyframes ship in the
        EDITOR too — elements there stay static, but presentation mode
        replays a slide's entrances from these same rules.

        translate/scale, not transform: an element's rotation lives in its
        transform, and a keyframe that animated transform would silently
        erase it mid-flight.
        """
        if getattr(self, "_anim_emitted", False):
            return ""
        has = (any(b.get("anim") for b in self.boxes)
               or any(x.get("anim") for x in self.shapes)
               or any(t.get("anim") for t in self.tables)
               or any(p.get("anim") for p in self.positions.values()))
        if not has:
            return ""
        self._anim_emitted = True
        css = (
            "@keyframes ds-a-fade{from{opacity:0}to{opacity:1}}"
            "@keyframes ds-a-rise{from{opacity:0;translate:0 14px}"
            "to{opacity:1;translate:0 0}}"
            "@keyframes ds-a-slide-left{from{opacity:0;translate:-18px 0}"
            "to{opacity:1;translate:0 0}}"
            "@keyframes ds-a-slide-right{from{opacity:0;translate:18px 0}"
            "to{opacity:1;translate:0 0}}"
            "@keyframes ds-a-grow{from{opacity:0;scale:.92}"
            "to{opacity:1;scale:1}}"
            "@keyframes ds-a-drop{from{opacity:0;translate:0 -14px}"
            "to{opacity:1;translate:0 0}}"
            "@keyframes ds-a-pop{0%{opacity:0;scale:.85}"
            "70%{opacity:1;scale:1.04}100%{opacity:1;scale:1}}"
            # Bars grow out of the axis they stand on: fill-box so the origin
            # is the bar's own edge, which for a column IS the baseline.
            "@keyframes ds-a-bar{from{scale:1 0}to{scale:1 1}}"
            "@keyframes ds-a-bar-x{from{scale:0 1}to{scale:1 1}}"
            ".ds-cbar{transform-box:fill-box;transform-origin:bottom}"
            ".ds-cbar-x{transform-origin:left}"
            ".ds-anim-wait{opacity:0}"
            ".ds-anim-in{animation-fill-mode:both;"
            "animation-timing-function:cubic-bezier(.2,.7,.3,1)}"
            + "".join(f'.ds-anim-in[data-ds-anim="{k}"]{{animation-name:ds-a-{k}}}'
                      for k in ANIM_KINDS if k not in ANIM_PART_KINDS)
            # A part animation leaves its element alone: the chart, its axes
            # and its labels are all there from the start — only the bars
            # arrive. Higher specificity than .ds-anim-wait, and after it, so
            # the shared opacity:0 never takes hold on one of these.
            + '[data-ds-anim="bars"].ds-anim-wait{opacity:1}'
            '[data-ds-anim="bars"].ds-anim-wait .ds-cbar{scale:1 0}'
            '[data-ds-anim="bars"].ds-anim-wait .ds-cbar-x{scale:0 1}'
            '[data-ds-anim="bars"].ds-anim-in .ds-cbar{animation-name:ds-a-bar;'
            "animation-fill-mode:both;"
            "animation-timing-function:cubic-bezier(.2,.7,.3,1)}"
            '[data-ds-anim="bars"].ds-anim-in .ds-cbar-x'
            "{animation-name:ds-a-bar-x}"
            + "@media print{[data-ds-anim]{opacity:1 !important;"
            "animation:none !important;translate:none !important;"
            "scale:none !important}"
            # A bar left at scale 0 prints as no bar at all — a chart of empty
            # axes, which reads as missing data rather than as missing motion.
            ".ds-cbar{animation:none !important;scale:1 1 !important}}"
            "@media (prefers-reduced-motion:reduce){[data-ds-anim]"
            "{opacity:1 !important;animation:none !important}"
            ".ds-cbar{animation:none !important;scale:1 1 !important}}")
        if os.environ.get("DOCSYNC_EDIT"):
            return f"<style>{css}</style>"
        # Deferred to DOM-ready, NOT run at its own position: this block is
        # emitted by whichever emitter fires first, which is usually the
        # page's shape layer — rendered BEFORE the text boxes in the same
        # section. Run inline, the query saw only the elements above it and
        # everything after was never observed (found by the publish e2e: the
        # top text box simply never animated in).
        script = (
            "(function(){function go(){"
            "if(matchMedia('(prefers-reduced-motion: reduce)').matches)return;"
            "var els=[].slice.call(document.querySelectorAll('[data-ds-anim]'));"
            "if(!els.length||!window.IntersectionObserver)return;"
            "els.forEach(function(e){e.classList.add('ds-anim-wait')});"
            "var io=new IntersectionObserver(function(es){es.forEach(function(en){"
            "if(!en.isIntersecting)return;var e=en.target;io.unobserve(e);"
            "e.style.animationDuration=(e.getAttribute('data-ds-ad')||'.6')+'s';"
            "e.style.animationDelay=(e.getAttribute('data-ds-aw')||'0')+'s';"
            "e.classList.remove('ds-anim-wait');e.classList.add('ds-anim-in');"
            "})},{threshold:.15});"
            "els.forEach(function(e){io.observe(e)})}"
            "if(document.readyState==='loading')"
            "document.addEventListener('DOMContentLoaded',go);else go()"
            "})();")
        return f"<style>{css}</style><script>{script}</script>"

    def chart_spec(self, el_id: str, base: dict) -> dict:
        """What an inline chart should actually draw: the renderer's own spec
        with the editor's changes laid over it.

        Shallow on purpose. A user who recolours a series, retitles a chart or
        turns on data labels changes only those keys, and the numbers stay
        whatever the build computed — which is what a chart driven by
        report_data.json needs, or it would freeze at whatever the figures
        were the day somebody nudged its title. A user who edits the DATA
        does override it, and should: at that point they have said the
        computed number is not the one they want. `docsync.check` is where a
        frozen-number chart gets noticed, not here.
        """
        over = self.charts.get(el_id)
        if not over:
            return dict(base)
        return {**base, **over}

    def attr(self, el_id: str, extra: str = "") -> str:
        """Attributes for an element with no style of its own.

        data-el is stamped only while editing, so the published build carries no
        editing scaffolding; the style appears only when the element has
        actually been moved. `extra` is for declarations the call site computed
        (a recoloured callout's background) — merged here because an element
        with two style attributes silently keeps only the first.
        """
        bits = []
        edit = bool(os.environ.get("DOCSYNC_EDIT"))
        hid = el_id in self.hidden
        if edit:
            bits.append(f'data-el="{el_id}"')
            if hid:
                bits.append('data-hidden="1"')
        p = self.positions.get(el_id)
        css = self._style(p) if p else ""
        if p:
            bits.append(PLACED.strip())
        # The hide css goes LAST: `extra` routinely carries its own display
        # (graphic() passes display:inline-block), and the later declaration
        # in an inline style is the one that wins. In edit mode a hidden
        # element ghosts instead of vanishing — still laid out, still
        # selectable, so Delete can find it again and restore it.
        hide = "display:none" if hid else ""
        both = ";".join(x for x in (css, extra, hide) if x)
        if both:
            bits.append(f'style="{both}"')
        if p and p.get("anim"):
            bits.append(anim_attrs(p["anim"]).strip())
        if p and p.get("anchor"):
            bits.append(anchor_attrs(p).strip())
        # The strut is the renderer's to emit (spacer(), beside the element),
        # and not every renderer does: C.t(), slot_attr() and most L.attr()
        # call sites never learned to. In those reports the editor held the
        # place live during the drag and the next render closed the gap — the
        # whole page lurched up. So the element also SAYS what it reserves,
        # and the runtime (ANCHOR_JS) puts a strut before any such element
        # whose page has none for it. One that spacer() already emitted is
        # found and left alone, so a renderer that does call it changes
        # nothing. Hidden gives the slot back, so it reserves nothing.
        if p and p.get("reserve") and not hid:
            rw = f' data-reserve-w="{p["w"]}"' if p.get("w") else ""
            bits.append(f'data-reserve-for="{el_id}" data-reserve="{p["reserve"]}"{rw}')
        return (" " + " ".join(bits)) if bits else ""

    def spacer(self, el_id: str) -> str:
        """Hold the place of an element that has been moved away.

        Positioning something absolutely takes it out of the flow, so whatever
        followed it slides up into the gap — move the logo and the title beneath
        it jumps. That is never what someone dragging one thing means to do, so
        the vacated slot stays reserved and its neighbours stay put.

        The slot has a width too, not only a height: a branch photo sits in a
        FLEX row beside its card, and reserving only the height let the card
        stretch across the gap the instant the photo moved. 'reserve' (the
        vacated height) and 'w' (the pinned width) together hold the exact box,
        and flex:none stops a flex parent from growing or shrinking it.

        'reserve' is only recorded for elements that were in the flow to begin
        with; an element that was already absolute (a lifecycle callout)
        reserves nothing, because it never occupied flow space. It is a
        different thing from 'h', which is how tall the element should be drawn.
        """
        # A hidden element gives its flow slot back: deleting something should
        # close the gap, in the editor exactly as on the published page.
        if el_id in self.hidden:
            return ""
        p = self.positions.get(el_id)
        if not p or not p.get("reserve"):
            return ""
        wid = f'width:{p["w"]}in;' if p.get("w") else ""
        # Named twice: `data-spacer-for` so the editor can find the strut and
        # re-measure it against the element it stands in for, and as an anchor
        # HOST, `spacer:<id>`, so a moved flow element can follow the place it
        # left. The spacer rides the flow; a pinned inch does not — so when the
        # prose above grew, the strut moved and the heading stayed, over
        # whatever had flowed under it. Anchored to its own vacated slot the
        # piece keeps its distance from the text it came out of.
        return (f'<div class="ds-spacer" data-spacer-for="{el_id}" '
                f'data-anc-host="spacer:{el_id}" style="{wid}height:{p["reserve"]}in;'
                f'flex:0 0 auto" aria-hidden="true"></div>')

    def sec(self, el_id: str) -> str:
        """Attributes for a resizable colored background section.

        Unlike attr(), the element is never taken out of the flow — the only
        override is min-height, so the band can stretch past its content or
        trim back toward it, while the text keeps flowing and the background
        stays glued to the section. data-sec (edit mode only) is what gives
        the editor's bottom-edge grip its target; the style ships in both
        modes so publish honours the drag.

        For sections that carry no style attribute of their own — the caller
        (docsync.propose skips styled sections) must guarantee that, or the
        first style attribute silently wins.
        """
        bits = []
        if os.environ.get("DOCSYNC_EDIT"):
            bits.append(f'data-sec="{el_id}"')
        s = self.sections.get(el_id)
        if s and s.get("h"):
            bits.append(f'style="min-height:{s["h"]}in"')
        return (" " + " ".join(bits)) if bits else ""

    def tag(self, el_id: str) -> str:
        """Just the data-el hook, for elements that already carry a style of
        their own and must merge the override into it rather than grow a second
        style attribute. Hiding for these call sites rides in style(), which is
        what lands inside that one style attribute — tag() only marks the ghost
        for the editor."""
        if not os.environ.get("DOCSYNC_EDIT"):
            return ""
        extra = ' data-hidden="1"' if el_id in self.hidden else ""
        return f' data-el="{el_id}"{extra}'

    def style(self, el_id: str, default: str = "") -> str:
        """For elements the renderer already positions itself (the lifecycle
        callouts): the override wins, otherwise the computed placement stands.
        The tag()/style() pair's half of hiding lives here — appended last so
        it beats whatever display the caller's own css set."""
        p = self.positions.get(el_id)
        css = self._style(p) if p else default
        if el_id in self.hidden:
            css = ";".join(x for x in (css, "display:none") if x)
        return css

    def moved(self, el_id: str) -> bool:
        return el_id in self.positions

    # ---- text ------------------------------------------------------------

    def _check_shape(self, s: dict, where: str, seen: set) -> None:
        """One shape, as the loops below and a master's items both need it."""
        sid = s.get("id")
        if not sid:
            raise LayoutError(f"{where}: needs an 'id'")
        if sid in seen:
            raise LayoutError(f"{where}: duplicate id '{sid}'")
        if "@" in str(sid) and not where.startswith("master"):
            raise LayoutError(f"{where}: an id cannot contain '@' — that marks a "
                              f"master item on a page")
        seen.add(sid)
        if s.get("kind") not in KINDS:
            raise LayoutError(
                f"{where}: kind {s.get('kind')!r} must be one of {', '.join(KINDS)}")
        if not isinstance(s.get("page"), (int, str)) or isinstance(s.get("page"), bool):
            raise LayoutError(f"{where}: 'page' must be a page number or blank-page id")
        for k in ("x", "y", "w", "h"):
            _num(s.get(k), f"{where}.{k}")
        # The look is checked on the RESOLVED shape — the one the page
        # will draw — so a style cannot hand it a value it could not
        # carry; the style's own keys were checked where it is defined.
        _check_object_use(s, "shape", self.object_styles, where)
        _check_shape_look(self.dressed(s), where)
        if s.get("rot") is not None:
            _num(s["rot"], f"{where}.rot")
        if s.get("anim") is not None:
            # A chart is the one shape with parts of its own to animate.
            _anim_check(s["anim"], where,
                        "bars" if s.get("kind") == "chart" else "")
        if s.get("kind") == "chart":
            _check_chart(s.get("chart"), f"{where}.chart")
        if s.get("kind") == "icon":
            check_icon_svg(s.get("svg"), where)
            vb = s.get("vb", "0 0 24 24")
            # The viewBox lands verbatim in an SVG attribute; four numbers
            # or nothing, so a stray quote cannot end the attribute early.
            if not isinstance(vb, str) or not _VIEWBOX_RE.match(vb):
                raise LayoutError(f"{where}: viewBox {vb!r} must be four numbers, "
                                  f"like '0 0 24 24'")
        _z(s)          # a bad layer must fail at load, not mid-render

    def _check_box(self, b: dict, where: str, seen: set) -> None:
        """One text box, as the loops below and a master's items both need it."""
        bid = b.get("id")
        if not bid:
            raise LayoutError(f"{where}: needs an 'id'")
        # One namespace with shapes: the editor resolves an id to a thing by
        # searching both, so a collision makes the right-click menu act on
        # whichever it happens to find first.
        if bid in seen:
            raise LayoutError(f"{where}: duplicate id '{bid}' — already a shape")
        if "@" in str(bid) and not where.startswith("master"):
            raise LayoutError(f"{where}: an id cannot contain '@' — that marks a "
                              f"master item on a page")
        seen.add(bid)
        if not isinstance(b.get("page"), (int, str)) or isinstance(b.get("page"), bool):
            raise LayoutError(f"{where}: 'page' must be a page number or blank-page id")
        for k in ("x", "y", "w"):
            _num(b.get(k), f"{where}.{k}")
        if b.get("anim") is not None:
            _anim_check(b["anim"], where)
        if b.get("h") is not None:      # optional min-height (never clips)
            _num(b["h"], f"{where}.h")
        if not str(b.get("md", "")).strip():
            raise LayoutError(f"{where}: has no text — 'md' is empty")
        # A box may ACT: 'pdf' (a Download-PDF button) or 'toggle' (an
        # expandable section — the button shows/hides another box). An
        # allowlist, because act lands in the published page as behaviour
        # — an unknown value must be a loud error here, not a dead button
        # discovered by a reader.
        if b.get("act") is not None and b["act"] not in ("pdf", "toggle", "endnotes"):
            raise LayoutError(f"{where}: unknown act '{b['act']}' — "
                              "'pdf', 'toggle' or 'endnotes'")
        if b.get("act") == "toggle":
            tgt = b.get("target")
            tgts = tgt if isinstance(tgt, list) else [tgt] if tgt else []
            if not tgts:
                raise LayoutError(f"{where}: act 'toggle' needs a 'target' "
                                  "— the id (or list of ids) it reveals")
            if b.get("tglSpeed") is not None:
                spd = _num(b["tglSpeed"], f"{where}.tglSpeed")
                if not 0.1 <= spd <= 2:
                    raise LayoutError(f"{where}: tglSpeed {spd!r} — "
                                      "seconds, 0.1 to 2")
            known = ({x.get("id") for x in self.boxes}
                     | {x.get("id") for x in self.shapes}
                     | {x.get("id") for x in self.tables})
            for one in tgts:
                if not re.match(r"^[A-Za-z0-9_-]+$", str(one)):
                    raise LayoutError(f"{where}: target '{one}' — letters, "
                                      "digits, - and _ only (it lands "
                                      "inside the button's own script)")
                if one == bid:
                    raise LayoutError(f"{where}: a toggle cannot reveal itself")
                if one not in known:
                    raise LayoutError(f"{where}: target '{one}' is not a "
                                      "box, shape or table on this layout")
        if "z" in b and not isinstance(b["z"], int):
            raise LayoutError(f"{where}: z {b['z']!r} is not a layer number")
        if b.get("rot") is not None:
            _num(b["rot"], f"{where}.rot")
        if b.get("anchor") is not None:
            _check_anchor(b["anchor"], where)
        _check_wrap(b, where)
        _check_object_use(b, "box", self.object_styles, where)
        _check_box_look(self.dressed(b), where)

    @staticmethod
    def _check_master_item(it: dict, where: str) -> None:
        """What a master item may not be: anchored (it is placed by the page,
        not by a paragraph), acting (a button copied onto every page is a
        button nobody meant), or named with the @ the page-instance ids use."""
        if "@" in str(it.get("id", "")):
            raise LayoutError(f"{where}: an id cannot contain '@' — that marks a "
                              f"master item on a page")
        for k in ("anchor", "wrap", "act"):
            if it.get(k) is not None:
                raise LayoutError(f"{where}: a master item cannot carry '{k}'")

    def _page_label(self, page) -> str:
        """The number a folio shows for this page: its place in the final
        order when one is set, otherwise the designed number itself."""
        order = self.pages.get("order") or []
        if page in order:
            return str(order.index(page) + 1)
        return str(page) if isinstance(page, int) and not isinstance(page, bool) else ""

    def master_items(self, page, kind: str) -> list:
        """The boxes or shapes the master this page uses puts on it, as page
        instances: id `<item>@<page>`, this page, `{page}` in a box's words
        replaced by the page's number. Stamped `_master` so edit mode can say
        what they are. A page using no master gets an empty list — and so a
        layout without masters renders exactly as it did."""
        name = self.page_masters.get(str(page))
        m = self.masters.get(name) if name else None
        if not m:
            return []
        out = []
        for it in m.get(kind) or []:
            x = dict(it, id=f"{it['id']}@{page}", page=page, _master=name)
            if kind == "boxes":
                x["md"] = str(x.get("md", "")).replace("{page}", self._page_label(page))
            out.append(x)
        return out

    def dressed(self, obj: dict) -> dict:
        """A shape, box or table with the object style it wears folded in —
        the ONE place `use` on an object is honoured, so the three renderers
        cannot disagree about what wearing a style means. An object wearing
        nothing is returned as itself."""
        return resolve_object(obj, self.object_styles)

    def styled_as(self, st) -> dict:
        """A style as authored, with any named style it uses folded in. The
        one place a `use` is honoured, so a slot, a text box and a table cannot
        disagree about what wearing a style means."""
        return resolve_style(st or {}, self.text_styles)

    def text_style(self, key: str) -> str:
        """The CSS for one slot's text, or "" when it was never styled."""
        return text_css(self.styled_as(self.text.get(key)))

    def text_attr(self, key: str) -> str:
        """ style="…" for a slot, or "" — never style="", which would change
        the bytes of a report nobody has styled."""
        css = self.text_style(key)
        return f' style="{css}"' if css else ""

    def styled(self, key: str) -> bool:
        return bool(self.text.get(key))

    def unknown_text_keys(self, styleable: set) -> list:
        """Styles aimed at slots the report cannot carry a style on.

        The renderer builds a few slots into a string before they reach the page
        (a caption that gets sliced, a label inside SVG). A style on those does
        nothing at all — silently. Better to say so.
        """
        return sorted(k for k in self.text if k not in styleable)

    def font_link(self) -> str:
        """The Google Fonts <link>, covering the brand's fonts plus anything a
        style asks for.

        It was a hardcoded literal. It has to keep producing that exact literal
        when nothing is styled — the head of an unstyled report must not move a
        byte — while also actually requesting a weight someone picks. Today
        Barlow 400 would simply be faked; now it is fetched.
        """
        want: dict[str, set] = {f: set(ws) for f, ws in BRAND_FONTS.items()}
        ital: dict[str, set] = {f: set(ws) for f, ws in BRAND_ITALICS.items()}
        # Slot styles AND box styles: a text box carries its style inline
        # (b["style"]), not in self.text, and skipping those meant a font a
        # box asked for was silently faked in the published page — right in
        # the editor (which loads every family), wrong everywhere else.
        # Resolved, because a slot that only says `use: "Heading"` names no
        # font of its own — and the family Heading asks for would have been
        # faked in the published page while looking right in the editor,
        # which loads every family. Named styles are scanned whole so one
        # defined and not yet worn still travels with the document.
        styles = ([self.styled_as(st) for st in self.text.values()]
                  + [self.styled_as(self.dressed(b).get("style")) for b in self.boxes]
                  + [self.styled_as(self.dressed(t).get("style")) for t in self.tables]
                  + [self.styled_as(dict(st, use=st.get("from")))
                     for st in self.text_styles.values()]
                  + [self.styled_as(st["style"]) for st in self.object_styles.values()
                     if isinstance(st.get("style"), dict)]
                  + [self.styled_as(self.dressed(b).get("style"))
                     for m in self.masters.values() for b in (m.get("boxes") or [])])
        for st in styles:
            fam = st.get("font")
            if not fam:
                continue
            w = int(st.get("weight") or 400)
            (ital if st.get("italic") else want).setdefault(fam, set()).add(w)
            want.setdefault(fam, set())
        parts = []
        for fam in list(BRAND_FONTS) + [f for f in want if f not in BRAND_FONTS]:
            roman, italic = sorted(want.get(fam, set())), sorted(ital.get(fam, set()))
            if not roman and not italic:
                continue
            name = fam.replace(" ", "+")
            if italic:
                axis = ";".join([f"0,{w}" for w in roman] + [f"1,{w}" for w in italic])
                parts.append(f"family={name}:ital,wght@{axis}")
            else:
                parts.append(f"family={name}:wght@{';'.join(str(w) for w in roman)}")
        return ('<link href="https://fonts.googleapis.com/css2?'
                + "&".join(parts) + '&display=swap" rel="stylesheet">')

    # ---- fills -----------------------------------------------------------

    def fill(self, el_id: str, default: str = "") -> str:
        """The colour an element should actually be painted.

        This has to be answered in Python, not patched onto the DOM afterwards,
        and that is not a preference. is_light_bg() reads a tile's luminance at
        build time to decide whether its text is white or charcoal — and the
        footnote pills ride the same class. Recolour a tile in the browser and
        that decision does not re-run: you get white text on a pale tile, which
        is not "wrong colour", it is invisible.
        """
        return self.fills.get(el_id) or default

    def refilled(self, el_id: str) -> bool:
        return el_id in self.fills

    # ---- pages -----------------------------------------------------------

    def page_order(self, designed: int) -> list:
        """The final page sequence: designed ids and blank ids, in order.

        With no override this is 1..designed exactly — the byte-identity case.
        A designed number outside the report is refused here, at render, where
        the count is finally known.
        """
        order = self.pages.get("order")
        if not order:
            return list(range(1, designed + 1))
        for pid in order:
            if isinstance(pid, int) and not 1 <= pid <= designed:
                raise LayoutError(f"pages.order: this report has pages "
                                  f"1–{designed}, not {pid}")
        return list(order)

    def blank_ids(self) -> list:
        return [b["id"] for b in (self.pages.get("blanks") or [])]

    def page_copy(self, page) -> dict | None:
        """The stored copy a blank page carries, or None (see pagecopy.py)."""
        for b in (self.pages.get("blanks") or []):
            if str(b.get("id")) == str(page) and isinstance(b.get("copy"), dict):
                return b["copy"]
        return None

    def copy_html(self, page) -> str:
        """A copied designed page's markup, filled from the current document.
        Empty for every page that is not one, so no other sheet moves a byte.
        Without a Content bound there are no words to fill it from — the
        renderer built its Layout unbound — and the copy is skipped rather
        than half-drawn."""
        cp = self.page_copy(page)
        if not cp or not cp.get("html"):
            return ""
        C = getattr(self, "_content", None)
        if C is None:
            return ""
        from .pagecopy import fill
        return fill(cp["html"], self, C)

    def pagemeta(self, pages) -> str:
        """Declare this report's DESIGNED pages to the editor's page strip.

        The strip is the page-order editor, so it has to know what exists
        before any of it is reordered — including pages currently HIDDEN,
        which are omitted from the order and so are absent from the DOM
        entirely. Nothing in the rendered page can carry that, which is why it
        is declared rather than inferred.

        Only the Primer's own renderer emitted this, so every other report
        opened with no strip at all — no page thumbnails, no reordering, and
        no route back to a page once hidden. One call puts a renderer on the
        same footing.

        `pages` is designed ids (blanks live in layout.json and the editor
        already knows them), or (id, label) pairs when the pages have names
        worth showing in the strip. Edit-mode only, like data-el and the
        mount markers, so the published bytes cannot move.
        """
        if not os.environ.get("DOCSYNC_EDIT"):
            return ""
        out = []
        for p in pages:
            pid, label = (tuple(p) + (None,))[:2] if isinstance(p, (tuple, list)) else (p, None)
            out.append({"id": pid, "label": label} if label else {"id": pid})
        return ('<script type="application/json" id="ds-pagemeta">'
                + json.dumps(out) + "</script>")

    def notices(self, messages) -> str:
        """Things the EDITOR should say to whoever opens this report — once.

        Conversion is what needs this. Turning a document into a project means
        making choices someone may want to revisit — a page size taken from the
        first page when later ones differ, text whose font could not be carried
        across — and the place to say so is the editor, where the report is
        looked at, not a line of terminal output from a command run once and
        scrolled away.

        Dismissable there, and remembered dismissed, because a notice that
        cannot be got rid of becomes furniture and stops being read.

        Edit-mode only, like pagemeta: the published page carries none of it.
        """
        msgs = [str(m).strip() for m in (messages or []) if str(m).strip()]
        if not msgs or not os.environ.get("DOCSYNC_EDIT"):
            return ""
        return ('<script type="application/json" id="ds-notices">'
                + json.dumps(msgs) + "</script>")

    def fill_tag(self, el_id: str) -> str:
        """The editor's right-click hook for recolourable surfaces.

        The editor must not carry a list of what is fillable — that would be
        report knowledge inside a generic tool, and the first new report would
        prove it wrong. The renderer stamps data-fill on exactly the elements
        whose colour it actually consults, so the page itself is the contract.
        Edit mode only, like data-el.
        """
        return f' data-fill="{el_id}"' if os.environ.get("DOCSYNC_EDIT") else ""

    def fill_attr(self, el_id: str) -> str:
        """fill_tag plus the background itself, for surfaces whose colour lives
        in CSS rather than in an inline style the renderer already writes (a
        page section). Emits nothing when unfilled outside edit mode, so the
        published bytes cannot move."""
        bits = []
        if os.environ.get("DOCSYNC_EDIT"):
            bits.append(f'data-fill="{el_id}"')
        if self.refilled(el_id):
            bits.append(f'style="background:{fill_css(self.fills[el_id])}"')
        return (" " + " ".join(bits)) if bits else ""

    # ---- images ----------------------------------------------------------

    def img_src(self, el_id: str, default: str) -> str:
        """The file an image element actually shows — replaced or designed."""
        return (self.imgs.get(el_id) or {}).get("src") or default

    def img_css(self, el_id: str) -> str:
        """Radius and colour-filter declarations for one image, or ""."""
        g = self.imgs.get(el_id) or {}
        out = []
        if g.get("radius"):
            out.append(f'border-radius:{g["radius"]}in')
        f = g.get("filter") or {}
        fx = []
        if f.get("bright") is not None:
            fx.append(f'brightness({f["bright"]:g})')
        if f.get("contrast") is not None:
            fx.append(f'contrast({f["contrast"]:g})')
        if f.get("sat") is not None:
            fx.append(f'saturate({f["sat"]:g})')
        if f.get("gray"):
            fx.append(f'grayscale({f["gray"]:g})')
        if fx:
            out.append("filter:" + " ".join(fx))
        return ";".join(out)

    def cropped(self, el_id: str):
        """The crop window's inner-image geometry, or None.

        Absolute inches, deliberately: imgW is how wide the full image is
        drawn, dx/dy how far the window sits into it. The editor measures
        these against the rendered page, so this code never needs to know a
        source file's pixel size."""
        return (self.imgs.get(el_id) or {}).get("crop")

    # ---- free-floating text ---------------------------------------------

    def text_boxes(self, page: int) -> str:
        """Text that belongs to the layout rather than to the prose.

        A slot says what the report always says; a box is a note someone put on
        one page. That is why it lives here and not in content.md — and the
        price of that is real: it never reaches the bound Google Doc, so an
        editor working there will never see it.

        Height is opt-in via `h`, and only ever a MIN-height: a box grows to at
        least that tall (so a coloured panel can be sized on all sides in the
        editor) but never clips — if the words are taller than `h`, the box
        grows past it. A box with no `h` is auto-height, as before.
        """
        mine = ([b for b in self.boxes if b.get("page") == page]
                + self.master_items(page, "boxes"))
        edit = bool(os.environ.get("DOCSYNC_EDIT"))
        # WHERE a new element may land, and under which number. Only the
        # Primer's own renderer stamps data-page on its sections; demo-report,
        # rxkids, our-mission, tax-testimony and everything docsync.scaffold
        # builds stamp nothing, so the editor was left guessing the page for
        # every insert — and guessed `undefined`. The first text box added to
        # any of them carried no page at all and the validator refused the
        # whole draft ("'page' must be a page number or blank-page id"), which
        # is how a brand-new report bricked on the first thing someone added.
        #
        # A renderer calls this with the very number it will look boxes up by,
        # so that number — stamped where the editor can read it — is the
        # truth, and it beats counting sections: a sheet the renderer never
        # mounts (demo-report's endnotes page) gets no marker, so nothing can
        # be dropped into a page that would silently swallow it. Edit-mode
        # only; the published page is byte-identical to before.
        mount = (f'<div class="ds-mount" data-ds-mount="{page}"'
                 ' style="display:none" aria-hidden="true"></div>') if edit else ""
        if not mine:
            return mount
        # An image inside a box (the Insert-image flow stores one as a box
        # whose markdown is just the image) is sized BY the box: the box's
        # width is the one thing the editor's resize drags, so the picture
        # must follow it. Engine-owned so it holds in every project, not
        # just ones whose own stylesheet happens to style .inline-img.
        out = [mount, self._anim_block(), self._anchor_once(),
               '<style>.ds-textbox img.inline-img{display:block;width:100%;'
               'height:auto;margin:0}</style>']
        # Acting boxes are real controls in the PUBLISHED page, so they carry
        # their own two rules once: the hand cursor, and absence from print —
        # a Download-PDF button rendered INTO the PDF it downloads is the
        # snake eating itself. Self-contained, like everything a box emits,
        # so it holds in a minimal scaffolded renderer with no CSS of its own.
        if any(b.get("act") for b in mine):
            # Toggle mechanics ride along: a target opens with a real height
            # transition (max-height, JS-measured — see the shared dsTgl
            # below), the button's arrow turns, and PRINT shows everything —
            # collapsed content is part of the document; hiding it is a
            # screen affordance, and a PDF with invisible sections would read
            # as missing content.
            #
            # ONE rule for box, table AND shape targets alike, on purpose: a
            # <div> or <table> genuinely has a height that max-height can
            # animate, but an SVG shape (positioned by the page's own
            # viewBox, not by document flow) has no CSS box height for
            # max-height to act on at all — the property is simply inert
            # there. Only opacity ever did anything for a shape target, on
            # this rule or the display:none it replaced, so one shared rule
            # costs nothing and a shape still fades correctly.
            out.append('<style>.ds-actbtn{cursor:pointer}'
                       '@media print{.ds-actbtn{display:none}}'
                       '.ds-tglable{overflow:hidden;max-height:0;opacity:0;'
                       'transition:max-height var(--ds-tgl-d,.3s) '
                       'cubic-bezier(.2,.7,.3,1),opacity var(--ds-tgl-d,.3s) ease}'
                       '.ds-tglable.ds-tgl-open{opacity:1}'
                       '.ds-tgl-i{display:inline-block;margin-left:.35em;'
                       'vertical-align:-.12em;'
                       'transition:transform var(--ds-tgl-d,.3s)}'
                       '.ds-tgl-on .ds-tgl-i{transform:rotate(180deg)}'
                       '@media print{.ds-tglable{max-height:none!important;'
                       'opacity:1!important;overflow:visible!important}}'
                       '@media (prefers-reduced-motion:reduce){'
                       '.ds-tglable{transition:none!important}'
                       '.ds-tgl-i{transition:none!important}}</style>')
            if not edit and not getattr(self, "_tgl_script_emitted", False):
                self._tgl_script_emitted = True
                # max-height cannot transition FROM 'none', and a fixed
                # generous max-height (the no-JS way to fake this) makes the
                # visible animation finish in whatever sliver of the duration
                # it takes the real content height to pass it — for typical
                # short content that reads as an instant snap, not a 0.3s
                # ease. So this measures: scrollHeight keeps reporting the
                # full laid-out content height even while max-height:0 is
                # clipping it, so opening reads it BEFORE animating to it,
                # and closing writes the CURRENT rendered height first (with
                # a forced reflow so the browser commits it) before dropping
                # to 0 — every close starts from a real number, never 'none'.
                out.append(
                    '<script>function __dsTgl(btn,ids,dur){'
                    "var open=!btn.classList.contains('ds-tgl-on');"
                    'ids.forEach(function(id){'
                    'var el=document.getElementById(id);if(!el)return;'
                    'if(open){'
                    "el.style.maxHeight=el.scrollHeight+'px';"
                    'var done=function(e){'
                    "if(e.target!==el||e.propertyName!=='max-height')return;"
                    "el.style.maxHeight='none';"
                    "el.removeEventListener('transitionend',done)};"
                    "el.addEventListener('transitionend',done)"
                    '}else{'
                    "el.style.maxHeight=el.scrollHeight+'px';"
                    'void el.offsetHeight;'
                    "el.style.maxHeight='0px'}"
                    "el.classList.toggle('ds-tgl-open',open);"
                    "el.setAttribute('aria-hidden',String(!open));"
                    "el.toggleAttribute('inert',!open)});"
                    "btn.classList.toggle('ds-tgl-on',open);"
                    "btn.setAttribute('aria-expanded',String(open))}"
                    '</script>')
        for b in mine:
            b = self.dressed(b)
            act = b.get("act")
            an = anim_attrs(b.get("anim")) + anchor_attrs(b)
            # border-box, always: w and h are what the editor MEASURED — the
            # outer rect its handles were drawn around. Read as content width
            # instead, a filled box rendered its padding wider than the number
            # said, the next resize measured that and wrote it back, and the
            # box grew by its own padding (.24in) on every drag. It also keeps
            # this div the same width as the <button> the reader gets for an
            # acting box, which has carried border-box from the start.
            css = (f'box-sizing:border-box;position:absolute;'
                   f'left:{b["x"]}in;top:{b["y"]}in;'
                   f'width:{b["w"]}in;z-index:{int(b.get("z", 2))}')
            if b.get("h"):
                css += f';min-height:{b["h"]}in'
            if b.get("fill"):
                # A background needs breathing room or the words sit on its
                # edge; padding only when filled, so a plain box's text keeps
                # sitting exactly where it was put.
                css += f';background:{fill_css(b["fill"])};padding:.08in .12in;border-radius:8px'
            elif act and act != "endnotes":
                # A button reads as a button even unfilled: same room, same
                # corners, in BOTH modes, so what the editor shows is what
                # the reader gets. Not the endnotes section — it is a block of
                # the document, not a control, and button padding on it just
                # indents the list away from everything it sits under.
                css += ';padding:.08in .12in;border-radius:8px'
            if b.get("rot"):
                css += f';rotate:{b["rot"]}deg'      # own property — see _style
            if b.get("alpha") is not None:
                css += f';opacity:{b["alpha"]:g}'
            if b.get("shadow"):
                css += f';box-shadow:{shadow_css(b["shadow"])}'
            # The look keys object styles brought. Each is written AFTER the
            # fill's own padding/radius above, so the later declaration wins
            # — an explicit pad replaces the default breathing room, and a box
            # that names none of these emits nothing new.
            if b.get("pad") is not None:
                css += ";" + box_pad_css(b["pad"])
            if b.get("radius") is not None:
                css += f';border-radius:{float(b["radius"]):g}px'
            if b.get("border"):
                bc = box_border_css(b["border"])
                if bc:
                    css += ";" + bc
            # Columns inside one box — CSS sets them, so the words flow between
            # them as they are edited, which is the InDesign two-column page
            # without a text engine. Released on a phone (see mobile_css).
            if b.get("cols") and int(b["cols"]) > 1:
                css += f';column-count:{int(b["cols"])}'
                if b.get("gap") is not None:
                    css += f';column-gap:{float(b["gap"]):g}in'
            if b.get("blend") and b["blend"] != "normal":
                css += f';mix-blend-mode:{b["blend"]}'
            style = text_css(self.styled_as(b.get("style")))
            # Style FIRST, geometry second — the geometry must win their one
            # collision: align's inline-slot compensation appends width:100%
            # (right for a span with no box of its own), and written after the
            # box's own width it silently overrode it, so any aligned text box
            # spanned the whole page — and the drag math, anchored to the box
            # it MEANT to draw, flung it to the left margin.
            full = f'{style + ";" if style else ""}{css}'
            tag = f' data-el="text.{b["id"]}"' if edit else ""
            if edit and b.get("_master"):
                tag += f' data-master="{b["_master"]}"'
            if act == "endnotes":
                # The endnotes SECTION, placed by the editor rather than built
                # into a report's renderer (see Footnotes.endnotes_html). The
                # box's own markdown is the heading — so it retitles, restyles,
                # moves and resizes like any other text box — and the numbered
                # list is generated under it.
                #
                # WHICH list depends on where this call lands relative to the
                # numbering: settled (order_by/resolve already ran, e.g. a
                # renderer that emits its boxes last) means render it now;
                # otherwise leave the mount for resolve() to fill, since the
                # order is not final and rendering here would freeze a partial
                # one. Every renderer hits exactly one of those two paths.
                body = (self._fn.endnotes_html(self)
                        if self._fn is not None and self._fn.settled
                        else Footnotes.MOUNT if self._fn is not None else "")
                out.append(f'<div class="ds-textbox ds-endnotes-sec"{tag}{an}{PLACED} '
                           f'style="{full}">'
                           f'{block_html(b["md"])}{body}</div>')
                continue
            if act == "toggle" and not edit:
                # Published: a real button whose click flips the target's
                # ds-tgl-open class. Inline and dependency-free, like every
                # other behaviour a box ships. The target id is validated at
                # load, so getElementById cannot miss.
                _t = b["target"]
                tgts = _t if isinstance(_t, list) else [_t]
                # The button's own state drives every target to the SAME
                # side, so a list can never drift half-open — and the same
                # call serves one target or ten.
                arr = ",".join(f"'ds-x-{t}'" for t in tgts)
                controls = " ".join(f"ds-x-{t}" for t in tgts)
                spd = b.get("tglSpeed", 0.3)
                # On the BUTTON's own style, not the target's: the arrow's
                # rotation rides this element, and a var read here is set
                # once at render time rather than by JS on every click.
                full_btn = f'{full};--ds-tgl-d:{spd:g}s'
                out.append(
                    f'<button type="button" class="ds-actbtn ds-tglbtn"{an}{PLACED} '
                    f'onclick="__dsTgl(this,[{arr}],{spd:g})" '
                    f'aria-expanded="false" aria-controls="{controls}" '
                    f'style="{full_btn};display:block;border:0;'
                    f'font-family:inherit;box-sizing:border-box">'
                    f'{paragraph(b["md"])}'
                    f'{self.tgl_arrow(b["id"], edit)}</button>')
                continue
            if act and not edit:
                # Published: a REAL button. window.print(), the same never-
                # stale choice blocks.pdf_button makes and for the same
                # reasons — generated from the page being read, no build
                # step, no committed binary, works from any host. The label
                # is the box's own markdown, collapsed to one line: a button
                # is a label, not a column of paragraphs.
                out.append(
                    f'<button type="button" class="ds-actbtn noprint"{an}{PLACED} '
                    f'onclick="window.print()" '
                    f'title="Opens your browser\'s print dialog — choose '
                    f'Save as PDF" '
                    f'style="{full};display:block;border:0;'
                    f'font-family:inherit;box-sizing:border-box">'
                    f'{paragraph(b["md"])}</button>')
                continue
            # In the EDITOR an acting box stays a plain div on purpose: a
            # live <button> would be excluded from the canvas's drag-start
            # guard (real controls must keep working mid-edit), which is
            # exactly how it would become unmovable. A toggle TARGET likewise
            # stays visible there: collapsed content you cannot see is
            # content you cannot edit.
            klass = "ds-textbox"
            extra = ""
            tglFull = full
            if not edit and b["id"] in self.toggle_targets:
                klass += " ds-tglable"
                extra = f' id="ds-x-{b["id"]}"'
                tglFull = f'{full};--ds-tgl-d:{self.toggle_speed.get(b["id"], 0.3):g}s'
            # A toggle button keeps its chevron on the editor canvas too —
            # it is part of what the button IS, and it is the thing you click
            # to recolour it. Shut-side (pointing down), because that is the
            # state a reader meets the button in.
            arrow = self.tgl_arrow(b["id"], edit) if act == "toggle" else ""
            out.append(f'<div class="{klass}"{extra}{tag}{an}{PLACED} '
                       f'style="{tglFull}">'
                       f'{block_html(b["md"])}{arrow}</div>')
        return "".join(out)

    def box(self, box_id: str) -> dict | None:
        return next((b for b in self.boxes if b.get("id") == box_id), None)

    def bind_content(self, content) -> None:
        """Called by Content's constructor with itself.

        A copied designed page (pagecopy.py) re-renders its slots from
        content.md on every build, and layer() — the one hook every renderer
        emits inside every sheet — is where that copy is drawn. Layout has no
        words of its own, so Content lends it the document, the same way it
        lends its Footnotes just below.
        """
        self._content = content

    def bind_footnotes(self, fn) -> None:
        """Called by Content's constructor with its Footnotes.

        The endnotes list is content (which sources, in which order) drawn as
        layout (a placed, movable box), so the two have to meet somewhere.
        Here, once, rather than in every report's renderer — a consumer repo
        vendors this package but owns its renderer, so anything that needed a
        line added THERE would not reach the reports that already exist.

        The link runs BOTH ways: resolve() fills a deferred endnotes mount
        without a Layout in hand, and the list has to carry the same data-el
        drag hooks either way, or reordering would work on one render path
        and silently not on the other.
        """
        self._fn = fn
        fn.layout = self

    # ---- tables ----------------------------------------------------------

    def tables_html(self, page: int) -> str:
        """A placed, editable grid. Like a text box, it is absolutely positioned
        in inches and width-driven — the rows set the height. `rows` is a grid
        of cell markdown; `header` makes the first row a <th> band. Each cell
        carries a data-cell hook in edit mode so the editor can edit it in
        place."""
        mine = [t for t in self.tables if t.get("page") == page]
        if not mine:
            return ""
        edit = bool(os.environ.get("DOCSYNC_EDIT"))
        out = []
        for t in mine:
            t = self.dressed(t)
            css = (f'box-sizing:border-box;position:absolute;'
                   f'left:{t["x"]}in;top:{t["y"]}in;'
                   f'width:{t["w"]}in;z-index:{int(t.get("z", 2))}')
            if t.get("rot"):
                css += f';rotate:{t["rot"]}deg'      # own property — see _style
            if t.get("alpha") is not None:
                css += f';opacity:{t["alpha"]:g}'
            if t.get("blend") and t["blend"] != "normal":
                css += f';mix-blend-mode:{t["blend"]}'
            style = text_css(self.styled_as(t.get("style")))
            tag = f' data-el="table.{t["id"]}"' if edit else ""
            header = bool(t.get("header"))
            rows = t.get("rows", [])
            ncols = len(rows[0]) if rows else 0
            border = t.get("border")
            outer = table_border_css(border, "outer")
            inner = table_border_css(border, "inner")
            over = t.get("cells") or {}
            # Column widths as a <colgroup>: percentages of the table's own
            # width, so dragging a column divider never changes the table's
            # placement on the page.
            colgroup = ""
            if t.get("colw") and ncols:
                total = sum(t["colw"]) or 1
                colgroup = "<colgroup>" + "".join(
                    f'<col style="width:{w / total * 100:.4f}%">' for w in t["colw"]
                ) + "</colgroup>"
            body = ""
            for ri, row in enumerate(rows):
                cells = ""
                for ci, c in enumerate(row):
                    th = header and ri == 0
                    name = "th" if th else "td"
                    hook = f' data-cell="{ri},{ci}"' if edit else ""
                    # Each edge picks the outer rule on the grid's rim and the
                    # inner rule between cells, so "outer only" and "inner
                    # only" both fall out of the same per-cell emit. Emitted
                    # ONLY when the table carries a border spec: without one,
                    # nothing is written and the report's own stylesheet keeps
                    # styling the grid exactly as it always did.
                    cs = ""
                    if border:
                        cs = (f'border-top:{outer if ri == 0 else inner};'
                              f'border-left:{outer if ci == 0 else inner};'
                              f'border-right:{outer if ci == ncols - 1 else inner};'
                              f'border-bottom:{outer if ri == len(rows) - 1 else inner}')
                    ov = over.get(f"{ri},{ci}") or {}
                    # Zebra striping counts from the first BODY row, so a table
                    # stripes the same whether or not it has a header band.
                    band = None
                    if t.get("band") and not th:
                        if (ri - (1 if header else 0)) % 2 == 1:
                            band = t["band"]
                    bg = ov.get("fill") or (t.get("headerFill") if th else None) \
                        or band or t.get("fill")
                    bits = [cs] if cs else []
                    if bg:
                        bits.append(f"background:{bg}")
                    fg = ov.get("color") or (t.get("headerColor") if th else None)
                    if fg:
                        bits.append(f"color:{fg}")
                    if ov.get("align"):
                        bits.append(f'text-align:{ov["align"]}')
                    if ov.get("bold") is not None:
                        bits.append(f'font-weight:{"700" if ov["bold"] else "400"}')
                    if ov.get("italic"):
                        bits.append("font-style:italic")
                    sty = f' style="{";".join(bits)}"' if bits else ""
                    # A cell is ONE string, so a newline in it can only be a
                    # break somebody typed (the cell editor's Shift-Enter).
                    # Prose is the opposite — content.md soft-wraps at about
                    # eighty columns, which is why md_inline itself leaves a
                    # newline alone and paragraphs() closes it up to a space.
                    body_c = md_inline(str(c)).replace("\n", "<br>")
                    cells += f'<{name}{hook}{sty}>{body_c}</{name}>'
                body += f"<tr>{cells}</tr>"
            klass = "ds-table"
            extra = ""
            tglSty = ""
            if not edit and t["id"] in self.toggle_targets:
                klass += " ds-tglable"
                extra = f' id="ds-x-{t["id"]}"'
                tglSty = f';--ds-tgl-d:{self.toggle_speed.get(t["id"], 0.3):g}s'
            out.append(f'<table class="{klass}"{extra}{tag}{PLACED}'
                       f'{anim_attrs(t.get("anim"))}{anchor_attrs(t)} '
                       f'style="{css}{";" + style if style else ""}{tglSty}">'
                       f'{colgroup}{body}</table>')
        return "".join(out)

    def table(self, table_id: str) -> dict | None:
        return next((t for t in self.tables if t.get("id") == table_id), None)

    # ---- shapes ----------------------------------------------------------

    def page_style(self) -> str:
        """The page-size override as CSS, or "" when there is none.

        Both halves matter: `.page` is the box on screen and in the editor,
        `@page` is the sheet the PDF is printed on, and a report whose preview
        and print size disagree is worse than one that cannot be resized.

        Only width and min-height are set. `.page`'s own max-width:100% is left
        alone, so a narrow screen still shrinks the sheet to fit instead of
        forcing a horizontal scrollbar on a reader."""
        if not self.page:
            return ""
        w, h = self.page
        box = (f".page{{width:{w}in;min-height:{h}in}}" if h is not None
               else f".page{{width:{w}in;min-height:0}}")
        sheet = (f"@page{{size:{w}in {h}in;margin:0}}" if h is not None
                 else f"@page{{size:{w}in auto;margin:0}}")
        return f"<style>{box}{sheet}</style>"

    def _page_style_once(self) -> str:
        """Rides out with the first layer() of a render.

        Deliberately not something each report's renderer has to remember: a
        consumer repo vendors this package but owns its own renderer, so a
        change that needed a line added THERE would not reach the reports that
        already exist. Every renderer already calls layer() for each page, so
        hanging it off that gets page sizing to all of them for free. A
        <style> in the body is valid and applies document-wide."""
        if self._page_style_sent or not self.page:
            return ""
        self._page_style_sent = True
        return self.page_style()

    def mobile_css(self) -> str:
        """Put everything this module pinned by inch back into the flow, on a
        screen too narrow to hold the sheet.

        A placed element's left/top/width are inches measured from the corner
        of a sheet. `.page` keeps `max-width:100%`, so on a phone the sheet
        narrows but those inches do not — the element stays where an 8.5in page
        would have put it, which is off the right-hand edge and on top of
        whatever is flowing underneath. `.page` is `overflow:hidden` by
        convention, so the reader can neither scroll to the part that ran off
        nor see what it landed on. Releasing the pin is the only thing that
        reads: the document becomes one column in DOM order.

        The breakpoint is the SHEET, not a device width — below it the page can
        no longer show the design at the size it was composed at, which is the
        actual condition, and it is right for a 12.5in web page as well as a
        letter one.

        Three things must ride along. `.ds-spacer` struts hold the flow slot a
        placed element vacated; back in the flow it occupies that space for
        real, so the strut is now a second copy of it. Page furniture that a
        report's own CSS hangs off the bottom of the sheet (a folio, a
        copyright line) lands in the middle of the text once `.page` collapses
        to its content — the engine cannot know those class names, so it
        releases what it can reach and the report's stylesheet handles the
        rest. And a table wide enough to need it gets its own scroller, rather
        than being clipped by the page or shrunk into unreadability.

        Emitted like page_style(): a <style> in the body, riding out with the
        first layer(). Note this is a width-conditional @media, so
        edit.html's exportHtml() resolves it away at VIEW_W — correct, and the
        reason it is safe to add: a frozen Squarespace fragment is not
        responsive and must not carry a phone rule into someone else's page.
        """
        # page_w, NOT self.page: the latter is only layout.json's override, so
        # reading it gave rxkids — a 12.5in web page that never used File >
        # Resize — the 8.5in default, and a phone-sized breakpoint on a sheet
        # that stops fitting at 1200px. page_w is the width the report is
        # actually drawn at, whether that came from the renderer or the
        # override, and it is what print_css() measures too.
        return (
            f"<style>@media screen and (max-width:{float(self.page_w):g}in){{"
            # Two selectors, because there are two ways to get pinned. PLACED
            # stamps what this module emits itself. But style() exists for
            # elements a RENDERER positions — it returns a css string with
            # nowhere to hang an attribute, so a dragged lifecycle callout is
            # pinned by an inline style carrying no stamp. Matching the style
            # too is what makes the release complete rather than nearly
            # complete, and .shape-layer is the one thing it must not reach:
            # that svg IS the page's background, and in the flow it detaches
            # from the page it paints.
            # !important throughout — inline styles outrank class rules.
            "[data-placed],.page [style*=\"position:absolute\"]:not(.shape-layer)"
            "{position:static !important;"
            "left:auto !important;right:auto !important;"
            "top:auto !important;bottom:auto !important;"
            "width:auto !important;max-width:100% !important;"
            "transform:none !important;rotate:none !important;"
            "margin:0 0 12px !important}"
            ".ds-spacer{display:none !important}"
            # Images are this module's business too — Insert image places them
            # and attr() sizes them in inches — and an inch-wide picture is
            # wider than the phone it is now being read on. Releasing the
            # wrapper is not enough on its own: the picture inside it keeps
            # whatever width it was given.
            ".page img{max-width:100% !important;height:auto}"
            # max-width, not width: a narrow table keeps its natural size and
            # only one that genuinely overflows starts scrolling.
            ".page table{display:block;max-width:100%;overflow-x:auto}"
            # Two columns of 11px type in 375px is two unreadable columns.
            # Only when some box asked for columns, so a layout without them
            # keeps emitting the bytes it always did.
            + (".ds-textbox{column-count:auto !important}"
               if any(int(self.dressed(b).get("cols") or 1) > 1 for b in self.boxes)
               else "")
            # A float in a one-column phone page is a figure with three
            # words beside it; back in the flow, like everything else placed.
            + ("[data-wrap]{float:none !important}"
               if any(o.get("wrap") for o in list(self.boxes)
                      + [p for p in self.positions.values() if isinstance(p, dict)])
               else "")
            + "}</style>")

    def _mobile_css_once(self) -> str:
        """Rides out with the first layer(), like _page_style_once() and for
        the same reason.

        NOT conditional on an explicit page size, the way _page_style_once()
        is: a report that never set one still places elements by inch against
        the 8.5in default and has exactly the same problem. It IS conditional
        on something actually being pinned, which keeps this module's promise
        that with no overrides the published HTML is byte-for-byte what it
        always was — a report that placed nothing has nothing to release."""
        if self._mobile_css_sent:
            return ""
        if not (self.positions or self.boxes or self.tables):
            return ""
        self._mobile_css_sent = True
        return self.mobile_css()

    def _chart_tip_once(self) -> str:
        """The hover-tooltip runtime, once per document, when some chart asked
        for tips.

        The report that grew this pattern kept its own copy in web/primer.js —
        which meant a chart drawn by THIS module had no tooltips in any other
        report, and the six figures that wanted them were hand-written SVG
        instead. It rides out with the first layer() for the same reason
        _page_style_once() does: a consumer vendors this package but owns its
        renderer, so anything a renderer had to opt into would never reach the
        reports that already exist.

        Not emitted in edit mode. A tooltip chasing the pointer while someone
        is dragging a chart around the page is noise, and the editor already
        shows the numbers in its own panel.
        """
        if not any(s.get("kind") == "chart" and (s.get("chart") or {}).get("tips")
                   for s in self.shapes):
            return ""
        return self.chart_tip_runtime()

    def chart_tip_runtime(self) -> str:
        """The same runtime, asked for by a caller that already knows it needs
        one. An INLINE chart is not in self.shapes — it is drawn wherever the
        renderer put it — so blocks.chart() has to say so itself, or a page
        whose only chart is inline would carry tooltips with nothing to show
        them."""
        if self._chart_tip_sent or os.environ.get("DOCSYNC_EDIT"):
            return ""
        self._chart_tip_sent = True
        return (
            '<div class="ds-tip" hidden></div>'
            '<style>.ds-tip{position:fixed;z-index:99;pointer-events:none;'
            'background:#2F3E46;color:#fff;font:500 12px/1.35 system-ui,sans-serif;'
            'padding:5px 8px;border-radius:5px;max-width:15em;opacity:0;'
            'transition:opacity .09s}.ds-tip[data-on]{opacity:1}'
            '[data-tip]{cursor:default}@media print{.ds-tip{display:none}}</style>'
            '<script>(function(){var t=document.querySelector(".ds-tip");'
            'if(!t)return;document.addEventListener("mousemove",function(e){'
            'var el=e.target.closest?e.target.closest("[data-tip]"):null;'
            'if(!el){t.removeAttribute("data-on");t.hidden=true;return;}'
            't.hidden=false;t.textContent=el.getAttribute("data-tip");'
            't.setAttribute("data-on","");'
            'var x=Math.min(e.clientX+14,innerWidth-t.offsetWidth-8);'
            't.style.left=Math.max(4,x)+"px";'
            't.style.top=Math.min(e.clientY+16,innerHeight-t.offsetHeight-8)+"px";'
            '});})();</script>')

    def layer(self, page: int) -> str:
        """Shapes for one page, grouped into one SVG per layer. Empty when there
        are none, so a report without shapes renders exactly as before — except
        for the page-size override, which has to reach the document even on a
        page that holds no shapes."""
        head = (self._mobile_css_once() + self._page_style_once()
                + self._chart_tip_once() + self._anchor_once())
        # A blank page that is a COPY of a designed one draws that page's
        # markup first — the designed content the renderer never knew to
        # emit for it — then its own shapes over it, like any other sheet.
        head += self.copy_html(page)
        mine = ([s for s in self.shapes if s.get("page") == page]
                + self.master_items(page, "shapes"))
        if not mine:
            return head
        by_z: dict[int, list] = {}
        for s in mine:
            by_z.setdefault(_z(s), []).append(s)
        return head + "".join(self._svg(by_z[z], z) for z in sorted(by_z))

    def _svg(self, shapes: list, z: int) -> str:
        body = "".join(self._shape(s) for s in shapes)
        # One <defs> per layer, holding any gradient definitions and — as before —
        # the arrowhead marker. With no gradient and no arrow the list is empty
        # and defs is "", so a plain shape layer is byte-for-byte unchanged; with
        # arrows only, the marker is exactly the string it was.
        defbits = [fill_svg_paint(s.get("fill"), f"ds-fill-{s['id']}")[1]
                   for s in shapes if isinstance(s.get("fill"), dict)]
        if any(s.get("kind") == "line" and s.get("ends") not in (None, "none")
               for s in shapes):
            # One arrowhead marker per layer. markerUnits="strokeWidth" scales
            # it with the line's weight; context-stroke paints it the line's
            # own colour (Chrome renders both screen and PDF here). The id
            # carries page and layer so two layers never fight over one def.
            pg = shapes[0].get("page")
            defbits.append(f'<marker id="ds-arr-{pg}-{z}" viewBox="0 0 10 10" '
                           f'refX="8" refY="5" markerUnits="strokeWidth" markerWidth="7" '
                           f'markerHeight="7" orient="auto-start-reverse">'
                           f'<path d="M0,0 L10,5 L0,10 z" fill="context-stroke"/>'
                           f'</marker>')
        defs = f'<defs>{"".join(defbits)}</defs>' if defbits else ""
        return self._anim_block() + (
                f'<svg class="shape-layer" style="position:absolute;left:0;top:0;'
                f'width:{self.page_w}in;height:{self.page_h}in;pointer-events:none;'
                f'z-index:{z}" viewBox="0 0 {self.page_w} {self.page_h}">{defs}{body}</svg>')

    def _shape(self, s: dict) -> str:
        s = self.dressed(s)
        x, y, w, h = (float(s[k]) for k in ("x", "y", "w", "h"))
        # Publish-mode hook for a shape some toggle reveals — on the same one
        # node that carries data-shape, whatever kind it is. .ds-tglable's
        # rule applies to SVG elements exactly as it does to HTML ones —
        # `max-height` is simply inert there (a shape has no box-model height
        # for it to act on), so only its opacity half ever does anything.
        # No --ds-tgl-d here deliberately: `shadow` below may already add
        # this node's ONE style="…" attribute, and a second style= on the
        # same element would be dropped by the parser, not merged — the
        # default .3s the CSS rule falls back to is worth more than the
        # per-shape speed. A shape target is also not a path either editor
        # UI (addExpandable) currently builds.
        tgl = ("" if os.environ.get("DOCSYNC_EDIT")
               or s["id"] not in self.toggle_targets
               else f' id="ds-x-{s["id"]}" class="ds-tglable"')
        # Animation data attributes ride the same node — in both modes, since
        # presentation replay (an editor feature) reads them there too.
        tgl += anim_attrs(s.get("anim"))
        if os.environ.get("DOCSYNC_EDIT") and s.get("_master"):
            tgl += f' data-master="{s["_master"]}"'
        # A gradient fill becomes fill="url(#…)" and a <defs> entry that _svg
        # collects; a hex/none stays verbatim, so a solid shape is byte-identical.
        fill, _ = fill_svg_paint(s.get("fill"), f"ds-fill-{s['id']}")
        stroke = s.get("stroke", "none")
        sw = s.get("sw", 0.02)
        common = (f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}" '
                  f'data-shape="{s["id"]}"') + tgl
        # Rotation turns about the shape's own centre; the viewBox is in
        # inches, so the pivot is plain geometry. Opacity and shadow ride the
        # same node — a wrapping <g> would put a second element between the
        # editor's data-shape lookups and the thing they mean.
        if s.get("rot"):
            common += (f' transform="rotate({s["rot"]} '
                       f'{round(x + w / 2, 4)} {round(y + h / 2, 4)})"')
        if s.get("alpha") is not None:
            common += f' opacity="{s["alpha"]:g}"'
        # ONE style attribute: a second on the same element is dropped by the
        # parser, not merged, so the shadow and the blend share it.
        sty = []
        if s.get("shadow"):
            sty.append(shape_shadow_css(s["shadow"]))
        if s.get("blend") and s["blend"] != "normal":
            sty.append(f'mix-blend-mode:{s["blend"]}')
        if sty:
            common += f' style="{";".join(sty)}"'
        if s.get("dash"):
            d = s["dash"]
            common += f' stroke-dasharray="{" ".join(str(v) for v in d)}"'
        if s["kind"] == "chart":
            # A <g> wrapper, not a leaf: a chart is many elements, but the
            # editor selects and drags by data-shape, so the id has to be on
            # ONE node that covers the whole drawing. The transparent rect
            # underneath is what gives it a grabbable surface — a chart is
            # mostly empty space, and without it a drag would only catch a bar.
            attrs = [f'data-shape="{s["id"]}"' + tgl]
            if s.get("rot"):
                attrs.append(f'transform="rotate({s["rot"]} '
                             f'{round(x + w / 2, 4)} {round(y + h / 2, 4)})"')
            if s.get("alpha") is not None:
                attrs.append(f'opacity="{s["alpha"]:g}"')
            if s.get("blend") and s["blend"] != "normal":
                attrs.append(f'style="mix-blend-mode:{s["blend"]}"')
            bg = s.get("fill")
            bg = bg if isinstance(bg, str) and bg != "none" else "none"
            # The hit area is the background RECT, not the <g>. A container has
            # no geometry of its own, so pointer-events:bounding-box on it is
            # unreliable; a full-size rect with pointer-events:all is the
            # standard way to make a mostly-empty drawing grabbable, and it is
            # what actually catches the pointer here.
            # Where this drawing was laid out, stamped on the node. A chart is
            # many elements at absolute inch coordinates, so there is no single
            # attribute the editor can nudge to move it — it translates the
            # group by how far it has travelled from this origin, and the next
            # full render redraws it at its new home.
            attrs.append(f'data-ox="{x}" data-oy="{y}" data-ow="{w}" data-oh="{h}"')
            return (f'<g {" ".join(attrs)}>'
                    f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{bg}"'
                    f' pointer-events="all"/>'
                    f'{chart_svg(s.get("chart") or {}, x, y, w, h, s.get("anim"))}</g>')
        if s["kind"] == "icon":
            # A nested <svg> so the icon's own viewBox does the scaling: the
            # glyph fits the box in inches whatever grid it was drawn on.
            # `common` is not reused — its fill/stroke would say nothing here
            # (the markup paints itself in currentColor) and its data-shape is
            # re-emitted below so the editor still finds one node per shape.
            bits = [f'x="{x}"', f'y="{y}"', f'width="{w}"', f'height="{h}"',
                    f'viewBox="{s.get("vb", "0 0 24 24")}"',
                    f'data-shape="{s["id"]}"' + tgl, 'overflow="visible"']
            css = [f'color:{icon_color(s.get("fill"))}']
            if s.get("shadow"):
                css.append(shape_shadow_css(s["shadow"]))
            if s.get("blend") and s["blend"] != "normal":
                css.append(f'mix-blend-mode:{s["blend"]}')
            bits.append(f'style="{";".join(css)}"')
            if s.get("rot"):
                bits.append(f'transform="rotate({s["rot"]} '
                            f'{round(x + w / 2, 4)} {round(y + h / 2, 4)})"')
            if s.get("alpha") is not None:
                bits.append(f'opacity="{s["alpha"]:g}"')
            return f'<svg {" ".join(bits)}>{s.get("svg", "")}</svg>'
        if s["kind"] == "rect":
            r = s.get("r", 0)
            return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" {common}/>'
        if s["kind"] == "ellipse":
            return (f'<ellipse cx="{x + w / 2}" cy="{y + h / 2}" rx="{w / 2}" '
                    f'ry="{h / 2}" {common}/>')
        if s["kind"] == "triangle":
            return (f'<polygon points="{_pts(triangle_points(x, y, w, h))}" {common}/>')
        if s["kind"] == "arrow":
            return (f'<polygon points="{_pts(arrow_points(x, y, w, h))}" {common}/>')
        ends = s.get("ends")
        if ends and ends != "none":
            mk = f"ds-arr-{s.get('page')}-{_z(s)}"
            if ends in ("start", "both"):
                common += f' marker-start="url(#{mk})"'
            if ends in ("end", "both"):
                common += f' marker-end="url(#{mk})"'
        return f'<line x1="{x}" y1="{y}" x2="{x + w}" y2="{y + h}" {common}/>'

    # ---- guardrail -------------------------------------------------------

    def check_bounds(self) -> list[str]:
        """Positions and shapes that fall outside the page.

        `.page` is `overflow: hidden`, so a bad drag does not look broken — the
        content is simply gone. Nothing else would catch that, which is exactly
        why this is a hard failure rather than a warning.

        A rotated element is judged by its rotated bounding box — the corners
        are what get clipped, and at 45 degrees they stand well proud of the
        unrotated frame. That needs a height; where one is not stored (flowed
        prose), the unrotated checks stand and the fit meter owns the rest.
        """
        def rot_aabb(x, y, w, h, deg):
            cx, cy = x + w / 2, y + h / 2
            rad = math.radians(deg)
            hw = abs(w / 2 * math.cos(rad)) + abs(h / 2 * math.sin(rad))
            hh = abs(w / 2 * math.sin(rad)) + abs(h / 2 * math.cos(rad))
            return cx - hw, cy - hh, cx + hw, cy + hh

        bad = []
        for el, p in self.positions.items():
            x, y = float(p["x"]), float(p["y"])
            if not (0 <= x <= self.page_w) or not (0 <= y <= self.page_h):
                bad.append(f"'{el}' sits at {x}in,{y}in — off the "
                           f"{self.page_w}x{self.page_h}in page")
            if p.get("w") and x + float(p["w"]) > self.page_w + 0.01:
                bad.append(f"'{el}' is {p['w']}in wide at x={x}in — "
                           f"{x + float(p['w']) - self.page_w:.2f}in past the right edge")
            if p.get("h") and y + float(p["h"]) > self.page_h + 0.01:
                bad.append(f"'{el}' is {p['h']}in tall at y={y}in — "
                           f"{y + float(p['h']) - self.page_h:.2f}in past the bottom edge")
            if p.get("rot") and p.get("w") and p.get("h"):
                x1, y1, x2, y2 = rot_aabb(x, y, float(p["w"]), float(p["h"]),
                                          float(p["rot"]))
                if x1 < -0.01 or y1 < -0.01 or x2 > self.page_w + 0.01 \
                        or y2 > self.page_h + 0.01:
                    bad.append(f"'{el}' rotated {p['rot']}° swings past the page edge")
        for b in self.boxes:
            x, y, w = (float(b[k]) for k in ("x", "y", "w"))
            if x < 0 or y < 0 or x > self.page_w or y > self.page_h:
                bad.append(f"text box '{b['id']}' sits off page {b['page']}")
            elif x + w > self.page_w + 0.01:
                bad.append(f"text box '{b['id']}' is {w}in wide at x={x}in — "
                           f"{x + w - self.page_w:.2f}in past the right edge")
        for s in self.shapes:
            x, y, w, h = (float(s[k]) for k in ("x", "y", "w", "h"))
            if s.get("rot"):
                x1, y1, x2, y2 = rot_aabb(x, y, w, h, float(s["rot"]))
                if x1 < -0.01 or y1 < -0.01 or x2 > self.page_w + 0.01 \
                        or y2 > self.page_h + 0.01:
                    bad.append(f"shape '{s['id']}' rotated {s['rot']}° swings "
                               f"past page {s['page']}")
            elif x < 0 or y < 0 or x + w > self.page_w + 0.01 or y + h > self.page_h + 0.01:
                bad.append(f"shape '{s['id']}' extends past page {s['page']}")
        return bad
