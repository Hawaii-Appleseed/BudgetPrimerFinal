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
    level: str = "error"                 # error | warn

    @property
    def is_error(self) -> bool:
        return self.level == "error"

    def __str__(self) -> str:
        mark = "ERROR" if self.is_error else "warn "
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


CHECKS = (check_citations, check_markdown, check_svg_bounds,
          check_text_size)


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
# Both are warnings, never errors: a model-driven chart deliberately freezes
# its numbers (retyping "$52.1M" by hand would make the chart lie), and chrome
# outside the sheet is not content. The point is that the choice shows up in
# the check output instead of surprising the person editing the page.

# Tags that never wrap content and never come back down through handle_endtag.
_VOID = frozenset("area base br col embed hr img input link meta source track "
                  "wbr".split())
_OPAQUE_TAGS = frozenset(("script", "style", "head", "title", "template"))
# Words = runs with a letter or digit; two alnum chars = worth a look.
_ALNUM2 = re.compile(r"[^\W_].*[^\W_]", re.S)
_SENT_END = re.compile(r"[.!?:;]\s*$")


class _Coverage(HTMLParser):
    """Classify every visible text node of an EDIT-MODE build.

    dead: no data-slot/data-el ancestor, outside any SVG.
    frozen_prose: sentence-shaped text inside an <svg>.
    Text outside the sheet (no `page`-classed ancestor) is chrome, not content
    — kept separately so a document with no .page container still gets a
    best-effort pass over everything.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool, bool, bool]] = []  # tag, hook, svg, page
        self.opaque = 0
        self.dead_paged: list[str] = []
        self.dead_all: list[str] = []
        self.saw_page = False
        self._svg_text: list[str] | None = None
        self.frozen_prose: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _VOID:
            return
        a = dict(attrs)
        hook = "data-slot" in a or "data-el" in a
        page = "page" in (a.get("class") or "").split()
        self.saw_page = self.saw_page or page
        self.stack.append((tag, hook, tag == "svg", page))
        if tag in _OPAQUE_TAGS:
            self.opaque += 1
        if tag == "text" and "data-slot" not in a:
            # An svg text element; aggregate its tspans for the prose test.
            # One CARRYING data-slot (C.slot_attr on the <text> tag — the
            # report2027 placeholder pattern) is editable, hence exempt.
            self._svg_text = []

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if tag in _OPAQUE_TAGS:
            self.opaque = max(0, self.opaque - 1)
        if tag == "text" and self._svg_text is not None:
            whole = " ".join(t for t in self._svg_text if t).strip()
            if len(whole.split()) >= 5 and _SENT_END.search(whole):
                self.frozen_prose.append(whole)
            self._svg_text = None

    def handle_data(self, data):
        if self.opaque:
            return
        t = " ".join(data.split())
        if not t or not _ALNUM2.search(t):
            return
        in_svg = any(s for _, _, s, _ in self.stack)
        if in_svg:
            if self._svg_text is not None:
                self._svg_text.append(t)
            return
        if any(h for _, h, _, _ in self.stack):
            return
        self.dead_all.append(t)
        if any(p for _, _, _, p in self.stack):
            self.dead_paged.append(t)


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
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "edit.html"
        env = {**os.environ, "DOCSYNC_EDIT": "1", "DOCSYNC_OUT": str(out)}
        r = subprocess.run([sys.executable, str(ed.render)], env=env, cwd=ROOT,
                           capture_output=True, text=True, timeout=180)
        if r.returncode or not out.exists():
            tail = (r.stderr.strip() or r.stdout.strip()).splitlines()
            return [Problem(
                "editability",
                f"edit-mode build failed ({tail[-1] if tail else 'no output'})"
                " — the draft editor cannot open what this check cannot build",
                "warn")]
        html = out.read_text(encoding="utf-8")

    cov = _Coverage()
    cov.feed(html)
    problems: list[Problem] = []
    dead = cov.dead_paged if cov.saw_page else cov.dead_all
    if dead:
        problems.append(Problem(
            "editability",
            f"{len(dead)} visible text string(s) carry no edit hook — not a "
            f"slot, not movable: {_samples(dead)}. Wire them (C.html / "
            f"C.slot_attr / L.attr) or accept them as chrome knowingly",
            "warn"))
    if cov.frozen_prose:
        problems.append(Problem(
            "editability",
            f"{len(cov.frozen_prose)} sentence(s) drawn inside a graphic, so "
            f"only the renderer can change them: {_samples(cov.frozen_prose)}. "
            f"Captions belong in a slot beside the SVG, not in it",
            "warn"))
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
            if eprobs:
                warned += 1
                print(f"warn {b.id}: edit-mode draft")
                for pr in eprobs:
                    print(f"     {pr}")
            else:
                print(f"  ok {b.id}: edit-mode draft")

    tail = f", {warned} with warnings" if warned else ""
    print(f"\n{checked} file(s) checked, {failed} failing{tail}")
    # Warnings never fail the run: they describe pages that still reach a
    # reader correctly in print, and a check nobody can get to green is a
    # check people learn to ignore.
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
