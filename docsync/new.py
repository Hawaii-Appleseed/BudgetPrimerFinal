"""Create a blank docsync project on disk — no GitHub, no token, no repo access.

    python3 -m docsync.new --id my-report --name "My report" [--w 8.5 --h 11]

This is the LOCAL twin of start.html's "+ New report" flow, which scaffolds
through the GitHub API and therefore needs a repo and a token before a person
has typed a word. Here the same files land straight on disk: a placed-canvas
renderer (everything on the page is a shape, text box or table in layout.json
— the blank-slate the editor's own tools fill), a content.md holding only the
title and the citation list every project needs, and a binding appended to
docsync.yml so the server picks it up.

Used by serve.py's /__scaffold endpoint; runnable by hand for the same result.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")

# Mirrors start.html's placedTemplate — the renderer an empty canvas needs.
# One page to begin with; everything on it comes from layout.json, which is
# the file the editor writes. Kept as a module-level template so the hosted
# scaffold and this one cannot drift apart silently without a diff showing it.
_RENDERER = '''"""Renderer for a PLACED document — content positioned, not flowing.

Scaffolded blank by the draft editor. Everything on the page is a shape, a
text box or a table in layout.json, which is what the editor writes;
content.md holds only the title and the citation list every project needs.

Page size ({w}in x {h}in) is fixed at creation time.
"""
from pathlib import Path
import os
import sys

HERE = Path(__file__).resolve().parent      # .../projects/<slug>
ROOT = HERE                                  # content.md and layout.json live here
REPO = HERE.parents[1]                       # the checkout, where docsync/ lives
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from docsync.content import Content
from docsync.layout import Layout

_LAYOUT = Path(os.environ.get("DOCSYNC_LAYOUT") or (ROOT / "layout.json"))
_CONTENT = Path(os.environ.get("DOCSYNC_CONTENT") or (ROOT / "content.md"))
_OUT = Path(os.environ.get("DOCSYNC_OUT") or (ROOT / "web" / "index.html"))

L = Layout(_LAYOUT, page=({w}, {h}))
C = Content(_CONTENT, styles=L)

# Page 1 always exists; pages added in the editor land in layout.pages and
# come back through the same helper every renderer uses. A converted document
# starts with the page count it had.
DESIGNED_PAGES = {pages}
PAGES = L.page_order(DESIGNED_PAGES)

# What conversion had to decide for you, said in the editor rather than in
# terminal output you will never see again. Empty for a blank project.
NOTICES = {notes}

body = "".join(
    f'<section class="page" data-page="{{pid}}"{{L.fill_attr(f"page.{{pid}}")}}>'
    f'{{L.layer(pid)}}{{L.text_boxes(pid)}}{{L.tables_html(pid)}}'
    f'</section>'
    for pid in PAGES
)
# Tell the editor's page strip which pages are designed, so it can draw a
# thumbnail per page and reorder them. Without this the strip stays hidden.
body += L.pagemeta(range(1, DESIGNED_PAGES + 1))
body += L.notices(NOTICES)
body = C.fn.resolve(body)

notes = C.fn.endnotes()
endnotes = "".join(
    f'<li id="en{{i + 1}}">{{txt}} <a href="{{url}}">{{url}}</a></li>'
    for i, (txt, url) in enumerate(notes)
)

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{C.text("title")}}</title>
<style>
  body {{{{ margin:0; background:#D6E0D2; font:15px/1.5 system-ui, sans-serif;
         color:#2F3E46; }}}}
  /* position:relative is load-bearing: every placed object is absolute
     against its page, so the page must be the containing block or the whole
     document stacks at the window's origin. */
  .page {{{{ width:{w}in; min-height:{h}in; margin:24px auto; background:#fff;
          box-shadow:0 4px 18px rgba(0,0,0,.12); position:relative;
          overflow:hidden; box-sizing:border-box; }}}}
  .ds-textbox p {{{{ margin:0 0 .5em; }}}}
  .ds-textbox p:last-child {{{{ margin-bottom:0; }}}}
  .ds-table {{{{ border-collapse:collapse; }}}}
  .ds-table td, .ds-table th {{{{ border:1px solid #C9D6CD; padding:4px 7px;
          text-align:left; }}}}
  .endnotes {{{{ font-size:13px; color:#52796F; }}}}
  @media print {{{{
    @page {{{{ size: {w}in {h}in; margin: 0; }}}}
    body {{{{ background:#fff; }}}}
    .page {{{{ box-shadow:none; margin:0; width:{w}in; height:{h}in; }}}}
  }}}}
</style>
</head>
<body>
{{body}}
{{f'<ol class="endnotes">{{endnotes}}</ol>' if endnotes else ''}}
</body>
</html>
"""

_OUT.parent.mkdir(parents=True, exist_ok=True)
_OUT.write_text(html)
'''

_CONTENT_MD = """<!--
  This report is a blank canvas: use the editor's Text / Shape / Table tools
  to put things on the page. This file holds only what every project needs —
  the title, and the citation list "Cite" writes into.
-->

[[title]]
{name}

[[sources]]
[example]: Replace or delete this placeholder source — https://example.com
"""

_BINDING = """
  # Added by "+ New report" (docsync/new.py) — a blank local project.
  - id: {slug}
    content: projects/{slug}/content.md
    build: python3 projects/{slug}/render_report.py && python3 -m docsync.stage --id {slug}
    outputs:
      - projects/{slug}/web/index.html
    editor:
      dir: docs/{slug}
      render: projects/{slug}/render_report.py
      out: projects/{slug}/web/index.html
      layout: projects/{slug}/layout.json
      palette: ["#6B9E78", "#95B7A2", "#CAD2C5", "#E8EDE6", "#D6E0D2", "#52796F", "#354F52", "#2F3E46", "#FFFFFF"]
      page: [{w}, {h}]
"""


class NewProjectError(Exception):
    pass


def create(slug: str, name: str, w: float = 8.5, h: float = 11.0,
           root: Path = ROOT, pages: int = 1, notices=None,
           layout: dict | None = None) -> Path:
    """Write the project and register it in docsync.yml. Returns its dir.

    Refuses rather than overwrites: an existing binding or directory means
    the slug is taken, and "create" must never be a way to lose work.

    `pages`, `notices` and `layout` are what CONVERSION adds (docsync.ingest):
    a document arrives with a page count, with things the conversion had to
    decide that the editor should say out loud, and with its content already
    placed. They default to the blank-canvas project this has always made, so
    the hosted "+ New report" path calls this exactly as before — and there is
    still ONE renderer template, which is the point of it living here.
    """
    if not SLUG_RE.match(slug):
        raise NewProjectError(
            f"'{slug}' is not a usable id — lowercase letters, digits and "
            "hyphens, starting with a letter or digit")
    if not name.strip():
        raise NewProjectError("the report needs a title")
    try:
        w, h = float(w), float(h)
    except (TypeError, ValueError) as e:
        raise NewProjectError("page size must be numbers, in inches") from e
    if not (3 <= w <= 30 and 3 <= h <= 40):
        raise NewProjectError(f"page size {w}x{h}in is outside anything printable")

    yml = root / "docsync.yml"
    if not yml.is_file():
        raise NewProjectError(f"no docsync.yml at {root} — is this a docsync checkout?")
    # The registry itself is the authority on taken ids — read it properly
    # rather than grepping, so a commented-out binding does not block a slug.
    # Spec-loaded from the GIVEN root (the same trick serve.py's
    # _load_bindings uses): `import docsync.registry` would answer for
    # whichever checkout happens to be in sys.modules already, which is not
    # necessarily the one being written to.
    import importlib.util
    reg_file = root / "docsync" / "registry.py"
    spec = importlib.util.spec_from_file_location(
        f"_new_registry_{abs(hash(str(root)))}", reg_file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    taken = {b.id for b in mod.load_registry()}
    if slug in taken:
        raise NewProjectError(f"'{slug}' already exists in docsync.yml")
    proj = root / "projects" / slug
    if proj.exists():
        raise NewProjectError(f"{proj} already exists — pick another id")

    if int(pages) < 1:
        raise NewProjectError("a report needs at least one page")
    proj.mkdir(parents=True)
    (proj / "content.md").write_text(_CONTENT_MD.format(name=name.strip()))
    # json.dumps for both, so a notice carrying an apostrophe or a quote cannot
    # end the Python string it is baked into.
    (proj / "render_report.py").write_text(_RENDERER.format(
        w=w, h=h, pages=int(pages),
        notes=json.dumps([str(m) for m in (notices or [])])))
    (proj / "layout.json").write_text(
        json.dumps(layout if layout is not None else {"positions": {}}, indent=2) + "\n")
    # Append the binding. docsync.yml is a hand-edited file, so this stays an
    # append of well-formed text at the end — never a parse-and-rewrite that
    # would strip its comments.
    with yml.open("a") as f:
        f.write(_BINDING.format(slug=slug, w=w, h=h))
    return proj


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--id", required=True, dest="slug")
    ap.add_argument("--name", required=True)
    ap.add_argument("--w", type=float, default=8.5)
    ap.add_argument("--h", type=float, default=11.0)
    a = ap.parse_args(argv)
    try:
        proj = create(a.slug, a.name, a.w, a.h)
    except NewProjectError as e:
        print(f"  new: {e}", file=sys.stderr)
        return 1
    print(f"  created {proj.relative_to(ROOT)} and registered '{a.slug}' in docsync.yml")
    print(f"  stage it:  python3 -m docsync.stage --id {a.slug}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
