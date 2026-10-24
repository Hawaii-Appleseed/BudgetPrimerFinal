"""Lay a written document out on a template's pages — the house style's
section pattern, repeated once per section, with no editor open.

    python3 -m docsync.new --id kupuna --template appleseed-report --text report.md

The report-editor skill's build-out recipe does this one pilot verb at a
time in an open editor: addPage, then a batch of addTextBox/addShape per the
style guide's patterns, then setBoxText for the words. That is the right
tool for a page somebody is looking at. For a document that already exists
as text — a Google Doc export, a draft written in a chat, a report the
appleseed-report skill produced — it is dozens of round trips to land what
this module lands in one call, with the same elements the editor would have
placed: a text box per run of paragraphs, a heading box, a hairline shape, a
footer, all in the template's own styles (read off the template, never
restated here, so the two cannot drift).

What the text can say
---------------------
  # Title                       the report's title (the typed --name wins)
  Subtitle: …  Date: …  Author: …   optional lines right under the title
  paragraphs before the first ## the executive summary
  ## Section                    a page of its own (the pattern page)
  ### Subheading                a second-level heading in the topic colour
  > a pull quote                the pull-quote pattern
  - bullets                     kept as a list
  [^id] in the prose            a citation; its source is a line
  [^id]: Source text — https…   anywhere (also plain [id]: …), lifted into
                                content.md's [[sources]]

Pagination is an ESTIMATE. The page is overflow:hidden and nothing here can
measure type; the estimate is deliberately generous (fewer characters per
line than the fonts set, a line of slack at the foot), so a page that is
"full" by this count is a little short of full on the sheet. The editor's
audit() is the measurement, and it runs the moment the document opens.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# --- the text ------------------------------------------------------------------

_SRC_LINE = re.compile(r"^\[\^?([A-Za-z0-9_.-]+)\]:\s*(\S.*?)\s*$")
_META_LINE = re.compile(r"^(Subtitle|Date|Author)\s*:\s*(\S.*?)\s*$", re.I)
_CITE = re.compile(r"\[\^([A-Za-z0-9_.-]+)\]")


@dataclass
class Block:
    kind: str            # para | list | sub | quote
    md: str


@dataclass
class Section:
    title: str
    blocks: list = field(default_factory=list)


@dataclass
class Doc:
    title: str = ""
    meta: dict = field(default_factory=dict)       # subtitle, date, author
    intro: list = field(default_factory=list)       # Blocks before the first ##
    sections: list = field(default_factory=list)
    sources: dict = field(default_factory=dict)     # id -> "text — url"

    def citations(self) -> set:
        found = set()
        for b in self.intro + [b for s in self.sections for b in s.blocks]:
            found.update(_CITE.findall(b.md))
        return found


def parse(text: str) -> Doc:
    """Markdown in, Doc out. Tolerant: a line that is not a heading, a
    source, a quote or a bullet is prose, and blank lines separate blocks."""
    doc = Doc()
    body: list = doc.intro
    para: list = []
    in_list = False

    def flush():
        nonlocal para, in_list
        if para:
            body.append(Block("list" if in_list else "para", "\n".join(para)))
        para, in_list = [], False

    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    seen_title = False
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        i += 1
        s = line.strip()
        if not s:
            flush()
            continue
        m = _SRC_LINE.match(s)
        if m and ("—" in m.group(2) or "http" in m.group(2) or seen_title or doc.sections):
            flush()
            doc.sources[m.group(1)] = m.group(2)
            continue
        if s.startswith("# ") and not seen_title and not doc.sections and not doc.intro:
            flush()
            doc.title = s[2:].strip()
            seen_title = True
            continue
        if seen_title and not doc.intro and not doc.sections and _META_LINE.match(s) \
                and not para:
            m = _META_LINE.match(s)
            doc.meta[m.group(1).lower()] = m.group(2)
            continue
        if s.startswith("## "):
            flush()
            doc.sections.append(Section(s[3:].strip()))
            body = doc.sections[-1].blocks
            continue
        if s.startswith("### ") or s.startswith("#### "):
            flush()
            body.append(Block("sub", s.lstrip("#").strip()))
            continue
        if s.startswith("# "):
            # A second H1 is a section too — people export Docs that way.
            flush()
            doc.sections.append(Section(s[2:].strip()))
            body = doc.sections[-1].blocks
            continue
        if s in ("---", "***", "___"):
            flush()
            continue
        if s.startswith("> "):
            flush()
            q = [s[2:].strip()]
            while i < len(lines) and lines[i].strip().startswith(">"):
                q.append(lines[i].strip().lstrip(">").strip())
                i += 1
            body.append(Block("quote", " ".join(x for x in q if x)))
            continue
        is_item = bool(re.match(r"^([-*•]|\d+[.)])\s+", s))
        if is_item and not in_list and para:
            flush()
        if is_item:
            in_list = True
            para.append(re.sub(r"^[•]\s+", "- ", s))
            continue
        if in_list and s:
            # a wrapped bullet continues the previous item
            para[-1] += " " + s
            continue
        para.append(s)
    flush()
    return doc


# --- the estimate --------------------------------------------------------------

PX_PER_IN = 96.0
# Average glyph advance as a fraction of the em, for the house body face.
# Source Sans sets about 0.47em on running text; 0.54 leaves the slack this
# estimate owes the reader (see the module docstring).
_EM_ADVANCE = 0.54
# Barlow 800 in capitals (the headline style) sets wider: measured ~0.58em,
# held at 0.64 for the same slack.
_EM_ADVANCE_CAPS = 0.64


def lines_for(md: str, w_in: float, size_px: float, indent_chars: int = 0,
              caps: bool = False) -> int:
    """How many lines a markdown block takes at this width and size, counting
    the words only (emphasis marks are not glyphs on the page)."""
    adv = _EM_ADVANCE_CAPS if caps else _EM_ADVANCE
    cpl = max(10, int(w_in * PX_PER_IN / (size_px * adv)) - indent_chars)
    plain = re.sub(r"[*_`]+", "", md)
    plain = _CITE.sub("", plain)
    n = 0
    for ln in plain.split("\n"):
        n += max(1, math.ceil(len(ln.strip()) / cpl))
    return n


def height_in(md: str, w_in: float, style: dict, kind: str = "para") -> float:
    size = float(style.get("size", 15))
    leading = float(style.get("leading", 1.4))
    caps = str(style.get("case", "")) == "upper"
    # A list indents 40px and carries the browser's 1em above and below.
    indent = 6 if kind == "list" else 0
    lines = lines_for(md, w_in, size, indent, caps)
    paras = max(1, md.count("\n\n") + 1) if kind != "list" else 1
    gap = 0.5 * size / PX_PER_IN          # the renderer's p margin, .5em
    extra = 2 * size / PX_PER_IN if kind == "list" else 0
    return lines * size * leading / PX_PER_IN + (paras - 1) * gap + extra


# --- the layout ----------------------------------------------------------------

def _lead_in(md: str) -> str:
    """The house opening: a section's first words in bold capitals. Left
    alone when the writer already opened with emphasis."""
    first, sep, rest = md.partition("\n\n")
    if first.lstrip().startswith(("**", "- ", "![", "#")):
        return md
    words = first.split(" ")
    n = min(len(words), 6)
    for j, w in enumerate(words[:n]):
        if w.endswith((",", ":", ";")) and j >= 1:
            n = j + 1
            break
    head, tail = " ".join(words[:n]), " ".join(words[n:])
    lead = f"**{head.upper()}**"
    return (lead + (" " + tail if tail else "")) + (sep + rest if sep else "")


def _short(name: str) -> str:
    return name if len(name) <= 28 else name[:27].rstrip() + "…"


class Composer:
    """Builds pages onto a template layout from a `pattern` (see
    docsync.templates.section_pattern): where the first section page is,
    which placeholder elements it replaces, and the styles to place in."""

    def __init__(self, layout: dict, pattern: dict, title: str):
        self.layout = layout
        self.p = pattern
        self.title = title
        self.boxes = layout.setdefault("boxes", [])
        self.shapes = layout.setdefault("shapes", [])
        self.page = pattern["first_page"] - 1
        self.y = 0.0
        self.toc: list = []
        self.n = 0                        # element serial, for unique ids

    def _id(self, what: str) -> str:
        self.n += 1
        return f"txt-{what}-{self.n}"

    def _box(self, what: str, y: float, md: str, style: dict, w: float,
             x: float | None = None, **extra) -> dict:
        b = {"id": self._id(what), "page": self.page,
             "x": self.p["x"] if x is None else x, "y": round(y, 3),
             "w": w, "z": 2, "md": md, "style": dict(style)}
        b.update(extra)
        self.boxes.append(b)
        return b

    def _new_page(self, heading: str | None) -> None:
        self.page += 1
        f = self.p["footer"]
        self._box("foot", f["y"], f"{self.page} · YOUR REPORT", f["style"],
                  f["w"], x=f["x"])
        if heading is not None:
            h = self.p["heading"]
            self._box("h", h["y"], heading, h["style"], h["w"])
            # The pattern page holds a one-line headline; a longer one pushes
            # the hairline and the body down by the lines it adds.
            one = (float(h["style"].get("size", 44))
                   * float(h["style"].get("leading", 1.1)) / PX_PER_IN)
            shift = (lines_for(heading, h["w"], float(h["style"].get("size", 44)),
                               caps=str(h["style"].get("case", "")) == "upper")
                     - 1) * one
            r = dict(self.p["rule"])
            r["y"] = round(r["y"] + shift, 3)
            self.shapes.append({"id": self._id("rule"), "page": self.page,
                                **r})
            self.y = self.p["body_top"] + shift
        else:
            self.y = self.p["cont_top"]

    def _fits(self, h: float) -> bool:
        return self.y + h <= self.p["bottom"] + 1e-6

    def section(self, title: str, blocks: list) -> None:
        self._new_page(title)
        self.toc.append((title, self.page))
        w, bs = self.p["w"], self.p["body"]
        run: list = []
        run_h = 0.0
        first = True

        def flush_run():
            nonlocal run, run_h
            if run:
                self._box("body", self.y, "\n\n".join(run), bs, w)
                self.y += run_h + 0.12
            run, run_h = [], 0.0

        for b in blocks:
            if b.kind in ("para", "list"):
                md = _lead_in(b.md) if first and b.kind == "para" else b.md
                first = False
                h = height_in(md, w, bs, b.kind)
                gap = 0.5 * float(bs.get("size", 15)) / PX_PER_IN
                if not self._fits(run_h + (gap if run else 0) + h):
                    flush_run()
                    if not self._fits(h):
                        self._new_page(None)
                run.append(md)
                run_h += h + (gap if len(run) > 1 else 0)
                continue
            flush_run()
            if b.kind == "sub":
                st = self.p["sub"]
                h = height_in(b.md, w, st) + 0.1
                # a subheading never ends a page: keep a line of body with it
                if not self._fits(h + 0.5):
                    self._new_page(None)
                self._box("sub", self.y, b.md, st, w)
                self.y += h
            elif b.kind == "quote":
                st = self.p["quote"]
                h = height_in(b.md, w, st) + 0.1
                if not self._fits(h):
                    self._new_page(None)
                self._box("quote", self.y + 0.1, b.md, st, w)
                self.y += h + 0.2
        flush_run()

    def finish(self) -> int:
        """Pages used: the last page number any element landed on."""
        return self.page


def lay_out(layout: dict, pattern: dict, doc: Doc, title: str) -> dict:
    """Mutate `layout` (a template's dict) so its pages carry `doc`. Returns
    {"pages": n, "toc": [(title, page)…]}.

    The template's placeholder section page is replaced by the first real
    one; the executive summary lands in the template's own summary box when
    it fits there (keeping the callout, pull-quote and chart devices around
    it), and becomes a section page of its own when it does not.
    """
    drop = set(pattern["drop"])
    layout["boxes"] = [b for b in layout.get("boxes", []) if b["id"] not in drop]
    layout["shapes"] = [s for s in layout.get("shapes", []) if s["id"] not in drop]
    boxes = {b["id"]: b for b in layout["boxes"]}

    c = Composer(layout, pattern, title)
    intro = doc.intro
    placed_intro = False
    ib = pattern.get("intro")
    if intro and ib and ib["id"] in boxes \
            and all(b.kind in ("para", "list") for b in intro):
        box = boxes[ib["id"]]
        md = "\n\n".join(_lead_in(b.md) if i == 0 and b.kind == "para" else b.md
                         for i, b in enumerate(intro))
        if height_in(md, box["w"], box["style"]) <= ib["max_h"]:
            box["md"] = md
            placed_intro = True
            c.toc.append((ib["title"], box["page"]))
    if intro and not placed_intro:
        c.section(pattern["intro_title"], intro)
    for s in doc.sections:
        c.section(s.title, s.blocks)
    pages = max(c.finish(), pattern["first_page"] - 1)

    toc_id = pattern.get("toc")
    if toc_id and toc_id in boxes and c.toc:
        boxes[toc_id]["md"] = "\n\n".join(f"{t} — {p}" for t, p in c.toc)
    for key, bid in pattern.get("meta", {}).items():
        v = doc.meta.get(key)
        if v and bid in boxes:
            boxes[bid]["md"] = (v.upper() if key == "date" else
                                f"Author: {v}" if key == "author" else v)
    return {"pages": pages, "toc": c.toc}


def sources_block(doc: Doc) -> str:
    """content.md [[sources]] lines for what the text declared, normalised
    to the engine's `[id]: text — url` shape."""
    out = []
    for sid, rest in doc.sources.items():
        m = re.match(r"^(.*?)\s*[—-]\s*(https?://\S+)$", rest)
        if m and "—" not in rest:
            rest = f"{m.group(1).strip()} — {m.group(2)}"
        elif re.match(r"^https?://\S+$", rest):
            rest = f"{sid} — {rest}"
        out.append(f"[{sid}]: {rest}")
    return "\n".join(out)
