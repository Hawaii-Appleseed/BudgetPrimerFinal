#!/usr/bin/env python3
"""Build web/text.html: the primer as a page you can copy words out of.

    python3 tools/build_text_page.py

The designed report is the deliverable; this is its companion for everyone
downstream of it — a colleague cutting social copy, a reviewer checking a
figure, anyone pasting a passage into an email. It is linked from the primer's
own toolbar and published beside it.

Copying out of the PDF is not the same thing, which is the whole reason this
page exists: a chart's values live in SVG geometry, so a bar labelled $4.9B is
a <path> and a <text> that a text layer renders as a scattered pile of numbers
with nothing attached to them. Here every figure comes out as a label/value
row you can read straight onto a slide.

The extraction is engine code (docsync/text.py) and knows nothing about this
report. This file is the primer-specific half: how those blocks are laid out,
and the two conventions only this report uses — captions that open "Figure N."
or "Table N.", and a heading that wore a revenue badge.

Reads web/index.html, so run it after `make render`. `make text` does both,
and `make pub` publishes the result beside the report.
"""
import html as H
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))

from docsync.text import pages_of, parse, variant_default    # noqa: E402

SRC = HERE / "web" / "index.html"
DEST = HERE / "web" / "text.html"

# A bar chart labels each bar name-then-value, after a run of axis ticks.
MONEY = re.compile(r"^\$[\d.,]+\s*[BMK]?$")
# docsync.text writes a heading that wore adjuncts as "1. Title  [badge]".
ADJUNCT = re.compile(r"(?:(\d+)\.\s+)?(.*?)(?:\s\s\[(.+?)\])?", re.S)
# The engine is structural — it cannot know this report's captions from its
# prose, because that IS a class name and nine reports share the engine. Here,
# in the primer's own build, reading the convention off the text is fair.
CAPTION = re.compile(r"^\*\*(?:Figure|Table)\s")
NOTE = re.compile(r"^(?:\*\*)?Note[:.]")


def totals_from_labels(labels):
    """Bar-chart totals: strip the leading run of axis ticks, then pair up.

    Both halves of the guard matter. A chart whose remaining labels do not
    alternate non-money/money is not a bar chart with per-bar totals — the
    obligated-costs series is FY18..FY27 followed by two figures — and
    pairing it would invent rows that are not in the report.
    """
    i = 0
    while i < len(labels) and MONEY.match(labels[i]):
        i += 1
    rest = labels[i:]
    if len(rest) < 4 or len(rest) % 2:
        return []
    if not all(MONEY.match(rest[k]) for k in range(1, len(rest), 2)):
        return []
    if any(MONEY.match(rest[k]) for k in range(0, len(rest), 2)):
        return []
    return [(rest[k], rest[k + 1]) for k in range(0, len(rest), 2)]


def rich(s):
    """The engine's markdown marks back into this page's inline HTML."""
    s = H.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"\[\^([\d\s,]+?)\]", r'<sup class="fn">\1</sup>', s)
    return s.replace("\n", "<br>")


def datarows(pairs):
    out = []
    for lab, val in pairs:
        parts = [p.strip() for p in val.split("\u00b7")] if "\u00b7" in val else [val]
        if len(parts) > 1:
            sub = "".join(f"<span>{H.escape(p)}</span>" for p in parts)
            out.append(f'<div class="drow drow-multi"><dt>{H.escape(lab)}</dt>'
                       f'<dd class="sub">{sub}</dd></div>')
        else:
            out.append(f'<div class="drow"><dt>{H.escape(lab)}</dt>'
                       f"<dd>{H.escape(val)}</dd></div>")
    return "".join(out)


def blocks(bs):
    o = []
    for b in bs:
        t = b["t"]
        if t == "h":
            num, title, tag = ADJUNCT.fullmatch(b["text"]).groups()
            lv = min(b["level"] + 1, 6)
            if num or tag:
                bits = f'<span class="rnum">{H.escape(num)}</span>' if num else ""
                bits += f'<span class="rttl">{rich(title)}</span>'
                bits += f'<span class="rtag">{rich(tag)}</span>' if tag else ""
                o.append(f'<h{lv} class="reform">{bits}</h{lv}>')
            else:
                cls = ' class="h-major"' if b["level"] == 1 else ""
                o.append(f"<h{lv}{cls}>{rich(b['text'])}</h{lv}>")
        elif t == "p":
            txt = b["text"]
            cls = (' class="figcap"' if CAPTION.match(txt)
                   else ' class="note"' if NOTE.match(txt) else "")
            o.append(f"<p{cls}>{rich(txt)}</p>")
        elif t == "list":
            tag = "ol" if b["ordered"] else "ul"
            o.append(f"<{tag}>" + "".join(f"<li>{rich(i)}</li>"
                                          for i in b["items"]) + f"</{tag}>")
        elif t == "table":
            rows = []
            for r in b["rows"]:
                cell = "th" if r["head"] else "td"
                rows.append("<tr>" + "".join(f"<{cell}>{rich(c)}</{cell}>"
                                             for c in r["cells"]) + "</tr>")
            o.append('<div class="scroll"><table>' + "".join(rows) + "</table></div>")
        elif t == "chart":
            tot = totals_from_labels(b["labels"]) if b["data"] else []
            body = ""
            if tot:
                body += ('<p class="dhead">Totals</p><dl class="dl">'
                         + datarows(tot) + "</dl>"
                         '<p class="dhead">By spending category</p><dl class="dl">'
                         + datarows(b["data"]) + "</dl>")
            elif b["data"]:
                body += '<dl class="dl">' + datarows(b["data"]) + "</dl>"
            elif b["labels"]:
                body += ('<p class="labs">'
                         + ' <span class="sep">/</span> '.join(
                             H.escape(x) for x in b["labels"]) + "</p>")
            if body:
                o.append('<div class="data"><p class="eyebrow">Chart data</p>'
                         + body + "</div>")
        elif t == "keys":
            keys = " ".join(f'<span class="key">{rich(i)}</span>' for i in b["items"])
            o.append(f'<p class="legend"><span class="eyebrow">Key</span>{keys}</p>')
        elif t == "step":
            o.append(f'<div class="step"><span class="step-k">{rich(b["label"])}</span>'
                     f'<span class="step-t">{rich(b["text"])}</span></div>')
        elif t == "row":
            o.append('<div class="tocrow">'
                     + "".join(f"<span>{rich(c)}</span>" for c in b["cells"])
                     + "</div>")
        elif t == "img":
            o.append(f'<p class="imgnote">Image \u2014 {H.escape(b["alt"])}</p>')
        elif t == "details":
            o.append(f"<details open><summary>{H.escape(b['label'])}"
                     f'<span class="dnote">shown only in the web primer</span>'
                     "</summary><div class=\"dbody\">"
                     + "".join(blocks(b["blocks"])) + "</div></details>")
    return o


def sections_of(pages, front):
    """Group pages under the report's own top-level headings.

    Derived, not hard-coded: an <h1> is where the primer starts a new part, so
    the parts follow the report even if a page is added or one moves. `front`
    is the set of page numbers that carry no part of their own — the cover and
    the contents, whose own display title would otherwise read as a part.
    """
    out, current = [], ["Front matter", "front-matter", []]
    for i, page in enumerate(pages, 1):
        top = None if i in front else next(
            (b["text"] for b in page if b["t"] == "h" and b["level"] == 1), None)
        if top:
            if current[2]:
                out.append(current)
            flat = re.sub(r"\s+", " ", top).strip()
            slug = re.sub(r"[^a-z0-9]+", "-", flat.lower()).strip("-") or f"part-{i}"
            current = [flat, slug, []]
        current[2].append(i)
    if current[2]:
        out.append(current)
    return out


# The cover and the contents page. Class names, and deliberately so: this file
# is the primer-specific half of the build, and only this report knows which of
# its pages are front matter rather than a part of the argument.
FRONT_CLASSES = {"cover", "toc-page"}


def front_pages(root):
    """Page numbers whose section is front matter rather than a part."""
    sections = [n for n in root.find_all("section") if "page" in n.classes]
    return {i for i, sec in enumerate(sections, 1)
            if sec.classes & FRONT_CLASSES}


def word_count(pages):
    """Every word the page shows, not just the words in its paragraphs."""
    n = 0
    def walk(bs):
        nonlocal n
        for b in bs:
            n += len(b.get("text", "").split()) + len(b.get("label", "").split())
            n += sum(len(x.split()) for x in b.get("items", []))
            n += sum(len(c.split()) for r in b.get("rows", []) for c in r["cells"])
            n += sum(len(lab.split()) + len(val.split()) for lab, val in b.get("data", []))
            n += sum(len(x.split()) for x in b.get("labels", []))
            n += len(b.get("alt", "").split())
            n += sum(len(c.split()) for c in b.get("cells", []))
            walk(b.get("blocks", []))
    walk([b for pg in pages for b in pg])
    return n


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
@ROBOTS@<title>Hawai\u02bbi Budget Primer FY2026\u201327 \u2014 plain text</title>
<meta name="description" content="The full text of the Hawai\u02bbi Budget Primer FY2026\u201327, with every chart's values written out, for quoting and reuse.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow:wght@600;700;800&family=IBM+Plex+Mono:wght@400;500&family=Source+Sans+3:ital,wght@0,400;0,600;1,400&display=swap">
<link rel="stylesheet" href="text.css">
</head>
<body>
<div class="wrap">
<header class="mast">
  <p class="eyebrow"><a class="back" href="index.html">\u2190 Back to the primer</a></p>
  <h1>Hawai\u02bbi Budget Primer FY2026\u201327</h1>
  <p class="lede">The complete text of the primer, laid out page by page, with every
  chart\u2019s values written out \u2014 for quoting, pulling into slides, and reuse.</p>
  <p class="meta"><span><b>Fiscal year</b> FY@FY@</span><span><b>Pages</b> @PAGES@</span><span><b>Words</b> @WORDS@</span></p>
</header>

<div class="howto">
  <ul>
@FYNOTE@    <li><b>Chart data</b> blocks hold the values behind each figure, so a number
    can go straight into a slide or a sentence without reading it off the chart.</li>
    <li><b>Superscript numbers</b> are endnote references; full citations are under
    Endnotes.</li>
    <li><b>Copy page text</b> lifts one page as plain text, chart values included.</li>
  </ul>
</div>

<nav class="nav">
@NAV@
</nav>
@BODY@
</div>
<script src="text.js"></script>
</body>
</html>
"""


def build():
    html = SRC.read_text(encoding="utf-8")
    root = parse(html)
    fy = variant_default(root)
    pages = pages_of(html, fy)
    words = word_count(pages)

    nav, body = [], []
    for title, slug, page_nos in sections_of(pages, front_pages(root)):
        nav.append(f'  <a href="#{slug}">{H.escape(title)}</a>')
        body.append(f'<section class="sect" id="{slug}">')
        body.append(f'<h2 class="sect-h">{H.escape(title)}</h2>')
        for n in page_nos:
            body.append(
                f'<article class="page" data-page="{n}">'
                f'<header class="page-h"><span class="chip">p.{n}</span>'
                f'<button class="copy" type="button">Copy page text</button>'
                f"</header>"
                f'<div class="page-b">{"".join(blocks(pages[n - 1]))}</div>'
                "</article>")
        body.append("</section>")

    # Match the report's own listing status: while it is unlisted, so is this.
    robots = ('<meta name="robots" content="noindex, nofollow">\n'
              if "noindex" in html[:2000] else "")
    fynote = (f"    <li><b>Every dollar figure is the FY{fy} number.</b> The report "
              f"itself has a year toggle; only the current year is reproduced "
              f"here.</li>\n" if fy else "")

    # str.replace, not str.format: the template is HTML and a format call would
    # have to escape every brace in it.
    page = PAGE
    for token, value in (("@ROBOTS@", robots), ("@FY@", H.escape(fy or "\u2014")),
                         ("@PAGES@", str(len(pages))), ("@WORDS@", f"{words:,}"),
                         ("@FYNOTE@", fynote), ("@NAV@", "\n".join(nav)),
                         ("@BODY@", "\n".join(body))):
        page = page.replace(token, value)

    DEST.write_text(page, encoding="utf-8")
    print(f"wrote {DEST} ({len(page):,} bytes, {words:,} words)")


if __name__ == "__main__":
    build()
