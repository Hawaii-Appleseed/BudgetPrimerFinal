"""Reusable, editor-aware building blocks ANY report renderer can import.

These grew up inside the Budget Primer's renderer and were trapped there — a
new report scaffolded beside it got plain prose and nothing else. Extracted
here they travel with the engine: `graphic()` for a movable/resizable inline
SVG, `card()` for a coloured tile whose title/bullets can optionally be pulled
apart in the editor, `pdf_button()` for the reader-facing "Download PDF" (with
the `print_css()` that makes its output correct), and the `is_light_bg()`
contrast test they share.

Deliberately zero-stylesheet: every visual rule is inlined on the markup, so a
minimal scaffolded renderer with no CSS of its own gets the same result as the
fully art-directed primer. The classes that ARE emitted (`ds-graphic`,
`ds-detachable`) are the draft editor's behavioural hooks — corner-resize
handles, independent grab inside a movable — not styling.

Usage, from a project renderer:

    from docsync.blocks import graphic, card, pdf_button

    graphic(L, "page1.diagram", '<svg viewBox="0 0 100 60">…</svg>', w=2.0)
    card(C, L, "page1.card.title", "page1.card.bullets", "#52796F",
         detachable=True, min_h=1.8)

    # Once, just inside <body> — a reader-facing download that stays in step
    # with the page, plus the print rules that make the PDF come out right.
    pdf_button(L, bg="#52796F")

The primer's own render_report.py keeps a thin wrapper (its `graphic()`
delegates here), so there is one implementation to maintain.
"""
from __future__ import annotations

import os
import re
from html.parser import HTMLParser

from .content import merge_attrs, split_style
from .layout import fill_css, fill_repr


def is_light_bg(hexc) -> bool:
    """True when a fill is light enough to need dark (not white) text.

    An 8-digit fill carries alpha: a half-transparent dark tile shows the white
    page through it and reads light, so the colour is composited over white
    before the luminance test."""
    h = str(hexc).lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    if len(h) == 8:
        a = int(h[6:8], 16) / 255
        r, g, b = (v * a + 255 * (1 - a) for v in (r, g, b))
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) > 130


def _fit_svg(svg: str) -> str:
    """Make the SVG fill its wrapper (the wrapper carries the width; the
    viewBox keeps the aspect). Styles go inline so no stylesheet is needed —
    only when the tag has none of its own, which is left alone."""
    if re.match(r"\s*<svg[^>]*\bstyle=", svg):
        return svg
    return re.sub(r"<svg\b", '<svg style="display:block;width:100%;height:auto"',
                  svg, count=1)


def _in(v) -> str:
    """An inch measurement, without a pointless trailing zero.

    The same size arrives as an int from a renderer's own page=(8.5, 11) and as
    a float from layout.json's {"h": 11}, and "11in" vs "11.0in" in the CSS is
    a diff in generated output that means nothing."""
    return f"{float(v):g}in"


def print_css(L, pad: str = "", link_ink: str = "") -> str:
    """The rules that make a report PRINT as the sheets it was designed as.

    Without these a report prints as what it technically is — a scrolling web
    page — and every "Download PDF" is wrong in the same four ways: the sheet
    carries the browser's own margins on top of the page's, the drop shadow and
    the 24px gutter between pages print as grey bands, nothing forces a break
    at a page boundary so sheet 2 starts halfway down sheet 1, and Chrome drops
    every background colour unless told not to. The renderer that art-directs a
    document cannot be relied on to remember all four, so they live here.

    Emitted as a <style> in the body, which is valid and applies document-wide
    — the same trick layout.py's page_style() uses, and for the same reason: it
    can ride along with a call the renderer is already making.

    `pad` overrides the page's padding for print only, for a report whose
    screen padding is deliberately different from its print margins (the Budget
    Primer's is). Left empty the screen padding stands, which is what a report
    designed at its real size wants. `link_ink` re-colours and underlines links
    for print, off by default: a document whose links are already styled as
    citations does not want them underlined too.
    """
    w = getattr(L, "page_w", 8.5)
    h = getattr(L, "page_h", 11.0)
    # A pageless layout ({"h": null}) is one continuous surface: it gets a
    # width and lets the sheet run, with no fixed height and no forced breaks.
    pageless = bool(getattr(L, "page", None)) and L.page[1] is None
    sheet = f"{_in(w)} auto" if pageless else f"{_in(w)} {_in(h)}"
    box = ["margin:0", "box-shadow:none", "max-width:none"]
    if not pageless:
        # Exact sheets, so N pages print as exactly N sheets. .page is
        # overflow:hidden by convention, so a page whose content genuinely
        # exceeds its height is clipped rather than spilling a near-blank
        # extra sheet — the editor's own "page cut in print" warning is what
        # catches that, before it reaches a PDF.
        box += [f"height:{_in(h)}", "page-break-after:always"]
    pagerule = ";".join(box) + (f";padding:{pad}" if pad else "")
    links = f"a{{color:{link_ink};text-decoration:underline}}" if link_ink else ""
    return (
        f"<style>@page{{size:{sheet};margin:0}}@media print{{"
        # Must hang off a selector — a bare declaration inside @media is
        # invalid and gets dropped silently.
        "*,*::before,*::after{-webkit-print-color-adjust:exact !important;"
        "print-color-adjust:exact !important}"
        "body{background:#fff}"
        # The engine-wide opt-out for on-screen chrome: toolbars, download
        # buttons, tooltips — anything that is part of the web page but not
        # part of the document.
        ".noprint{display:none !important}"
        f".page{{{pagerule}}}"
        ".page:last-child{page-break-after:auto}"
        f"{links}}}</style>")


# The floor a chart's smallest label must clear on screen, in CSS px. Kept in
# step with tests/editor/text-legibility.spec.js FLOOR.screen, which is what
# fails a build when a chart drops under it, and a shade above it so a chart
# sitting exactly on the line does not fail on a rounding difference.
CHART_MIN_LABEL_PX = 10.5


def _viewbox_w(svg: str) -> float | None:
    """The design width an SVG's own viewBox declares, or None."""
    m = re.search(r'viewBox="\s*[-\d.]+\s+[-\d.]+\s+([\d.]+)', svg)
    return float(m.group(1)) if m and float(m.group(1)) > 0 else None


def chart_scroll(svg: str, *, smallest_label: float = 11.5,
                 floor_px: float = CHART_MIN_LABEL_PX) -> str:
    """A chart that stays LEGIBLE on a phone, by scrolling instead of shrinking.

    The asymmetry this exists for: `.page` keeps `max-width:100%`, so on a
    375px screen an 8.5in sheet renders at about 0.42x. HTML text does not
    shrink with it — a px is a px — but an SVG with a viewBox does, and its
    text goes down with everything else it draws. The primer's chart labels
    measured 4.8px to 9px on a phone while the prose beside them was fine, and
    nothing in the design looked wrong at the desk it was designed at.

    Growing the labels instead is the obvious fix and the wrong one: they are
    positioned in the same user units, so a chart dense enough to need the help
    (twenty-four department rows) is exactly the one whose labels collide when
    they get it. Scrolling changes no geometry at all.

    So: below the sheet's own width the chart stops shrinking and its wrapper
    scrolls, the way layout.py's mobile_css() already handles a table too wide
    to fit. `smallest_label` is the smallest font-size the chart uses in ITS
    user units — the one that hits the floor first — and the stopping point
    falls out of it, so a chart with generous labels shrinks further before it
    starts scrolling and never scrolls further than it must. Pair with one
    chart_scroll_css() anywhere in the document.
    """
    w = _viewbox_w(svg)
    if w is None:
        # No viewBox means no scaling to reason about (and nothing to compute a
        # stopping point from). Hand it back untouched rather than wrap it in a
        # scroller whose min-width would be a guess.
        return svg
    return (f'<div class="ds-chart-scroll" style="--ds-chart-min:'
            f'{w * floor_px / smallest_label:.0f}px">{svg}</div>')


def chart_scroll_css(breakpoint_in: float = 8.5) -> str:
    """The one rule chart_scroll()'s wrappers need. Emit once, anywhere.

    A <style> in the body, like print_css() and layout.py's page_style(): it
    rides along with a call the renderer is already making rather than needing
    a line added to a <head> the engine does not own.

    The breakpoint is the SHEET, not a device — below its own width the page
    can no longer show the design at the size it was composed at, which is the
    actual condition, and it is the same test mobile_css() makes.

    A report whose pages are not white sets `--ds-chart-bg` to the page colour
    on the wrapper (or anywhere above it); the scroll shadows are drawn over
    that colour, so a wrong value shows as a pale seam at the chart's edges.
    """
    return (
        f"<style>@media screen and (max-width:{_in(breakpoint_in)}){{"
        ".ds-chart-scroll{overflow-x:auto;overscroll-behavior-x:contain;"
        "-webkit-overflow-scrolling:touch;"
        # A shadow at whichever edge still has content behind it, and none at
        # an edge that doesn't. Without it a clipped figure just looks broken —
        # a bar chart running off the right reads as "there is more", but a
        # circular diagram cut down its side reads as a rendering fault, and a
        # phone shows no persistent scrollbar to say otherwise. The two `local`
        # layers are the page background painting over the shadow when the
        # scroller is at that end; the two `scroll` layers are the shadows
        # themselves, fixed to the frame. Pure CSS — no scroll listener.
        "background:"
        "linear-gradient(to right,var(--ds-chart-bg,#fff) 30%,"
        "rgba(255,255,255,0)) left/22px 100% no-repeat local,"
        "linear-gradient(to left,var(--ds-chart-bg,#fff) 30%,"
        "rgba(255,255,255,0)) right/22px 100% no-repeat local,"
        "radial-gradient(farthest-side at 0 50%,rgba(47,62,70,.17),"
        "rgba(47,62,70,0)) left/11px 100% no-repeat scroll,"
        "radial-gradient(farthest-side at 100% 50%,rgba(47,62,70,.17),"
        "rgba(47,62,70,0)) right/11px 100% no-repeat scroll}"
        # min-width beats width:100% whatever the report's own stylesheet says,
        # which is what stops the sheet from scaling the chart any further. The
        # margin moves to the wrapper so a scrolled chart keeps its spacing
        # without the scrollbar sitting inside it.
        ".ds-chart-scroll>svg{min-width:var(--ds-chart-min);margin:0}"
        "}</style>")


# The published button's tooltip. The edit build's button does something else
# (a server-side export of the draft), so it says something else — which makes
# this the one string the engine itself emits ONLY when publishing, and the
# one docsync.check's publish-only diff is told to expect. Named, not matched
# by pattern, so nothing else can shelter behind it.
PDF_PRINT_TITLE = "Opens your browser's print dialog — choose Save as PDF"
ENGINE_PUBLISH_ONLY = frozenset({PDF_PRINT_TITLE})


def pdf_button(L, label: str = "Download PDF", *, bg: str = "#2F3E46",
               ink: str = "#FFFFFF", top: str = "18px", right: str = "18px",
               pad: str = "", link_ink: str = "", css: bool = True) -> str:
    """A screen-only "Download PDF" control, pinned to the upper-right corner.

    Prints through the browser (window.print() -> "Save as PDF") rather than
    linking to a PDF file, which is the Budget Primer's approach and the reason
    it holds up: the download is generated from the page the reader is looking
    at, so it can never go stale, it needs no build step or committed binary,
    it works from any host, and the text stays selectable vector with its links
    live. A pre-rendered PDF beside the page is one content edit away from
    quietly shipping last month's document.

    Carries print_css() with it by default, because a button that produces a
    badly formatted PDF is worse than no button — the two halves are one
    capability. Call once per document; pass css=False for a second button.

    Drawn in the editor too, and it WORKS there: it was hidden at first, then
    drawn but inert — and an inert button that looks clickable reads as broken,
    which is exactly what got reported. In the editor a click posts a message
    up to the parent chrome, which runs the same server-side Chrome export as
    File > Download > PDF — so the button downloads the current draft, unsaved
    edits and all, rather than window.print()-ing the artboard iframe with its
    selection handles and edit affordances baked in.
    """
    sheet = print_css(L, pad=pad, link_ink=link_ink) if css else ""
    edit = bool(os.environ.get("DOCSYNC_EDIT"))
    act = ('onclick="parent.postMessage({ds:\'export-pdf\'},\'*\')" '
           'title="Download this draft as a PDF"' if edit else
           'onclick="window.print()" '
           f'title="{PDF_PRINT_TITLE}"')
    # The ds- class prefix is the editor's "this control stays live" hook:
    # deafenStickyChrome() sets pointer-events:none on every fixed/sticky child
    # of <body> so a report's standing chrome cannot eat canvas clicks — which
    # silently made this button unclickable on the artboard. Real clicks passed
    # straight through it; only synthetic dispatch (which ignores
    # pointer-events) still fired, which is how the breakage got past a test.
    klass = "ds-pdfbtn noprint" if edit else "noprint"
    return sheet + (
        f'<button type="button" class="{klass}" {act} '
        f'style="position:fixed;top:{top};right:{right};z-index:60;'
        f'background:{bg};color:{ink};border:0;border-radius:8px;'
        f'padding:9px 15px;font-family:inherit;font-size:14px;font-weight:700;'
        f'cursor:pointer;box-shadow:0 2px 10px rgba(0,0,0,.18)">'
        f'↓&nbsp;{label}</button>')


# ---- words inside a drawing ---------------------------------------------------
# A label a renderer writes INTO an SVG is frozen to the editor: the graphic
# moves and resizes, the words can only be changed by editing Python. That is
# the one way an "AI-generated element" ended up uneditable on the page, and
# a warning after the fact did not stop it — so graphic() refuses at build
# time, in edit mode, and the editor's draft does not render until every word
# inside the drawing is one of three things:
#
#   a SLOT           svg_text(C, key, default, …) — the words live in
#                    content.md and edit on the page;
#   DERIVED DATA     <text … {C.derived("<how it is remade>")}> — a category
#                    name, a series label that comes from a data file, named
#                    with the command that regenerates it;
#   FROZEN ON PURPOSE  graphic(…, frozen="<why>") — the whole drawing's words
#                    are the drawing (a logo lockup, a wordmark), declared in
#                    one place with a reason; docsync.check lists every such
#                    declaration so the choice stays visible.
#
# Data marks pass on their own: numbers, currency, percentages, a tick like
# "FY26", "TY23", "$5M", "1st", a month's three letters. The rule is one
# function — is_data_mark — shared with docsync.check so the two agree.
_WORD_RE = re.compile(r"[A-Za-z\u00C0-\u024F\u02BB\u2018]+")


def is_data_mark(t: str) -> bool:
    """True for text that is a data mark, not words: no letters at all, or a
    single run of at most three (a unit, a year prefix, an ordinal, a month)."""
    words = _WORD_RE.findall(t)
    return not words or (len(words) == 1 and len(words[0]) <= 3)


_SENT_END_RE = re.compile(r"[.!?…][\"'”’)]*$")


def is_sentence(t: str) -> bool:
    """Sentence-shaped: five or more words ending in sentence punctuation.
    A caption, wherever its numbers came from — which is why C.derived does
    not excuse one inside a drawing (a category name, yes; a sentence, no)."""
    return len(t.split()) >= 5 and bool(_SENT_END_RE.search(t))


_QTY_RE = re.compile(r"""(?x)
    \$\s?\d                              # $1,500   $86.1M   $3.0M
  | \d[\d,.]*\s?%                        # 46%      84.0%
  | \b\d[\d,.]*\s?(?:M|B|K)\b            # 705M     1.4B
  | \b\d[\d,.]*\s(?:million|billion|thousand|percent)\b
  | \b\d{1,3}(?:,\d{3})+\b               # 12,345
""")


def is_quantity(t: str) -> bool:
    """True when a label STATES a figure — currency, a percentage, a magnitude,
    a thousands-separated count.

    Not "contains a digit": a product name ("Claude Opus 5"), a fiscal year
    ("FY26") and an ordinal are digits that stand for nothing the drawing also
    encodes. A quantity is the case that matters, because the drawing encodes
    it TWICE — once as the words and once as the geometry — and only the words
    are editable. See svg_text's `restates`.
    """
    return bool(_QTY_RE.search(t or ""))


def declared(a: dict, name: str) -> bool:
    """True when attribute `name` is present AND says something.

    A declaration is only worth what it says. `data-fixed=""` — C.derived("")
    — named no command that remakes the value, and `data-restates=" "` no
    source for the figure, yet presence alone used to clear a whole subtree:
    the escape hatch with no reason attached, and so no review. The same test
    in graphic(), C.derived, svg_text and docsync.check, so none of them can
    be satisfied by an empty string."""
    return bool((a.get(name) or "").strip())


class _SvgWords(HTMLParser):
    """Collect every run of words in an SVG the editor could never reach.

    A parser rather than a <text> regex, because the regex was the gap: it
    never saw HTML inside a <foreignObject> — sentences in a <div> inside a
    drawing, refused as SVG text and waved through as markup — nor an
    upper-case <TEXT>, and it read `class="data-slot"` as a hook."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        # Per open element: tag, slot/chart hook, fixed declaration.
        self.stack: list[tuple[str, bool, bool]] = []
        self.text: list[str] | None = None     # the open <text>'s words
        self.text_hook = self.text_fixed = False
        self.fo = 0                            # <foreignObject> depth
        self.fo_runs: list[tuple[str, bool, bool]] = []
        self.out: list[str] = []

    def _judge(self, t: str, hooked: bool, fixed: bool) -> None:
        if not t or hooked:
            return
        if fixed:
            if is_sentence(t):
                self.out.append(t)
        elif not is_data_mark(t):
            self.out.append(t)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        hook = "data-slot" in a or "data-ch" in a
        fixed = declared(a, "data-fixed")
        if tag == "text" and self.text is None:
            self.text = []
            self.text_hook, self.text_fixed = hook, fixed
        if tag == "foreignobject":
            self.fo += 1
        self.stack.append((tag, hook, fixed))

    def handle_startendtag(self, tag, attrs):
        # <text/> and <foreignObject/> carry nothing; void either way.
        pass

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        else:
            return
        if tag == "text" and self.text is not None:
            self._judge(" ".join(" ".join(self.text).split()),
                        self.text_hook, self.text_fixed)
            self.text = None
        if tag == "foreignobject":
            self.fo = max(0, self.fo - 1)

    def handle_data(self, data):
        t = " ".join(data.split())
        if not t:
            return
        if self.text is not None:
            self.text.append(t)
        elif self.fo:
            # HTML inside the drawing. Judged run by run, by the ancestors
            # it sits under — a slot there is as reachable as anywhere.
            self._judge(t, any(h for _, h, _ in self.stack),
                        any(f for *_, f in self.stack))


def svg_literals(svg: str) -> list[str]:
    """Every run of words in `svg` the editor could never reach — the strings
    graphic() refuses: words in a <text> with no hook at all, words in HTML
    inside a <foreignObject> with no slot above them, and a whole SENTENCE
    even under data-fixed (derived data is a value, not a caption). tspans
    are read as part of their <text>; a data-fixed with no reason in it is no
    declaration (see declared())."""
    p = _SvgWords()
    p.feed(svg or "")
    p.close()
    return p.out


class SvgLiteralError(ValueError):
    """A graphic was handed words the editor could never reach."""


def graphic(L, el_id: str, svg: str, w: float = 1.5, cls: str = "",
            frozen: str | None = None) -> str:
    """A free-standing SVG the editor can MOVE, RESIZE (proportionally, from
    any corner) and ROTATE like an image — its placement lives in layout.json
    under el_id, so a drag or a resize sticks across rebuilds.

    This is the ONE way to add an SVG a report editor should be able to
    reposition: a bare <svg> in the markup is frozen, invisible to the editor.
    Give every graphic a unique, stable el_id; the SVG MUST carry a viewBox
    (it scales to fill the wrapper); `w` is its default width in inches, which
    applies only until the user resizes — after that layout.json's width wins,
    so a rebuild never overwrites their sizing.

    Words inside the drawing are REFUSED in edit mode unless each is a slot
    (svg_text), derived data (C.derived) or the graphic is declared
    `frozen="<why>"` — see the note above is_data_mark. Publishing never
    refuses (the published bytes are the renderer's to keep), but the draft
    editor and docsync.check's edit-mode pass both build in edit mode, so a
    literal cannot reach a person without being seen here first."""
    edit = bool(os.environ.get("DOCSYNC_EDIT"))
    # A reason of only whitespace is no reason: the same rule as declared().
    frozen = (frozen or "").strip() or None
    if edit and not frozen:
        lits = svg_literals(svg)
        if lits:
            shown = "; ".join(repr(x[:60]) for x in lits[:6])
            more = f" … and {len(lits) - 6} more" if len(lits) > 6 else ""
            raise SvgLiteralError(
                f"graphic '{el_id}': {len(lits)} label(s) drawn inside the SVG as "
                f"plain words, which the editor can never reach: {shown}{more}. "
                f"Draw each with docsync.blocks.svg_text(C, key, default, …) so it "
                f"edits on the page; a category or series name that comes from "
                f"data takes C.derived('<how it is remade>') on its <text>; and a "
                f"drawing whose words ARE the drawing says graphic(…, "
                f"frozen='<why>').")
    klass = ("ds-graphic " + cls).strip()
    sized = L.positions.get(el_id, {}).get("w")
    base = "display:inline-block;vertical-align:middle;line-height:0"
    pre = ""
    if w and not sized:
        base += f";width:{w}in"
        pre = _graphic_mobile_once(L)
    # The declaration rides the wrapper in edit mode so docsync.check can
    # list it; published, the bytes are what they were.
    fz = ""
    if edit and frozen:
        safe = str(frozen).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
        fz = f' data-frozen="{safe}"'
    return (f"{pre}{L.spacer(el_id)}"
            f'<span class="{klass}"{L.attr(el_id, base)}{fz}>{_fit_svg(svg)}</span>')


def svg_text(C, key: str, default: str, x, y, size, fill: str,
             weight=None, anchor: str | None = None, extra: str = "",
             restates: str | None = None) -> str:
    """A label INSIDE a chart that the person can rephrase on the page.

    A chart's legend entry, a step name under a circle, the caption on a bar
    — words, not data marks — used to be literals in the renderer, so the
    graphic moved and resized while its words could only be changed by
    editing Python. This draws the <text> with C.slot_attr(key) on it (the
    editor floats a field over an SVG slot; see editSvgSlot in edit.html) and
    reads the words through C.text_or(key, default): the renderer's own
    wording is the default, so a document written before the slot existed
    renders exactly as it did and publishing does not raise on a label the
    author never listed. Give every label a stable key (`<chart>.<what>`);
    the key is what the person's edit is filed under.

    `size` is in the SVG's own user units, as every other label there — the
    three-units trap in the renderer's own comments applies unchanged.

    `restates` is required of a label that STATES A FIGURE the drawing also
    draws — a bar's value, a share, a total. Making a label editable made a
    new failure possible: retyping "$3.0M" to "$4.1M" leaves the bar at its
    old height, so the figure disagrees with itself and nothing says so. The
    bar cannot follow the words (the geometry is the renderer's), so the
    honest thing is to declare where the number comes from, exactly as
    C.derived does — the string is HOW IT IS REMADE, and the editor shows it
    to whoever edits the label. docsync.check refuses an undeclared quantity
    (blocks.is_quantity) drawn inside a graphic.

    Pass a data-derived label's DEFAULT from the DATA constant so it stays in
    step with the model until someone retypes it.
    """
    v = C.text_or(key, default).replace("&", "&amp;").replace("<", "&lt;")
    wt = f' font-weight="{weight}"' if weight else ""
    an = f' text-anchor="{anchor}"' if anchor else ""
    rs = ""
    if (restates or "").strip() and os.environ.get("DOCSYNC_EDIT"):
        rs = f' data-restates="{_xml_attr(restates)}"'
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}"{wt}{an}'
            f'{extra}{C.slot_attr(key)}{rs}>{v}</text>')


# ---- the words a screen reader hears INSTEAD of the drawing -------------------
# role="img" makes a figure opaque: the reader is told the aria-label and
# NOTHING inside it is announced. So for a blind reader the description IS the
# figure — and it was the one string on the page nobody could edit, written as
# an f-string literal in the renderer beside the drawing. Making the drawing's
# own labels slots made that worse, not better: retype them on the page and
# the spoken version still says what the renderer said, silently.
#
# A description is an ATTRIBUTE, so it carries no contenteditable span and
# cannot be clicked on the page. What it CAN be is a slot like any other —
# read through C.text_or so a room seeded before it existed renders unchanged,
# and hooked with data-desc so docsync.check can tell a description that is
# wired from one that is frozen.


def describe(C, key: str, default: str, role: str = "img") -> str:
    """The accessible description of a figure, as a SLOT.

    Returns the attributes to drop into the tag that carries the drawing — an
    <svg>, or the <div>/<span> of a bar drawn in CSS:

        f'<svg viewBox="0 0 820 250"{describe(C, "chart.claims.desc", "…")}>'

    in place of a literal `role="img" aria-label="…"`. Give every description
    a stable key (`<figure>.desc`); the renderer's own wording is the default,
    so nothing on the page changes until somebody edits it.

    `role=""` omits the role, for a tag that has one already or must not be
    made opaque.
    """
    v = C.text_or(key, default)
    r = f' role="{role}"' if role else ""
    hook = f' data-desc="{_xml_attr(key)}"' if os.environ.get("DOCSYNC_EDIT") else ""
    return f'{r} aria-label="{_xml_attr(v)}"{hook}'


_DESC_ATTR_RE = re.compile(r'\b(aria-label|alt)="([^"]*)"')


def slot_descriptions(C, html: str, prefix: str) -> str:
    """Turn every literal aria-label/alt in a block of STATIC html into a slot.

    For an ingested page whose body is hand-maintained markup rather than
    renderer f-strings (docsync.propose's `body.slotted.html`): there is no
    call site to pass describe() to, and hand-keying dozens of descriptions
    would only move the same literals to a different file. Each is keyed
    `<prefix>.<n>` in document order and read through C.text_or with the
    markup's own wording as the default — so the published bytes are
    unchanged until somebody edits one, and no room needs reseeding for them.

    Document order IS the key, so inserting a described element renumbers
    every description after it. That is the trade the ⟦A:key⟧ markers beside
    them already make, on a page that is a frozen ingestion.
    """
    n = 0

    def one(m):
        nonlocal n
        n += 1
        attr, val = m.group(1), m.group(2)
        key = f"{prefix}.{n}"
        v = C.text_or(key, val)
        hook = (f' data-desc="{_xml_attr(key)}"'
                if os.environ.get("DOCSYNC_EDIT") else "")
        return f'{attr}="{_xml_attr(v)}"{hook}'

    return _DESC_ATTR_RE.sub(one, html)


# --- an ingested page's markers ----------------------------------------------
# docsync.propose writes an imported page's body with markers where the
# editor's hooks go — ⟦A:key⟧ in a start tag (this element is slot `key`),
# ⟦T:key⟧ as its words, ⟦E:id⟧ / ⟦S:id⟧ on a free-standing image (movable, and
# the strut that holds its place), ⟦B:key⟧ on a coloured band. Every renderer
# used to fill them with five re.sub lines of its own, and that is where the
# hole was: a slot got data-slot and nothing else, so every field on an
# imported page could be typed into and not one of them could be moved or
# resized (tfc-2027-priorities, our-mission — every word of both). The markers
# are the engine's grammar, so the engine fills them, and the one rule that
# makes a field MOVABLE lives here where no renderer can leave it out.

_MARK_RE = re.compile("⟦([ATSEB]):([a-z0-9_.-]+)⟧")
_TOKEN_RE = re.compile(
    r"<!--.*?-->"
    r"|<(/?)([A-Za-z][A-Za-z0-9:_-]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>", re.S)
_VOID_EL = frozenset("area base br col embed hr img input link meta param "
                     "source track wbr".split())
_RAW_EL = frozenset(("script", "style", "textarea", "title"))
# The inline pieces one line of a page is built from. A parent made of nothing
# but such pieces (and text-free decoration beside them) is ONE field: a
# stat's number with its label, a legend's swatch with its word, a heading
# the ingest split into runs around a styled word. Moving the number without
# its label, or one run out of the middle of its heading, is never what a
# drag on it means.
_PIECE_EL = frozenset("span a b strong i em small sup sub mark s u q cite abbr "
                      "code time data label button".split())
# Never a unit, whatever it holds: page structure and anything tabular or
# listed, whose children lay themselves out by their parent's rules.
_NO_UNIT_EL = frozenset("html body main header footer section article aside "
                        "nav form table thead tbody tfoot tr td th ul ol dl "
                        "figure details".split())
_NBSP_RE = re.compile("&nbsp;|&#160;|&#xa0;|\u00a0", re.I)


class _El:
    __slots__ = ("tag", "a0", "a1", "attrs", "marks", "parent", "kids",
                 "text", "cls")

    def __init__(self, tag, a0, a1, attrs, parent):
        self.tag, self.a0, self.a1, self.attrs = tag, a0, a1, attrs
        self.parent, self.kids, self.text = parent, [], False
        self.marks = {k: v for k, v in _MARK_RE.findall(attrs or "")}
        m = re.search(r"""\bclass\s*=\s*(?:"([^"]*)"|'([^']*)')""", attrs or "", re.I)
        self.cls = ((m.group(1) or m.group(2) or "") if m else "").split()


def _tree(body: str) -> list[_El]:
    """The body's elements in document order, each knowing its parent, its
    children, its markers and whether it holds words of its own. A marker is
    words only when it is ⟦T⟧ — the others are hooks, not text."""
    root = _El("#root", 0, 0, "", None)
    stack, out, pos = [root], [], 0

    def words(s: str) -> bool:
        s = re.sub("⟦[SEAB]:[a-z0-9_.-]+⟧", "", s)
        return bool(_NBSP_RE.sub("", s).strip())

    while True:
        m = _TOKEN_RE.search(body, pos)
        if not m:
            if words(body[pos:]):
                stack[-1].text = True
            break
        if words(body[pos:m.start()]):
            stack[-1].text = True
        pos = m.end()
        if m.group(0).startswith("<!--"):
            continue
        close, tag, attrs = m.group(1), m.group(2).lower(), m.group(3) or ""
        if close:
            for i in range(len(stack) - 1, 0, -1):
                if stack[i].tag == tag:
                    del stack[i:]
                    break
            continue
        el = _El(tag, m.start(), m.end(), attrs, stack[-1])
        stack[-1].kids.append(el)
        out.append(el)
        if tag in _RAW_EL:
            end = re.compile(rf"</{tag}\s*>", re.I).search(body, pos)
            pos = end.end() if end else len(body)
            continue
        if tag not in _VOID_EL and not attrs.rstrip().endswith("/"):
            stack.append(el)
    return out


def _has_words(el: _El) -> bool:
    return el.text or any(_has_words(k) for k in el.kids)


def _has_marks(el: _El) -> bool:
    return bool(el.marks) or any(_has_marks(k) for k in el.kids)


def _unit(p: _El | None) -> bool:
    """A parent that IS one field: made only of marked inline pieces and
    decoration that carries no words (a swatch, a bar drawn in CSS)."""
    if (p is None or p.tag == "#root" or p.tag in _NO_UNIT_EL or p.marks
            or p.text or "page" in p.cls):
        return False
    pieces = 0
    for k in p.kids:
        if "A" in k.marks and k.tag in _PIECE_EL:
            pieces += 1
        elif _has_words(k) or _has_marks(k):
            return False
    return pieces > 0


def field_plan(body: str) -> list[tuple[str, str, list[str]]]:
    """Which element carries each field's move/resize hook, as data:
    [(field id, the tag that carries it, the slot keys it holds)] in document
    order. What fill_markers stamps, for a test or a check to read."""
    els = _tree(body)
    plan = _plan(els)

    def keys(el: _El) -> list[str]:
        own = [el.marks["A"]] if "A" in el.marks else []
        return own + [k for c in el.kids for k in keys(c)]

    return [(fid, el.tag, keys(el)) for el, fid in plan.items()]


def _plan(els: list[_El]) -> dict:
    """{element: field id}: every ⟦A⟧ slot is its own field or sits inside
    one. The outermost slot of a nest owns it (a stat's "of 30" rides its
    number), and a parent made only of slot pieces owns them all (_unit). A
    slot inside an image's ⟦E⟧ element, or another slot, is inside a field
    already. The id is `field.<key>`, keyed by the first slot the field
    holds — stable for as long as that key is, which is the promise every
    other layout id makes."""
    plan: dict = {}

    def inside(el: _El) -> bool:
        p = el.parent
        while p is not None:
            if "A" in p.marks or "E" in p.marks or p in plan:
                return True
            p = p.parent
        return False

    for el in els:
        if "A" not in el.marks or inside(el):
            continue
        owner = el.parent if _unit(el.parent) else el
        plan.setdefault(owner, f"field.{el.marks['A']}")
    return plan


def fill_markers(C, L, body: str) -> str:
    """An ingested page's body with every docsync.propose marker filled.

    What the renderer's own five re.sub lines did, plus the half they never
    did: every field is MOVABLE and RESIZABLE (field_plan decides which
    element carries the hook). The hook is `L.attr(field id)` — data-el while
    editing, and once moved the position that ships — merged with the slot's
    own attributes and the element's own inline style into ONE style
    attribute. Two style attributes on one tag was a silent loss: the parser
    keeps the first, so a text style on a bar segment with its own width, or
    a move on any element that already had a style, never reached the page.

    No strut is written here: a moved field says what room it leaves
    (L.attr's data-reserve-for) and the anchor runtime puts the strut in
    before it — markup can't, when the field sits inside a <p> (a <div>
    there closes the paragraph) or a table row. Published and untouched, the
    bytes are exactly what the old substitution produced.
    """
    els = _tree(body)
    plan = _plan(els)
    edits: list[tuple[int, int, str]] = []
    for el in els:
        slot_key = el.marks.get("A")
        field = plan.get(el)
        if not (slot_key or field or "E" in el.marks or "B" in el.marks):
            continue
        raw = body[el.a0:el.a1]
        # Where the additions go: at the first marker, as the old substitution
        # put them — or, on an unmarked parent that became a field, just
        # before the tag closes.
        first = _MARK_RE.search(raw)
        head = raw[:first.start()] if first else raw[:-1]
        tail = raw[first.start():] if first else ">"
        tail = _MARK_RE.sub("", tail)
        if not first and head.endswith("/"):
            head, tail = head[:-1], "/>"
        adds: list[str] = []
        if slot_key:
            adds.append(C.slot_attr(slot_key))
        if field:
            adds.append(L.attr(field))
        if "E" in el.marks:
            adds.append(L.attr(el.marks["E"]))
        if "B" in el.marks:
            adds.append(L.sec(el.marks["B"]))
        # The element's own style joins the merge only when something else
        # brings one too; otherwise it stays exactly where the page wrote it.
        bare, own = split_style(head)
        if own and any(split_style(a)[1] for a in adds):
            text = bare + merge_attrs(f' style="{own}"', *adds) + tail
        else:
            text = head + merge_attrs(*adds) + tail
        edits.append((el.a0, el.a1, text))
    out, last = [], 0
    for a0, a1, text in edits:
        out.append(body[last:a0])
        out.append(text)
        last = a1
    out.append(body[last:])
    body = "".join(out)
    body = re.sub("⟦T:([a-z0-9_.-]+)⟧", lambda m: C(m.group(1)), body)
    body = re.sub("⟦S:([a-z0-9_.-]+)⟧", lambda m: L.spacer(m.group(1)), body)
    return body


def _graphic_mobile_once(L) -> str:
    """Release a graphic's inch width on a screen too narrow to hold the sheet.
    Once per document, and from HERE rather than from Layout.mobile_css().

    `.ds-graphic` is this module's own class, and an un-dragged graphic is an
    inline-block with a plain width — not `[data-placed]`, not an absolute
    inline style — so neither of mobile_css()'s two selectors can reach it.
    Nor is mobile_css() even emitted for a report that pinned nothing, which
    is every freshly authored one. A 7.26in figure therefore stayed about
    697px wide inside a 375px page whose overflow is hidden: the right-hand
    third was cut off silently, with nothing to scroll to it.

    width:auto lets the wrapper narrow to the page; chart_scroll()'s own
    --ds-chart-min then keeps the drawing at a readable size inside it and
    scrolls, instead of shrinking the labels under the legibility floor.
    A graphic somebody has already resized carries a position instead, and
    mobile_css releases that one — so this is emitted only for the inch width
    written just above, and a report without one keeps its bytes.
    """
    if getattr(L, "_graphic_mobile_sent", False):
        return ""
    L._graphic_mobile_sent = True
    # @media screen, and the SHEET's own width as the breakpoint — the same
    # condition mobile_css() uses, so the two agree about when a page has
    # stopped being able to show the design at the size it was composed at.
    return (f"<style>@media screen and (max-width:{float(L.page_w):g}in)"
            "{.ds-graphic{width:auto !important;max-width:100% !important}}"
            "</style>")


def chart(L, el_id: str, spec: dict, w: float = 3.6, h: float = 2.4,
          cls: str = "", attrs: str = "", smallest_label: float = 0.0,
          desc: str = "") -> str:
    """A chart drawn INLINE, in the document's flow, that the editor can still
    open in its Chart panel and edit.

    The other kind of chart is a shape: pinned by inch in a page's SVG layer,
    which is right for a designed sheet and wrong for a report whose figures
    sit in the column with the prose. A flow report had no editable chart at
    all before this — which is why the Budget Primer's six figures were each
    written out as frozen SVG by hand, where nobody but the renderer can
    change a label or a number.

    `spec` is what the RENDERER knows: the type, the data it computed, the
    colours it chose. The editor's changes live in layout.json under
    `charts[el_id]` and are laid over it by `Layout.chart_spec()`, exactly as
    `positions[el_id]` lays a drag over a graphic's default width. So a
    rebuild with new numbers keeps the user's styling, and the renderer
    remains the source of the figures.

    Sized in inches like graphic(); `smallest_label` hands chart_scroll the
    smallest type the chart draws, so a phone scrolls it rather than shrinking
    it under the legibility floor.

    `desc` takes a blocks.describe(C, key, default) — the figure's accessible
    description as an editable slot. Pass one for any chart a reader is meant
    to understand; without it the spec's `title` names the figure, and with
    neither the chart is left unnamed rather than named after its id.
    """
    from .layout import chart_svg, MIN_SUBLABEL_IN

    c = L.chart_spec(el_id, spec)
    # Drawn in its own INCH coordinate system, so the geometry is the same one
    # chart_svg would emit for a shape of this size — one set of rules to
    # reason about — and the viewBox does every bit of the scaling. w and h
    # are therefore the design size and the aspect ratio, not the rendered
    # width: by default the figure fills its column the way the primer's
    # hand-built ones always have.
    body = chart_svg(c, 0.0, 0.0, w, h)
    # What a screen reader is given for the figure. `desc` is a slot
    # (blocks.describe), so the words edit on the page like any other.
    # Without one, the chart's own TITLE names it — and with neither, the
    # figure gets no role="img" at all, so its labels are announced instead.
    # It used to fall back to the el_id, which meant a reader who could not
    # see budget-primer's Figure 6 was told "whopays.fig6": an internal
    # identifier, read aloud, as the entire description of a chart.
    if desc:
        name = desc
    elif c.get("title"):
        name = f' role="img" aria-label="{_xml_attr(c["title"])}"'
    else:
        name = ""
    svg = (f'<svg viewBox="0 0 {w:g} {h:g}" class="chart"{name}{attrs}'
           f' style="display:block;width:100%;height:auto">{body}</svg>')
    klass = ("ds-graphic ds-chart " + cls).strip()
    base = "display:block;line-height:0"
    if not L.positions.get(el_id, {}).get("w"):
        base += ";width:100%"
    # The editor finds an inline chart by this hook, the way it finds a chart
    # shape by kind:"chart". Edit mode only, like every other data-* the
    # engine stamps — a published page carries no editing scaffolding.
    # The editor finds an inline chart by data-chart, and reads what it is
    # actually drawing off data-chart-spec. The EFFECTIVE spec has to travel
    # with the markup because only this render knows it: the renderer's half
    # is computed in Python from the project's data, and the browser has no
    # other way to see it. Writes go back the other way, into
    # layout.json's charts[el_id], so the renderer stays the source of the
    # numbers and the override stays only what a person changed.
    hook = ""
    if os.environ.get("DOCSYNC_EDIT"):
        import json as _json
        hook = (f' data-chart="{el_id}"'
                f' data-chart-spec="{_xml_attr(_json.dumps(c, separators=(",", ":")))}"')
    # Phone behaviour comes free: _lfs() floors every label chart_svg draws
    # at MIN_SUBLABEL_IN, so the smallest type in the drawing is known
    # without the caller working it out — and it is already in the viewBox's
    # units, which is what chart_scroll wants. It is the SUB-label floor, not
    # MIN_LABEL_IN: a value or category label derived from the base size
    # bottoms out a point lower, and sizing the scroller off the larger of
    # the two leaves the real smallest label under the floor on a phone.
    #
    # The scroller wraps the svg DIRECTLY, inside the movable span:
    # chart_scroll_css targets `.ds-chart-scroll>svg`, so a wrapper one level
    # further out matches nothing at all, and the chart answers a 375px phone
    # by shrinking its labels to 5.8px instead of scrolling. The span stays
    # the element the editor moves; the div inside it is what scrolls.
    scroller = chart_scroll(svg, smallest_label=smallest_label or MIN_SUBLABEL_IN)
    el = f'<span class="{klass}"{L.attr(el_id, base)}{hook}>{scroller}</span>'
    # An inline chart is invisible to layer()'s survey of the shapes, so if it
    # wants tooltips it has to ask for the runtime itself. Once per document,
    # like every other _once: the second chart on the page adds nothing.
    tips = L.chart_tip_runtime() if c.get("tips") else ""
    return f'{tips}{L.spacer(el_id)}{el}'


def _xml_attr(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def card(C, L, title_key: str, bullets_key: str, bg, light=None,
         icon: str = "", icon_id: str = "", detachable: bool = False,
         min_h: float | None = None, ink: str = "#2F3E46",
         radius: int = 16) -> str:
    """A coloured tile with a bold title and a bullet list — inline-styled, so
    it looks right in a renderer with no stylesheet.

    title_key / bullets_key are content.md slots (the text stays editable
    prose). The tile itself is movable/recolourable under `card.<bullets_key>`.
    detachable=True renders the title and bullets as their OWN movable objects
    (ds-detachable) laid out inside the tile by default — seed a default group
    in layout.json (["card.<key>", "<title_key>", "<bullets_key>"]) so the
    three move as one until the user Ungroups and pulls a piece out. min_h
    keeps the tile a visible panel after its text is dragged elsewhere.
    An icon (inline SVG string) sits left of the title; give icon_id to make
    the glyph its own movable graphic."""
    el_id = f"card.{bullets_key}"
    if L.refilled(el_id):
        bg = L.fill(el_id)
        light = None                       # re-judge contrast on the new colour
    if light is None:
        light = is_light_bg(fill_repr(bg))
    color = ink if light else "#fff"
    override = L.style(el_id, "")
    # position:relative from birth: the tile is the coordinate frame for any
    # movable laid out inside it (a detached title or bullets, an icon
    # graphic), and a frame must exist BEFORE anything is saved against it.
    # When the tile was static its pieces pinned against the page; the first
    # move of the tile then made it their containing block and every saved
    # coordinate re-based against it — the text landed displaced by exactly
    # the tile's offset, clipped or white-on-white: "the words disappeared".
    # The override appends after this, so a moved tile's position:absolute
    # still wins (later declaration takes the property).
    style = (f"background:{fill_css(bg)};color:{color};position:relative;"
             f"border-radius:{radius}px;padding:16px 18px")
    if override:
        style += ";" + override
    if detachable and min_h:
        style += f";min-height:{min_h}in"

    h4_style = "font-size:15px;margin:0 0 8px"
    ico = ""
    if icon:
        ico = (graphic(L, icon_id, icon, w=0.42, cls="card-ico")
               if icon_id else f'<span style="display:inline-block;width:0.42in;'
                               f'vertical-align:middle;line-height:0">{_fit_svg(icon)}</span>')
        h4_style = "display:flex;align-items:center;gap:13px;font-size:20px;line-height:1.13;margin:0 0 12px"

    lis = "".join(f'<li style="font-weight:600;margin:4px 0">{b}</li>'
                  for b in C.list(bullets_key))
    # One style attribute: a text style on the bullets comes through ul_attr,
    # and beside a second style="" the browser would keep only that one.
    ul_style = ' style="margin:0;padding-left:17px"'
    ul = f'<ul{merge_attrs(C.ul_attr(bullets_key), ul_style)}>{lis}</ul>'
    title = C.t(title_key)

    if detachable:
        head = (f'{L.spacer(title_key)}<h4 class="ds-detachable"'
                f'{L.attr(title_key, h4_style)}>{ico}{title}</h4>')
        body = (f'{L.spacer(bullets_key)}<div class="ds-detachable"'
                f'{L.attr(bullets_key)}>{ul}</div>')
    else:
        head = f'<h4 style="{h4_style}">{ico}{title}</h4>'
        body = ul
    tag = L.tag(el_id) + L.fill_tag(el_id)
    return (f'{L.spacer(el_id)}<div{tag} style="{style}">{head}{body}</div>')
