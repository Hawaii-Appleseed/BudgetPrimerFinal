"""One renderer for a testimony analysis, whatever the bill subject is.

`projects/tax-testimony/render_report.py` was the only consumer of a
`report_data.json` in this repo, and it is 615 lines shaped around the six
2026 tax-fairness coalition campaigns — its own chart, its own height-packed
pagination, its own headings. A second issue area had nowhere to land, so a
housing or labour analysis meant either copying those 615 lines or typing
measured numbers into a placed canvas by hand, which is the paste step the
export machinery exists to abolish.

This module is the generic answer: the SAME renderer for every scaffolded
testimony project, parameterised only by that project's own
`report_data.json`, `content.md` and `layout.json`. Fix a bug here and every
testimony report gets the fix. It deliberately does NOT reproduce the
one-pager's height-packed pagination — argument rows chunk at a fixed count
per page, which is predictable and never overflows, and a report that
outgrows it has earned a hand-built renderer like the tax one.

`report_data.json` is written by `python -m testimony export --themes <file>
--project <slug>` in ~/repos/Legislative-Research-Tool. The schema is that
command's, and the two move together.

WHY A HEADING MAY BE BLANK. The ranked lists are ordered by frequency, so a
re-tally can reorder them while the headings in content.md stay put — pairing
a heading with another argument's number, silently. `_check_headings` makes
that fatal. The scaffolder seeds each heading slot from the tally, so the
guard is armed from the first build; a slot somebody deliberately BLANKS
falls back to the measured theme name rather than rendering an empty line,
which is also what makes the page survive a theme being reworded upstream.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .blocks import graphic
from .content import Content
from .layout import Layout

# Appleseed brand, matching the one-pager the templates are digested from.
INK = "#2F3E46"
SLATE = "#354F52"
DEEP = "#52796F"
ASH = "#CAD2C5"
CREAM = "#F4F7F4"

# The validated diverging pair. The brand greens sit below the chroma floor
# and read gray as data marks, so the poles are stepped up until they pass;
# these are the two the tax one-pager already ships.
SUP = "#00907A"
OPP = "#C4602F"

ARGS_PER_PAGE = 6


def _fig(here: Path) -> dict:
    p = here / "report_data.json"
    if not p.exists():
        raise SystemExit(
            f"no report_data.json in {here}. Write one with:\n"
            f"  python -m testimony export --project {here.name} "
            f"[--themes themes/<session>/<slug>.yml]\n"
            f"in ~/repos/Legislative-Research-Tool.")
    return json.loads(p.read_text(encoding="utf-8"))


def _check_headings(C: Content, pre: str, themes: list[dict]) -> None:
    """A count and its heading must describe the same argument.

    Fatal rather than a warning: the page's whole claim is that its figures
    are measured, and a mispaired figure is worse than no page. An empty slot
    is not a mismatch — see the module docstring.
    """
    for i, t in enumerate(themes, 1):
        slot = f"{pre}.{i}.h"
        heading = " ".join(C.text(slot).split())
        if heading and heading != t["theme"]:
            raise SystemExit(
                f"{slot} says {heading!r} but report_data.json ranks "
                f"{t['theme']!r} at #{i}. The tally reordered; update the "
                f"heading slots in content.md to match before rebuilding.")


def _heading(C: Content, pre: str, i: int, theme: str) -> str:
    """The written heading if there is one, else the measured theme name."""
    written = " ".join(C.text(f"{pre}.{i}.h").split())
    return C.t(f"{pre}.{i}.h") if written else theme


def _chart(C: Content, bills: list[dict], tally: str) -> str:
    """Support and opposition per bill, diverging from a shared baseline.

    The two axis labels are slots (slot_attr works on an SVG <text>, where a
    wrapper span would be invalid) — under `editability: strict` a literal
    string here is dead text the build rejects, and it is prose somebody may
    reasonably want to reword.
    """
    if not bills:
        return ""
    peak = max([max(b["support"], b["oppose"]) for b in bills]) or 1
    row_h, mid, half = 26, 250, 210
    height = len(bills) * row_h + 34
    out = [f'<svg viewBox="0 0 520 {height}" xmlns="http://www.w3.org/2000/svg" '
           f'role="img" aria-label="Support and opposition by bill, with outcome">',
           f'<text x="{mid - 6}" y="12" font-size="9" fill="{SLATE}" '
           f'text-anchor="end"{C.slot_attr("chart.axis.oppose")}>'
           f'{C.text("chart.axis.oppose")}</text>',
           f'<text x="{mid + 6}" y="12" font-size="9" fill="{SLATE}"'
           f'{C.slot_attr("chart.axis.support")}>'
           f'{C.text("chart.axis.support")}</text>']
    for i, b in enumerate(bills):
        y = 22 + i * row_h
        wo = b["oppose"] / peak * half
        ws = b["support"] / peak * half
        passed = b["outcome"] == "PASSED"
        out += [
            f'<rect x="{mid - wo:.1f}" y="{y}" width="{wo:.1f}" height="13" fill="{OPP}"/>',
            f'<rect x="{mid}" y="{y}" width="{ws:.1f}" height="13" fill="{SUP}"/>',
            f'<text x="{mid - wo - 6:.1f}" y="{y + 10}" font-size="9" '
            f'fill="{SLATE}" text-anchor="end">{b["oppose"]}</text>',
            f'<text x="{mid + ws + 6:.1f}" y="{y + 10}" font-size="9" '
            f'fill="{SLATE}">{b["support"]}</text>',
            f'<text x="4" y="{y + 10}" font-size="9.5" fill="{INK}" '
            f'font-weight="600">{b["bill"]}</text>',
            f'<text x="62" y="{y + 10}" font-size="9" fill="{SLATE}">'
            f'{b["short"]}</text>',
            f'<text x="516" y="{y + 10}" font-size="8.5" text-anchor="end" '
            f'font-weight="{"700" if passed else "400"}" '
            f'fill="{SUP if passed else SLATE}">{b["outcome"]}</text>',
        ]
    out.append("</svg>")
    return "".join(out)


def _rows(C: Content, pre: str, themes: list[dict], accent: str,
          tally: str) -> list[str]:
    rows = []
    for i, t in enumerate(themes, 1):
        rows.append(
            f'<div class="row">'
            f'<span class="rank" style="background:{accent}"{C.derived(tally)}>{i}</span>'
            f'<span class="n" style="color:{accent}"{C.derived(tally)}>{t["raw"]}</span>'
            f'<span class="d"{C.derived(tally)}>{t["distinct"]} distinct</span>'
            f'<span class="h">{_heading(C, pre, i, t["theme"])}</span>'
            f'</div>')
    return rows


def _orgs(C: Content, rows: list[dict], accent: str, tally: str) -> str:
    """Who filed, on one side.

    The names are DERIVED, not slots. The tax one-pager makes each an
    `[[org.<slug>.name]]` slot so an editor can correct a filer's letterhead,
    but that costs a slot per organisation in content.md and raises
    ContentError the moment the corpus turns up a filer the file has no slot
    for — which is a rebuild that fails on new data, the one thing a
    scaffolded project must not do. The name is measured; correct it in
    `orgs.yml` upstream and re-export.
    """
    items = []
    for r in rows:
        plural = "s" if r["bills"] != 1 else ""
        items.append(
            f'<li><span class="on" style="color:{accent}"{C.derived(tally)}>'
            f'{r["submissions"]}</span> '
            f'<span{C.derived(tally)}>{r["organization"]}</span>'
            f'<span class="ob"{C.derived(tally)}> · {r["bills"]} bill{plural}'
            f'</span></li>')
    return f'<ol class="orgs">{"".join(items)}</ol>'


def _chunk(seq: list, n: int) -> list[list]:
    return [seq[i:i + n] for i in range(0, len(seq), n)] or [[]]


def render(here: Path | str, *, page: tuple[float, float] = (8.5, 11.0)) -> Path:
    """Render the testimony project rooted at `here`. Returns the output path."""
    here = Path(here).resolve()
    layout_p = Path(os.environ.get("DOCSYNC_LAYOUT") or (here / "layout.json"))
    content_p = Path(os.environ.get("DOCSYNC_CONTENT") or (here / "content.md"))
    out_p = Path(os.environ.get("DOCSYNC_OUT") or (here / "index.html"))

    F = _fig(here)
    tally = F["source"]["command"]
    L = Layout(layout_p, page=page)
    C = Content(content_p, styles=L)

    oppose = F["arguments"]["oppose"]
    support = F["arguments"]["support"]
    _check_headings(C, "arg", oppose["themes"])
    _check_headings(C, "sup", support["themes"])

    w, h = page
    pages = []

    # Page 1 — the hero, the chart, and the two denominators.
    pages.append(
        f'<div class="eyebrow"{L.attr("hero.eyebrow")}>{C.t("hero.eyebrow")}</div>'
        f'{L.spacer("hero.h1")}<h1{L.attr("hero.h1")}>{C.t("hero.h1")}</h1>'
        f'{C.html("hero.standfirst", "standfirst")}'
        f'<h2{L.attr("chart.title")}>{C.t("chart.title")}</h2>'
        f'{graphic(L, "chart.svg", _chart(C, F["bills"], tally), w=6.6)}'
        f'{C.html("chart.note", "note")}'
        # The whole line is derived, words and all: the frame around these
        # numbers is remade by the same command that remakes them, so it gets
        # ONE hook rather than six, and the connective prose between the
        # figures is not left as dead text the strict pass rejects.
        f'<p class="denom"{C.derived(tally)}>'
        f'<b>{oppose["submissions"]}</b> opposing submissions '
        f'(<b>{oppose["distinct"]}</b> distinct) · '
        f'<b>{support["submissions"]}</b> supporting '
        f'(<b>{support["distinct"]}</b> distinct)</p>')

    # Argument pages — opposition, then support.
    for pre, side, accent, head in (("arg", oppose, OPP, "arg.head"),
                                    ("sup", support, SUP, "sup.head")):
        chunks = _chunk(_rows(C, pre, side["themes"], accent, tally), ARGS_PER_PAGE)
        for n, chunk in enumerate(chunks):
            if not chunk:
                continue
            title = (f'{L.spacer(f"{head}.h2")}<h2{L.attr(f"{head}.h2")}>'
                     f'{C.t(f"{head}.h2")}</h2>' if n == 0 else
                     f'<div class="contd">{C.t(f"{head}.contd")}</div>')
            pages.append(title + "".join(chunk))

    # Closing page — who filed, on each side.
    orgs = F["organisations"]
    pages.append(
        f'{L.spacer("orgs.h2")}<h2{L.attr("orgs.h2")}>{C.t("orgs.h2")}</h2>'
        f'<div class="cols">'
        f'<div><h3 style="color:{OPP}"{L.attr("orgs.oppose.h3")}>'
        f'{C.t("orgs.oppose.h3")}</h3>{_orgs(C, orgs["oppose"], OPP, tally)}</div>'
        f'<div><h3 style="color:{SUP}"{L.attr("orgs.support.h3")}>'
        f'{C.t("orgs.support.h3")}</h3>{_orgs(C, orgs["support"], SUP, tally)}</div>'
        f'</div>')

    body = "".join(
        f'<section class="page" data-page="{i}">{p}'
        f'{C.extras(f"page{i}")} {L.layer(i)}{L.text_boxes(i)}{L.tables_html(i)}'
        f'</section>'
        for i, p in enumerate(pages, 1))
    body += L.pagemeta(range(1, len(pages) + 1))
    body = C.fn.resolve(body)

    notes = C.fn.endnotes()
    endnotes = "".join(
        f'<li id="en{i + 1}">{txt} <a href="{url}">{url}</a></li>'
        for i, (txt, url) in enumerate(notes))
    if endnotes:
        body += (f'<section class="page">'
                 f'<h2{L.attr("sources.h2")}>{C.t("sources.h2")}</h2>'
                 f'<ol class="endnotes">{endnotes}</ol></section>')

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{C.text("title")}</title>
{L.font_link()}
<style>
  body {{ margin:0; background:#D6E0D2; color:{INK};
         font:15px/1.55 Poppins, system-ui, sans-serif; }}
  .page {{ width:{w}in; min-height:{h}in; margin:24px auto; background:#fff;
          box-shadow:0 4px 18px rgba(0,0,0,.12); padding:0.55in 0.5in;
          box-sizing:border-box; position:relative; isolation:isolate;
          overflow:hidden; }}
  .eyebrow {{ font:600 10.5px/1.2 Manrope, system-ui, sans-serif;
             letter-spacing:.14em; text-transform:uppercase; color:{DEEP}; }}
  h1 {{ font:800 30px/1.1 Manrope, system-ui, sans-serif; margin:.28em 0 .4em; }}
  h2 {{ font:700 17px/1.2 Manrope, system-ui, sans-serif; margin:0 0 .7em;
       color:{SLATE}; }}
  h3 {{ font:700 12.5px/1.2 Manrope, system-ui, sans-serif; margin:0 0 .5em; }}
  .standfirst {{ font-size:13.5px; color:{SLATE}; margin:0 0 1.1em; }}
  .note, .denom {{ font-size:13.5px; color:{SLATE}; }}
  .denom {{ border-top:1px solid {ASH}; padding-top:8px; margin-top:14px; }}
  .contd {{ font:600 9.5px/1.2 Manrope, system-ui, sans-serif;
           letter-spacing:.14em; text-transform:uppercase; color:{DEEP};
           margin-bottom:1em; }}
  .row {{ display:grid; grid-template-columns:22px 42px 74px 1fr;
         align-items:baseline; gap:9px; padding:9px 0;
         border-bottom:1px solid {ASH}; }}
  .rank {{ color:#fff; font:700 10px/18px Manrope, system-ui, sans-serif;
          text-align:center; border-radius:50%; height:18px; }}
  .n {{ font:800 17px/1 Manrope, system-ui, sans-serif; text-align:right; }}
  .d {{ font-size:13.5px; color:{SLATE}; }}
  .h {{ font-size:13.5px; }}
  .cols {{ display:grid; grid-template-columns:1fr 1fr; gap:26px; }}
  .orgs {{ list-style:none; padding:0; margin:0; font-size:13.5px; }}
  .orgs li {{ padding:5px 0; border-bottom:1px solid {CREAM}; }}
  .on {{ font-weight:800; display:inline-block; min-width:22px; }}
  .ob {{ color:{SLATE}; font-size:13.5px; }}
  .endnotes {{ font-size:13.5px; color:{SLATE}; }}
  @media print {{
    @page {{ size:{w}in {h}in; margin:0; }}
    body {{ background:#fff; }}
    .page {{ box-shadow:none; margin:0; width:{w}in; height:{h}in; }}
  }}
</style>
</head>
<body>
{body}
</body>
</html>
"""
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(html, encoding="utf-8")
    return out_p
