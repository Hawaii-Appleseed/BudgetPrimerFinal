#!/usr/bin/env python3
"""A rendered report as plain text, for people who need the words, not the page.

    python3 -m docsync.text                    # every binding's committed output
    python3 -m docsync.text --id budget-primer
    python3 -m docsync.text --id budget-primer --stdout
    python3 -m docsync.text --build            # rebuild each binding first

Why this exists: a finished report is a designed artefact — absolutely
positioned text boxes, inline SVG charts, a year toggle that renders both
years into the same file. Everyone downstream of it (a comms colleague cutting
Instagram slides, a reviewer fact-checking a figure, anyone pasting a passage
into an email) wants the words and the numbers, and there was no way to get
them except reading the PDF and retyping.

Copying out of the PDF loses exactly the parts people most want to copy: chart
values live in SVG geometry, so a bar labelled $4.9B is a `<path>` and a `<text>`
that a text layer renders as a scattered pile of numbers with no labels attached.
This walks the DOM instead, so a chart comes out as label/value pairs.

It reads the rendered OUTPUT, not content.md, for two reasons. The prose source
is full of `[[slot]]` markers and `{placeholder}` templates — the dollar figures
are interpolated at render time and simply are not in it. And walking output
means walking docsync.yml: a project is covered the day it is added to the
registry, with no per-project code to remember.

STRUCTURAL, NOT COSMETIC. Every rule below keys off document structure —
headings, lists, tables, `<details>`, `<svg>`, a `<section class="page">` (the
editor's one hard structural requirement, see docsync/scaffold.py) — never off
a project's own class names. Projects style themselves with whatever prefix
they like (`tfc-`, `ha-`, `arg-`, `rxk-`), so a rule that named classes would
work for exactly one report. The one convention it does know is `data-fy`,
below.

VARIANTS. A renderer that offers the reader a choice between two years emits
both, tagging each with `data-fy` and pairing them with a `<select>`. Text has
no toggle, so one variant has to win: the first `<option>` in the picker, which
is the current year. `--fy` picks the other one.
"""
from __future__ import annotations

import argparse
import html as _html
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docsync.registry import ROOT, RegistryError, load_registry   # noqa: E402


# --- a minimal DOM -----------------------------------------------------------
#
# stdlib only, on purpose: this module is part of the engine, and CI installs
# pyyaml and nothing else. HTMLParser gives us events; the rules below need to
# look at an element's children, so we build the tree ourselves.

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr", "path", "circle", "rect",
        "line", "ellipse", "polygon", "polyline", "use", "stop"}

# Elements that flow inside a line of text. Anything else starts a new block.
INLINE = {"span", "b", "i", "em", "strong", "a", "sup", "sub", "small", "code",
          "u", "s", "mark", "abbr", "time", "cite", "q", "var", "select",
          "option", "label", "tspan", "textpath", "br", "img", "wbr"}

# Never contribute text: markup, or chrome that exists only on screen. <title>
# is absent on purpose — inside an <svg> it is the picture's accessible name,
# which a text version wants, and the document's own <title> is unreachable
# from here because <head> is dropped whole.
DROP = {"script", "style", "noscript", "template", "head", "defs",
        "clippath", "lineargradient", "radialgradient", "filter", "mask",
        "symbol", "marker", "metadata"}


class Node:
    __slots__ = ("tag", "attrs", "children", "text", "parent")

    def __init__(self, tag, attrs=None, text=""):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list[Node] = []
        self.text = text
        self.parent: Node | None = None

    # -- convenience the rules read like prose ---------------------------------
    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def get(self, name, default=None):
        return self.attrs.get(name, default)

    @property
    def elements(self) -> list["Node"]:
        return [c for c in self.children if c.tag]

    @property
    def is_inline(self) -> bool:
        return self.tag in INLINE

    def find_all(self, tag=None, attr=None):
        """Depth-first descendants, optionally filtered by tag or attribute."""
        out = []
        for c in self.children:
            if not c.tag:
                continue
            if (tag is None or c.tag == tag) and (attr is None or attr in c.attrs):
                out.append(c)
            out.extend(c.find_all(tag, attr))
        return out

    def all_text(self) -> str:
        if not self.tag:
            return self.text
        if self.tag in DROP:
            return ""
        return "".join(c.all_text() for c in self.children)


class _Tree(HTMLParser):
    """HTMLParser -> Node tree, tolerant of the shapes real renderers emit."""

    # Tags that close an open sibling of the same kind. Renderers here emit
    # well-formed markup, but HTML permits omitting these and a text dump is
    # not worth crashing over.
    AUTOCLOSE = {"p": {"p"}, "li": {"li"}, "td": {"td", "th"},
                 "th": {"td", "th"}, "tr": {"tr"}, "option": {"option"},
                 "dt": {"dt", "dd"}, "dd": {"dt", "dd"}}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self.stack = [self.root]

    def _push(self, node):
        node.parent = self.stack[-1]
        self.stack[-1].children.append(node)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        closes = self.AUTOCLOSE.get(tag)
        if closes:
            for i in range(len(self.stack) - 1, 0, -1):
                if self.stack[i].tag in closes:
                    del self.stack[i:]
                    break
                if self.stack[i].tag in ("table", "ul", "ol", "dl", "section"):
                    break
        node = Node(tag, {k.lower(): (v if v is not None else "") for k, v in attrs})
        self._push(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag.lower(),
                    {k.lower(): (v if v is not None else "") for k, v in attrs})
        self._push(node)

    def handle_endtag(self, tag):
        tag = tag.lower()
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return
        # Stray close tag: ignore it rather than unwinding the document.

    def handle_data(self, data):
        if data:
            self._push(Node("", text=data))


def parse(html: str) -> Node:
    t = _Tree()
    t.feed(html)
    t.close()
    return t.root


# --- variants ----------------------------------------------------------------

def variant_default(root: Node) -> str | None:
    """Which `data-fy` to keep when a report renders more than one year.

    The picker lists the current year first, so its first <option> is the
    answer. Reports that tag variants without offering a picker fall back to
    the first value in document order, which is the same rule by a different
    route.
    """
    tagged = {n.get("data-fy") for n in root.find_all(attr="data-fy")}
    tagged.discard(None)
    if not tagged:
        return None
    for sel in root.find_all("select"):
        for opt in sel.find_all("option"):
            v = opt.get("value")
            if v in tagged:
                return v
    for n in root.find_all(attr="data-fy"):
        return n.get("data-fy")
    return None


# --- inline text -------------------------------------------------------------

_WS = re.compile(r"\s+")
_BR = "\x00"      # a real line break, safe from whitespace collapsing


def inline_text(node: Node, variant: str | None, *, marks: bool = True) -> str:
    """One line of text, with bold/italic and footnote markers kept as markdown."""
    out = []
    for c in node.children:
        if not c.tag:
            out.append(c.text)
            continue
        if c.tag in DROP:
            continue
        if c.tag == "br":
            out.append(_BR)
            continue
        if c.tag == "svg":
            continue                       # charts are blocks, handled elsewhere
        if c.tag == "select":
            # A picker collapses to the variant this dump is about.
            chosen = None
            for opt in c.find_all("option"):
                if opt.get("value") == variant:
                    chosen = opt
                    break
            out.append((chosen or c).all_text().strip() if chosen
                       else _first_option_text(c))
            continue
        inner = inline_text(c, variant, marks=marks).strip()
        if not inner:
            continue
        if marks and c.tag in ("b", "strong"):
            out.append(f"**{inner}**")
        elif marks and c.tag in ("i", "em"):
            out.append(f"*{inner}*")
        elif marks and c.tag == "sup" and _is_marker(inner):
            out.append(f"[^{inner}]")
        else:
            out.append(inner)
    # Newlines inside a text node are source formatting, not content; only a
    # <br> is a line the author asked for.
    joined = _WS.sub(" ", "".join(out))
    return "\n".join(line.strip() for line in joined.split(_BR)).strip()


def _first_option_text(sel: Node) -> str:
    for opt in sel.find_all("option"):
        return opt.all_text().strip()
    return ""


def _is_marker(s: str) -> bool:
    """A footnote marker is digits (possibly several, space separated)."""
    return bool(re.fullmatch(r"[\d\s,]+", s))


# --- charts ------------------------------------------------------------------

_MONEYISH = re.compile(r"^[^:]*?[\d$%]")
_TRAILING_HINT = re.compile(r"\s*[·|]\s*(click|tap|hover)\b[^·|]*$", re.I)


def chart_data(svg: Node) -> tuple[list[tuple[str, str]], list[str]]:
    """A chart's readable content: (label, value) pairs, and its text labels.

    `data-tip` is where the numbers actually live. A renderer that draws a bar
    writes the accessible/hover string onto the shape ("Transportation: $2.7B")
    while the visible <text> carries only the axis ticks and a bare figure. The
    pairs are what a reader wants; the labels are the fallback for charts drawn
    without tips.
    """
    pairs, seen = [], set()
    for el in svg.find_all(attr="data-tip"):
        t = _WS.sub(" ", el.get("data-tip", "")).strip()
        t = _TRAILING_HINT.sub("", t)      # "· click for tracker link"
        if not t or t in seen:
            continue
        seen.add(t)
        m = re.match(r"^(.*?)[:—]\s*([^:—]*[\d$%][^:—]*)$", t)
        pairs.append((m.group(1).strip(" —:"), m.group(2).strip())
                     if m else (t, ""))

    labels, prev = [], None
    for t in svg.find_all("text"):
        s = _WS.sub(" ", t.all_text()).strip()
        if s and s != prev:
            labels.append(s)
            prev = s
    return pairs, labels


# --- blocks ------------------------------------------------------------------
#
# Each rule below is a shape, not a class name. The comment on each says which
# shape it recognises, so a new report either matches one or falls through to
# the generic paragraph rule — never to nothing.

HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


def _is_leaf(node: Node) -> bool:
    """No block-level children: everything inside flows as one line of text."""
    return all(not c.tag or c.is_inline or c.tag in DROP for c in node.children)


def _empty(node: Node) -> bool:
    return not node.all_text().strip()


_DIGITS = re.compile(r"\d+")


def _folio_candidate(node: Node, page_no: str | None) -> str | None:
    """This node's folio signature, or None if it cannot be one.

    A folio is a short leaf at a page's edge that restates the page's own
    number. The signature is that text with the digits blanked, so the same
    running head on page 3 and page 7 produces the same string.
    """
    if not page_no or not _is_leaf(node):
        return None
    sibs = [c for c in node.parent.children if c.tag] if node.parent else []
    if node not in (sibs[:1] + sibs[-1:]):
        return None
    txt = node.all_text().strip()
    if len(txt) > 60 or not re.search(rf"(?<!\d){re.escape(page_no)}(?!\d)", txt):
        return None
    return _DIGITS.sub("#", txt)


def furniture_of(sections: list[Node]) -> frozenset[str]:
    """Folio signatures that recur across pages.

    Only the report's own renderer knows what class its running head wears, so
    there is nothing to match on but behaviour — and a folio's behaviour is
    that it appears on every page, saying the same thing with the number
    changed. Recurrence is what separates it from an ordinary short line that
    happens to hold the page number ("1st reading", "Act 7 passed"), which is
    prose and must survive. A one-page report therefore has no folio to find,
    which is correct: with nothing to repeat, nothing has proven itself
    furniture.
    """
    seen: dict[str, int] = {}
    for i, sec in enumerate(sections, 1):
        page_no = sec.get("data-page") or str(i)
        for child in sec.children:
            if not child.tag:
                continue
            sig = _folio_candidate(child, page_no)
            if sig:
                seen[sig] = seen.get(sig, 0) + 1
    return frozenset(sig for sig, n in seen.items() if n > 1)


def blocks_of(node: Node, variant: str | None, page_no: str | None = None,
              furniture: frozenset[str] = frozenset()) -> list[dict]:
    """Walk one container into an ordered list of text blocks."""
    out: list[dict] = []
    for n in node.children:
        if not n.tag or n.tag in DROP:
            continue
        # A variant this dump is not about (the other fiscal year).
        fy = n.get("data-fy")
        if fy is not None and variant is not None and fy != variant:
            continue

        # -- a chart --------------------------------------------------------
        if n.tag == "svg":
            pairs, labels = chart_data(n)
            if pairs or labels:
                out.append({"t": "chart", "data": pairs, "labels": labels})
            else:
                out.extend(_graphic_blocks(_wrap(n)))
            continue

        # -- a heading ------------------------------------------------------
        if n.tag in HEADINGS:
            t = inline_text(n, variant)
            if t:
                out.append({"t": "h", "level": HEADINGS[n.tag], "text": t})
            continue

        # -- a list ---------------------------------------------------------
        if n.tag in ("ul", "ol"):
            items = [inline_text(li, variant)
                     for li in n.elements if li.tag == "li"]
            items = [i for i in items if i]
            if items:
                out.append({"t": "list", "ordered": n.tag == "ol", "items": items})
            continue

        # -- a table --------------------------------------------------------
        if n.tag == "table":
            rows = []
            for tr in n.find_all("tr"):
                cells = [inline_text(c, variant)
                         for c in tr.elements if c.tag in ("td", "th")]
                if any(cells):
                    rows.append({"head": any(c.tag == "th" for c in tr.elements),
                                 "cells": cells})
            if rows:
                out.append({"t": "table", "rows": rows})
            continue

        # -- something the reader expands on screen --------------------------
        if n.tag == "details":
            label, body = "", []
            for c in n.elements:
                if c.tag == "summary" and not label:
                    label = inline_text(c, variant)
                else:
                    body.extend(blocks_of(_wrap(c), variant, page_no, furniture))
            out.append({"t": "details", "label": label or "Details", "blocks": body})
            continue

        # -- an image, named by its alt text ---------------------------------
        if n.tag == "img":
            alt = (n.get("alt") or "").strip()
            if alt:
                out.append({"t": "img", "alt": alt})
            continue

        if n.tag == "figure":
            out.extend(blocks_of(n, variant, page_no, furniture))
            continue

        if _folio_candidate(n, page_no) in furniture:
            continue

        # -- a heading wearing adjuncts --------------------------------------
        # Shape: one heading plus one or more sibling spans inside a wrapper —
        # a step number before the title, a badge after it. Flattening it keeps
        # the badge (often the figure a reader most wants) attached to its
        # heading instead of orphaned on its own line.
        head = [c for c in n.elements if c.tag in HEADINGS]
        spans = [c for c in n.elements if c.tag == "span" and not _empty(c)]
        if len(head) == 1 and spans and len(head) + len(spans) == len(n.elements):
            title = inline_text(head[0], variant)
            before = [inline_text(s, variant) for s in spans
                      if _precedes(s, head[0])]
            after = [inline_text(s, variant) for s in spans
                     if not _precedes(s, head[0])]
            lead = "".join(f"{b}. " for b in before if b)
            tail = "".join(f"  [{a}]" for a in after if a)
            out.append({"t": "h", "level": HEADINGS[head[0].tag],
                        "text": f"{lead}{title}{tail}".strip()})
            continue

        # -- a chart key ------------------------------------------------------
        # Shape: a wrapper whose children each begin with an empty inline
        # element (the colour swatch) followed by a short label. That is a
        # legend in any report; joining it onto one line keeps it from reading
        # like four more paragraphs.
        kids = n.elements
        if len(kids) >= 2 and all(_swatch_led(k) for k in kids):
            items = [inline_text(k, variant) for k in kids]
            items = [i for i in items if i]
            if items:
                out.append({"t": "keys", "items": items})
                continue

        if _is_leaf(n):
            out.extend(_leaf_blocks(n, variant))
            continue

        out.extend(blocks_of(n, variant, page_no, furniture))
    return out


def _wrap(node: Node) -> Node:
    """A one-child container, so blocks_of() can be pointed at a single node."""
    holder = Node("#holder")
    holder.children = [node]
    return holder


def _precedes(a: Node, b: Node) -> bool:
    sibs = a.parent.children if a.parent else []
    try:
        return sibs.index(a) < sibs.index(b)
    except ValueError:
        return False


def _swatch_led(node: Node) -> bool:
    """Starts with an empty inline element — a colour chip, not a word."""
    if not node.tag or not _is_leaf(node):
        return False
    for c in node.children:
        if not c.tag:
            if c.text.strip():
                return False
            continue
        return c.is_inline and _empty(c) and bool(node.all_text().strip())
    return False


def _graphic_blocks(node: Node) -> list[dict]:
    """Named artwork inside this block: <img alt> and <svg><title>.

    Both are the accessible name of a picture, and a picture with a name is
    worth a line in a text version — a logo, a portrait, an icon lockup. A
    picture without one is decoration and stays silent.
    """
    out = []
    for img in node.find_all("img"):
        alt = (img.get("alt") or "").strip()
        if alt:
            out.append({"t": "img", "alt": alt})
    for svg in node.find_all("svg"):
        pairs, labels = chart_data(svg)
        if pairs or labels:
            continue                       # a chart, not a picture
        for t in svg.find_all("title"):
            name = _WS.sub(" ", t.all_text()).strip()
            if name:
                out.append({"t": "img", "alt": name})
            break
    return out


def _leaf_blocks(node: Node, variant: str | None) -> list[dict]:
    """One block of inline content — but three shapes hide in here."""
    pics = _graphic_blocks(node)
    kids = [c for c in node.children
            if c.tag or c.text.strip()]

    # A swatch followed by a label: drop the empty chip, keep the word.
    if kids and kids[0].tag and _empty(kids[0]) and kids[0].is_inline:
        kids = kids[1:]

    # Shape: several spans and no loose text — a row of cells. A table of
    # contents line ("Budget Basics", "3"), a stat pair, a caption and its
    # figure. Joined, not stacked, because they are one line on the page.
    spans = [c for c in kids if c.tag == "span"]
    loose = "".join(c.text for c in kids if not c.tag).strip()
    if len(spans) > 1 and not loose and len(spans) == len(kids):
        cells = [inline_text(s, variant) for s in spans]
        cells = [c for c in cells if c]
        return pics + ([{"t": "row", "cells": cells}] if cells else [])

    # Shape: one span then loose text — a label and the sentence it labels
    # ("DEC" / "The governor submits the executive budget proposal").
    if (len(kids) > 1 and kids[0].tag == "span" and not _empty(kids[0])
            and not kids[1].tag and kids[1].text.strip()):
        label = inline_text(kids[0], variant)
        rest = Node(node.tag, node.attrs)
        rest.children = kids[1:]
        body = inline_text(rest, variant)
        if label and body:
            return pics + [{"t": "step", "label": label, "text": body}]

    t = inline_text(node, variant)
    return pics + ([{"t": "p", "text": t}] if t else [])


# --- the document ------------------------------------------------------------

def pages_of(html: str, variant: str | None = None) -> list[list[dict]]:
    """Every `<section class="page">`, in order, as lists of blocks.

    A report with no page sections (a fragment, a web-only one-pager) is not an
    error — the whole body is one "page".
    """
    root = parse(html)
    if variant is None:
        variant = variant_default(root)
    sections = [n for n in root.find_all("section") if "page" in n.classes]
    if not sections:
        body = next((n for n in root.find_all("body")), root)
        return [blocks_of(body, variant)]
    # A renderer that numbers its sections says so in data-page; one that does
    # not still numbers its folios from one, so the ordinal is the same answer.
    furniture = furniture_of(sections)
    return [blocks_of(s, variant, s.get("data-page") or str(i), furniture)
            for i, s in enumerate(sections, 1)]


def title_of(html: str) -> str:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title\s*>", html)
    return _WS.sub(" ", _html.unescape(re.sub(r"<[^>]+>", "", m.group(1)))).strip() \
        if m else ""


# --- markdown ----------------------------------------------------------------

def _render(bs: list[dict], out: list[str]) -> None:
    for b in bs:
        t = b["t"]
        if t == "h":
            out += ["#" * b["level"] + " " + b["text"], ""]
        elif t == "p":
            out += [b["text"], ""]
        elif t == "list":
            for i, it in enumerate(b["items"], 1):
                out.append((f"{i}. " if b["ordered"] else "- ") + it)
            out.append("")
        elif t == "table":
            for r in b["rows"]:
                out.append(" | ".join(r["cells"]))
            out.append("")
        elif t == "chart":
            if b["data"]:
                out.append("DATA:")
                for lab, val in b["data"]:
                    out.append(f"  {lab}: {val}" if val else f"  {lab}")
            elif b["labels"]:
                out.append("LABELS: " + " | ".join(b["labels"]))
            out.append("")
        elif t == "keys":
            out += ["KEY: " + " | ".join(b["items"]), ""]
        elif t == "step":
            out += [f'{b["label"]} — {b["text"]}', ""]
        elif t == "row":
            out += ["  —  ".join(b["cells"]), ""]
        elif t == "img":
            out += [f'[image: {b["alt"]}]', ""]
        elif t == "details":
            out += [f'--- [shown on the web version: "{b["label"]}"] ---', ""]
            _render(b["blocks"], out)
            out += ["--- [end] ---", ""]


def to_markdown(pages: list[list[dict]], *, title: str = "",
                variant: str | None = None, source: str = "") -> str:
    head = [f"# {title or 'Report'} — full text", ""]
    head.append("Plain-text version of the rendered report, for pulling copy "
                "into slides, email and other collateral.")
    head.append("")
    if variant:
        head.append(f"- Figures are the **FY{variant}** numbers; where the report "
                    f"offers a year toggle, the other year is omitted here.")
    head += [
        "- `DATA:` lines are the values behind each chart; `KEY:` lines are its keys.",
        "- `[^n]` markers are endnote references — full citations at the end.",
    ]
    if source:
        head.append(f"- Generated by `python3 -m docsync.text` from `{source}`.")
    head.append("")

    body: list[str] = []
    multi = len(pages) > 1
    for i, pg in enumerate(pages, 1):
        if multi:
            body += ["", "=" * 72, f"PAGE {i}", "=" * 72, ""]
        lines: list[str] = []
        _render(pg, lines)
        for line in lines:                       # squeeze blank runs
            if not line.strip() and (not body or not body[-1].strip()):
                continue
            body.append(line)
    return "\n".join(head + body).rstrip() + "\n"


def extract(html: str, *, variant: str | None = None, source: str = "") -> str:
    """Rendered HTML in, markdown out. The whole module in one call."""
    root = parse(html)
    if variant is None:
        variant = variant_default(root)
    return to_markdown(pages_of(html, variant), title=title_of(html),
                       variant=variant, source=source)


# --- walking the registry ----------------------------------------------------

def output_for(binding) -> Path | None:
    """The one HTML file that best represents this binding.

    `outputs` lists every copy a build writes — the working render and the
    published copy under docs/. They are the same bytes; the first is the one
    the renderer owns, so that is the one to read.
    """
    for raw in binding.outputs:
        p = Path(raw)
        p = p if p.is_absolute() else ROOT / p
        if p.suffix.lower() in (".html", ".htm"):
            return p
    if binding.editor is not None and binding.editor.out:
        return binding.editor.out
    return None


def dest_for(binding, out_html: Path) -> Path:
    return out_html.parent / f"{binding.id}-text.md"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python3 -m docsync.text",
        description="Write a plain-text version of each rendered report.")
    ap.add_argument("--id", action="append", dest="ids", metavar="BINDING",
                    help="only this binding (repeatable); default: all")
    ap.add_argument("--fy", metavar="YEAR",
                    help="which year to keep where a report toggles between "
                         "two (default: the one the picker lists first)")
    ap.add_argument("--out", metavar="FILE", type=Path,
                    help="write here instead of beside the rendered report "
                         "(one --id only)")
    ap.add_argument("--stdout", action="store_true", help="print instead of writing")
    ap.add_argument("--build", action="store_true",
                    help="run each binding's build command first")
    args = ap.parse_args(argv)

    try:
        bindings = load_registry()
    except RegistryError as e:
        print(f"docsync.text: {e}", file=sys.stderr)
        return 2

    if args.ids:
        known = {b.id for b in bindings}
        unknown = sorted(set(args.ids) - known)
        if unknown:
            print(f"docsync.text: no such binding: {', '.join(unknown)}\n"
                  f"  known: {', '.join(sorted(known))}", file=sys.stderr)
            return 2
        bindings = [b for b in bindings if b.id in args.ids]

    if args.out and len(bindings) != 1:
        print("docsync.text: --out needs exactly one --id", file=sys.stderr)
        return 2

    written = skipped = 0
    for b in bindings:
        if args.build and b.build:
            r = subprocess.run(b.build, shell=True, cwd=ROOT,
                               capture_output=True, text=True)
            if r.returncode:
                tail = (r.stderr or r.stdout).strip().splitlines()
                print(f"FAIL {b.id}: build failed\n  $ {b.build}"
                      + (f"\n  {tail[-1]}" if tail else ""), file=sys.stderr)
                return 1

        src = output_for(b)
        if src is None or not src.exists():
            where = src.relative_to(ROOT) if src else "no html output"
            print(f"skip {b.id}: {where} not built")
            skipped += 1
            continue

        html = src.read_text(encoding="utf-8")
        try:
            rel = str(src.relative_to(ROOT))
        except ValueError:
            rel = str(src)
        md = extract(html, variant=args.fy, source=rel)

        if args.stdout:
            sys.stdout.write(md)
            written += 1
            continue
        dest = args.out or dest_for(b, src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(md, encoding="utf-8")
        written += 1
        try:
            shown = dest.relative_to(ROOT)
        except ValueError:
            shown = dest
        print(f"  ok {b.id}: {shown}  ({len(md.split())} words)")

    if not args.stdout:
        tail = f", {skipped} skipped" if skipped else ""
        print(f"\n{written} file(s) written{tail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
