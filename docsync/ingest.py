"""Turn a finished document into an editable project.

    python3 -m docsync.ingest report.pdf --id my-report

A PDF is already what this editor calls a PLACED document: every piece of it has
a position on a fixed sheet, which is exactly the model docsync.new scaffolds
blank and layout.json stores. So conversion is a coordinate translation, not a
new document model — points divided by 72 are the inches the editor thinks in.

What arrives: a project with the document's own page size, one sheet per page,
and every text block as a real EDITABLE text box (drag it, retype it, restyle
it) rather than a picture of one. That is the deliberate trade — see FIDELITY
below for what it costs.

HTML goes elsewhere on purpose: `docsync.scaffold` wraps a web page as a web
page, keeping its own stylesheet and reflowing. A PDF is a print artifact and is
treated as one.

FIDELITY, and why editability wins where they conflict:

- Fonts are NOT carried. The engine loads a known set of web fonts (see FONTS in
  layout.py) and refuses a style naming anything else — which is right: a font
  it cannot load would be faked by the browser. A PDF's fonts are usually
  embedded subsets with mangled names ("ABCDEF+Helvetica"), so text comes across
  at the right SIZE and COLOUR in the report's own family. Expect the words to
  sit differently; that is the visible cost of getting editable text.
- Vector artwork (charts, rules, logos drawn as paths) is not converted here.
  Phase 2. A page whose meaning is in its drawings will arrive with its words
  and without its picture, and says so in a notice.
- One page size per document, because layout.json has one: a document with
  pages of differing sizes takes the FIRST page's size and names the outliers
  in a notice you can dismiss.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# PDF points to inches. The one conversion this module is really about.
PT = 72.0
# Below this a "text block" is a stray glyph, a page number artefact or an
# empty box — not something worth a draggable object in the editor.
MIN_CHARS = 1
MIN_BOX_IN = 0.04
# Two pages count as the same size within this. Generators emit 611.976 for
# letter often enough that an exact comparison would call every page an outlier.
SIZE_TOL_IN = 0.02


class IngestError(Exception):
    pass


def _in(pt: float) -> float:
    """Points to inches, at a precision the editor's own drags produce."""
    return round(pt / PT, 3)


def page_size_in(page) -> tuple[float, float]:
    """The VISIBLE sheet, in inches.

    page.rect — not mediabox: the cropbox is what a reader sees, and PyMuPDF's
    rect already accounts for /Rotate (a 90-rotated letter page reports 11x8.5,
    verified), so nothing here swaps width and height by hand.
    """
    r = page.rect
    return _in(r.width), _in(r.height)


def size_outliers(sizes: list[tuple[float, float]]) -> list[int]:
    """1-based page numbers whose sheet differs from the first page's."""
    if not sizes:
        return []
    w0, h0 = sizes[0]
    return [i + 1 for i, (w, h) in enumerate(sizes)
            if abs(w - w0) > SIZE_TOL_IN or abs(h - h0) > SIZE_TOL_IN]


def _colour(srgb: int) -> str | None:
    """A span's packed sRGB int as #RRGGBB. Black is left unsaid — it is the
    default, and a style that repeats the default is noise in layout.json."""
    if not isinstance(srgb, int) or srgb <= 0:
        return None
    return f"#{srgb & 0xFFFFFF:06X}"


def _dominant_span(block: dict) -> dict:
    """The span whose run of characters is longest in this block — the one that
    decides how the block reads. Averaging sizes across a heading and its
    footnote marker would describe neither."""
    best, best_len = {}, -1
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            n = len(span.get("text", ""))
            if n > best_len:
                best, best_len = span, n
    return best


def _style_for(block: dict) -> dict:
    """size (px) and colour. Never `font`: see FIDELITY in the module docstring
    — layout.py refuses a family it cannot load, so naming the PDF's own font
    would not degrade, it would refuse the whole draft."""
    span = _dominant_span(block)
    st = {}
    size_pt = span.get("size")
    if isinstance(size_pt, (int, float)) and size_pt > 0:
        # CSS px, which is what text_css emits: 96/72 per point.
        st["size"] = round(size_pt * 96 / PT, 1)
    col = _colour(span.get("color"))
    if col and col != "#000000":
        st["color"] = col
    return st


def _md(text: str) -> str:
    """A block's text as markdown that says what the PDF said.

    Whitespace is collapsed: a PDF breaks lines wherever the column ended, and
    keeping those breaks would freeze one layout's line endings into prose that
    is about to be re-flowed in a box of a different width. Markdown's own
    leading markers are escaped so a line starting "1." or "-" stays that line
    rather than silently becoming a list.
    """
    flat = " ".join(text.split())
    return re.sub(r"^([-*+]|\d+[.)])(\s)", r"\\\1\2", flat)


def blocks_to_boxes(page, pageno: int, w_in: float, h_in: float) -> list[dict]:
    """One page's text blocks as editor text boxes.

    Coordinates are the block's own, in inches. Width comes from the block, so
    the words re-wrap inside the space they occupied; height is deliberately
    left off — it is a MIN-height in this schema, and pinning it would clip the
    moment a substituted font runs a line longer.
    """
    out = []
    data = page.get_text("dict")
    for i, block in enumerate(data.get("blocks", [])):
        if block.get("type") != 0:                  # 0 = text, 1 = image
            continue
        text = "".join(s.get("text", "")
                       for ln in block.get("lines", [])
                       for s in ln.get("spans", []))
        if len(text.strip()) < MIN_CHARS:
            continue
        x0, y0, x1, y1 = block.get("bbox", (0, 0, 0, 0))
        x, y = _in(x0), _in(y0)
        w = _in(x1 - x0)
        if w < MIN_BOX_IN or _in(y1 - y0) < MIN_BOX_IN:
            continue
        # A block starting off the sheet is a generator artefact; one that
        # merely runs past the edge keeps its position and gets clamped, since
        # the sheet is overflow:hidden and the editor can drag it back.
        if x >= w_in or y >= h_in or x < -1 or y < -1:
            continue
        box = {"id": f"p{pageno}.t{i + 1}", "page": pageno,
               "x": max(x, 0.0), "y": max(y, 0.0),
               "w": min(w, round(w_in - max(x, 0.0), 3)) or MIN_BOX_IN,
               "md": _md(text)}
        style = _style_for(block)
        if style:
            box["style"] = style
        out.append(box)
    return out


def page_images(page, pageno: int, assets: Path, w_in: float, h_in: float,
                doc) -> list[dict]:
    """Raster images, written into the project's assets and placed as boxes.

    A box whose markdown is just an image is how the editor's own Insert-image
    flow stores one, so an ingested picture is the same kind of object as a
    hand-placed one — draggable, resizable, deletable, no new schema.
    """
    out = []
    for n, info in enumerate(page.get_images(full=True)):
        xref = info[0]
        try:
            rects = page.get_image_rects(xref)
        except Exception:                            # noqa: BLE001
            rects = []
        if not rects:
            continue
        try:
            pix = doc.extract_image(xref)
        except Exception:                            # noqa: BLE001
            continue
        ext = (pix.get("ext") or "png").lower()
        name = f"p{pageno}-img{n + 1}.{ext}"
        assets.mkdir(parents=True, exist_ok=True)
        (assets / name).write_bytes(pix["image"])
        r = rects[0]
        x, y, w = _in(r.x0), _in(r.y0), _in(r.width)
        if x >= w_in or y >= h_in:
            continue
        out.append({"id": f"p{pageno}.img{n + 1}", "page": pageno,
                    "x": max(x, 0.0), "y": max(y, 0.0),
                    "w": min(w, round(w_in - max(x, 0.0), 3)) or MIN_BOX_IN,
                    "md": f"![]({'assets/' + name})"})
    return out


def read_pdf(path: Path) -> dict:
    """Everything conversion needs from a PDF, and nothing about projects yet:
    kept separate so the translation can be tested without writing a project."""
    try:
        import fitz                                  # PyMuPDF
    except ImportError as e:
        raise IngestError(
            "PDF conversion needs PyMuPDF — pip install pymupdf") from e
    try:
        doc = fitz.open(path)
    except Exception as e:                           # noqa: BLE001
        raise IngestError(f"could not open {path.name}: {e}") from e
    if doc.page_count == 0:
        raise IngestError(f"{path.name} has no pages")
    sizes = [page_size_in(p) for p in doc]
    return {"doc": doc, "sizes": sizes, "count": doc.page_count,
            "w": sizes[0][0], "h": sizes[0][1],
            "outliers": size_outliers(sizes),
            "title": (doc.metadata or {}).get("title") or path.stem}


def notices_for(info: dict, vectors: list[int]) -> list[str]:
    """What the editor should say about choices this conversion made."""
    out = []
    if info["outliers"]:
        pages = ", ".join(str(p) for p in info["outliers"][:8])
        more = "" if len(info["outliers"]) <= 8 else f" (+{len(info['outliers']) - 8} more)"
        first = f"{info['w']}×{info['h']}in"
        out.append(
            f"Page size {first} was taken from page 1. Page{'s' if len(info['outliers']) > 1 else ''} "
            f"{pages}{more} {'were' if len(info['outliers']) > 1 else 'was'} a different size and may "
            f"clip — a report has one sheet size. File ▸ Resize changes it.")
    if vectors:
        pages = ", ".join(str(p) for p in vectors[:8])
        out.append(
            f"Drawn artwork (charts, rules, logos) on page{'s' if len(vectors) > 1 else ''} "
            f"{pages} was not converted — the words came across, the drawings did not. "
            f"Add them with the editor's Shape and Chart tools, or Insert image.")
    out.append("Fonts were not carried across: text kept its size and colour but "
               "uses this report's own family, so lines will sit differently. "
               "Select any text to restyle it.")
    return out


def ingest_pdf(path: Path, slug: str, name: str | None = None,
               root: Path = ROOT, max_pages: int = 0) -> tuple[Path, dict]:
    """Convert a PDF into a registered, buildable project."""
    from . import new as newmod

    info = read_pdf(path)
    doc, w_in, h_in = info["doc"], info["w"], info["h"]
    count = info["count"] if not max_pages else min(info["count"], max_pages)

    boxes, vectors = [], []
    assets = root / "projects" / slug / "assets"
    for i in range(count):
        page = doc[i]
        pageno = i + 1
        boxes += blocks_to_boxes(page, pageno, w_in, h_in)
        boxes += page_images(page, pageno, assets, w_in, h_in, doc)
        try:
            if page.get_drawings():
                vectors.append(pageno)
        except Exception:                            # noqa: BLE001
            pass

    layout = {"positions": {}, "shapes": [], "boxes": boxes, "tables": []}
    try:
        proj = newmod.create(slug, name or info["title"], w_in, h_in, root=root,
                             pages=count, notices=notices_for(info, vectors),
                             layout=layout)
    except newmod.NewProjectError as e:
        raise IngestError(str(e)) from e
    return proj, {"pages": count, "boxes": len(boxes),
                  "w": w_in, "h": h_in, "outliers": info["outliers"],
                  "vectors": vectors,
                  "images": sum(1 for b in boxes if b["md"].startswith("!["))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("file", type=Path, help="the document to convert (.pdf)")
    ap.add_argument("--id", required=True, dest="slug")
    ap.add_argument("--name", default=None,
                    help="title (default: the document's own, or its filename)")
    ap.add_argument("--max-pages", type=int, default=0,
                    help="convert only the first N pages")
    a = ap.parse_args(argv)
    if not a.file.is_file():
        print(f"  ingest: no such file: {a.file}", file=sys.stderr)
        return 1
    if a.file.suffix.lower() != ".pdf":
        print(f"  ingest: {a.file.suffix or 'that'} is not converted yet — .pdf is. "
              "An .html page belongs in docsync.scaffold, which keeps it a web page.",
              file=sys.stderr)
        return 1
    try:
        proj, r = ingest_pdf(a.file, a.slug, a.name, max_pages=a.max_pages)
    except IngestError as e:
        print(f"  ingest: {e}", file=sys.stderr)
        return 1
    print(f"  converted {a.file.name} -> {proj.relative_to(ROOT)}")
    print(f"    {r['pages']} page(s) at {r['w']}x{r['h']}in, "
          f"{r['boxes']} editable objects ({r['images']} image(s))")
    if r["outliers"]:
        print(f"    pages of a different size: {r['outliers']} — page 1's size was used")
    if r["vectors"]:
        print(f"    drawn artwork not converted on pages: {r['vectors']}")
    print(f"  build it:  python3 projects/{a.slug}/render_report.py "
          f"&& python3 -m docsync.stage --id {a.slug}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
