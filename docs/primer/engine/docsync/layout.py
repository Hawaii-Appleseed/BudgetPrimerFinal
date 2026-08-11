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
               "pie", "donut", "line", "scatter", "histogram",
               "radar", "funnel", "packed", "treemap")
# Which types read a value PER LABEL (one series) rather than per series, and
# so name their key by label the way a pie does.
CHART_BY_ITEM = ("pie", "donut", "funnel", "packed", "treemap", "histogram")
CHART_COLORS = ("#6B9E78", "#52796F", "#95B7A2", "#354F52",
                "#CAD2C5", "#2F3E46", "#A8C4B0", "#7A8E92")


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


def _fmt_num(v: float) -> str:
    """Axis and value labels: no trailing .0, thousands separated."""
    if v == int(v):
        return f"{int(v):,}"
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def _xml(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))

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
        out.append(f'font-size:{st["size"]}px')
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
    """Normalised series: every one has a name, a data list and a colour."""
    out = []
    for i, s in enumerate(c.get("series") or []):
        out.append({
            "name": s.get("name") or f"Series {i + 1}",
            "data": [float(v or 0) for v in (s.get("data") or [])],
            "color": s.get("color") or CHART_COLORS[i % len(CHART_COLORS)],
        })
    return out


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
    fs = max(0.07, min(0.13, h * 0.055))            # label size tracks the box
    parts = []
    top = y
    if title:
        parts.append(f'<text x="{x + w / 2:.4f}" y="{y + fs * 1.1:.4f}" '
                     f'text-anchor="middle" font-size="{fs * 1.25:.4f}" '
                     f'font-weight="700" fill="{c_title}"'
                     f'{_ch_hook(c, "title")}>{_xml(title)}</text>')
        top = y + fs * 2.0
    legend = bool(c.get("legend")) and len(series) > 0
    legend_h = fs * 1.9 if legend else 0.0
    body_h = max(0.2, (y + h) - top - legend_h)

    ink = {"label": c_label, "axis": c_axis, "grid": c_grid}
    args = (c, kind, labels, series, x, top, w, body_h, fs, ink)
    # Only the bar family has bars to grow; every other chart type takes a
    # whole-element entrance instead, which needs nothing drawn into it.
    bar_anim = anim if kind not in ("pie", "donut", "line", "scatter", "radar",
                                    "funnel", "packed", "treemap") else None
    if kind in ("pie", "donut"):
        parts.append(_pie_svg(*args))
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

    if legend:
        ly = y + h - fs * 0.5
        # A by-item chart's legend names the ITEMS (slices, bins, blocks); a
        # bar or line legend names the series. Either way each entry is
        # editable in place — a legend IS the label.
        by_item = kind in CHART_BY_ITEM and series
        keys = ([{"name": labels[i] if i < len(labels) else f"#{i + 1}",
                  "color": _slice_color(c, i), "hook": f"label:{i}"}
                 for i in range(len(series[0]["data"]))]
                if by_item else [{**s, "hook": f"series:{i}"} for i, s in enumerate(series)])
        gap = w / max(1, len(keys))
        for i, k in enumerate(keys):
            kx = x + i * gap
            parts.append(f'<rect x="{kx:.4f}" y="{ly - fs * 0.75:.4f}" '
                         f'width="{fs * 0.72:.4f}" height="{fs * 0.72:.4f}" rx="{fs * 0.16:.4f}" '
                         f'fill="{k["color"]}"/>')
            parts.append(f'<text x="{kx + fs:.4f}" y="{ly - fs * 0.12:.4f}" '
                         f'font-size="{fs * 0.85:.4f}" fill="{c_label}"'
                         f'{_ch_hook(c, k["hook"])}>{_xml(k["name"])}</text>')
    return "".join(parts)


def _slice_color(c: dict, i: int) -> str:
    cols = c.get("colors") or []
    if i < len(cols) and cols[i]:
        return cols[i]
    return CHART_COLORS[i % len(CHART_COLORS)]


def _bars_svg(c, kind, labels, series, x, y, w, h, fs, ink, anim=None) -> str:
    """'bar' = vertical columns, 'row' = horizontal bars ('column' is the old
    name for row), each also available STACKED. Stacking is the same geometry
    with the bars laid end to end instead of side by side, so it shares every
    axis, gridline and label decision rather than forking the whole routine."""
    if not series:
        return ""
    n = max(len(labels), max((len(s["data"]) for s in series), default=0))
    if not n:
        return ""
    stacked = kind.startswith("stacked")
    if stacked:
        # The axis has to reach the tallest TOTAL, not the tallest single bar.
        vmax = _nice_max(max(
            (sum(s["data"][i] if i < len(s["data"]) else 0 for s in series)
             for i in range(n)), default=0))
    else:
        vmax = _nice_max(max((v for s in series for v in s["data"]), default=0))
    grid = c.get("grid") is not False
    show_vals = bool(c.get("values"))
    parts = []
    horizontal = kind in ("row", "column", "stacked-row")
    # Room for the tick labels along the value axis and the category names.
    pad_l = (fs * 2.6) if not horizontal else (fs * 3.4)
    pad_b = fs * 1.6
    px, py = x + pad_l, y
    pw, ph = max(0.1, w - pad_l - fs * 0.4), max(0.1, h - pad_b)

    # gridlines + ticks
    if grid:
        for i in range(5):
            t = i / 4
            val = vmax * t
            if horizontal:
                gx = px + pw * t
                parts.append(f'<line x1="{gx:.4f}" y1="{py:.4f}" x2="{gx:.4f}" '
                             f'y2="{py + ph:.4f}" stroke="{ink["grid"]}" stroke-width="0.006"/>')
                parts.append(f'<text x="{gx:.4f}" y="{py + ph + fs:.4f}" text-anchor="middle" '
                             f'font-size="{fs * 0.8:.4f}" fill="{ink["axis"]}">{_fmt_num(val)}</text>')
            else:
                gy = py + ph - ph * t
                parts.append(f'<line x1="{px:.4f}" y1="{gy:.4f}" x2="{px + pw:.4f}" '
                             f'y2="{gy:.4f}" stroke="{ink["grid"]}" stroke-width="0.006"/>')
                parts.append(f'<text x="{px - fs * 0.3:.4f}" y="{gy + fs * 0.3:.4f}" '
                             f'text-anchor="end" font-size="{fs * 0.8:.4f}" '
                             f'fill="{ink["axis"]}">{_fmt_num(val)}</text>')

    slot = (ph if horizontal else pw) / n
    inner = slot * 0.78
    bw = inner if stacked else inner / max(1, len(series))
    for gi in range(n):
        base = (py if horizontal else px) + gi * slot + (slot - inner) / 2
        run = 0.0                       # how far along the stack we have got
        for si, s in enumerate(series):
            v = s["data"][gi] if gi < len(s["data"]) else 0
            frac = 0.0 if vmax == 0 else max(0.0, v / vmax)
            if horizontal:
                blen = pw * frac
                by = base if stacked else base + si * bw
                bx0 = px + (pw * run if stacked else 0)
                parts.append(f'<rect class="ds-cbar ds-cbar-x" x="{bx0:.4f}" '
                             f'y="{by:.4f}" width="{blen:.4f}" '
                             f'height="{bw * 0.86:.4f}" fill="{s["color"]}" '
                             f'rx="{min(0.02, bw * 0.2):.4f}"'
                             f'{bar_anim_attrs(anim, gi)}/>')
                if stacked:
                    run += frac
                if show_vals and not stacked:
                    parts.append(f'<text x="{px + blen + fs * 0.22:.4f}" '
                                 f'y="{by + bw * 0.62:.4f}" font-size="{fs * 0.78:.4f}" '
                                 f'fill="{ink["label"]}">{_fmt_num(v)}</text>')
            else:
                bh = ph * frac
                bx = base if stacked else base + si * bw
                by0 = py + ph - bh - (ph * run if stacked else 0)
                parts.append(f'<rect class="ds-cbar" x="{bx:.4f}" y="{by0:.4f}" '
                             f'width="{bw * 0.86:.4f}" height="{bh:.4f}" '
                             f'fill="{s["color"]}" rx="{min(0.02, bw * 0.2):.4f}"'
                             f'{bar_anim_attrs(anim, gi)}/>')
                if stacked:
                    run += frac
                if show_vals and not stacked:
                    parts.append(f'<text x="{bx + bw * 0.43:.4f}" '
                                 f'y="{py + ph - bh - fs * 0.22:.4f}" text-anchor="middle" '
                                 f'font-size="{fs * 0.78:.4f}" fill="{ink["label"]}">{_fmt_num(v)}</text>')
        name = labels[gi] if gi < len(labels) else ""
        if name:
            if horizontal:
                parts.append(f'<text x="{px - fs * 0.3:.4f}" '
                             f'y="{base + inner / 2 + fs * 0.3:.4f}" text-anchor="end" '
                             f'font-size="{fs * 0.82:.4f}" fill="{ink["label"]}"'
                             f'{_ch_hook(c, f"label:{gi}")}>{_xml(name)}</text>')
            else:
                parts.append(f'<text x="{base + inner / 2:.4f}" y="{py + ph + fs:.4f}" '
                             f'text-anchor="middle" font-size="{fs * 0.82:.4f}" '
                             f'fill="{ink["label"]}"{_ch_hook(c, f"label:{gi}")}'
                             f'>{_xml(name)}</text>')
    # the axis itself, last so it sits over the gridlines
    if horizontal:
        parts.append(f'<line x1="{px:.4f}" y1="{py:.4f}" x2="{px:.4f}" y2="{py + ph:.4f}" '
                     f'stroke="{ink["axis"]}" stroke-width="0.008"/>')
    else:
        parts.append(f'<line x1="{px:.4f}" y1="{py + ph:.4f}" x2="{px + pw:.4f}" '
                     f'y2="{py + ph:.4f}" stroke="{ink["axis"]}" stroke-width="0.008"/>')
    return "".join(parts)


def _plot_frame(px, py, pw, ph, vmin, vmax, ink, fs, grid, xlabels=None,
                xmin=None, xmax=None) -> str:
    """Gridlines, ticks and the two axis rules — shared by every chart drawn in
    a value plane (line, scatter, histogram), so they cannot drift apart."""
    parts = []
    if grid:
        for i in range(5):
            t = i / 4
            gy = py + ph - ph * t
            parts.append(f'<line x1="{px:.4f}" y1="{gy:.4f}" x2="{px + pw:.4f}" '
                         f'y2="{gy:.4f}" stroke="{ink["grid"]}" stroke-width="0.006"/>')
            parts.append(f'<text x="{px - fs * 0.3:.4f}" y="{gy + fs * 0.3:.4f}" '
                         f'text-anchor="end" font-size="{fs * 0.8:.4f}" '
                         f'fill="{ink["axis"]}">{_fmt_num(vmin + (vmax - vmin) * t)}</text>')
    if xlabels is not None:
        for i, name in enumerate(xlabels):
            if not name:
                continue
            gx = px + (pw * (i + 0.5) / len(xlabels) if len(xlabels) else 0)
            parts.append(f'<text x="{gx:.4f}" y="{py + ph + fs:.4f}" text-anchor="middle" '
                         f'font-size="{fs * 0.82:.4f}" fill="{ink["label"]}"'
                         f'{_ch_hook({}, f"label:{i}")}>{_xml(name)}</text>')
    elif xmin is not None:
        for i in range(5):
            t = i / 4
            gx = px + pw * t
            parts.append(f'<text x="{gx:.4f}" y="{py + ph + fs:.4f}" text-anchor="middle" '
                         f'font-size="{fs * 0.8:.4f}" '
                         f'fill="{ink["axis"]}">{_fmt_num(xmin + (xmax - xmin) * t)}</text>')
    parts.append(f'<line x1="{px:.4f}" y1="{py + ph:.4f}" x2="{px + pw:.4f}" '
                 f'y2="{py + ph:.4f}" stroke="{ink["axis"]}" stroke-width="0.008"/>')
    parts.append(f'<line x1="{px:.4f}" y1="{py:.4f}" x2="{px:.4f}" y2="{py + ph:.4f}" '
                 f'stroke="{ink["axis"]}" stroke-width="0.008"/>')
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
        ymax = _nice_max(max(ys[:n]))
        parts.append(_plot_frame(px, py, pw, ph, 0, ymax, ink, fs, grid,
                                 xmin=xmin, xmax=xmax))
        for i in range(n):
            cx = px + pw * (xs[i] - xmin) / (xmax - xmin)
            cy = py + ph - ph * (ys[i] / ymax if ymax else 0)
            parts.append(f'<circle cx="{cx:.4f}" cy="{cy:.4f}" r="{fs * 0.34:.4f}" '
                         f'fill="{col}" fill-opacity="0.85"/>')
        return "".join(parts)

    n = max(len(labels), max((len(s["data"]) for s in series), default=0))
    if not n:
        return ""
    vmax = _nice_max(max((v for s in series for v in s["data"]), default=0))
    parts.append(_plot_frame(px, py, pw, ph, 0, vmax, ink, fs, grid,
                             xlabels=[labels[i] if i < len(labels) else "" for i in range(n)]))
    show_vals = bool(c.get("values"))
    for s in series:
        pts = []
        for i in range(n):
            v = s["data"][i] if i < len(s["data"]) else 0
            cx = px + pw * (i + 0.5) / n
            cy = py + ph - ph * (v / vmax if vmax else 0)
            pts.append((cx, cy, v))
        parts.append(f'<polyline points="{" ".join(f"{a:.4f},{b:.4f}" for a, b, _ in pts)}" '
                     f'fill="none" stroke="{s["color"]}" stroke-width="0.022" '
                     f'stroke-linejoin="round" stroke-linecap="round"/>')
        for cx, cy, v in pts:
            parts.append(f'<circle cx="{cx:.4f}" cy="{cy:.4f}" r="{fs * 0.26:.4f}" '
                         f'fill="{s["color"]}"/>')
            if show_vals:
                parts.append(f'<text x="{cx:.4f}" y="{cy - fs * 0.45:.4f}" '
                             f'text-anchor="middle" font-size="{fs * 0.75:.4f}" '
                             f'fill="{ink["label"]}">{_fmt_num(v)}</text>')
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
    pad_l, pad_b = fs * 2.6, fs * 1.6
    px, py = x + pad_l, y
    pw, ph = max(0.1, w - pad_l - fs * 0.4), max(0.1, h - pad_b)
    parts = [_plot_frame(px, py, pw, ph, 0, cmax, ink, fs,
                         c.get("grid") is not False, xmin=lo, xmax=hi)]
    bw = pw / nb
    for i, ct in enumerate(counts):
        bh = ph * (ct / cmax if cmax else 0)
        parts.append(f'<rect class="ds-cbar" x="{px + i * bw + bw * 0.06:.4f}" '
                     f'y="{py + ph - bh:.4f}" '
                     f'width="{bw * 0.88:.4f}" height="{bh:.4f}" '
                     f'fill="{_slice_color(c, i)}"{bar_anim_attrs(anim, i)}/>')
        if c.get("values") and ct:
            parts.append(f'<text x="{px + i * bw + bw / 2:.4f}" y="{py + ph - bh - fs * 0.22:.4f}" '
                         f'text-anchor="middle" font-size="{fs * 0.75:.4f}" '
                         f'fill="{ink["label"]}">{ct}</text>')
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
                     f'font-size="{fs * 0.8:.4f}" fill="{ink["label"]}"'
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
                         f'text-anchor="middle" font-size="{fs * 0.8:.4f}" fill="#fff" '
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
        if name and r > fs * 1.1:
            parts.append(f'<text x="{cx + r:.4f}" y="{cy + r + fs * 0.28:.4f}" '
                         f'text-anchor="middle" font-size="{min(fs * 0.8, r * 0.5):.4f}" '
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
                         f'font-size="{fs * 0.78:.4f}" fill="#fff" font-weight="600"'
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
    r = max(0.05, min(w, h) / 2 - fs * 0.4)
    inner = r * 0.58 if kind == "donut" else 0.0
    show_vals = bool(c.get("values"))
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
        col = _slice_color(c, i)
        if inner:
            i1x, i1y = cx + inner * math.cos(a2), cy + inner * math.sin(a2)
            i2x, i2y = cx + inner * math.cos(ang), cy + inner * math.sin(ang)
            d = (f'M{x1:.4f},{y1:.4f} A{r:.4f},{r:.4f} 0 {big} 1 {x2:.4f},{y2:.4f} '
                 f'L{i1x:.4f},{i1y:.4f} A{inner:.4f},{inner:.4f} 0 {big} 0 {i2x:.4f},{i2y:.4f} Z')
        else:
            d = (f'M{cx:.4f},{cy:.4f} L{x1:.4f},{y1:.4f} '
                 f'A{r:.4f},{r:.4f} 0 {big} 1 {x2:.4f},{y2:.4f} Z')
        parts.append(f'<path d="{d}" fill="{col}" stroke="#fff" stroke-width="0.01"/>')
        if show_vals:
            mid = ang + sweep / 2
            lr = (inner + r) / 2 if inner else r * 0.62
            tx, ty = cx + lr * math.cos(mid), cy + lr * math.sin(mid)
            pct = v / total * 100
            parts.append(f'<text x="{tx:.4f}" y="{ty + fs * 0.3:.4f}" text-anchor="middle" '
                         f'font-size="{fs * 0.8:.4f}" fill="#fff" font-weight="600">'
                         f'{pct:.0f}%</text>')
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
    for j, col in enumerate(c.get("colors") or []):
        if col:
            _hex(col, f"{where}.colors[{j}]")
    for flag in ("legend", "values", "grid"):
        if c.get(flag) is not None and not isinstance(c[flag], bool):
            raise LayoutError(f"{where}.{flag}: expected true or false")
    for k in ("titleColor", "labelColor", "axisColor", "gridColor"):
        if c.get(k):
            _hex(c[k], f"{where}.{k}")


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
        if not isinstance(b, dict):
            raise LayoutError(f"{where}.border: expected an object")
        if b.get("w") is not None:
            w = _num(b["w"], f"{where}.border.w")
            if not 0 <= w <= 12:
                raise LayoutError(f"{where}.border.w: {w} is outside 0–12px")
        if b.get("color"):
            _hex(b["color"], f"{where}.border.color")
        if b.get("style") and b["style"] not in TABLE_BORDER_STYLES:
            raise LayoutError(f"{where}.border.style: expected one of "
                              f"{', '.join(TABLE_BORDER_STYLES)}")
        if b.get("sides") and b["sides"] not in TABLE_BORDER_SIDES:
            raise LayoutError(f"{where}.border.sides: expected one of "
                              f"{', '.join(TABLE_BORDER_SIDES)}")
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
        self.positions = raw.get("positions") or {}
        self.shapes = raw.get("shapes") or []
        self.text = raw.get("text") or {}
        self.boxes = raw.get("boxes") or []
        self.tables = raw.get("tables") or []
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
        seen = set()
        for i, s in enumerate(self.shapes):
            where = f"shape #{i + 1}"
            sid = s.get("id")
            if not sid:
                raise LayoutError(f"{where}: needs an 'id'")
            if sid in seen:
                raise LayoutError(f"{where}: duplicate id '{sid}'")
            seen.add(sid)
            if s.get("kind") not in KINDS:
                raise LayoutError(
                    f"{where}: kind {s.get('kind')!r} must be one of {', '.join(KINDS)}")
            if not isinstance(s.get("page"), (int, str)) or isinstance(s.get("page"), bool):
                raise LayoutError(f"{where}: 'page' must be a page number or blank-page id")
            for k in ("x", "y", "w", "h"):
                _num(s.get(k), f"{where}.{k}")
            # These land verbatim inside SVG attributes: a malformed value
            # does not error, it renders an invisible shape. Fill may be a
            # gradient; stroke stays a solid hex.
            if s.get("fill") not in (None, "none"):
                _fill(s["fill"], f"{where}.fill")
            if s.get("stroke") not in (None, "none"):
                _hex(s["stroke"], f"{where}.stroke")
            if s.get("rot") is not None:
                _num(s["rot"], f"{where}.rot")
            if s.get("alpha") is not None:
                _alpha(s["alpha"], f"{where}.alpha")
            if s.get("anim") is not None:
                # A chart is the one shape with parts of its own to animate.
                _anim_check(s["anim"], where,
                            "bars" if s.get("kind") == "chart" else "")
            if s.get("shadow") is not None:
                _check_shadow(s["shadow"], f"{where}.shadow")
            if s.get("r") is not None and _num(s["r"], f"{where}.r") < 0:
                raise LayoutError(f"{where}: corner radius cannot be negative")
            if s.get("kind") == "chart":
                _check_chart(s.get("chart"), f"{where}.chart")
            if s.get("dash") is not None:
                d = s["dash"]
                if not isinstance(d, list) or not 1 <= len(d) <= 2 or any(
                        _num(v, f"{where}.dash") <= 0 for v in d):
                    raise LayoutError(f"{where}: dash must be one or two positive "
                                      f"lengths, like [0.08, 0.05]")
            if s.get("ends") is not None and s["ends"] not in LINE_ENDS:
                raise LayoutError(f"{where}: ends {s['ends']!r} must be one of "
                                  f"{', '.join(LINE_ENDS)}")
            if s.get("kind") == "icon":
                check_icon_svg(s.get("svg"), where)
                vb = s.get("vb", "0 0 24 24")
                # The viewBox lands verbatim in an SVG attribute; four numbers
                # or nothing, so a stray quote cannot end the attribute early.
                if not isinstance(vb, str) or not _VIEWBOX_RE.match(vb):
                    raise LayoutError(f"{where}: viewBox {vb!r} must be four numbers, "
                                      f"like '0 0 24 24'")
            _z(s)          # a bad layer must fail at load, not mid-render
        for el, p in self.positions.items():
            if "z" in p and not isinstance(p["z"], int):
                raise LayoutError(f"position '{el}': z {p['z']!r} is not a layer number")
        for key, st in self.text.items():
            if not isinstance(st, dict):
                raise LayoutError(f"text '{key}': expected a style object")
            _check_text(st, f"text '{key}'")
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
            where = f"box #{i + 1}"
            bid = b.get("id")
            if not bid:
                raise LayoutError(f"{where}: needs an 'id'")
            # One namespace with shapes: the editor resolves an id to a thing by
            # searching both, so a collision makes the right-click menu act on
            # whichever it happens to find first.
            if bid in seen:
                raise LayoutError(f"{where}: duplicate id '{bid}' — already a shape")
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
            if b.get("fill"):
                _fill(b["fill"], f"{where}.fill")
            if b.get("rot") is not None:
                _num(b["rot"], f"{where}.rot")
            if b.get("alpha") is not None:
                _alpha(b["alpha"], f"{where}.alpha")
            if b.get("shadow") is not None:
                _check_shadow(b["shadow"], f"{where}.shadow")
            if b.get("style"):
                _check_text(b["style"], f"{where}.style")

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
            if t.get("alpha") is not None:
                _alpha(t["alpha"], f"{where}.alpha")
            if t.get("style"):
                _check_text(t["style"], f"{where}.style")
            _check_table_look(t, where, len(rows), width)

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
        # One transform declaration for all of it: a second would silently
        # replace the first, which is exactly how a flip would eat a rotation.
        # Default (centre) origin, so scale grows a graphic from its middle and
        # rotate/flip pivot in place — one origin that suits every operation.
        tf = []
        if p.get("rot"):
            tf.append(f'rotate({p["rot"]}deg)')
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
        return (f'<div class="ds-spacer" style="{wid}height:{p["reserve"]}in;'
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

    def text_style(self, key: str) -> str:
        """The CSS for one slot's text, or "" when it was never styled."""
        return text_css(self.text.get(key) or {})

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
        for st in self.text.values():
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
        mine = [b for b in self.boxes if b.get("page") == page]
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
        out = [mount, self._anim_block(),
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
            act = b.get("act")
            an = anim_attrs(b.get("anim"))
            css = (f'position:absolute;left:{b["x"]}in;top:{b["y"]}in;'
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
                css += f';transform:rotate({b["rot"]}deg)'
            if b.get("alpha") is not None:
                css += f';opacity:{b["alpha"]:g}'
            if b.get("shadow"):
                css += f';box-shadow:{shadow_css(b["shadow"])}'
            style = text_css(b.get("style") or {})
            # Style FIRST, geometry second — the geometry must win their one
            # collision: align's inline-slot compensation appends width:100%
            # (right for a span with no box of its own), and written after the
            # box's own width it silently overrode it, so any aligned text box
            # spanned the whole page — and the drag math, anchored to the box
            # it MEANT to draw, flung it to the left margin.
            full = f'{style + ";" if style else ""}{css}'
            tag = f' data-el="text.{b["id"]}"' if edit else ""
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
                out.append(f'<div class="ds-textbox ds-endnotes-sec"{tag}{an} '
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
                    f'<button type="button" class="ds-actbtn ds-tglbtn"{an} '
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
                    f'<button type="button" class="ds-actbtn noprint"{an} '
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
            out.append(f'<div class="{klass}"{extra}{tag}{an} '
                       f'style="{tglFull}">'
                       f'{block_html(b["md"])}{arrow}</div>')
        return "".join(out)

    def box(self, box_id: str) -> dict | None:
        return next((b for b in self.boxes if b.get("id") == box_id), None)

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
            css = (f'position:absolute;left:{t["x"]}in;top:{t["y"]}in;'
                   f'width:{t["w"]}in;z-index:{int(t.get("z", 2))}')
            if t.get("rot"):
                css += f';transform:rotate({t["rot"]}deg)'
            if t.get("alpha") is not None:
                css += f';opacity:{t["alpha"]:g}'
            style = text_css(t.get("style") or {})
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
                    cells += f'<{name}{hook}{sty}>{md_inline(str(c))}</{name}>'
                body += f"<tr>{cells}</tr>"
            klass = "ds-table"
            extra = ""
            tglSty = ""
            if not edit and t["id"] in self.toggle_targets:
                klass += " ds-tglable"
                extra = f' id="ds-x-{t["id"]}"'
                tglSty = f';--ds-tgl-d:{self.toggle_speed.get(t["id"], 0.3):g}s'
            out.append(f'<table class="{klass}"{extra}{tag}'
                       f'{anim_attrs(t.get("anim"))} '
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

    def layer(self, page: int) -> str:
        """Shapes for one page, grouped into one SVG per layer. Empty when there
        are none, so a report without shapes renders exactly as before — except
        for the page-size override, which has to reach the document even on a
        page that holds no shapes."""
        head = self._page_style_once()
        mine = [s for s in self.shapes if s.get("page") == page]
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
        if s.get("shadow"):
            common += f' style="{shape_shadow_css(s["shadow"])}"'
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
