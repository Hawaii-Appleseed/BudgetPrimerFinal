"""Auto-slot proposal — the mechanical 60-80% of STAGE TWO.

    python3 -m docsync.propose --id my-page

Runs on a project that `docsync.scaffold` already made openable. Walks
original.html, finds every substantial text leaf, and mechanically wires each
one as an editable slot:

- the element keeps its own tag and styling; it just gains the editor's
  data-slot hook (via C.slot_attr — no wrapper span),
- its text moves into content.md under a generated [[key]],
- free-standing <img>/<svg> elements gain movable data-el hooks
  (L.spacer + L.attr) — the only element class that is safe to auto-wire;
  flow text blocks are NOT auto-wired because dragging one absolutises it
  and reflows its siblings.

What it deliberately does NOT do (see STAGE2_AUTOMATION.md): meaningful slot
names (`hero.title` beats `s3.p-2` — rename in content.md + body.slotted.html
together), pruning chrome (nav labels, buttons are skipped by tag but any
other should-not-be-editable text needs a human eye), restructuring repeated
widgets into structured slot groups, citations, and page-specific JS
overrides.

Output:
- projects/<id>/body.slotted.html — the original body with slot/movable
  markers; render_report.py is rewritten to substitute them at build time,
- [[key]] blocks appended to content.md,
- the docsync.yml engine list gains body.slotted.html (Pyodide's virtual
  filesystem only has what is listed there).

A text leaf qualifies when its inner markup is only text plus the inline
tags md_inline can round-trip (<b>/<strong>/<i>/<em>/<a href="http…">).
Anything richer stays frozen and is counted in the report — those are the
pieces a follow-up AI/hand pass wires properly.
"""
from __future__ import annotations

import argparse
import re
import sys
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "docsync.yml"

# Text-bearing tags worth proposing as slots.
_TEXT_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li",
              "blockquote", "figcaption", "div", "span"}
# div/span only qualify with at least this much text — below it they are
# almost always layout scaffolding or icon labels.
_MIN_DIV_SPAN_TEXT = 20
_MIN_TEXT = 3

# Inside these, text is chrome or inert — never propose it.
_SKIP_INSIDE = {"nav", "button", "a", "form", "select", "option", "head",
                "script", "style", "svg", "template", "noscript"}

# Inline tags md_inline can represent, so the slot round-trips faithfully.
_INLINE_OK = {"b", "strong", "i", "em", "a"}

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
         "link", "meta", "param", "source", "track", "wbr"}


class _Walker(HTMLParser):
    """Records candidate elements as absolute offset ranges in the source."""

    def __init__(self, src: str):
        super().__init__(convert_charrefs=False)
        self.src = src
        # (line, col) -> absolute offset
        self._line_off = [0]
        for line in src.splitlines(keepends=True):
            self._line_off.append(self._line_off[-1] + len(line))
        self.stack: list[dict] = []          # open elements
        self.candidates: list[dict] = []     # closed, qualifying text leaves
        self.rejected: list[dict] = []       # text tags with complex children
        self.text_nodes: list[dict] = []     # direct text runs, merged
        self.bands: list[dict] = []          # section/header/footer open tags
        self.images: list[dict] = []         # free-standing img/svg
        self.in_body = False
        self.skip_depth = 0                  # inside a _SKIP_INSIDE subtree

    def _abs(self) -> int:
        line, col = self.getpos()
        return self._line_off[line - 1] + col

    # -- tag handling -------------------------------------------------------

    def handle_starttag(self, tag, attrs):
        start = self._abs()
        raw = self.get_starttag_text() or ""
        if tag == "body":
            self.in_body = True
        # band candidates for the resizable-section pass — whether one really
        # carries a background is decided later against the page's own CSS
        if (tag in ("section", "header", "footer") and self.in_body
                and not self.skip_depth and "style" not in dict(attrs)):
            self.bands.append({"tag_start": start, "tag_end": start + len(raw),
                               "attrs": dict(attrs)})
        if tag in _SKIP_INSIDE:
            self.skip_depth += 1
        if tag in _VOID:
            self._void(tag, attrs, start, raw)
            return
        self.stack.append({
            "tag": tag, "attrs": dict(attrs),
            "tag_start": start,
            "inner_start": start + len(raw),
            "children": 0, "child_tags": set(),
            "skipped": self.skip_depth > 0 or not self.in_body,
        })
        if len(self.stack) > 1:
            parent = self.stack[-2]
            parent["children"] += 1
            parent["child_tags"].add(tag)

    def handle_startendtag(self, tag, attrs):
        self._void(tag, attrs, self._abs(), self.get_starttag_text() or "")

    def _void(self, tag, attrs, start, raw):
        if self.stack:
            self.stack[-1]["children"] += 1
            self.stack[-1]["child_tags"].add(tag)
        if (tag == "img" and self.in_body and self.skip_depth == 0
                and "style" not in dict(attrs)):
            self.images.append({"tag_start": start, "tag_end": start + len(raw),
                                "attrs": dict(attrs),
                                "path": [f["tag"] for f in self.stack],
                                "ctx": self._context()})

    # -- text runs (merged across entity refs, e.g. "a &amp; b") -------------

    def _text(self, length: int):
        if not self.in_body or self.skip_depth or not self.stack:
            return
        start = self._abs()
        parent = self.stack[-1]
        last = self.text_nodes[-1] if self.text_nodes else None
        if last and last["parent"] is parent and last["end"] == start:
            last["end"] = start + length
        else:
            self.text_nodes.append({"start": start, "end": start + length,
                                    "parent": parent})

    def handle_data(self, data):
        self._text(len(data))

    def handle_entityref(self, name):
        self._text(len(name) + 2)

    def handle_charref(self, ref):
        self._text(len(ref) + 3)

    def handle_endtag(self, tag):
        if tag in _SKIP_INSIDE:
            self.skip_depth = max(0, self.skip_depth - 1)
        # pop to the matching open tag (tolerates unclosed inline tags)
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                el = self.stack[i]
                del self.stack[i:]
                el["inner_end"] = self._abs()
                self._consider(el)
                return

    @staticmethod
    def _token(attrs: dict) -> str:
        for key in ("id", "class"):
            v = (attrs.get(key) or "").strip()
            if v:
                token = re.sub(r"[^a-z0-9]+", "-",
                               v.split()[0].lower()).strip("-")
                if token:
                    return token[:24]
        return ""

    def _context(self, own: dict | None = None) -> str:
        """A short kebab name — the element's own id/class if it has one, else
        the nearest ancestor's."""
        if own is not None:
            t = self._token(own)
            if t:
                return t
        for f in reversed(self.stack):
            t = self._token(f["attrs"])
            if t:
                return t
        return "page"

    def _consider(self, el: dict):
        if el["skipped"] or el["tag"] not in _TEXT_TAGS:
            return
        el["ctx"] = self._context(el["attrs"])
        inner = self.src[el["inner_start"]:el["inner_end"]]
        el["inner"] = inner
        # only text + representable inline tags inside — anything richer is
        # rejected whole, and the fragment pass slots its pieces instead
        tags_inside = set(re.findall(r"</?\s*([a-zA-Z0-9]+)", inner))
        if tags_inside - _INLINE_OK:
            self.rejected.append(el)
            return
        text = re.sub(r"<[^>]+>", "", inner)
        text = unescape(re.sub(r"\s+", " ", text)).strip()
        # a span that is a fragment of prose (its parent is itself a text tag)
        # can be short — "economic justice" inside the hero h1; a free-standing
        # div/span below the threshold is layout scaffolding
        parent_tag = self.stack[-1]["tag"] if self.stack else ""
        min_len = _MIN_TEXT
        if el["tag"] == "div" or (el["tag"] == "span"
                                  and parent_tag not in _TEXT_TAGS):
            min_len = _MIN_DIV_SPAN_TEXT
        if len(text) < min_len:
            return
        self.candidates.append(el)


def _to_md(inner: str) -> str:
    """Inner HTML (text + _INLINE_OK tags) -> the markdown md_inline emits back."""
    s = inner
    s = re.sub(r"<\s*(b|strong)[^>]*>(.*?)</\s*\1\s*>", r"**\2**", s, flags=re.S | re.I)
    s = re.sub(r"<\s*(i|em)[^>]*>(.*?)</\s*\1\s*>", r"*\2*", s, flags=re.S | re.I)
    s = re.sub(r'<\s*a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</\s*a\s*>',
               r"[\2](\1)", s, flags=re.S | re.I)
    # anchors md_inline can't round-trip (relative/anchor hrefs): keep the text
    s = re.sub(r"<\s*a[^>]*>(.*?)</\s*a\s*>", r"\1", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return unescape(re.sub(r"\s+", " ", s)).strip()


_RENDERER_TEMPLATE = r'''#!/usr/bin/env python3
"""@TITLE@ renderer — auto-slotted by `python3 -m docsync.propose`.

body.slotted.html is original.html's body with slot/movable markers; every
marker is substituted at build time from content.md ([[key]] blocks) and
layout.json. Rename a slot by changing the key in BOTH files. Richer wiring
(structured widgets, citations, page-specific edit-mode overrides) still
belongs in this file — see the report-editor skill.
"""
from pathlib import Path
import os
import re
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from docsync.content import Content              # noqa: E402
from docsync.layout import Layout                # noqa: E402

_LAYOUT = Path(os.environ.get("DOCSYNC_LAYOUT") or (HERE / "layout.json"))
_CONTENT = Path(os.environ.get("DOCSYNC_CONTENT") or (HERE / "content.md"))
_OUT = Path(os.environ.get("DOCSYNC_OUT") or (HERE / "index.html"))

L = Layout(_LAYOUT, page=(@PAGE_W@, @PAGE_H@))
C = Content(_CONTENT, styles=L)

EDIT = bool(os.environ.get("DOCSYNC_EDIT"))

EDIT_CSS = """
[class*="reveal"], [class*="fade"], [class*="animate"], [data-aos] {
  opacity: 1 !important; transform: none !important;
  visibility: visible !important;
}
""" if EDIT else ""

SRC = (HERE / "original.html").read_text()
_body_m = re.search(r"<body[^>]*>(.*)</body>", SRC, re.S | re.I)
_head_src = SRC[:_body_m.start()] if _body_m else ""
STYLE = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", _head_src, re.S | re.I))

# An imported page's DARK palette is a trap in the editor. The sheet this
# wrapper draws is always white, and a dark theme only recolours the text — so
# its near-white --ink lands on white paper. Measured on the page that showed
# this up: text luminance 179 against a 255 sheet, which is legible nowhere.
#
# Every page that ships a dark block ships a light one too, and that is the
# palette a printed report wants (such pages usually say so themselves, in an
# @media print block). So the dark half is switched off — by making its media
# condition unsatisfiable. An unknown media-feature VALUE invalidates only the
# component it appears in, so a comma-separated list keeps its other arms.
#
# EDIT MODE ONLY, deliberately: the published page is a real web page and keeps
# its dark mode, and these bytes stay identical to before outside the editor.
if EDIT:
    STYLE = re.sub(r"prefers-color-scheme\s*:\s*dark",
                   "prefers-color-scheme:ds-edit-light-only", STYLE, flags=re.I)

BODY = (HERE / "body.slotted.html").read_text()
# marker substitution: A=slot attr, T=slot text, S=movable spacer,
# E=movable attr, B=resizable background band
BODY = re.sub("\u27e6A:([a-z0-9_.-]+)\u27e7", lambda m: C.slot_attr(m.group(1)), BODY)
BODY = re.sub("\u27e6T:([a-z0-9_.-]+)\u27e7", lambda m: C(m.group(1)), BODY)
BODY = re.sub("\u27e6S:([a-z0-9_.-]+)\u27e7", lambda m: L.spacer(m.group(1)), BODY)
BODY = re.sub("\u27e6E:([a-z0-9_.-]+)\u27e7", lambda m: L.attr(m.group(1)), BODY)
BODY = re.sub("\u27e6B:([a-z0-9_.-]+)\u27e7", lambda m: L.sec(m.group(1)), BODY)

# Every sheet, in order: this page, then any blank page added in the editor.
# Going through L.page_order() plus L.pagemeta() is what lets the editor's page
# strip offer "+ Page" and reordering \u2014 it withholds both from a renderer that
# never declared its pages, since an order nothing reads draws nothing.
DESIGNED_PAGES = 1


def sheet(pid):
    """One <section class="page">: this report's markup for the designed page,
    empty for a blank page added in the editor.

    data-page carries the page's IDENTITY, which stops matching its position
    the moment the order can be changed.
    """
    inner = BODY if pid == DESIGNED_PAGES else ""
    return (f'<section class="page" data-page="{pid}"{L.fill_attr(f"page.{pid}")}>'
            f'{inner}'
            # Inside the section: .page is the positioning context every placed
            # element is measured against, so as siblings they sat a box out.
            f'{L.layer(pid)}{L.text_boxes(pid)}{L.tables_html(pid)}'
            f'</section>')


SHEETS = ("".join(sheet(pid) for pid in L.page_order(DESIGNED_PAGES))
          + L.pagemeta(range(1, DESIGNED_PAGES + 1)))

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{C.text("title")}</title>
<style>
  body {{ margin:0; background:#EDF1EE; }}
  .page {{ width:{L.page_w}in; min-height:{L.page_h}in; margin:0 auto;
           background:#fff; position:relative; overflow:hidden; }}
  {STYLE}
  {EDIT_CSS}
</style>
</head>
<body>
{SHEETS}
</body>
</html>
"""

_OUT.write_text(html)
print(f"wrote {_OUT} ({len(html):,} bytes)")
'''


def _renderer(title: str, page_w: float, page_h: float) -> str:
    return (_RENDERER_TEMPLATE
            .replace("@TITLE@", title)
            .replace("@PAGE_W@", str(page_w))
            .replace("@PAGE_H@", str(page_h)))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Auto-propose editable slots for a scaffolded project.")
    ap.add_argument("--id", dest="slug", required=True)
    args = ap.parse_args()

    from docsync.registry import get  # noqa: PLC0415
    b = get(args.slug)
    proj = ROOT / "projects" / args.slug
    orig = proj / "original.html"
    if not orig.is_file():
        print(f"{orig} not found — scaffold first", file=sys.stderr)
        return 1
    if (proj / "body.slotted.html").exists():
        print(f"{proj}/body.slotted.html already exists — proposal already "
              "run; delete it (and its [[slots]] in content.md) to re-run",
              file=sys.stderr)
        return 1

    src = orig.read_text()
    w = _Walker(src)
    w.feed(src)

    body_m = re.search(r"<body[^>]*>(.*)</body>", src, re.S | re.I)
    if not body_m:
        print("no <body> found", file=sys.stderr)
        return 1
    body_lo, body_hi = body_m.start(1), body_m.end(1)

    # ---- pick candidates: document order, no nesting, unique keys ----------
    existing_keys = set(re.findall(r"^\[\[([^\]]+)\]\]", (proj / "content.md").read_text(), re.M))
    taken: list[tuple[int, int]] = []
    counters: dict[str, int] = {}
    slots, edits = [], []   # edits: (lo, hi, replacement)

    for el in sorted(w.candidates, key=lambda e: e["tag_start"]):
        if any(lo <= el["tag_start"] < hi for lo, hi in taken):
            continue
        md = _to_md(el["inner"])
        if not md:
            continue
        base = f'{el["ctx"]}.{el["tag"]}'
        counters[base] = counters.get(base, 0) + 1
        key = f"{base}-{counters[base]}"
        while key in existing_keys:
            counters[base] += 1
            key = f"{base}-{counters[base]}"
        existing_keys.add(key)
        taken.append((el["tag_start"], el["inner_end"]))
        raw_tag = src[el["tag_start"]:el["inner_start"]]
        edits.append((el["inner_start"] - 1, el["inner_start"],
                      f"⟦A:{key}⟧" + raw_tag[-1]))
        edits.append((el["inner_start"], el["inner_end"], f"⟦T:{key}⟧"))
        slots.append((key, md))

    movables = []
    for im in w.images:
        if any(lo <= im["tag_start"] < hi for lo, hi in taken):
            continue
        base = im["ctx"] + ".img"
        counters[base] = counters.get(base, 0) + 1
        key = f"{base}-{counters[base]}"
        edits.append((im["tag_start"], im["tag_start"], f"⟦S:{key}⟧"))
        tag_raw = src[im["tag_start"]:im["tag_end"]]
        close = 2 if tag_raw.endswith("/>") else 1
        edits.append((im["tag_end"] - close, im["tag_end"] - close, f"⟦E:{key}⟧"))
        movables.append(key)

    # ---- fragment pass: text runs inside rejected headings/paragraphs ------
    # A heading rejected for a styled <span> child still holds prose the
    # page's owner will want to edit. Its simple span children were already
    # accepted above (relaxed threshold); here the BARE text runs between
    # them get a generated wrapper span each, so the whole line is editable
    # in pieces without touching the styled markup.
    fragments = 0
    rejected_frames = {id(el): el for el in w.rejected
                       if not any(lo <= el["tag_start"] < hi for lo, hi in taken)}
    for tn in w.text_nodes:
        el = rejected_frames.get(id(tn["parent"]))
        if el is None:
            continue
        if any(lo <= tn["start"] < hi for lo, hi in taken):
            continue
        seg = src[tn["start"]:tn["end"]]
        val = unescape(re.sub(r"\s+", " ", seg)).strip()
        if len(val) < _MIN_TEXT:
            continue
        base = f'{el["ctx"]}.{el["tag"]}-txt'
        counters[base] = counters.get(base, 0) + 1
        key = f"{base}-{counters[base]}"
        while key in existing_keys:
            counters[base] += 1
            key = f"{base}-{counters[base]}"
        existing_keys.add(key)
        # wrap only the non-whitespace extent, so spacing around the run stays
        lo = tn["start"] + (len(seg) - len(seg.lstrip()))
        hi = tn["end"] - (len(seg) - len(seg.rstrip()))
        taken.append((lo, hi))
        edits.append((lo, hi, f"<span⟦A:{key}⟧>⟦T:{key}⟧</span>"))
        slots.append((key, val))
        fragments += 1

    # ---- resizable background bands -----------------------------------------
    # A section whose own CSS class carries a background declaration is a
    # colored band the page's owner may want taller or shorter. It gets
    # L.sec()'s hook: a bottom-edge grip in the editor that drags a
    # min-height override into layout.json — background stays glued, text
    # keeps flowing (see STAGE2_AUTOMATION.md, model B).
    head_css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>",
                                    src[:body_lo], re.S | re.I))
    bands = []
    for bd in w.bands:
        classes = (bd["attrs"].get("class") or "").split()
        tok = next((c for c in classes if re.search(
            r"\." + re.escape(c) + r"[^{}]*\{[^}]*background", head_css)), None)
        if not tok:
            continue
        base = "sec." + re.sub(r"[^a-z0-9-]+", "-", tok.lower()).strip("-")
        counters[base] = counters.get(base, 0) + 1
        key = f"{base}-{counters[base]}" if counters[base] > 1 else base
        raw_tag = src[bd["tag_start"]:bd["tag_end"]]
        close = 2 if raw_tag.endswith("/>") else 1
        edits.append((bd["tag_end"] - close, bd["tag_end"] - close, f"⟦B:{key}⟧"))
        bands.append(key)

    if not slots and not movables:
        print("nothing to propose — no qualifying text leaves found")
        return 1

    # ---- write body.slotted.html -------------------------------------------
    out = src
    for lo, hi, rep in sorted(edits, key=lambda e: e[0], reverse=True):
        out = out[:lo] + rep + out[hi:]
    # re-locate the body in the edited text (markers shifted offsets)
    body2 = re.search(r"<body[^>]*>(.*)</body>", out, re.S | re.I).group(1)
    (proj / "body.slotted.html").write_text(body2)

    # ---- append [[key]] blocks to content.md --------------------------------
    cmd_path = proj / "content.md"
    lines = ["\n<!-- slots below proposed by `python3 -m docsync.propose` — "
             "keys are mechanical (ctx.tag-n); rename in BOTH this file and "
             "body.slotted.html -->\n"]
    for key, md in slots:
        lines.append(f"[[{key}]]\n{md}\n")
    txt = cmd_path.read_text()
    # [[sources]] must stay last-ish is not required, but keep proposals before it
    if "[[sources]]" in txt:
        head, tail = txt.split("[[sources]]", 1)
        cmd_path.write_text(head.rstrip("\n") + "\n" + "\n".join(lines)
                            + "\n[[sources]]" + tail)
    else:
        cmd_path.write_text(txt.rstrip("\n") + "\n" + "\n".join(lines))

    # ---- rewrite render_report.py -------------------------------------------
    title = "this page"
    m = re.search(r"^\[\[title\]\]\n(.+)$", cmd_path.read_text(), re.M)
    if m:
        title = m.group(1).strip()
    pw, ph = b.editor.page
    (proj / "render_report.py").write_text(_renderer(title, pw, ph))

    # ---- docsync.yml: engine list needs body.slotted.html -------------------
    reg = REGISTRY.read_text()
    orig_line = f"- projects/{args.slug}/original.html"
    slotted_line = f"- projects/{args.slug}/body.slotted.html"
    if slotted_line not in reg:
        if orig_line not in reg:
            print(f"WARNING: couldn't find '{orig_line}' in docsync.yml — add "
                  f"'{slotted_line}' to the '{args.slug}' engine list by hand",
                  file=sys.stderr)
        else:
            i = reg.index(orig_line)
            indent = reg[:i].rsplit("\n", 1)[-1]
            reg = reg.replace(indent + orig_line,
                              indent + orig_line + "\n" + indent + slotted_line, 1)
            REGISTRY.write_text(reg)

    print(f"proposed {len(slots)} text slots ({fragments} of them fragments "
          f"of styled headings/paragraphs) + {len(movables)} movable images "
          f"+ {len(bands)} resizable background bands")
    print("next: rebuild + restage —")
    print(f"  python3 projects/{args.slug}/render_report.py && "
          f"python3 -m docsync.stage --id {args.slug}")
    # NOT a courtesy note — read this before calling the project done. Slots
    # and movable images are everything propose can safely wire; anything
    # else in the page (stat cards, poll bars, any other structural "content
    # box") stays completely inert: no selection ring, no floating mini
    # toolbar, nothing. Deciding what should become a movable element is a
    # judgment call (STAGE2_AUTOMATION.md calls it "not worth automating"),
    # not something this script can guess at — a scaffolded+proposed project
    # was opened, clicked on, and found to do nothing on exactly this gap
    # (see the tfc-2027-priorities incident this comment postdates), with
    # nothing anywhere having said that would happen. The editor now carries
    # its own warning for this (checkStage2() in edit.html — fires on every
    # project this script touches, since body.slotted.html is the tell), but
    # that is a net under a human's own inspection, not a reason to skip
    # doing the wiring pass or saying plainly what remains undone.
    print()
    print("STAGE TWO IS NOT DONE. Structural content — cards, bars, anything")
    print("besides prose and free-standing images — is not selectable or")
    print("movable yet. Either do that pass now (docsync/blocks.py's")
    print("graphic()/card(), see the report-editor skill) or tell whoever")
    print("you're doing this for exactly what remains unwired. Do not")
    print("report this project as finished/editable without one of those.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
