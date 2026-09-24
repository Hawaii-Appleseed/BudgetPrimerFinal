#!/usr/bin/env python3
"""Verify a rendered report against the invariants no renderer enforces itself.

    python3 -m docsync.check            # every binding's committed output
    python3 -m docsync.check --id rxkids-fiscal
    python3 -m docsync.check --build    # rebuild each binding first

Why this exists, in the words of the bug that prompted it: two one-pagers each
computed an `endnotes` string and never interpolated it into the html they
wrote. The superscripts still rendered, so both pages carried numbered markers
that linked nowhere and no source list at all.

Nothing was going to catch that:

  * The one guard the repo had -- "content.md declares sources never cited" --
    is hand-copied into individual renderers (report2027, rxkids) instead of
    living in the engine, so pages written later never inherited it. And it
    only checks the direction that did not break: declared-but-uncited, not
    cited-but-never-anchored.
  * A linter does not help. pyflakes reports an unused LOCAL variable; both
    dead `endnotes` assignments were at module scope, where it says nothing.

So the checks live here, they run over the OUTPUT rather than the source, and
they are found by walking docsync.yml -- a new report is covered the day it is
added to the registry, with no per-project code to remember.

Checking committed output (rather than rebuilding) is deliberate: it also
catches a stale or hand-edited index.html that no longer matches its renderer.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docsync import layout                                     # noqa: E402
from docsync.registry import ROOT, RegistryError, load_registry   # noqa: E402


@dataclass
class Problem:
    """One failed invariant, phrased so the fix is obvious from the line.

    Two levels, because the failures are not equal. A marker with no anchor
    anywhere is the bug this module was written for -- a number the reader
    cannot follow to anything, on a page that believes it has sources. A
    marker that resolves but was never wrapped in a link still reaches the
    reader in print; it is worth saying, not worth failing a build over.
    """
    check: str
    detail: str
    level: str = "error"                 # error | warn | note

    @property
    def is_error(self) -> bool:
        return self.level == "error"

    def __str__(self) -> str:
        mark = ("ERROR" if self.is_error else
                "note " if self.level == "note" else "warn ")
        return f"{mark} [{self.check}] {self.detail}"


# --- text extraction ---------------------------------------------------------

# Tags whose CONTENT is not prose and must not be scanned for stray markdown.
_OPAQUE = re.compile(r"(?is)<(script|style|pre|code|textarea)\b.*?</\1\s*>")


def visible_text(html: str) -> str:
    """The document's text nodes, with every tag replaced by a NUL.

    Tags become \\x00 rather than "" so that a pattern can never be assembled
    across an element boundary -- `<b>*</b><b>*</b>` must not read as `**`.
    Attribute values disappear with their tags, which keeps hrefs and alt text
    from tripping the markdown checks.
    """
    h = _OPAQUE.sub(" ", html)
    return re.sub(r"(?s)<[^>]*>", "\x00", h)


# --- 1. citation integrity ---------------------------------------------------

# Only <sup> that is purely a footnote marker: digits, thin spaces, and the
# anchors wrapped around them. "1st" or a chemistry superscript is not ours.
_SUP = re.compile(r"(?is)<sup\b[^>]*>(.*?)</sup\s*>")
_SUP_IS_MARKER = re.compile(r"(?is)^(?:\s|&thinsp;|&nbsp;|\d|<a\b[^>]*>|</a\s*>)+$")
_ANCHOR_ID = re.compile(r'id="en(\d+)"')
_ANCHOR_HREF = re.compile(r'href="#en(\d+)"')


def check_citations(html: str) -> list[Problem]:
    """Every footnote marker resolves, and every source is reachable.

    Both directions, because the repo's hand-rolled guard only ever checked
    one of them and the other is what shipped broken twice.
    """
    problems: list[Problem] = []
    ids = {int(n) for n in _ANCHOR_ID.findall(html)}

    cited: set[int] = set()
    for inner in _SUP.findall(html):
        if not _SUP_IS_MARKER.match(inner):
            continue                     # not a footnote marker; leave it be
        for num in re.findall(r"\d+", re.sub(r"(?s)<[^>]*>", "", inner)):
            cited.add(int(num))

    for n in sorted(cited - ids):
        problems.append(Problem(
            "citations",
            f"footnote marker {n} has no id=\"en{n}\" anywhere in the output — "
            f"the sources list was built but never placed on the page"))

    for n in sorted({int(x) for x in _ANCHOR_HREF.findall(html)} - ids):
        problems.append(Problem(
            "citations", f'a link points at #en{n}, which does not exist'))

    # A marker that is not a link is a number the reader cannot follow. This is
    # the exact shape of the bug: <sup>1</sup> rendered, pointing nowhere.
    for inner in _SUP.findall(html):
        if _SUP_IS_MARKER.match(inner) and "<a" not in inner.lower():
            problems.append(Problem(
                "citations",
                f"footnote marker <sup>{inner.strip()}</sup> is a bare numeral, "
                f"not a link — run the markers through a linkify pass",
                "warn"))

    for n in sorted(ids - cited):
        problems.append(Problem(
            "citations",
            f'source {n} (id="en{n}") is listed but nothing cites it',
            "warn"))

    return problems


# --- 2. unconsumed markdown --------------------------------------------------

# C.t() emits a slot's RAW text; C.html() runs the inline markdown. Reaching
# for the first where the second was needed prints the syntax verbatim --
# "**Method.**" shipped on a finished page exactly that way.
_MARKDOWN = [
    ("bold", re.compile(r"\*\*(?=\S)[^*\x00\n]{1,200}?\*\*")),
    ("footnote ref", re.compile(r"\[\^[A-Za-z0-9_.-]+\]")),
    ("link", re.compile(r"\[[^\]\x00\n]{1,120}\]\(\s*(?:https?:|mailto:|[/#])")),
    ("heading", re.compile(r"(?m)^\x00*#{1,6}\s+\S")),
]


def check_markdown(html: str) -> list[Problem]:
    """No markdown syntax survives into the rendered page."""
    text = visible_text(html)
    problems: list[Problem] = []
    for name, pat in _MARKDOWN:
        for m in pat.finditer(text):
            snippet = m.group(0).replace("\x00", "").strip()
            problems.append(Problem(
                "markdown",
                f"unrendered {name} in the output: {snippet[:90]!r} — "
                f"the slot needs C.html(), not C.t()"))
    return problems


# --- 3. drawn content inside its viewBox -------------------------------------

_SVG_OPEN = re.compile(r"(?is)<svg\b([^>]*?)(/?)>")
_VIEWBOX = re.compile(r'viewBox="\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*"')
_TEXT_EL = re.compile(r"(?is)<text\b([^>]*)>(.*?)</text\s*>")
_RECT_EL = re.compile(r"(?is)<rect\b([^>]*?)/?>")

# Glyphs that drop below the baseline. A caption sitting at exactly the canvas
# edge only LOOKS fine until one of these appears in it -- which is how a
# clipped line survives review.
_DESCENDERS = set("gjpqy,;()[]{}$_@Q")


def _attr(tag: str, name: str) -> float | None:
    m = re.search(rf'\b{name}="(-?[\d.]+)"', tag)
    return float(m.group(1)) if m else None


def iter_svgs(html: str):
    """Yield (attrs, inner) for each <svg>, matching tags in a BALANCED way.

    A naive non-greedy `<svg.*?</svg>` pairs an outer element's attributes with
    an inner one's content the moment anything nests -- which is exactly what
    the editor's shape layer does, wrapping 24-unit icons inside a page-sized
    layer. That mismatch reported an icon's coordinates against the page's
    viewBox and invented a failure. Depth-count instead.
    """
    pos = 0
    while (m := _SVG_OPEN.search(html, pos)) is not None:
        if m.group(2):                       # <svg .../> — self-closing, empty
            pos = m.end()
            continue
        depth, i = 1, m.end()
        while depth and (nxt := re.compile(r"(?is)<svg\b[^>]*?(/?)>|</svg\s*>")
                         .search(html, i)) is not None:
            if nxt.group(0).lower().startswith("</"):
                depth -= 1
            elif not nxt.group(1):
                depth += 1
            i = nxt.end()
        if depth:                            # unbalanced; nothing to say
            return
        yield m.group(1), html[m.end():i - len("</svg>")]
        pos = m.end()                        # re-enter to reach nested svgs


def check_svg_bounds(html: str) -> list[Problem]:
    """Generated chart content must fit the viewBox its renderer hand-set.

    Conservative by design -- this check exists to catch a renderer's hand-set
    canvas being one line too short, not to police artwork. It stands down
    wherever it cannot reason honestly:

      * a nested <svg>, whose child coordinates belong to another system;
      * `transform=`, which moves content out from under its own attributes;
      * `overflow="visible"`, which says drawing outside the box is intended;
      * the editor's `shape-layer`, whose contents a person positions by hand
        and may deliberately run off the page.
    """
    problems: list[Problem] = []
    for attrs, inner in iter_svgs(html):
        vb = _VIEWBOX.search(attrs)
        if not vb:
            continue
        height = float(vb.group(2))
        low = (attrs + inner).lower()
        if ("<svg" in inner.lower() or "transform=" in low
                or "overflow=\"visible\"" in low or "shape-layer" in attrs.lower()):
            continue

        label = (re.search(r'aria-label="([^"]{0,60})', attrs) or [None, "svg"])[1]

        for tag, body in _TEXT_EL.findall(inner):
            y = _attr(tag, "y")
            if y is None or "dy=" in tag:
                continue
            fs = _attr(tag, "font-size") or 12.0
            txt = re.sub(r"(?s)<[^>]*>", "", body)
            # Only fault a baseline at the edge when the string actually has a
            # glyph that would be cut -- otherwise every caption sitting neatly
            # on the last line reads as a failure.
            drop = 0.25 * fs if (_DESCENDERS & set(txt)) else 0.0
            if y + drop > height:
                why = ("descenders are clipped" if drop
                       else "the baseline is below the canvas")
                problems.append(Problem(
                    "svg-bounds",
                    f'"{label}": text at y={y:g} in a {height:g}-unit viewBox '
                    f"— {why}: {txt.strip()[:60]!r}"))

        for tag in _RECT_EL.findall(inner):
            y, h = _attr(tag, "y"), _attr(tag, "height")
            if y is None or h is None:
                continue
            if y + h > height + 0.5:
                problems.append(Problem(
                    "svg-bounds",
                    f'"{label}": a rect ends at y={y + h:g}, past the '
                    f"{height:g}-unit viewBox"))
    return problems


# --- 4. text a reader can actually read --------------------------------------
#
# The recurring bug this catches: type that is fine on the screen it was
# designed on and unreadable everywhere else. Three units feed one page and
# only one of them is what the reader sees, so a "small number" in the source
# is not obviously small type:
#
#   * CSS px, on a page whose inches are real inches  -> pt = px * 0.75
#   * SVG user units, in the page's INCH coordinates  -> pt = units * 72
#     (a chart label at 0.07 was five-point type, and read as a rounding knob)
#   * .page{zoom:1.25} on a wide screen, which flatters every desktop review
#     and applies to neither the PDF nor a phone.
#
# So this check converts everything to POINTS ON PAPER and judges it there.
# The floors come from docsync.layout, so the renderer's clamps and this
# check can never disagree about where the line is.
#
# What it deliberately does NOT do: resolve a class to a size. A stylesheet
# is a separate file with a cascade, and guessing at it would either miss
# most of the document or invent failures. tests/editor/text-legibility.spec.js
# measures COMPUTED sizes in a real browser, at the four viewports that
# matter, and is where that half of the coverage lives.

MIN_TEXT_PT = layout.MIN_TEXT_PT
MIN_LABEL_PT = layout.MIN_LABEL_PT
MIN_SUBLABEL_PT = layout.MIN_SUBLABEL_PT

_STYLE_BLOCK = re.compile(r"(?is)<style\b[^>]*>(.*?)</style\s*>")
_INLINE_STYLE = re.compile(r'(?is)style="([^"]*)"')
_FONT_SIZE_PX = re.compile(r"font-size\s*:\s*([\d.]+)px")
_SVG_WIDTH_IN = re.compile(r'(?is)(?:\bwidth="\s*([\d.]+)in"|width\s*:\s*([\d.]+)in)')
_TEXT_FS = re.compile(r'(?is)<text\b([^>]*)>(.*?)</text\s*>')


def _svg_in_per_unit(attrs: str) -> float | None:
    """Inches per SVG user unit, or None when it cannot be known honestly.

    Only a width stated in INCHES is usable. A percentage width (the common
    responsive chart) means the answer depends on the viewport, which is a
    browser's question, not a regex's -- so this stands down and lets the
    Playwright spec have it.
    """
    vb = _VIEWBOX.search(attrs)
    if not vb:
        return None
    vbw = float(vb.group(1))
    if vbw <= 0:
        return None
    m = _SVG_WIDTH_IN.search(attrs)
    if not m:
        return None
    return float(m.group(1) or m.group(2)) / vbw


def _size_problem(check: str, pt: float, what: str,
                  can_fail: bool = True,
                  target: float = MIN_LABEL_PT) -> Problem | None:
    """One judgement, so the callers cannot drift apart on the threshold.

    `can_fail=False` for sizes this module cannot prove are page CONTENT. A
    <style> block in a published report holds the sheet's typography and the
    surrounding site's chrome in the same list of rules — a nav brand tag and
    a dropdown chevron are 9px on purpose, and failing a build over them would
    train everyone to stop reading this check. Those report as warnings;
    text-legibility.spec.js, which can see what is actually inside .page,
    is what fails a build over them.
    """
    if pt < MIN_TEXT_PT and can_fail:
        return Problem(check, f"{what} renders at {pt:.1f}pt on paper — below the "
                              f"{MIN_TEXT_PT:g}pt floor; no reader can read it")
    if pt < target:
        under = ("below the floor" if pt < MIN_TEXT_PT
                 else f"under the {target:g}pt target for text at this size")
        return Problem(check, f"{what} renders at {pt:.1f}pt on paper — {under}",
                       level="warn")
    return None


def check_text_size(html: str) -> list[Problem]:
    """No text on the page is smaller than a person can read.

    Two sources, because the two ways this engine sets a size fail
    differently. An SVG label is sized in inches and shrinks with its box, so
    it goes wrong quietly as a chart is resized. A CSS px size is authored
    once and goes wrong loudly, in a stylesheet nobody re-reads.
    """
    problems: list[Problem] = []

    for attrs, inner in iter_svgs(html):
        per_unit = _svg_in_per_unit(attrs)
        if per_unit is None:
            continue
        label = (re.search(r'aria-label="([^"]{0,60})', attrs) or [None, "svg"])[1]
        for tag, body in _TEXT_FS.findall(inner):
            fs = _attr(tag, "font-size")
            if fs is None:                   # inherited from CSS; not ours to judge
                continue
            txt = re.sub(r"(?s)<[^>]*>", "", body).strip()
            pr = _size_problem("text-size", fs * per_unit * 72.0,
                               f'"{label}": chart text {txt[:40]!r}',
                               target=MIN_SUBLABEL_PT)
            if pr:
                problems.append(pr)

    # CSS px, from <style> blocks and from style="" attributes. Everything the
    # engine itself writes lands in one of these two; a report's own linked
    # stylesheet is the browser spec's job.
    # Inline style="" is the engine's own writing on a real page element, so a
    # size there is content and can fail the build. A <style> block is the
    # report's stylesheet AND whatever site chrome ships beside it, so it can
    # only advise. Same threshold either way — different consequence.
    for css, can_fail in ([(c, False) for c in _STYLE_BLOCK.findall(html)]
                          + [(c, True) for c in _INLINE_STYLE.findall(html)]):
        for px in _FONT_SIZE_PX.findall(css):
            pr = _size_problem("text-size", float(px) * 0.75,
                               f"font-size:{px}px", can_fail)
            if pr:
                problems.append(pr)

    return problems


# --- 5. every attribute said once --------------------------------------------
# A start tag that names an attribute twice keeps the FIRST and drops the rest,
# with no error anywhere. Two helpers writing into one tag is how it happens,
# and each is right alone: a renderer's own style="color:…" beside L.attr's
# position, and a heading someone dragged snaps back on the next render; a
# slot's text style beside card()'s list style, and the bullets lose their
# indent. Neither happens until a person moves or styles something, so the
# committed page is clean and the edit-mode check below also renders a STRESS
# build (stress_layout) to find the tag before a person does.

class _Twice(HTMLParser):
    """Each start tag that names an attribute more than once."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found: list[str] = []

    def handle_starttag(self, tag, attrs):
        names = [k for k, _ in attrs]
        twice = [k for k in dict.fromkeys(names) if names.count(k) > 1]
        if not twice:
            return
        # Named the way a person would find it: the hook, else the class.
        val = dict(reversed(attrs))                  # first value wins, as in a browser
        hook = next((f'{k}="{val[k]}"' for k in ("data-el", "data-slot", "id", "class")
                     if val.get(k)), "")
        self.found.append(f"<{tag}{' ' + hook if hook else ''}> (line {self.getpos()[0]}): "
                          + ", ".join(twice))

    handle_startendtag = handle_starttag


def attributes_twice(html: str) -> list[str]:
    p = _Twice()
    p.feed(html)
    return p.found


def check_attributes(html: str) -> list[Problem]:
    """No start tag names one attribute twice."""
    return [Problem("attributes",
                    f"{t} given twice — the browser keeps the first and drops the "
                    f"rest. Merge them into one: L.attr(id, extra), or merge_attrs()")
            for t in attributes_twice(html)]


CHECKS = (check_citations, check_markdown, check_svg_bounds,
          check_text_size, check_attributes)


# --- editability coverage ----------------------------------------------------
# Why this exists: the rxkids-fiscal one-pager passed every check above and the
# editor's own audit, yet a reader of the page in the editor found "so many
# elements uneditable" — because everything those nets measure is the wiring
# that EXISTS (slots resolve, sources anchor), never the text that was left
# with no wiring at all. Two failure shapes, both invisible until now:
#
#   * DEAD TEXT — a visible string with no data-slot and no data-el anywhere
#     above it. Not editable, not movable, invisible to the audit because the
#     audit walks hooks. The classic source is an ingested page whose widget
#     markup was carried over verbatim.
#   * FROZEN PROSE — a full sentence drawn INSIDE an SVG graphic. The graphic
#     is movable (data-el on the wrapper), so coverage looks fine, but the
#     sentence itself can only be changed by editing the renderer. Data marks
#     ("$20.7M", axis ticks) belong in the drawing; sentences are captions and
#     belong in a slot beside it.
#
# Two more, found once the first two were closed and the drawings' own labels
# became slots:
#
#   * A FROZEN DESCRIPTION — aria-label, alt, an SVG <title>. Words in an
#     ATTRIBUTE, which no slot can reach, and under role="img" the only words
#     a screen reader is given for that figure. Every hook above can be
#     perfect while the figure's entire spoken form is a renderer literal.
#   * A RESTATED FIGURE — a slotted label inside a drawing that states a
#     number the drawing also draws. This one is NEW: it exists because
#     labels became editable. The words move, the geometry does not, and
#     nothing says the two have parted.
#
# Both are warnings, never errors: a model-driven chart deliberately freezes
# its numbers (retyping "$52.1M" by hand would make the chart lie), and chrome
# outside the sheet is not content. The point is that the choice shows up in
# the check output instead of surprising the person editing the page.
#
# And four holes an audit found in the nets themselves (2026-09-22), each an
# open way to put words on a page that nobody could edit with every check
# green:
#
#   * HTML inside a <foreignObject> — invisible to graphic()'s <text> regex
#     and to the SVG branch here alike. Now judged like SVG text.
#   * EMPTY DECLARATIONS — data-fixed="" (C.derived("")) cleared a subtree by
#     presence alone. A declaration now has to say something, a sentence
#     under data-fixed is flagged outside an SVG too, and every declaration
#     is tallied as a note so the choices stay visible.
#   * HOOKS WITH NOTHING BEHIND THEM — data-slot was trusted by presence: a
#     made-up key covered literal prose, and a slot's element could hold
#     renderer words beside the slot's own. Under DOCSYNC_SLOTLOG the
#     renderer's Content writes out what each key served, and the page is
#     held to it (check_slot_fidelity).
#   * PUBLISH-ONLY TEXT — only the edit build was measured, so an
#     `if not DOCSYNC_EDIT` branch reached readers unseen. The publish build
#     is diffed against it (check_publish_only).

# Tags that never wrap content and never come back down through handle_endtag.
_VOID = frozenset("area base br col embed hr img input link meta source track "
                  "wbr".split())
_OPAQUE_TAGS = frozenset(("script", "style", "head", "title", "template"))
_GRAPHIC_TAGS = frozenset(("img", "svg", "video", "canvas", "iframe", "object"))
# Words = runs with a letter or digit; two alnum chars = worth a look.
_ALNUM2 = re.compile(r"[^\W_].*[^\W_]", re.S)
_SENT_END = re.compile(r"[.!?:;]\s*$")

# data-el namespaces whose TEXT is edited through a dedicated editor panel
# rather than click-to-edit in place: layout text boxes and tables (their
# words live in layout.json), and endnote entries (the Sources panel). A
# data-el in any other namespace makes an element movable and nothing more,
# so prose inside one with no data-slot is the C() trap — the skill's
# "#1 why-can't-I-edit-this cause": the paragraph drags, the words are
# frozen, and hook-counting coverage looks perfect.
_PANEL_EDITED = ("text.", "table.", "endnote.")

# Attributes that carry reader-facing words. `title` is the tooltip, not the
# <title> element (which _Coverage handles separately, inside an <svg>).
_DESC_ATTRS = ("aria-label", "alt", "title")


from .blocks import (ENGINE_PUBLISH_ONLY,         # noqa: E402  — one rule,
                     declared, is_data_mark,      # shared with graphic()
                     is_quantity, is_sentence)


def _prose(t: str, loose: bool = False) -> bool:
    """Sentence-shaped: what a person would expect to retype.

    Data marks ("$20.7M", "ITEP: +$83M — rates only") stay under the word
    threshold or lack sentence punctuation. `loose` also accepts long
    unpunctuated runs — headings hit the C() trap without ever ending in a
    period, but an SVG's multi-word annotations should not be flagged for
    length alone.
    """
    n = len(t.split())
    return (n >= 5 and bool(_SENT_END.search(t))) or (loose and n >= 8)


class _Coverage(HTMLParser):
    """Classify every visible text node of an EDIT-MODE build.

    dead: no data-slot/data-fixed/data-el ancestor, outside any SVG.
    frozen_prose: sentence-shaped text inside an <svg> — judged on data-slot
    alone, because a caption is prose wherever its numbers came from.
    frozen_desc: an accessible description (aria-label / alt / title, or an
    SVG <title>/<desc>) carrying words, with no data-desc, no data-fixed and
    no aria-hidden — the one string on a figure that nobody can edit.
    restated: a SLOTTED label inside a drawing that states a figure the
    drawing also draws, undeclared — editable words over geometry that will
    not follow them.
    Text outside the sheet (no `page`-classed ancestor) is chrome, not content
    — kept separately so a document with no .page container still gets a
    best-effort pass over everything.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        # Per ancestor: tag, COVERED (data-slot or data-fixed), any data-el,
        # TRAP data-el (movable, not panel-edited), is-svg, is-.page.
        self.stack: list[tuple[str, bool, bool, bool, bool, bool]] = []
        self.opaque = 0
        self.dead_paged: list[str] = []
        self.dead_all: list[str] = []
        self.trapped: list[str] = []      # movable wrapper, no slot: C() trap
        self.saw_page = False
        self._svg_text: list[str] | None = None
        self.frozen_prose: list[str] = []
        self.frozen_declared: list[str] = []
        self._svg_fixed = False
        self._svg_slotted = False
        self._svg_declared = False
        self._svg_key = ""
        self.desc_paged: list[str] = []
        self.desc_all: list[str] = []
        self.desc_declared: list[str] = []
        self.restated: list[str] = []
        # An SVG <title>/<desc>: the OTHER way to name a figure, and the one a
        # renderer would reach for the moment aria-label is checked.
        self._desc_el: list[str] | None = None
        self._desc_ok = False
        self._desc_key = ""
        # Per stack entry, parallel to self.stack: "slot", "fixed" or "".
        # Kept apart so the six-field tuples above keep their shape.
        self.kinds: list[str] = []
        # Also parallel to self.stack: (the role="img" figure this element is
        # inside, or None; whether an engine chart already draws it). A width
        # typed as a percentage inside such a figure is a HAND-DRAWN BAR.
        self.bar_ctx: list[tuple[str | None, bool]] = []
        # Images and drawings nothing can move (_graphic), by alt/label/file.
        self.graphics_all: list[str] = []
        self.cls_stack: list[str] = []      # parallel to stack: first class, to name a graphic
        self.graphics_paged: list[str] = []
        self.hand_bars: list[str] = []
        self._hand_seen: set[int] = set()
        # <foreignObject> depth: HTML inside a drawing, judged like SVG text.
        self.fo = 0
        # A sentence whose only cover is data-fixed. C.derived is for a
        # tally or a computed share; prose under it is the escape hatch used
        # as a lid, and inside an SVG that was already refused.
        self.fixed_prose: list[str] = []
        # Every declaration that excused something, so the choice is listed
        # the way graphic(frozen=) already is: value -> how many elements.
        self.fixed_decl: dict[str, int] = {}
        self.restates_decl: dict[str, int] = {}
        # Slot fidelity: each data-slot element's own words (not a nested
        # slot's, not a footnote <sup>), and each data-desc's attribute
        # words — measured against what the renderer was actually served.
        self._slots: list[tuple[int, str, list[str]]] = []
        self.slot_runs: list[tuple[str, list[str]]] = []
        self.sup = 0
        # IMMOVABLE TEXT: a slot with no data-el on it or anywhere above it.
        # Its words can be typed into and the box they sit in can never be
        # dragged or given a width — every field of an imported page was this
        # (tfc-2027-priorities: 141 of 141). Keys, first sighting only.
        self.immovable_paged: list[str] = []
        self.immovable_all: list[str] = []

    def _desc_hit(self, text: str, declared: bool) -> None:
        """Record one accessible description, and whether it is wired."""
        if not text or is_data_mark(text):
            return
        if declared:
            self.desc_declared.append(text)
            return
        self.desc_all.append(text)
        if any(pg for *_, pg in self.stack):
            self.desc_paged.append(text)

    def _descriptions(self, tag, a) -> None:
        """The words a reader is given INSTEAD of what the tag draws.

        aria-label, alt and title live in an ATTRIBUTE, so every hook this
        class counts — data-slot on the text, data-el on the wrapper — can be
        perfect and the description still be a literal only the renderer can
        change. With role="img" it is worse than uneditable: the element's
        contents are not announced at all, so the description is the WHOLE
        figure for anyone who cannot see it, and the page's own labels
        becoming slots means a person can now retype the figure and leave its
        spoken version saying the old thing.

        Wired by blocks.describe / blocks.slot_descriptions (data-desc), by
        C.derived (data-fixed), or by not being announced at all
        (aria-hidden="true").
        """
        if not any(k in a for k in _DESC_ATTRS):
            return
        wired = (declared(a, "data-desc") or declared(a, "data-fixed")
                 or (a.get("aria-hidden") or "").lower() == "true")
        # The element's own class counts: _descriptions runs before the stack
        # append, so a description ON the .page wrapper is still in the sheet.
        page = "page" in (a.get("class") or "").split()
        if page:
            self.saw_page = True
        for k in _DESC_ATTRS:
            v = " ".join((a.get(k) or "").split())
            if not v or is_data_mark(v):
                continue
            if wired:
                self.desc_declared.append(v)
                if declared(a, "data-desc"):
                    self.slot_runs.append((a["data-desc"].strip(), [v]))
            elif page or any(pg for *_, pg in self.stack):
                self.desc_all.append(v)
                self.desc_paged.append(v)
            else:
                self.desc_all.append(v)

    def _graphic(self, tag: str, a: dict) -> None:
        """An image or drawing on the page that nothing can pick up.

        Text had four nets; a picture had none. An <img>, an outermost <svg>,
        a <video>/<canvas>/<iframe> with no data-el on it or on anything
        holding it cannot be selected, moved, resized or swapped — the
        Budget Primer's lifecycle wheel and fixed-costs chart were this
        (chart_scroll() around a bare <svg>, never graphic()). Exempt: the
        page's own shape layer (it holds movable shapes), and a glyph inside a
        link or button — the skill's rule that an icon living inside a control
        stays inline with it."""
        if tag not in _GRAPHIC_TAGS:
            return
        if any(sv for *_, sv, _ in self.stack):
            return                                   # inside a drawing already
        cls = (a.get("class") or "").split()
        if "shape-layer" in cls or "data-el" in a or "data-shape" in a or "data-chart" in a:
            return
        if any(el for _, _, el, *_ in self.stack):
            return
        if any(t in ("a", "button") for t, *_ in self.stack):
            return
        near = next((c for c in reversed(self.cls_stack) if c), "")
        name = (a.get("alt") or a.get("aria-label") or
                (a.get("src") or "").rsplit("/", 1)[-1] or
                (f"{tag}.{cls[0]}" if cls else f"{tag} in .{near}" if near else tag))
        name = " ".join(name.split())[:80]
        self.graphics_all.append(name)
        if any(pg for *_, pg in self.stack) or "page" in cls:
            self.graphics_paged.append(name)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        # BEFORE the _VOID return: <img> never comes back down, and alt is a
        # description exactly as much as aria-label is.
        self._descriptions(tag, a)
        self._graphic(tag, a)
        if tag in _VOID:
            return
        classes = (a.get("class") or "").split()
        # data-fixed (C.derived) is a DECLARATION: this text is a tally or a
        # computed value, named with the command that remakes it. It answers
        # the same question data-slot answers, so it clears dead text and the
        # C() trap alike. It does NOT excuse a sentence drawn inside an SVG —
        # a caption is prose wherever its numbers came from.
        slot = "data-slot" in a
        fixed = declared(a, "data-fixed")
        covered = slot or fixed
        if fixed:
            v = a["data-fixed"].strip()
            self.fixed_decl[v] = self.fixed_decl.get(v, 0) + 1
        if declared(a, "data-restates"):
            v = a["data-restates"].strip()
            self.restates_decl[v] = self.restates_decl.get(v, 0) + 1
        el = "data-el" in a
        trap = (el and not (a.get("data-el") or "").startswith(_PANEL_EDITED)
                and "ds-textbox" not in classes)
        page = "page" in classes
        self.saw_page = self.saw_page or page
        self.stack.append((tag, covered, el, trap, tag == "svg", page))
        self.kinds.append("slot" if slot else "fixed" if fixed else "")
        self.cls_stack.append(classes[0] if classes else "")
        self._bar(a, classes)
        if slot and (a.get("data-slot") or "").strip():
            self._slots.append((len(self.stack), a["data-slot"].strip(), []))
            # Movable is data-el on the slot or on anything holding it: the
            # field itself, its paragraph block, its card, its drawing.
            if not any(e for _, _, e, *_ in self.stack):
                key = a["data-slot"].strip()
                if key not in self.immovable_all:
                    self.immovable_all.append(key)
                    if any(pg for *_, pg in self.stack):
                        self.immovable_paged.append(key)
        if tag == "foreignobject":
            self.fo += 1
        if tag == "sup":
            self.sup += 1
        if tag in _OPAQUE_TAGS:
            self.opaque += 1
        if "data-frozen" in a:
            # A graphic declared frozen on purpose (graphic(frozen="…")):
            # listed, so the decision stays visible; never a finding.
            self.frozen_declared.append(a.get("data-frozen") or "")
        if tag == "text" and "data-ch" not in a:
            # An svg text element; aggregate its tspans for the words test.
            # A native chart's data-ch text is skipped: it edits in the Chart
            # panel. Everything else is collected, slotted or not, because the
            # two tests at the close are opposites — an UNSLOTTED label is
            # words nobody can reach, a SLOTTED one stating a figure is words
            # that move without the geometry under them.
            self._svg_text = []
            self._svg_fixed = fixed
            self._svg_slotted = slot
            self._svg_key = a.get("data-slot") or ""
            self._svg_declared = (declared(a, "data-restates")
                                  or declared(a, "data-fixed"))
        if tag in ("title", "desc") and any(sv for *_, sv, _ in self.stack[:-1]):
            # An SVG <title>/<desc> is the accessible name in element form.
            # data-slot on it is not enough to be reachable — it has no
            # geometry, so the editor cannot float a field over it — but
            # data-desc says a slot IS what fills it.
            self._desc_el = []
            self._desc_ok = declared(a, "data-desc") or declared(a, "data-fixed")
            self._desc_key = (a.get("data-desc") or "").strip()

    def _bar(self, a: dict, classes: list[str]) -> None:
        """Catch a bar drawn by hand: a part sized by a typed-in percentage
        width, inside a figure (role="img"), that no engine chart draws.

        That was every vote bar on tfc-2027-priorities — <i style="width:
        43.8%"> in a role="img" span — and it passed every other test here:
        the tally beside it was a slot, the aria-label a slot, the row movable.
        What nothing could reach was the bar itself. Retyping the tally never
        moved it, no panel opened it, and a regenerated count left it drawing
        the old number. The fix is blocks.meter (or blocks.chart): a chart the
        Chart panel edits, with the words that restate it derived from it."""
        parent = self.bar_ctx[-1] if self.bar_ctx else (None, False)
        fig, in_chart = parent
        in_chart = (in_chart or "data-chart" in a
                    or bool({"ds-graphic", "ds-chart", "ds-meter"} & set(classes)))
        if (a.get("role") or "").lower() == "img" and fig is None:
            fig = " ".join((a.get("aria-label") or a.get("class") or "figure").split())
            self._fig_n = getattr(self, "_fig_n", 0) + 1
            fig = f"{self._fig_n}\0{fig}"
        self.bar_ctx.append((fig, in_chart))
        if fig and not in_chart and re.search(
                r"(?:^|;)\s*width\s*:\s*[\d.]+%", a.get("style") or ""):
            n, label = fig.split("\0", 1)
            if int(n) not in self._hand_seen:
                self._hand_seen.add(int(n))
                self.hand_bars.append(label)

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                del self.kinds[i:]
                del self.bar_ctx[i:]
                del self.cls_stack[i:]
                break
        while self._slots and self._slots[-1][0] > len(self.stack):
            _, key, runs = self._slots.pop()
            self.slot_runs.append((key, runs))
        if tag == "foreignobject":
            self.fo = max(0, self.fo - 1)
        if tag == "sup":
            self.sup = max(0, self.sup - 1)
        if tag in _OPAQUE_TAGS:
            self.opaque = max(0, self.opaque - 1)
        if tag == "text" and self._svg_text is not None:
            whole = " ".join(t for t in self._svg_text if t).strip()
            if whole and self._svg_slotted:
                # The opposite failure. The label is editable; the bar, the
                # wedge or the band beside it is the renderer's and does not
                # follow. So a slot that STATES A FIGURE can be retyped into
                # disagreeing with the drawing it sits on, silently — unless
                # it says where the number comes from (svg_text restates=, or
                # C.derived, which makes it not a slot at all).
                # A SENTENCE is a caption, not a figure — prose wherever its
                # numbers came from, which is the frozen-prose rule read the
                # other way round. Retyping one is editing the words about a
                # number, and no shape claims to follow it.
                if (is_quantity(whole) and not is_sentence(whole)
                        and not self._svg_declared):
                    # Named by its KEY as well as its words: the key is what
                    # the fix edits, and two bars can carry the same figure.
                    self.restated.append(f"{self._svg_key}: {whole}"
                                         if self._svg_key else whole)
            elif whole and (is_sentence(whole) if self._svg_fixed
                            else not is_data_mark(whole)):
                # Any WORDS, not only a sentence: a legend entry or a step name
                # is exactly as unreachable as a caption. The rule is
                # graphic()'s own (blocks.is_data_mark), so the build and this
                # check agree.
                self.frozen_prose.append(whole)
            self._svg_text = None
            self._svg_slotted = self._svg_declared = False
        if tag in ("title", "desc") and self._desc_el is not None:
            whole = " ".join(t for t in self._desc_el if t).strip()
            self._desc_hit(whole, self._desc_ok)
            if self._desc_key and whole:
                self.slot_runs.append((self._desc_key, [whole]))
            self._desc_el = None

    def handle_data(self, data):
        # Before the opaque gate: <title> is an opaque tag (its text is never
        # page content), but an SVG <title> IS the figure's accessible name.
        if self._desc_el is not None:
            self._desc_el.append(" ".join(data.split()))
            return
        if self.opaque:
            return
        t = " ".join(data.split())
        # The 2-char floor exists to keep icon-font glyphs and stray bullet
        # characters (a single letter or symbol) out of the dead-text count.
        # A bare digit needs no such exemption — "7" is exactly as dead as
        # "71" — so it clears the gate on its own. Found by hand, not by this
        # check: tfc-2027-priorities had two single-digit stats ("7", "3")
        # sitting unwired while every prior run called the page clean.
        if not t or not (_ALNUM2.search(t) or t.isdigit()):
            return
        # The innermost open slot owns these words, whatever else they are —
        # unless they are a footnote marker, which resolve() puts there.
        if self._slots and not self.sup:
            self._slots[-1][2].append(t)
        if any(svg for *_, svg, _ in self.stack):
            if self._svg_text is not None:
                self._svg_text.append(t)
            elif self.fo:
                # HTML inside a drawing: graphic()'s rule, run by run.
                if "slot" in self.kinds:
                    pass
                elif "fixed" in self.kinds:
                    if is_sentence(t):
                        self.frozen_prose.append(t)
                elif not is_data_mark(t):
                    self.frozen_prose.append(t)
            return
        if "slot" in self.kinds:
            return
        if "fixed" in self.kinds:
            if is_sentence(t):
                self.fixed_prose.append(t)
            return
        if any(el for _, _, el, *_ in self.stack):
            # Movable, not slotted: the C() trap. It used to count only a
            # sentence or a long heading, on the theory that short strings
            # "ride along with their graphic" — but that is true of words in
            # a DRAWING (the svg branch above), not of HTML. In HTML a short
            # label is exactly as frozen: rxkids' timeline ("Month 4",
            # "Prenatal", "TANF") and option cards ("Option 1") sat in movable
            # blocks, draggable and impossible to retype, and the check called
            # the page clean. Any words now; data marks ("$500", "1") pass as
            # everywhere else. Panel-edited namespaces never reach here.
            # The NEAREST movable decides: words in an endnote (panel-edited,
            # never a trap) inside a movable endnotes box are the endnote's.
            near = next(e for e in reversed(self.stack) if e[2])
            if near[3] and not is_data_mark(t):
                self.trapped.append(t)
            return
        self.dead_all.append(t)
        if any(page for *_, page in self.stack):
            self.dead_paged.append(t)


def _squash(t: str) -> str:
    """Text reduced to what survives markup: entities decoded, one Unicode
    form, typographic quotes read as plain, and NO whitespace — a slot's words
    reach the page split across tags, joined and re-wrapped, so spacing is the
    one thing two faithful copies of the same words need not share."""
    import html as _html
    import unicodedata
    t = unicodedata.normalize("NFC", _html.unescape(t))
    t = (t.replace("\u2018", "'").replace("\u2019", "'")
         .replace("\u201c", '"').replace("\u201d", '"'))
    return re.sub(r"\s+", "", t)


# A str.format field in a slot's markdown — "in FY{fy} is {cip_total}" — which
# the renderer fills before the words reach the page. Filled with data
# (a year, a total), so it is matched as a short wildcard, never as words.
_FIELD = re.compile(r"\{[A-Za-z_][\w.\[\]]*(?:![rsa])?(?::[^{}]*)?\}")
_FILL = 80          # squashed characters one filled field may stand for


def _served_forms(words: list[str]) -> list[list[str]]:
    """Everything a slot's served markdown can render as — the raw block,
    and the block through each of the engine's own converters — each as its
    squashed literal segments, split wherever a format field is filled. A
    run of page text under the slot is faithful when one of these produces
    it."""
    from .content import block_html, md_inline, paragraph
    forms: list[list[str]] = []
    for w in words:
        variants = [w]
        for fn in (block_html, md_inline, paragraph):
            try:
                variants.append(re.sub(r"<[^>]+>", "", fn(w)))
            except Exception:                           # noqa: BLE001
                pass
        for v in variants:
            segs = [_squash(x) for x in _FIELD.split(v)]
            if segs not in forms:
                forms.append(segs)
    return forms


_MAGNITUDE = re.compile(r"(?i)hundred|thousand|million|billion|trillion|percent")


def _fill_ok(fill: str) -> bool:
    """A filled field holds data — "2027", "$4.53 billion" — never words:
    otherwise any short literal a renderer put beside a templated slot would
    pass as "what the field was filled with"."""
    return not re.search(r"[^\W\d_]{4,}", _MAGNITUDE.sub("", fill))


def _fits(run: str, segs: list[str]) -> bool:
    """Could `run` be a stretch of segs[0] FIELD segs[1] FIELD … once each
    field is filled with at most _FILL characters of data? It may start and
    end part-way through a segment — a run is whatever one text node holds."""
    def after(rest: str, j: int) -> bool:
        # `rest` begins at the fill between segs[j-1] and segs[j].
        seg = segs[j]
        for f in range(0, min(_FILL, len(rest)) + 1):
            if not _fill_ok(rest[:f]):
                continue                # "bill" is not data; "billion" is
            t = rest[f:]
            if seg.startswith(t):
                return True             # ends inside (or at the end of) seg
            if (t.startswith(seg) and j + 1 < len(segs)
                    and after(t[len(seg):], j + 1)):
                return True
        return False

    for i, seg in enumerate(segs):
        if run in seg:
            return True
        if i + 1 == len(segs):
            break                       # nothing is filled after the last
        # The run starts with some tail of this segment — or none of it,
        # when it starts inside the fill that follows.
        for k in range(len(seg), -1, -1):
            if run.startswith(seg[len(seg) - k:]) and after(run[k:], i + 1):
                return True
    return False


def check_slot_fidelity(runs: list[tuple[str, list[str]]],
                        served: dict[str, list[str]]):
    """(unbacked keys, foreign words) — data-slot hooks with nothing behind
    them, and words under a real slot that the slot never gave out.

    data-slot was trusted by presence. slot_attr() accepts ANY key, so a
    hook with a made-up key covered literal prose; and a real slot's element
    could hold renderer words beside the slot's own, which read as editable
    and are not — the edit rewrites the slot, the literal stays."""
    unbacked: list[str] = []
    foreign: list[str] = []
    forms: dict[str, list[list[str]]] = {}
    for key, texts in runs:
        if key not in served:
            if key not in unbacked:
                unbacked.append(key)
            continue
        if key not in forms:
            forms[key] = _served_forms(served[key])
        for t in texts:
            sq = _squash(t)
            if (sq and _ALNUM2.search(sq)
                    and not any(_fits(sq, f) for f in forms[key])):
                foreign.append(f"{key}: {t}")
    return unbacked, foreign


class _Visible(HTMLParser):
    """Every string a reader is shown or read, in document order: text nodes
    outside script/style/head, and the description attributes."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.opaque = 0
        self.runs: list[str] = []
        self.descs: list[str] = []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k in _DESC_ATTRS and v and v.strip():
                self.descs.append(" ".join(v.split()))
        if tag in _OPAQUE_TAGS and tag not in _VOID:
            self.opaque += 1

    def handle_endtag(self, tag):
        if tag in _OPAQUE_TAGS:
            self.opaque = max(0, self.opaque - 1)

    def handle_data(self, data):
        t = " ".join(data.split())
        if t and not self.opaque:
            self.runs.append(t)


def check_publish_only(edit_html: str, pub_html: str) -> list[str]:
    """Strings the PUBLISHED build shows that the edit-mode build never did.

    Every net above measures the edit-mode draft, because that is where the
    hooks are. A renderer branch that runs only when publishing — `if not
    DOCSYNC_EDIT: html += …` — was therefore invisible to all of them, and the
    one build a reader actually sees could carry words no editor ever showed.
    Scaffolding runs the other way (edit mode adds, never removes), so any
    string here is one that bypassed the editor entirely."""
    ed, pub = _Visible(), _Visible()
    ed.feed(edit_html)
    pub.feed(pub_html)
    # Joined, so a run the edit build split across a slot span still matches.
    whole = _squash("".join(ed.runs))
    descs = {_squash(d) for d in ed.descs}
    out: list[str] = []
    for t in pub.runs:
        sq = _squash(t)
        if sq and _ALNUM2.search(sq) and sq not in whole and t not in out:
            out.append(t)
    for d in pub.descs:
        sq = _squash(d)
        if d in ENGINE_PUBLISH_ONLY:
            continue
        if sq not in descs and not is_data_mark(d) and d not in out:
            out.append(d)
    return out


def _build(render: Path, out: Path, edit: bool, slotlog: Path | None = None,
           layout_json: Path | None = None):
    env = {k: v for k, v in os.environ.items()
           if k not in ("DOCSYNC_EDIT", "DOCSYNC_SLOTLOG", "DOCSYNC_LAYOUT")}
    env["DOCSYNC_OUT"] = str(out)
    if edit:
        env["DOCSYNC_EDIT"] = "1"
    if slotlog is not None:
        env["DOCSYNC_SLOTLOG"] = str(slotlog)
    if layout_json is not None:
        env["DOCSYNC_LAYOUT"] = str(layout_json)
    return subprocess.run([sys.executable, str(render)], env=env, cwd=ROOT,
                          capture_output=True, text=True, timeout=180)


def stress_layout(layout_json: Path | None, edit_html: str) -> dict:
    """The report's layout with everything a person can do to its page done at
    once: every movable (data-el) placed and holding its place, and every slot
    (data-slot) wearing a text style. Markup that only an edit makes a
    renderer emit is then emitted, where a check can read it."""
    import json
    raw = {}
    if layout_json is not None and layout_json.is_file():
        raw = json.loads(layout_json.read_text() or "{}")
    pos = raw.setdefault("positions", {})
    for el in dict.fromkeys(re.findall(r'\bdata-el="([^"]+)"', edit_html)):
        pos.setdefault(el, {"x": 0.5, "y": 0.5, "w": 2, "reserve": 0.4})
    text = raw.setdefault("text", {})
    for key in dict.fromkeys(re.findall(r'\bdata-slot="([^"]+)"', edit_html)):
        text.setdefault(key, {"color": "#2F3E46"})
    return raw


def _tally(counts: dict[str, int], n: int = 4) -> str:
    items = sorted(counts.items(), key=lambda kv: -kv[1])
    out = ", ".join(f"{k[:50]!r}" + (f" (x{c})" if c > 1 else "")
                    for k, c in items[:n])
    more = len(items) - n
    return out + (f" … and {more} more" if more > 0 else "")


def _samples(strings: list[str], n: int = 4) -> str:
    out = ", ".join(repr(s[:50]) for s in strings[:n])
    more = len(strings) - n
    return out + (f" … and {more} more" if more > 0 else "")


def check_editability(binding) -> list[Problem]:
    """Build the binding's EDIT-mode draft and measure what a user can touch.

    Runs the report's own renderer with DOCSYNC_EDIT=1 into a temp file — the
    same build the browser editor makes — because the committed output is the
    PUBLISH build, which strips every hook this check exists to count.
    """
    ed = binding.editor
    if ed is None or not ed.render:
        return []
    # "strict" makes findings ERRORS (CI fails): the generators write it into
    # every new project, so future ingestions cannot ship uneditable text
    # silently. Deliberate exceptions are named in editability_ok — an exact
    # string per line, a reviewable decision in the diff.
    strict = binding.editability == "strict"
    level = "error" if strict else "warn"
    accepted = set(binding.editability_ok)
    import json
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "edit.html"
        log = Path(td) / "served.json"
        r = _build(ed.render, out, edit=True, slotlog=log)
        if r.returncode or not out.exists():
            tail = (r.stderr.strip() or r.stdout.strip()).splitlines()
            return [Problem(
                "editability",
                f"edit-mode build failed ({tail[-1] if tail else 'no output'})"
                " — the draft editor cannot open what this check cannot build",
                level)]
        html = out.read_text(encoding="utf-8")
        # No log at all means a renderer that never built a Content (or one
        # vendored before the log existed): nothing to hold the hooks to, so
        # the fidelity test is skipped rather than failing every slot.
        served = (json.loads(log.read_text(encoding="utf-8"))
                  if log.exists() else None)
        pout = Path(td) / "publish.html"
        pr = _build(ed.render, pout, edit=False)
        pub_html = (pout.read_text(encoding="utf-8")
                    if not pr.returncode and pout.exists() else None)
        ptail = (pr.stderr.strip() or pr.stdout.strip()).splitlines()
        # Once more with every movable moved and every slot styled (see
        # "every attribute said once"): the tags only an edit makes.
        slay = Path(td) / "stress-layout.json"
        slay.write_text(json.dumps(stress_layout(ed.layout, html)))
        sout = Path(td) / "stress.html"
        sr = _build(ed.render, sout, edit=True, layout_json=slay)
        stress_html = (sout.read_text(encoding="utf-8")
                       if not sr.returncode and sout.exists() else None)
        stail = (sr.stderr.strip() or sr.stdout.strip()).splitlines()

    cov = _Coverage()
    cov.feed(html)
    problems: list[Problem] = []
    dead = [t for t in (cov.dead_paged if cov.saw_page else cov.dead_all)
            if t not in accepted]
    frozen = [s for s in cov.frozen_prose if s not in accepted]
    trapped = [t for t in cov.trapped if t not in accepted]
    descs = [t for t in (cov.desc_paged if cov.saw_page else cov.desc_all)
             if t not in accepted]
    restated = [t for t in cov.restated if t not in accepted]
    fixed_prose = [t for t in cov.fixed_prose if t not in accepted]
    unbacked, foreign = (check_slot_fidelity(cov.slot_runs, served)
                         if served is not None else ([], []))
    unbacked = [k for k in unbacked if k not in accepted]
    foreign = [t for t in foreign if t not in accepted]
    pub_only = ([t for t in check_publish_only(html, pub_html)
                 if t not in accepted] if pub_html is not None else [])
    immovable = [k for k in (cov.immovable_paged if cov.saw_page
                             else cov.immovable_all) if k not in accepted]
    hint = ("Wire them (C.html / C.slot_attr / L.attr), declare derived "
            "values with C.derived('<how to remake it>'), or list each in "
            "this binding's editability_ok" if strict else
            "Wire them (C.html / C.slot_attr / L.attr), declare derived "
            "values with C.derived(…), or accept them as chrome knowingly")
    if dead:
        problems.append(Problem(
            "editability",
            f"{len(dead)} visible text string(s) carry no edit hook — not a "
            f"slot, not movable: {_samples(dead)}. {hint}",
            level))
    if immovable:
        # The editor's promise is that any text box can be MOVED and RESIZED,
        # not only typed into. A slot with no data-el on it or above it keeps
        # half of that: its words edit, and nothing can pick the box up or
        # give it a width. Every field of an imported page was this until the
        # engine filled propose's markers itself (blocks.fill_markers).
        problems.append(Problem(
            "editability",
            f"{len(immovable)} text field(s) can be typed into but never moved "
            f"or resized — no data-el on the field or on anything holding it: "
            f"{_samples(immovable)}. Give each a movable box: C.html for a "
            f"paragraph, L.attr on the element or on the block it sits in "
            f"(its card, its list, its table), graphic() for words in a "
            f"drawing, blocks.fill_markers for an imported page's markers",
            level))
    graphics = [t for t in (cov.graphics_paged if cov.saw_page else cov.graphics_all)
                if t not in accepted]
    if graphics:
        problems.append(Problem(
            "editability",
            f"{len(graphics)} image(s) or drawing(s) nothing can select, move or "
            f"resize — no data-el on it or on anything holding it: "
            f"{_samples(graphics)}. Draw each through blocks.graphic (a drawing), "
            f"blocks.chart / blocks.meter (a chart), L.attr on the <img> or its "
            f"figure, or L.wrap around a composite",
            level))
    hand_bars = [t for t in cov.hand_bars if t not in accepted]
    if hand_bars:
        problems.append(Problem(
            "editability",
            f"{len(hand_bars)} bar(s) drawn by hand — parts sized by a typed-in "
            f"width, which no editor panel opens and no retyped number moves: "
            f"{_samples(hand_bars)}. Draw each with blocks.meter (one bar of "
            f"parts) or blocks.chart, so it edits in the Chart panel, and build "
            f"any tally that restates it from blocks.meter_values",
            level))
    if frozen:
        problems.append(Problem(
            "editability",
            f"{len(frozen)} label(s) drawn inside a graphic as plain words, so "
            f"only the renderer can change them: {_samples(frozen)}. Draw each "
            f"with blocks.svg_text (a slot), mark data with C.derived, or "
            f"declare the graphic frozen='<why>'",
            level))
    if cov.frozen_declared:
        problems.append(Problem(
            "editability",
            f"{len(cov.frozen_declared)} graphic(s) declared frozen on purpose "
            f"(words are the drawing): {_samples(cov.frozen_declared)}",
            "warn"))
    if trapped:
        problems.append(Problem(
            "editability",
            f"{len(trapped)} text string(s) inside a movable (data-el) wrapper "
            f"with no data-slot — draggable, words frozen (the C() trap): "
            f"{_samples(trapped)}. Give the text a slot: C.html / C.slot_span "
            f"/ C.slot_attr",
            level))
    if descs:
        problems.append(Problem(
            "editability",
            f"{len(descs)} accessible description(s) are renderer literals — "
            f"words a reader is given INSTEAD of the drawing, in an attribute "
            f"no slot can reach: {_samples(descs)}. Write each with "
            f"blocks.describe(C, key, default) (a whole static body: "
            f"blocks.slot_descriptions), or mark it aria-hidden=\"true\" when "
            f"the element only repeats text beside it",
            level))
    if restated:
        problems.append(Problem(
            "editability",
            f"{len(restated)} figure(s) inside a drawing are free-text slots: "
            f"retyping one does not move the shape it labels, so the drawing "
            f"can be made to disagree with itself: {_samples(restated)}. Say "
            f"where the number comes from — svg_text(…, restates='<how it is "
            f"remade>') — or C.derived to take it out of the slot entirely",
            level))
    if fixed_prose:
        problems.append(Problem(
            "editability",
            f"{len(fixed_prose)} sentence(s) covered only by data-fixed "
            f"(C.derived): {_samples(fixed_prose)}. C.derived is for a value "
            f"a person must not retype — a tally, a share — not a caption; "
            f"give the words a slot and derive only the number inside them",
            level))
    if unbacked:
        problems.append(Problem(
            "editability",
            f"{len(unbacked)} data-slot / data-desc key(s) the renderer never "
            f"read, so the words under them came from somewhere else and no "
            f"edit reaches them: {_samples(unbacked)}. Read the key through "
            f"C (C.html / C.t / C.text_or) or remove the hook",
            level))
    if foreign:
        problems.append(Problem(
            "editability",
            f"{len(foreign)} string(s) sit inside a slot's element but are not "
            f"the slot's words — they look editable, and an edit rewrites the "
            f"slot and leaves them: {_samples(foreign)}. Move each out of the "
            f"slot's element and wire it on its own, or into content.md",
            level))
    if pub_only:
        problems.append(Problem(
            "editability",
            f"{len(pub_only)} string(s) appear in the PUBLISHED build and "
            f"never in the edit-mode draft, so no editor ever showed them: "
            f"{_samples(pub_only)}. Render them in both modes and wire them",
            level))
    if pub_html is None:
        problems.append(Problem(
            "editability",
            f"publish build failed ({ptail[-1] if ptail else 'no output'}) — "
            f"publish-only text was not checked",
            "warn"))
    if cov.fixed_decl:
        problems.append(Problem(
            "editability",
            f"{sum(cov.fixed_decl.values())} value(s) declared derived "
            f"(C.derived), from {len(cov.fixed_decl)} source(s): "
            f"{_tally(cov.fixed_decl)}",
            "note"))
    if cov.restates_decl:
        problems.append(Problem(
            "editability",
            f"{sum(cov.restates_decl.values())} figure label(s) declared "
            f"restated (svg_text restates=), from {len(cov.restates_decl)} "
            f"source(s): {_tally(cov.restates_decl)}",
            "note"))
    # Not an editability finding, and an error whatever the binding's
    # editability says: a move or a style the page drops is a broken page.
    # The committed pages are held to the same by check_attributes.
    if stress_html is None:
        problems.append(Problem(
            "attributes",
            f"the build with everything moved and every slot styled failed "
            f"({stail[-1] if stail else 'no output'}) — an edited page's "
            f"attributes were not checked",
            "warn"))
    else:
        for t in attributes_twice(stress_html):
            problems.append(Problem(
                "attributes",
                f"{t} given twice once the page is edited (everything moved, "
                f"every slot styled) — the browser keeps the first, so that move "
                f"or style never reaches the page. Merge them into one: "
                f"L.attr(id, extra), or merge_attrs()"))
    return problems


def check_html(html: str) -> list[Problem]:
    """Every invariant, over one rendered document."""
    return [p for fn in CHECKS for p in fn(html)]


# --- walking the registry ----------------------------------------------------

def outputs_for(binding) -> list[Path]:
    """Every HTML file this binding is responsible for, deduped, in order."""
    paths: list[Path] = []
    for raw in binding.outputs:
        paths.append(ROOT / raw if not Path(raw).is_absolute() else Path(raw))
    if binding.editor is not None and binding.editor.out:
        paths.append(binding.editor.out)
    seen, out = set(), []
    for p in paths:
        p = p.resolve()
        if p not in seen and p.suffix.lower() in (".html", ".htm"):
            seen.add(p)
            out.append(p)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python3 -m docsync.check",
        description="Verify rendered reports against the engine's invariants.")
    ap.add_argument("--id", action="append", dest="ids", metavar="BINDING",
                    help="only this binding (repeatable); default: all")
    ap.add_argument("--build", action="store_true",
                    help="run each binding's build command first")
    args = ap.parse_args(argv)

    try:
        bindings = load_registry()
    except RegistryError as e:
        print(f"docsync.check: {e}", file=sys.stderr)
        return 2

    if args.ids:
        known = {b.id for b in bindings}
        unknown = sorted(set(args.ids) - known)
        if unknown:
            print(f"docsync.check: no such binding: {', '.join(unknown)}\n"
                  f"  known: {', '.join(sorted(known))}", file=sys.stderr)
            return 2
        bindings = [b for b in bindings if b.id in args.ids]

    failed = warned = checked = 0
    dupes: dict[str, int] = {}
    for b in bindings:
        if args.build and b.build:
            r = subprocess.run(b.build, shell=True, cwd=ROOT,
                               capture_output=True, text=True)
            if r.returncode:
                print(f"FAIL {b.id}: build failed\n"
                      f"  $ {b.build}\n"
                      f"  {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ''}")
                failed += 1
                continue

        for path in outputs_for(b):
            if not path.exists():
                # Not every binding's output is committed; a missing file is a
                # reason to say so, not to fail a check that never ran.
                print(f"skip {b.id}: {path.relative_to(ROOT)} not built")
                continue
            checked += 1
            problems = check_html(path.read_text(encoding="utf-8"))
            rel = path.relative_to(ROOT)

            # The same warning repeated once per marker is noise; the reader
            # needs to know the shape of the problem and how widespread it is.
            seen, unique = set(), []
            for pr in problems:
                if str(pr) in seen:
                    dupes[str(pr)] = dupes.get(str(pr), 1) + 1
                    continue
                seen.add(str(pr))
                unique.append(pr)

            errors = [pr for pr in unique if pr.is_error]
            warns = [pr for pr in unique if not pr.is_error]
            if errors:
                failed += 1
                print(f"FAIL {b.id}: {rel}")
            elif warns:
                warned += 1
                print(f"warn {b.id}: {rel}")
            else:
                print(f"  ok {b.id}: {rel}")
            for pr in (errors + warns)[:20]:
                n = dupes.get(str(pr), 1)
                print(f"     {pr}" + (f"  (x{n})" if n > 1 else ""))
            if len(errors) + len(warns) > 20:
                print(f"     … and {len(errors) + len(warns) - 20} more")

        # The editability pass builds its own (edit-mode) document, so it is
        # per binding, not per committed output file.
        if b.editor is not None and b.editor.render:
            checked += 1
            eprobs = check_editability(b)
            if any(pr.is_error for pr in eprobs):
                failed += 1
                strict = any(pr.is_error and pr.check == "editability" for pr in eprobs)
                print(f"FAIL {b.id}: edit-mode draft"
                      + (" (editability: strict)" if strict else ""))
            elif any(pr.level == "warn" for pr in eprobs):
                warned += 1
                print(f"warn {b.id}: edit-mode draft")
            else:
                print(f"  ok {b.id}: edit-mode draft")
            for pr in eprobs:
                print(f"     {pr}")

    tail = f", {warned} with warnings" if warned else ""
    print(f"\n{checked} file(s) checked, {failed} failing{tail}")
    # Warnings never fail the run: they describe pages that still reach a
    # reader correctly in print, and a check nobody can get to green is a
    # check people learn to ignore.
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
