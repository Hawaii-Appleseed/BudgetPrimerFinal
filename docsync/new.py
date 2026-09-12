"""Create a blank docsync project on disk — no GitHub, no token, no repo access.

    python3 -m docsync.new --id my-report --name "My report" [--w 8.5 --h 11]

There are two kinds, and each has exactly ONE renderer:

  · the PLACED canvas (default, and with --template a designed starting
    point) — everything on the page lives in layout.json, and a template is
    data riding this renderer, never code;
  · a TESTIMONY analysis (--data <report_data.json>) — a shim calling
    docsync.testimony_report.render, whose page is generated from measured
    figures rather than placed by hand. Also one renderer for every such
    project: the shim carries no rendering code, so a fix in the shared
    module reaches every testimony report.

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
{{L.font_link()}}
<style>
  body {{{{ margin:0; background:#D6E0D2; font:15px/1.5 system-ui, sans-serif;
         color:#2F3E46; }}}}
  /* position:relative is load-bearing: every placed object is absolute
     against its page, so the page must be the containing block or the whole
     document stacks at the window's origin. isolation:isolate equally so:
     it makes the page a stacking context, which is what keeps a
     send-to-back shape (z-index -1) ABOVE the page's own background — on a
     page with a fill, a plain relative page painted that shape underneath
     its background, i.e. invisible. */
  .page {{{{ width:{w}in; min-height:{h}in; margin:24px auto; background:#fff;
          box-shadow:0 4px 18px rgba(0,0,0,.12); position:relative;
          isolation:isolate; overflow:hidden; box-sizing:border-box; }}}}
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

# A TESTIMONY project's renderer. Unlike the placed-canvas one above this is a
# shim: every testimony report runs the same docsync.testimony_report.render,
# parameterised by its own report_data.json. The rule the placed template keeps
# ("a template is data, never code") is kept here too, just one level up — there
# is one testimony renderer, not a copy of it per project.
_TESTIMONY_RENDERER = '''#!/usr/bin/env python3
"""Testimony analysis — rendered by the shared engine renderer.

There is deliberately no rendering code in this file. Every testimony project
calls docsync.testimony_report.render() with its own report_data.json,
content.md and layout.json, so a fix in the shared module reaches every
testimony report at once.

report_data.json is written by, and only by:

    python -m testimony export --project {slug} [--themes themes/<session>/<f>.yml]

in ~/repos/Legislative-Research-Tool. Never hand-edit a figure in it — the
whole point of the file is that no number on this page was typed.
"""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from docsync.testimony_report import render      # noqa: E402

out = render(HERE, page=({w}, {h}))
print(f"wrote {{out}} ({{out.stat().st_size:,}} bytes)")
'''

_TESTIMONY_BINDING = """
  # Added by docsync/new.py --kind testimony — {origin}.
  - id: {slug}
    name: "{name}"
    content: projects/{slug}/content.md
    editability: strict
    build: python3 projects/{slug}/render_report.py && python3 -m docsync.stage --id {slug}
    outputs:
      - projects/{slug}/index.html
    editor:
      dir: docs/{slug}
      render: projects/{slug}/render_report.py
      out: projects/{slug}/index.html
      layout: projects/{slug}/layout.json
      palette: [{palette}]
      page: [{w}, {h}]
      margins: [0.5, 0.55]
      engine:
        # Measured figures. An undeclared data file builds on disk and then
        # fails in Pyodide, where the editor has only the staged copies.
        - projects/{slug}/report_data.json
"""

# The one-pager's swatches plus the two validated chart poles, so recolouring
# in the editor stays on-palette.
_TESTIMONY_PALETTE = ["#FFFFFF", "#2F3E46", "#354F52", "#52796F", "#84A98C",
                      "#CAD2C5", "#00907A", "#C4602F"]


def _testimony_content_md(name: str, fig: dict) -> str:
    """content.md with one heading slot per measured theme.

    The headings are SEEDED from report_data.json rather than left blank, and
    that is the whole trick: the page reads correctly the moment it is
    scaffolded, and `_check_headings` is armed from then on, so a later
    re-tally that reorders two themes fails loudly instead of pairing a
    heading with another argument's number.
    """
    def slots(pre: str, themes: list) -> str:
        return "".join(f"\n[[{pre}.{i}.h]]\n{t['theme']}\n"
                       for i, t in enumerate(themes, 1))

    args = fig["arguments"]
    bills = ", ".join(b["bill"] for b in fig["bills"]) or "the measure"
    return f"""<!--
  Testimony analysis, rendered by docsync.testimony_report.

  Every FIGURE on the page comes from report_data.json and none of them is in
  this file — regenerate them with `testimony export`, never by typing. What
  lives here is the prose around them, plus one heading per ranked argument.

  The [[arg.N.h]] / [[sup.N.h]] headings were seeded from the tally that
  scaffolded this project. Reword them freely; do NOT reorder them. They are
  checked against the tally's own order on every build, because a heading
  that has drifted from its number is worse than no page.
-->

[[title]]
{name}

[[hero.eyebrow]]
Testimony analysis

[[hero.h1]]
{name}

[[hero.standfirst]]
Every figure on this page is measured from the testimony filed on {bills},
as submitted to the Legislature and published at data.capitol.hawaii.gov.
Counts overlap: one submission usually makes several of these arguments.

[[chart.title]]
Support and opposition, by bill

[[chart.axis.oppose]]
oppose

[[chart.axis.support]]
support

[[chart.note]]
Submissions filed, by position. A raw count includes every copy of an
organised form letter; the distinct count below deduplicates them.

[[arg.head.h2]]
What opponents argued

[[arg.head.contd]]
continued
{slots("arg", args["oppose"]["themes"])}
[[sup.head.h2]]
What supporters argued

[[sup.head.contd]]
continued
{slots("sup", args["support"]["themes"])}
[[orgs.h2]]
Who filed

[[orgs.oppose.h3]]
Opposed

[[orgs.support.h3]]
Supported

[[sources.h2]]
Sources

[[sources]]
[capitol]: Testimony as filed, Hawaiʻi State Legislature — https://data.capitol.hawaii.gov
"""


_BINDING = """
  # Added by "+ New report" (docsync/new.py) — {origin}.
  - id: {slug}
    content: projects/{slug}/content.md
    # Editability findings (dead text / frozen prose) are build ERRORS for
    # this project — see docsync.check. Name deliberate exceptions in an
    # editability_ok list rather than downgrading to warn.
    editability: strict
    build: python3 projects/{slug}/render_report.py && python3 -m docsync.stage --id {slug}
    outputs:
      - projects/{slug}/web/index.html
    editor:
      dir: docs/{slug}
      render: projects/{slug}/render_report.py
      out: projects/{slug}/web/index.html
      layout: projects/{slug}/layout.json
      palette: [{palette}]
      page: [{w}, {h}]
"""

# The blank canvas's swatches, exactly as they were before templates existed.
_DEFAULT_PALETTE = ["#6B9E78", "#95B7A2", "#CAD2C5", "#E8EDE6", "#D6E0D2",
                    "#52796F", "#354F52", "#2F3E46", "#FFFFFF"]


class NewProjectError(Exception):
    pass


def create(slug: str, name: str, w: float = 8.5, h: float = 11.0,
           root: Path = ROOT, pages: int = 1, notices=None,
           layout: dict | None = None, template: str = "",
           scheme: str = "", data: Path | str | None = None) -> Path:
    """Write the project and register it in docsync.yml. Returns its dir.

    Refuses rather than overwrites: an existing binding or directory means
    the slug is taken, and "create" must never be a way to lose work.

    `pages`, `notices` and `layout` are what CONVERSION adds (docsync.ingest):
    a document arrives with a page count, with things the conversion had to
    decide that the editor should say out loud, and with its content already
    placed. They default to the blank-canvas project this has always made, so
    the hosted "+ New report" path calls this exactly as before — and there is
    still ONE renderer template, which is the point of it living here.

    `template` names an entry in docsync.templates: the SAME scaffold, with
    the starter layout, palette, page size and asset files coming from the
    template instead of being blank. A template is data riding the one
    renderer, never a different renderer. `scheme` picks one of the
    template's topic colours (docsync.templates.SCHEMES) — empty means the
    template's own default; on a blank canvas there is nothing to recolour,
    so a scheme without a template is refused rather than ignored.
    """
    palette = _DEFAULT_PALETTE
    tpl_assets: list = []
    content_md = _CONTENT_MD.format(name=name.strip())
    origin = "a blank local project"
    figures: dict | None = None
    if data is not None:
        if template:
            raise NewProjectError(
                "a testimony project is not a placed-canvas template — pass "
                "--data or --template, not both")
        src = Path(data).expanduser()
        if not src.is_file():
            raise NewProjectError(f"no report_data.json at {src}")
        try:
            figures = json.loads(src.read_text(encoding="utf-8"))
        except ValueError as e:
            raise NewProjectError(f"{src} is not readable JSON: {e}") from e
        missing = [k for k in ("bills", "arguments", "organisations", "source")
                   if k not in figures]
        if missing:
            raise NewProjectError(
                f"{src} is missing {', '.join(missing)} — that is not a "
                f"`testimony export` artifact")
        content_md = _testimony_content_md(name.strip(), figures)
        palette = _TESTIMONY_PALETTE
        origin = f"seeded from {src.name}"
    if scheme and not template:
        raise NewProjectError("a colour scheme needs a template — the blank "
                              "canvas has nothing to recolour")
    if template:
        if layout is not None:
            raise NewProjectError("pass a template or a converted layout, not both")
        from .templates import (ASSETS, CONTENT_MD as TPL_CONTENT_MD, SCHEMES,
                                TEMPLATES)
        t = TEMPLATES.get(template)
        if t is None:
            raise NewProjectError(
                f"'{template}' is not a template — one of: "
                + ", ".join(sorted(TEMPLATES)) + ", or none for a blank canvas")
        color = None
        if scheme:
            if scheme not in t.get("schemes", []):
                raise NewProjectError(
                    f"'{scheme}' is not one of this template's schemes — "
                    + ", ".join(t.get("schemes", [])) + ", or none for its default")
            color = SCHEMES[scheme]["color"]
        # The designed layout was measured against ITS page; a template pick
        # therefore brings its sheet with it, whatever size the form held.
        w, h = t["page"]
        pages = t["pages"]
        # A scheme-less template (the one-pager) takes no colour argument.
        layout = t["layout"](color) if color else t["layout"]()
        palette = t["palette"]
        if color:
            # The scheme's colour leads the swatches, as the default's did.
            palette = [color] + [c for c in palette if c != color]
        tpl_assets = [(ASSETS / a, a) for a in t["assets"]]
        for src, a in tpl_assets:
            if not src.is_file():
                raise NewProjectError(f"template asset missing: {a}")
        content_md = TPL_CONTENT_MD.format(name=name.strip(), label=t["name"])
        origin = f'from the "{t["name"]}" template'
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
    (proj / "content.md").write_text(content_md)
    if figures is not None:
        # The data lands in the project so the very next render works, and so
        # `testimony export --project <slug>` has something to overwrite.
        (proj / "report_data.json").write_text(
            json.dumps(figures, indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8")
        (proj / "render_report.py").write_text(
            _TESTIMONY_RENDERER.format(slug=slug, w=w, h=h))
    else:
        # json.dumps for both, so a notice carrying an apostrophe or a quote
        # cannot end the Python string it is baked into.
        (proj / "render_report.py").write_text(_RENDERER.format(
            w=w, h=h, pages=int(pages),
            notes=json.dumps([str(m) for m in (notices or [])])))
    (proj / "layout.json").write_text(
        json.dumps(layout if layout is not None else {"positions": {}}, indent=2) + "\n")
    # Template assets land beside the OUTPUT, where the page's own relative
    # "assets/…" srcs resolve — the same place /__upload puts a dropped image.
    if tpl_assets:
        import shutil
        adir = proj / "web" / "assets"
        adir.mkdir(parents=True, exist_ok=True)
        for src, a in tpl_assets:
            shutil.copy2(src, adir / a)
    # Append the binding. docsync.yml is a hand-edited file, so this stays an
    # append of well-formed text at the end — never a parse-and-rewrite that
    # would strip its comments.
    with yml.open("a") as f:
        tpl = _TESTIMONY_BINDING if figures is not None else _BINDING
        f.write(tpl.format(
            slug=slug, w=w, h=h, origin=origin, name=name.strip(),
            palette=", ".join(f'"{c}"' for c in palette)))
    return proj


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--id", required=True, dest="slug")
    ap.add_argument("--name", required=True)
    ap.add_argument("--w", type=float, default=8.5)
    ap.add_argument("--h", type=float, default=11.0)
    ap.add_argument("--template", default="",
                    help="start from a docsync.templates entry instead of blank")
    ap.add_argument("--scheme", default="",
                    help="one of the template's colour schemes (see "
                         "docsync.templates.SCHEMES); default its own")
    ap.add_argument("--data", default=None,
                    help="a report_data.json from `testimony export` — makes "
                         "this a TESTIMONY project: the shared "
                         "docsync.testimony_report renderer, and a heading "
                         "slot seeded per measured argument")
    a = ap.parse_args(argv)
    try:
        proj = create(a.slug, a.name, a.w, a.h, template=a.template,
                      scheme=a.scheme, data=a.data)
    except NewProjectError as e:
        print(f"  new: {e}", file=sys.stderr)
        return 1
    print(f"  created {proj.relative_to(ROOT)} and registered '{a.slug}' in docsync.yml")
    print(f"  stage it:  python3 -m docsync.stage --id {a.slug}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
