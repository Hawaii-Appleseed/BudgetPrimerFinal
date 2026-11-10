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
  ## Introduction (or Executive summary, Summary, Overview) as the FIRST
                                section, with no words before it: the
                                summary, under that heading
  ## Section                    a page of its own (the pattern page)
  ### Subheading                a second-level heading in the topic colour
  > a pull quote                the pull-quote pattern
  > [!NOTE] Title               a callout: ONE box in the house panel style,
  > words, `>` between paras    its title a run in the callout head style
  Figure 1. Caption             a figure: caption above, the picture at the
  ![alt](assets/x.png)          column's width (as tall as its file draws),
  Source: …                     source below, grouped; caption and source
                                are optional
  Table 1. Caption              a real table (the Table panel's): header in
  | A | B |  /  |---|--:|       the topic colour, banded, numbers right-
  | … | … |                     aligned; caption and Source: as a figure's
  - bullets                     kept as a list
  [^id] in the prose            a citation; its source is a line
  [^id]: Source text — https…   anywhere (also plain [id]: …), lifted into
                                content.md's [[sources]]; a text that cites
                                gets an Endnotes page after its last section

Pagination is an ESTIMATE. The page is overflow:hidden and nothing here can
measure type; the estimate is deliberately generous (fewer characters per
line than the fonts set, a line of slack at the foot), so a page that is
"full" by this count is a little short of full on the sheet. The editor's
audit() is the measurement, and it runs the moment the document opens.
"""
from __future__ import annotations

import datetime
import math
import re
from dataclasses import dataclass, field

try:
    from .metrics import ADV as _ADV       # the house fonts' measured advances
except ImportError:                        # a checkout from before the table
    _ADV = {}

# --- the text ------------------------------------------------------------------

_SRC_LINE = re.compile(r"^\[\^?([A-Za-z0-9_.-]+)\]:\s*(\S.*?)\s*$")
_META_LINE = re.compile(r"^(Subtitle|Date|Author)\s*:\s*(\S.*?)\s*$", re.I)
_CITE = re.compile(r"\[\^([A-Za-z0-9_.-]+)\]")
# A picture on a line of its own; a pipe table's rows; a callout's first line
# (GitHub's alert syntax, `> [!NOTE] Title`); a figure's or table's caption
# ("Figure 1." / "**Table 2:**" first) and its source note ("Source:").
_IMG_LINE = re.compile(r'^!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)$')
_SEP_CELL = re.compile(r"^:?-{3,}:?$")
_CALLOUT = re.compile(r"^\[!(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION|INFO|CALLOUT)\]\s*(.*)$", re.I)
_CAPTION = re.compile(r"^\**\s*((?:Figure|Table|Chart|Exhibit)\s+\w+[.:])\s*\**\s*", re.I)
_NOTE = re.compile(r"^[*_]*(?:Sources?|Notes?)\s*:", re.I)


@dataclass
class Block:
    kind: str            # para | list | sub | quote | figure | table | callout
    md: str
    # figure: src, alt, caption, source; table: rows, header, align, caption,
    # source; callout: title.
    data: dict = field(default_factory=dict)


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
            for v in (b.data.get("caption", ""), b.data.get("source", ""),
                      b.data.get("title", "")):
                found.update(_CITE.findall(v))
            for row in b.data.get("rows", []):
                for cell in row:
                    found.update(_CITE.findall(cell))
        return found


def _cells(line: str) -> list:
    """One pipe-table row's cells; `\\|` is a pipe inside a cell."""
    inner = line.strip()
    inner = inner[1:] if inner.startswith("|") else inner
    inner = inner[:-1] if inner.endswith("|") and not inner.endswith("\\|") else inner
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", inner)]


def _table_block(raw: list) -> Block:
    rows = [_cells(r) for r in raw]
    header, align = False, None
    if len(rows) > 1 and all(_SEP_CELL.match(c.replace(" ", "")) for c in rows[1]):
        sep = [c.replace(" ", "") for c in rows.pop(1)]
        header = True
        align = ["center" if c.startswith(":") and c.endswith(":") else
                 "right" if c.endswith(":") else "left" if c.startswith(":") else None
                 for c in sep]
    n = max(len(r) for r in rows)
    rows = [r + [""] * (n - len(r)) for r in rows]
    if align is not None:
        align = (align + [None] * n)[:n]
    return Block("table", "\n".join(raw), {"rows": rows, "header": header, "align": align})


def _gather(blocks: list) -> list:
    """A picture or a table takes the "Figure N." / "Table N." paragraph just
    before it as its caption and a "Source:" paragraph just after it as its
    source, so the three are placed (and grouped) as one figure."""
    out: list = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if b.kind in ("image", "table"):
            cap = out.pop() if out and out[-1].kind == "para" \
                and _CAPTION.match(out[-1].md) else None
            src = None
            if i + 1 < len(blocks) and blocks[i + 1].kind == "para" \
                    and _NOTE.match(blocks[i + 1].md):
                src = blocks[i + 1]
                i += 1
            out.append(Block("figure" if b.kind == "image" else "table", b.md,
                             {**b.data, "caption": cap.md if cap else "",
                              "source": src.md if src else ""}))
        else:
            out.append(b)
        i += 1
    return out


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
        mi = _IMG_LINE.match(s)
        if mi:
            flush()
            body.append(Block("image", s, {"alt": mi.group(1).strip(), "src": mi.group(2)}))
            continue
        if s.startswith("|"):
            flush()
            raw = [s]
            while i < len(lines) and lines[i].strip().startswith("|"):
                raw.append(lines[i].strip())
                i += 1
            body.append(_table_block(raw))
            continue
        if s.startswith("> "):
            flush()
            q = [s[2:].strip()]
            while i < len(lines) and lines[i].strip().startswith(">"):
                q.append(lines[i].strip().lstrip(">").strip())
                i += 1
            mc = _CALLOUT.match(q[0])
            if mc:
                # A callout keeps its paragraphs (a bare `>` line between them).
                paras, cur = [], []
                for ln in q[1:]:
                    if ln:
                        cur.append(ln)
                    elif cur:
                        paras.append(" ".join(cur))
                        cur = []
                if cur:
                    paras.append(" ".join(cur))
                body.append(Block("callout", "\n\n".join(paras),
                                  {"title": mc.group(1).strip()}))
            else:
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
    doc.intro = _gather(doc.intro)
    for sec in doc.sections:
        sec.blocks = _gather(sec.blocks)
    return doc


def image_size(data: bytes):
    """(width, height) of a PNG, GIF, JPEG or SVG, from its own header; None
    for anything else. Enough to give a placed picture its height before any
    browser has drawn it."""
    import struct
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return struct.unpack("<HH", data[6:10])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            m = data[i + 1]
            if m in (0xD8, 0x01, 0xFF) or 0xD0 <= m <= 0xD7:
                i += 1 if m == 0xFF else 2
                continue
            if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return (w, h)
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
        return None
    head = data[:4096].decode("utf-8", "ignore")
    if "<svg" in head:
        m = re.search(r'viewBox="\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)', head)
        if m:
            return (float(m.group(1)), float(m.group(2)))
        mw = re.search(r'<svg[^>]*?\swidth="([\d.]+)', head)
        mh = re.search(r'<svg[^>]*?\sheight="([\d.]+)', head)
        if mw and mh:
            return (float(mw.group(1)), float(mh.group(1)))
    return None


# --- the estimate --------------------------------------------------------------

PX_PER_IN = 96.0
# Average glyph advance as a fraction of the em, for the house body face.
# Source Sans sets about 0.47em on running text; 0.54 leaves the slack this
# estimate owes the reader (see the module docstring).
_EM_ADVANCE = 0.54
# Barlow 800 in capitals (the headline style) sets wider: measured ~0.58em,
# held at 0.64 for the same slack.
_EM_ADVANCE_CAPS = 0.64


def _face(style: dict | None, bold: bool = False):
    """The measured advances (docsync/metrics.py) for a style's font, its
    weight (bold raises it to 700 at least) and slant; the nearest weight of
    the family when that exact one was not measured; None for a font the
    table does not have."""
    if not style or not _ADV:
        return None
    fam = str(style.get("font") or "")
    w = int(style.get("weight") or 400)
    w = max(w, 700) if bold else w
    slant = "italic" if style.get("italic") else "normal"
    got = _ADV.get(f"{fam}|{w}|{slant}")
    if got is None:
        near = [k for k in _ADV if k.startswith(fam + "|") and k.endswith("|" + slant)]
        got = _ADV[min(near, key=lambda k: abs(int(k.split("|")[1]) - w))] if near else None
    return got


# A sum of single-glyph advances runs about 1% over what a browser sets
# (kerning tightens real text): the Wealth Tax report's lines fit Chrome's
# 681.6px column at 684px by that sum. Measured against that report's own
# line breaks, every paragraph and headline counts true from 1.005 to 1.02;
# the low end keeps the count leaning toward a line too many (white space),
# never a line too few (an overlap).
_SLACK = 1.005
# Where a browser may end a line with no space: after an em or en dash or a
# hyphen ("…stakes in businesses—" / "minus what they owe").
_BREAK_AFTER = "—–-"


def _measured_lines(text: str, avail_px: float, size_px: float, style: dict,
                    caps: bool):
    """Lines a run of text takes, wrapped word by word against the font's
    real advances (bold runs in the bold face). None when the font is not in
    the table."""
    if _face(style) is None:
        return None
    n = 0
    for para in text.split("\n"):
        words: list = []                  # (width px, width of the spaces before it)
        word, gap, bold, i = 0.0, 0.0, False, 0
        while i < len(para):
            if para.startswith("**", i):
                bold = not bold
                i += 2
                continue
            ch = para[i]
            i += 1
            if ch in "*_`":
                continue
            face = _face(style, bold)
            ch = ch.upper() if caps else ch
            px = face.get(ch, face.get("n", 0.5)) * size_px
            if ch in " \t":
                if word:
                    words.append((word, gap))
                    word, gap = 0.0, 0.0
                gap += px
            else:
                word += px
                if ch in _BREAK_AFTER:            # a line may end after it
                    words.append((word, gap))
                    word, gap = 0.0, 0.0
        if word:
            words.append((word, gap))
        lines, x = 1, 0.0
        for w, before in words:
            if x == 0.0:
                x = w
            elif x + before + w <= avail_px:
                x += before + w
            else:
                lines += 1
                x = w
            while x > avail_px:               # a word longer than the line
                lines += 1
                x -= avail_px
        n += lines
    return n


def lines_for(md: str, w_in: float, size_px: float, indent_chars: int = 0,
              caps: bool = False, style: dict | None = None) -> int:
    """How many lines a markdown block takes at this width and size, counting
    the words only (emphasis marks are not glyphs on the page). With the
    block's `style` and its font measured (docsync/metrics.py), the words are
    wrapped against the real advances; otherwise the old per-character
    estimate stands."""
    plain = _CITE.sub("", md)
    plain = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", plain)     # a link's words
    measured = _measured_lines(
        plain, (w_in * PX_PER_IN - indent_chars * size_px * 0.5) * _SLACK,
        size_px, style, caps) if style else None
    if measured is not None:
        return measured
    adv = _EM_ADVANCE_CAPS if caps else _EM_ADVANCE
    cpl = max(10, int(w_in * PX_PER_IN / (size_px * adv)) - indent_chars)
    plain = re.sub(r"[*_`]+", "", plain)
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
    lines = lines_for(md, w_in, size, indent, caps, style)
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
    # Capitals for the words only: a [^id] token inside the lead-in keeps
    # its id, or the citation stops matching its source.
    up = "".join(p if _CITE.fullmatch(p) else p.upper()
                 for p in re.split(r"(\[\^[A-Za-z0-9_.-]+\])", head))
    lead = f"**{up}**"
    return (lead + (" " + tail if tail else "")) + (sep + rest if sep else "")


def _short(name: str) -> str:
    return name if len(name) <= 28 else name[:27].rstrip() + "…"


# Space between a figure's caption, picture and source, and around a figure,
# table or callout in the column (inches).
GAP = 0.09
AROUND = 0.2
# A picture whose file did not come with the text is placed at this shape
# (height over width) until somebody drops the real one in.
DEFAULT_RATIO = 0.62
_NUMERIC = re.compile(r"^[\s$€£(−-]*[\d][\d,.]*\s*%?\)?\s*"
                      r"(?:billion|million|thousand|[kKMB])?$")


def _caption_md(text: str) -> str:
    """"Figure 1. Words" as the house sets it: the label in bold."""
    m = _CAPTION.match(text)
    return f"**{m.group(1)}** {text[m.end():].strip()}" if m else text


def _asset_src(src: str) -> str:
    """A picture's address as the page resolves it: a bare file name is one
    of the project's assets."""
    return src if "/" in src or ":" in src else f"assets/{src}"


def _right_aligned(rows: list, j: int, header: bool) -> bool:
    body = [r[j] for r in rows[1 if header else 0:] if r[j].strip()]
    return bool(body) and all(_NUMERIC.match(_CITE.sub("", c)) for c in body)


def _col_widths(rows: list, w: float) -> list:
    """Column widths in inches, by how much each column has to say."""
    need = [min(40, max(6, max(len(_CITE.sub("", r[j])) for r in rows)))
            for j in range(len(rows[0]))]
    total = sum(need)
    widths = [round(w * n / total, 2) for n in need]
    widths[-1] = round(w - sum(widths[:-1]), 2)
    return widths


def _table_height(rows: list, widths: list, size_px: float = 15.0) -> float:
    """A table's height at the scaffold's cell type (15px at 1.5, 4px insets),
    each row as tall as its fullest cell."""
    line = size_px * 1.5 / PX_PER_IN
    h = 0.0
    for r in rows:
        n = max(lines_for(c or " ", max(0.4, wd - 0.12), size_px)
                for c, wd in zip(r, widths))
        h += n * line + 8 / PX_PER_IN
    return h


class Composer:
    """Builds pages onto a template layout from a `pattern` (see
    docsync.templates.section_pattern): where the first section page is,
    which placeholder elements it replaces, and the styles to place in."""

    def __init__(self, layout: dict, pattern: dict, title: str,
                 sizes: dict | None = None):
        self.layout = layout
        self.p = pattern
        self.title = title
        self.boxes = layout.setdefault("boxes", [])
        self.shapes = layout.setdefault("shapes", [])
        self.page = pattern["first_page"] - 1
        self.y = 0.0
        self.toc: list = []
        self.n = 0                        # element serial, for unique ids
        self.sizes = sizes or {}          # "assets/x.png" -> (w, h) pixels
        self.pictures: list = []          # every picture src placed
        self.used: set = set()            # house styles to register

    def _group(self, ids: list) -> None:
        if len(ids) > 1:
            self.layout.setdefault("groups", []).append(ids)

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

    def _new_page(self, heading: str | None) -> str | None:
        """Start a page; returns the headline box's id when it has one."""
        self.page += 1
        f = self.p["footer"]
        # {page}, filled after layout: a footer that follows its page when
        # pages are added or reordered, where a typed number went stale.
        self._box("foot", f["y"], FOOTER, f["style"], f["w"], x=f["x"])
        hid = None
        if heading is not None:
            h = self.p["heading"]
            hid = self._box("h", h["y"], heading, h["style"], h["w"])["id"]
            # The pattern page holds a one-line headline; a longer one pushes
            # the hairline and the body down by the lines it adds.
            one = (float(h["style"].get("size", 44))
                   * float(h["style"].get("leading", 1.1)) / PX_PER_IN)
            shift = (lines_for(heading, h["w"], float(h["style"].get("size", 44)),
                               caps=str(h["style"].get("case", "")) == "upper",
                               style=h["style"])
                     - 1) * one
            r = dict(self.p["rule"])
            r["y"] = round(r["y"] + shift, 3)
            self.shapes.append({"id": self._id("rule"), "page": self.page,
                                **r})
            self.y = self.p["body_top"] + shift
        else:
            self.y = self.p["cont_top"]
        return hid

    def _fits(self, h: float) -> bool:
        return self.y + h <= self.p["bottom"] + 1e-6

    def section(self, title: str, blocks: list) -> None:
        hid = self._new_page(title)
        self.toc.append((title, self.page, hid, False))
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
            elif b.kind == "figure":
                self._figure(b)
            elif b.kind == "table":
                self._table(b)
            elif b.kind == "callout":
                self._callout(b)
        flush_run()

    def _stack(self, total: float) -> float:
        """Where a figure, table or callout `total` inches tall starts: here,
        or at the top of a fresh page when it does not fit, never split."""
        if self.y > self.p["cont_top"] + 1e-6:
            if not self._fits(AROUND + total):
                self._new_page(None)
            else:
                self.y += AROUND - 0.12
        return self.y

    def _figure(self, b: Block) -> None:
        """Caption above, the picture at the column's width, source below:
        three boxes grouped, so they move as one figure."""
        w, cs, ss = self.p["w"], self.p["caption"], self.p["source"]
        src = _asset_src(b.data["src"])
        cap = _caption_md(b.data.get("caption", ""))
        note = b.data.get("source", "")
        size = self.sizes.get(src)
        ratio = size[1] / size[0] if size and size[0] else DEFAULT_RATIO
        cap_h = height_in(cap, w, cs) + GAP if cap else 0.0
        src_h = height_in(note, w, ss) + GAP if note else 0.0
        img_w, img_h = w, w * ratio
        room = self.p["bottom"] - self.p["cont_top"] - cap_h - src_h
        if img_h > room:                  # taller than a page: shrink to fit one
            img_w, img_h = room / ratio, room
        y = self._stack(cap_h + img_h + src_h)
        ids = []
        if cap:
            ids.append("text." + self._box("cap", y, cap, cs, w)["id"])
            y += cap_h
        alt = b.data.get("alt") or re.sub(r"\*\*[^*]+\*\*\s*", "", cap).strip()
        pic = self._box("fig", y, f"![{alt}]({src})", {}, round(img_w, 2), h=round(img_h, 2))
        del pic["style"]
        ids.append("text." + pic["id"])
        y += img_h + GAP
        if note:
            ids.append("text." + self._box("src", y, note, ss, w)["id"])
            y += src_h - GAP
        self._group(ids)
        self.pictures.append(src)
        self.y = y + AROUND

    def _table(self, b: Block) -> None:
        """A real table (the Table panel's), its caption above and source
        below, grouped; numbers right-aligned, the header in the topic colour."""
        w, cs, ss = self.p["w"], self.p["caption"], self.p["source"]
        rows, header = b.data["rows"], b.data.get("header", False)
        cols = len(rows[0])
        align = [a or ("right" if _right_aligned(rows, j, header) else "left")
                 for j, a in enumerate(b.data.get("align") or [None] * cols)]
        colw = _col_widths(rows, w)
        cap = _caption_md(b.data.get("caption", ""))
        note = b.data.get("source", "")
        cap_h = height_in(cap, w, cs) + GAP if cap else 0.0
        src_h = height_in(note, w, ss) + GAP if note else 0.0
        tbl_h = _table_height(rows, colw)
        y = self._stack(cap_h + tbl_h + src_h)
        ids = []
        if cap:
            ids.append("text." + self._box("cap", y, cap, cs, w)["id"])
            y += cap_h
        tid = self._id("tbl")
        t = {"id": tid, "page": self.page, "x": self.p["x"], "y": round(y, 3),
             "w": w, "z": 2, "rows": rows, "headerRows": 1 if header else 0,
             "colw": colw, "colAlign": align, **self.p["table"]}
        if header:
            t["header"] = True
        self.layout.setdefault("tables", []).append(t)
        ids.append("table." + tid)
        y += tbl_h + GAP
        if note:
            ids.append("text." + self._box("src", y, note, ss, w)["id"])
            y += src_h - GAP
        self._group(ids)
        self.y = y + AROUND

    def _callout(self, b: Block) -> None:
        """ONE box wearing the house callout panel (fill, padding, the left
        rule in the topic colour), its title a run in the callout head style:
        it grows with its words and edits as one object."""
        w, cp = self.p["w"], self.p["callout"]
        title = b.data.get("title", "")
        md = (f"[{title}]{{.Callout head}}\n\n" if title else "") + b.md
        pad = float(cp["panel"]["pad"])
        inner = w - 2 * pad - 0.06
        h = (height_in(b.md, inner, cp["panel"]["style"])
             + (height_in(title, inner, cp["head"]) + 0.12 if title else 0) + 2 * pad)
        y = self._stack(h)
        box = self._box("callout", y, md, {}, w, use="Callout panel")
        del box["style"]
        self.used.add("callout")
        self.y = y + h + AROUND

    def endnotes(self) -> str:
        """A last page: the engine's endnotes section, headed like a section
        (its own words are the heading), with the house hairline under the
        heading grouped to it. Returns the section's box id."""
        self._new_page(None)
        h, en = self.p["heading"], self.p["endnotes"]
        box = self._box("notes", h["y"], "## [Endnotes]{.Section head}", en["style"],
                        self.p["w"], act="endnotes")
        r = dict(self.p["rule"])
        r["y"] = round(r["y"] - 0.16, 3)      # under an h2, as set on 2026-11-09
        rid = self._id("notesrule")
        self.shapes.append({"id": rid, "page": self.page, **r})
        self._group([rid, "text." + box["id"]])
        self.used.add("endnotes")
        return box["id"]

    def finish(self) -> int:
        """Pages used: the last page number any element landed on."""
        return self.page


# A text whose FIRST section has one of these titles, and no words before it,
# opens with its summary: that section fills the template's summary page
# (its heading and box) instead of a section page of its own.
INTRO_TITLES = ("introduction", "executive summary", "summary", "overview")
# Every footer of a report laid out from text, the template's own included:
# {page} is filled after layout, so it follows its page when pages move.
FOOTER = "{page} · YOUR REPORT"
_TYPED_FOOTER = re.compile(r"\d+ · YOUR REPORT")


def _year(doc: Doc) -> str:
    """The text's year (its Date: line), else this one."""
    m = re.search(r"\b(?:19|20)\d{2}\b", doc.meta.get("date", ""))
    return m.group(0) if m else str(datetime.date.today().year)


def lay_out(layout: dict, pattern: dict, doc: Doc, title: str,
            sizes: dict | None = None) -> dict:
    """Mutate `layout` (a template's dict) so its pages carry `doc`. Returns
    {"pages": n, "toc": [(title, page, heading box id, plain)…], "pictures":
    [every picture src placed]}. `sizes` gives each picture's pixel size by
    its src ("assets/x.png" -> (w, h)), so its box is as tall as it draws.

    The template's placeholder section page is replaced by the first real
    one; the executive summary lands in the template's own summary box when
    it fits there (keeping the callout, pull-quote and chart devices around
    it), and becomes a section page of its own when it does not. Words the
    template holds only for an empty report go: the cover's how-to note, the
    copyright's 20XX, typed page numbers in footers and contents.
    """
    drop = set(pattern["drop"])
    if pattern.get("cover_note"):
        drop.add(pattern["cover_note"])
    layout["boxes"] = [b for b in layout.get("boxes", []) if b["id"] not in drop]
    layout["shapes"] = [s for s in layout.get("shapes", []) if s["id"] not in drop]
    boxes = {b["id"]: b for b in layout["boxes"]}

    c = Composer(layout, pattern, title, sizes)
    intro, sections, intro_title = doc.intro, list(doc.sections), None
    if not intro and sections and sections[0].title.strip().lower() in INTRO_TITLES:
        intro, intro_title = sections[0].blocks, sections[0].title.strip()
        sections = sections[1:]
    placed_intro = False
    ib = pattern.get("intro")
    dev = pattern.get("devices") or {}
    words = [b for b in intro if b.kind in ("para", "list")]
    quotes = [b for b in intro if b.kind == "quote"]
    panels = [b for b in intro if b.kind == "callout"]
    if intro and ib and ib["id"] in boxes and words and len(quotes) <= 1 \
            and len(panels) <= 1 and len(words) + len(quotes) + len(panels) == len(intro):
        box = boxes[ib["id"]]
        md = "\n\n".join(_lead_in(b.md) if i == 0 and b.kind == "para" else b.md
                         for i, b in enumerate(words))
        # The summary page's demo devices hold the text's own pull quote and
        # callout, or go: the box takes the column's width when no panel
        # stands beside it, and runs to the foot when no pull quote is under it.
        keep_q = bool(quotes) and dev.get("quote") in boxes
        keep_c = bool(panels) and dev.get("callout") in boxes
        w = box["w"] if keep_c else pattern["w"]
        bottom = boxes[dev["quote"]]["y"] - 0.2 if keep_q else pattern["bottom"]
        if height_in(md, w, box["style"]) <= bottom - box["y"]:
            box["md"], box["w"] = md, w
            placed_intro = True
            if keep_q:
                boxes[dev["quote"]]["md"] = quotes[0].md
            if keep_c:
                t = panels[0].data.get("title", "")
                boxes[dev["callout"]]["md"] = (f"**{t}**\n\n" if t else "") + panels[0].md
            gone = set(dev.get("chart") or []) | (set() if keep_q else {dev.get("quote")}) \
                | (set() if keep_c else {dev.get("callout")})
            # In place: the composer holds this very list.
            layout["boxes"][:] = [b for b in layout["boxes"] if b["id"] not in gone]
            head = ib.get("heading") if ib.get("heading") in boxes else None
            if head:
                boxes[head]["md"] = intro_title or ib["title"]
            c.toc.append((intro_title or ib["title"], box["page"], head, False))
    if not placed_intro and ib and ib["id"] in boxes:
        # No summary of the text's own, or one too long (or too varied) for
        # the summary box: the summary page's template words are all demo, so
        # the page is cleared and the next section page laid out on it,
        # instead of a report shipping "YOUR FIRST WORDS…" on its page 3.
        pg = boxes[ib["id"]]["page"]
        layout["boxes"][:] = [b for b in layout["boxes"] if b.get("page") != pg]
        layout["shapes"][:] = [s for s in layout["shapes"] if s.get("page") != pg]
        c.page = pg - 1
    if intro and not placed_intro:
        c.section(intro_title or pattern["intro_title"], intro)
    for s in sections:
        c.section(s.title, s.blocks)
    # The citations' own page, after the last section: the engine's endnotes
    # section, so the list is placed and listed in the contents, never the
    # renderer's stand-in after the last sheet.
    if doc.citations() and pattern.get("endnotes"):
        nid = c.endnotes()
        c.toc.append(("Endnotes", c.page, nid, True))
    pages = max(c.finish(), pattern["first_page"] - 1)
    styles = layout.setdefault("textStyles", {}) if c.used else {}
    if "callout" in c.used:
        styles.setdefault("Callout head", dict(pattern["callout"]["head"]))
        layout.setdefault("objectStyles", {}).setdefault(
            "Callout panel", dict(pattern["callout"]["panel"]))
    if "endnotes" in c.used:
        styles.setdefault("Section head", dict(pattern["endnotes"]["head"]))

    toc_id = pattern.get("toc")
    if toc_id and toc_id in boxes and c.toc:
        # Tokens, not typed numbers: {titleof:} is the heading's own words and
        # {pageof:} the page it is on, so the contents follow a renamed
        # heading or a moved page (the engine hooks each box they name). A
        # heading worn as a styled run (the endnotes') keeps its plain title.
        boxes[toc_id]["md"] = "\n\n".join(
            (f"{t} — {{pageof:text.{hid}}}" if plain else
             f"{{titleof:text.{hid}}} — {{pageof:text.{hid}}}") if hid else f"{t} — {p}"
            for t, p, hid, plain in c.toc)
    for b in layout["boxes"]:
        if _TYPED_FOOTER.fullmatch(str(b.get("md", ""))):
            b["md"] = FOOTER
    cid = pattern.get("copyright")
    if cid and cid in boxes:
        boxes[cid]["md"] = boxes[cid]["md"].replace("20XX", _year(doc))
    unsaid = set()
    for key, bid in pattern.get("meta", {}).items():
        v = doc.meta.get(key)
        if bid not in boxes:
            continue
        if v:
            boxes[bid]["md"] = (v.upper() if key == "date" else
                                f"Author: {v}" if key == "author" else v)
        elif key == "date":
            # "MONTH 20XX" on a report's cover: this month, until somebody says.
            boxes[bid]["md"] = datetime.date.today().strftime("%B %Y").upper()
        else:
            # "Author: Your Name", "A subtitle in spaced capitals…": the
            # template's stand-ins, which a text that names none must not keep.
            unsaid.add(bid)
    layout["boxes"][:] = [b for b in layout["boxes"] if b["id"] not in unsaid]
    return {"pages": pages, "toc": c.toc, "pictures": c.pictures}


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
