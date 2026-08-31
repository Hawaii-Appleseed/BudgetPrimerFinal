#!/usr/bin/env python3
"""Render the Budget Primer FY2026-27 as a single HTML document.

Reads data/report_data.json and emits web/index.html. All charts are inline SVG
generated here, so the screen version and the Chrome-printed PDF are identical;
web/primer.js layers tooltips/hover on top for the interactive build.
"""
import json
import math
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from docsync.content import Content, ContentError  # noqa: E402
from docsync.blocks import chart_scroll, chart_scroll_css  # noqa: E402
from docsync.layout import (Layout, LayoutError, check_icon_svg,  # noqa: E402
                            fill_css, fill_repr, icon_color)

HERE = Path(__file__).resolve().parent.parent
# Input/output paths default to the working tree, so an ordinary render (CLI,
# CI, the Pyodide editor) is byte-identical. The live server's export endpoint
# overrides them to render the editor's current draft from temp files into a
# throwaway output — a PDF/PNG of unsaved work without touching content.md or
# layout.json. Empty/unset env -> the defaults below.
_LAYOUT = Path(os.environ.get("DOCSYNC_LAYOUT") or (HERE / "layout.json"))
_CONTENT = Path(os.environ.get("DOCSYNC_CONTENT") or (HERE / "content.md"))
_OUT = Path(os.environ.get("DOCSYNC_OUT") or (HERE / "web" / "index.html"))
# Position/shape/text overrides. Empty by default: the design below is the
# default, and this only speaks where someone has moved or restyled something.
# Built BEFORE C because C consumes it — and because some slots are rendered at
# import time, so a later hand-off would work only by luck of line order.
L = Layout(_LAYOUT)
# Authored prose + sources. Synced from the Google Doc via `make pull-doc`.
C = Content(_CONTENT, styles=L)
DATA = json.loads((HERE / "data" / "report_data.json").read_text())
BUD = DATA["budget"]
RES = DATA["research"]
REV = DATA["tax_revenue_fy2027"]
# FY2026 companions for the year-picker figures (3/4/5). Same category logic as
# FY2027; validated to reproduce the published FY2025-26 primer numbers.
BUD26 = DATA["budget_fy2026"]
REV26 = DATA["tax_revenue_fy2026"]

# General-fund obligated ("fixed") costs FY2018-27, hand-sourced from each
# biennium's Budget in Brief (p.18); see manual/obligated_costs.json for sources.
OBLIG = json.loads((HERE / "manual" / "obligated_costs.json").read_text())

# --- budget tracker interlink (same repo, docs/ SPA on GitHub Pages) ---
TRACKER = "https://dtomkatsu.github.io/BudgetPrimerFinal/"
_dept_src = json.loads(
    (HERE.parent / "docs" / "js" / "departments_act175_fy2027.json").read_text())
_hist = json.loads(
    (HERE.parent / "docs" / "js" / "historical_trends.json").read_text())
_hist_by_dept = {d["dept_code"]: [round(p["nominal"] / 1e6) for p in d["series"]]
                 for d in _hist["by_department"]}
_hist_fys = [p["fy"] for p in _hist["by_department"][0]["series"]]

_SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on",
                "or", "the", "to", "with"}
_ACRONYMS = {"UH", "OHA", "HHSC", "EUTF", "EMS", "IT", "TANF", "DNA", "ADA"}

def smart_title(name):
    """ALL-CAPS act program names -> title case with small words and acronyms."""
    def cap(word):
        if word.upper() in _ACRONYMS:
            return word.upper()
        return "-".join(w[:1].upper() + w[1:].lower() for w in word.split("-"))
    words = name.split()
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        if 0 < i < len(words) - 1 and lw.strip(",") in _SMALL_WORDS:
            out.append(lw)
        else:
            out.append(cap(w))
    return " ".join(out)

# split on sentence-ending periods only — a period preceded by an uppercase
# letter (e.g. "John A. Burns", "U.S.") is almost always a mid-name initial or
# abbreviation, not a sentence boundary, so it's excluded from the split.
_SENTENCE_SPLIT = re.compile(r"(?<![A-Z])\.\s+(?=[A-Z])")

DEPT_INFO = {}
for _d in _dept_src:
    desc = _SENTENCE_SPLIT.split((_d.get("description") or "").strip())
    # aggregate fund-line rows into whole programs, mirroring the tracker's dept page
    progs = {}
    for _p in _d["programs"]:
        key = _p["program_id"]
        progs.setdefault(key, {"n": smart_title(_p["program_name"]), "v": 0})
        progs[key]["v"] += _p["amount"]
    top = sorted(progs.values(), key=lambda r: -r["v"])[:4]
    DEPT_INFO[_d["code"]] = {
        "name": _d["name"],
        "blurb": ". ".join(desc[:2]) + ("." if desc[:2] and not desc[1 if len(desc) > 1 else 0].endswith(".") else ""),
        "operating": _d.get("operating_budget", 0) or 0,
        "capital": _d.get("capital_budget", 0) or 0,
        "positions": round(_d.get("positions") or 0),
        "programs": [[r["n"], round(r["v"])] for r in top],
        "nprogs": len(progs),
        "hist": _hist_by_dept.get(_d["code"]),
        "url": f"{TRACKER}#/department/{_d['id']}",
    }
HIST_FY_SPAN = [_hist_fys[0], _hist_fys[-1]]

# ---------- palette (Hawaiʻi Appleseed brand) ----------
# Muted Teal (logo) / light teal / Ash Grey + light tints; Deep Teal, Dark Slate
# Grey, Charcoal Blue for the darks. Keep in sync with the vars in primer.css.
SAGE, SAGE_MID, SAGE_LIGHT, MINT, PALE = "#6B9E78", "#95B7A2", "#CAD2C5", "#E8EDE6", "#D6E0D2"
DARK, FOREST, DARKEST = "#52796F", "#354F52", "#2F3E46"
INK = "#2F3E46"
SERIES = {"operating": SAGE, "capital": SAGE_MID, "one_time": DARK, "emergency": DARKEST}

def is_light_bg(hexc):
    """True when a fill is light enough to need dark (not white) text. The brand's
    Muted Teal / Ash Grey tiles read best with charcoal text; only Deep Teal,
    Dark Slate, and Charcoal Blue take reversed (white) text.

    An 8-digit fill carries alpha: a half-transparent dark tile shows the white
    page through it and reads light, so the colour is composited over white
    before the luminance test — otherwise the text reverses to white on what
    looks like a pale tile. Fills mostly sit on the white page; over a darker
    element the composite is approximate, which is the honest limit of deciding
    contrast without knowing every layer beneath."""
    h = hexc.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    if len(h) == 8:
        a = int(h[6:8], 16) / 255
        r, g, b = (v * a + 255 * (1 - a) for v in (r, g, b))
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) > 130

# ---------- formatting ----------
def words(n):
    """$19.88 billion / $450.67 million / $684,385 / $0 — original Table 1 style."""
    if n == 0:
        return "$0"
    for div, unit in ((1e9, "billion"), (1e6, "million")):
        if n >= div:
            v = f"{n / div:.2f}".rstrip("0").rstrip(".")
            return f"${v} {unit}"
    return f"${n:,.0f}"

def short(n):
    """$4.8B / $659M / $80.7M / $2.56M — original chart-label style."""
    if n >= 1e9:
        return f"${n / 1e9:.1f}".rstrip("0").rstrip(".") + "B"
    m = n / 1e6
    if m >= 100:
        return f"${m:.0f}M"
    if m >= 10:
        return f"${m:.1f}".rstrip("0").rstrip(".") + "M"
    return f"${m:.2f}".rstrip("0").rstrip(".") + "M"

def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;")

# ---------- research-derived numbers ----------
JUD = RES["branches_fy2027"]["judiciary"]
LEG = RES["branches_fy2027"]["legislature"]
OHA = RES["branches_fy2027"]["oha"]
FEATURED = RES["one_time_fy2027"]["featured"]
EMERG27 = RES["emergency_fy2027"]

# featured acts only, like the FY26 edition; Act 32 ($55M PLT) counts in the OHA
# column, not Executive, so it is excluded here.
EXEC_ONE_TIME = sum(f["amount"] for f in FEATURED if not f["act"].startswith("Act 032"))
EXEC_EMERG = sum(e["amount_fy27"] for e in EMERG27)
JUD_ONE_TIME = 684385                                        # Act 126 juror pay
OHA_ONE_TIME = 55000000                                      # Act 32 PLT transfer

EXEC_OP = BUD["executive"]["operating"]
EXEC_CIP = BUD["executive"]["capital"] + BUD["executive"]["county_cip_grants"]

# one-time / emergency dept mapping for Figure 2 segments (featured acts)
ONE_TIME_BY_DEPT = {"TRN": 612e6, "AGS": 176e6, "LNR": 12e6, "BUF": 100e6,
                    "BED": 49.5e6, "UOH": 28.5e6, "HMS": 16.5e6, "EDN": 3.365e6}
EMERG_BY_DEPT = {"BUF": EXEC_EMERG}

# Per-branch figures by fiscal year, for Table 1 and Figure 2's branch rows.
# Executive op/cip come from the Budget Tracker (Act 175). Judiciary/Legislature/
# OHA op/cip are research-derived for FY2027; for FY2026 they are the published
# FY2025-26 primer figures (the tracker carries no branch data). One-time and
# emergency appropriations are researched totals.
BRANCHES_BY_FY = {
    2027: {
        "exec": {"op": EXEC_OP, "cip": EXEC_CIP, "one": EXEC_ONE_TIME, "emerg": EXEC_EMERG},
        "jud":  {"op": JUD["operating_fy27"], "cip": JUD["cip_fy27"], "one": JUD_ONE_TIME, "emerg": 0},
        "leg":  {"op": LEG["total_fy27_published_style"], "cip": 0, "one": 0, "emerg": 0},
        "oha":  {"op": OHA["operating_fy27"], "cip": 0, "one": OHA_ONE_TIME, "emerg": 0},
    },
    2026: {
        "exec": {"op": BUD26["executive"]["operating"],
                 "cip": BUD26["executive"]["capital"] + BUD26["executive"]["county_cip_grants"],
                 "one": 450_670_000, "emerg": 121_830_000},
        "jud":  {"op": 214_570_000, "cip": 12_900_000, "one": 0, "emerg": 4_700_000},
        "leg":  {"op": 51_630_000, "cip": 0, "one": 0, "emerg": 0},
        "oha":  {"op": 6_000_000, "cip": 0, "one": 0, "emerg": 0},
    },
}
FY_LABEL = {2026: "FY 2025–2026", 2027: "FY 2026–2027"}
# county CIP grants ride in the tracker's dept list but are footnoted, not charted
FIG2_COUNTY_CODES = {"COM", "COH", "CCH", "COK"}

# ---------- svg helpers ----------
def arc_path(cx, cy, r1, r2, a0, a1):
    """Annular sector path. Angles in degrees, 0 = 12 o'clock, clockwise."""
    def pt(r, a):
        rad = math.radians(a - 90)
        return cx + r * math.cos(rad), cy + r * math.sin(rad)
    large = 1 if (a1 - a0) > 180 else 0
    x0, y0 = pt(r2, a0); x1, y1 = pt(r2, a1)
    x2, y2 = pt(r1, a1); x3, y3 = pt(r1, a0)
    return (f"M{x0:.1f},{y0:.1f} A{r2},{r2} 0 {large} 1 {x1:.1f},{y1:.1f} "
            f"L{x2:.1f},{y2:.1f} A{r1},{r1} 0 {large} 0 {x3:.1f},{y3:.1f} Z")

def pie(slices, size=400, r=158, cls="", width_in=3.6, label_pt=14.0, start=0.0, attrs=""):
    """slices: [(name, value, color, label_lines)] clockwise from 12 o'clock.

    size leaves room around the r=158 disc for the outside labels of thin slices,
    which can sit ~40px beyond the rim plus their own text width.

    The SVG scales to width_in on the page, so a user unit is
    (width_in / size) inches. label_pt is converted into user units accordingly,
    which is how the original's per-figure label sizes (13.7-15.5pt) are matched.
    """
    unit_pt = width_in / size * 72.0
    lab_u = label_pt / unit_pt
    dy = lab_u * 1.08
    cx = cy = size / 2
    total = sum(s[1] for s in slices)
    a = start
    paths, labels = [], []
    small_n = 0
    for name, val, color, lab in slices:
        sweep = 360 * val / total
        paths.append(f'<path d="{arc_path(cx, cy, 0, r, a, a + sweep)}" fill="{color}" '
                     f'class="iv" data-tip="{esc(name)}: {short(val)}"/>')
        mid = math.radians(a + sweep / 2 - 90)
        big = sweep > 55
        lr = r * 0.62 if big else r + 18
        if sweep < 26:                       # stagger consecutive small-slice labels
            lr = r + (14 + 18 * (small_n % 2))
            small_n += 1
        lx, ly = cx + lr * math.cos(mid), cy + lr * math.sin(mid)
        anchor = "middle" if big else ("start" if math.cos(mid) > 0.25 else
                                       "end" if math.cos(mid) < -0.25 else "middle")
        ty = ly - (len(lab) - 1) * dy / 2
        # keep multi-line labels inside the viewBox
        ty = min(max(ty, lab_u), size - 4 - (len(lab) - 1) * dy)
        lx = min(max(lx, 6), size - 6)
        t = "".join(f'<tspan x="{lx:.0f}" dy="{dy:.0f}" >{l}</tspan>' if i else
                    f'<tspan x="{lx:.0f}">{l}</tspan>' for i, l in enumerate(lab))
        # a label sitting inside a dark slice needs reversed (white) text
        # Inline STYLE, not a presentation attribute: .pie-lab in primer.css
        # sets `fill: var(--ink)`, and in SVG any CSS rule beats a presentation
        # attribute — so a bare fill="#fff" here silently lost, and the labels
        # inside the dark first slices (Transportation, General Funds, GET)
        # rendered ink-on-forest. Only an inline style outranks the stylesheet.
        lab_fill = ' style="fill:#fff"' if (big and not is_light_bg(color)) else ''
        labels.append(f'<text x="{lx:.0f}" y="{ty:.0f}" text-anchor="{anchor}" '
                      f'class="pie-lab"{lab_fill} font-size="{lab_u:.1f}">{t}</text>')
        a += sweep
    return (f'<svg viewBox="0 0 {size} {size}" class="chart pie {cls}"{attrs} '
            f'preserveAspectRatio="xMidYMid meet" role="img">'
            + "".join(paths) + "".join(labels) + "</svg>")

def legend(items):
    """items: (label_html, colour). Labels arrive ready to print — authored ones
    via C.t(..., esc=True), data-derived ones via esc(). Escaping here instead
    would mangle the editor's slot markup into visible tags."""
    rows = "".join(
        f'<div class="lg"><span class="sw" style="background:{c}"></span>{n}</div>'
        for n, c in items)
    return f'<div class="legend">{rows}</div>'

# Display bands for the obligated-costs stacked area, bottom -> top. Certificate
# of Participation (a lease-financing debt instrument, <0.1%) folds into Debt
# Service so the stack total equals the BIB "Fixed Sub-total" exactly.
OBLIG_BANDS = [
    ("Retirement (ERS)", ["Retirement System"], DARK),
    ("Health Benefits (EUTF)", ["Health Fund"], SAGE),
    ("Medicaid & Entitlements", ["Medicaid & Entitlements"], SAGE_MID),
    ("Debt Service", ["Debt Service", "Certificate of Participation"], SAGE_LIGHT),
]

def fig_obligated():
    """Stacked-area chart of general-fund obligated costs, FY2018-FY2027."""
    series = OBLIG["series"]
    years = sorted(int(y) for y in series)
    def val(fy, keys):
        return sum(series[str(fy)][k] for k in keys)
    def bill(n):
        return f"${n / 1e9:.2f}B"
    W, H, L, R, TM, BM = 720, 400, 60, 14, 22, 30
    pw, ph = W - L - R, H - TM - BM
    ymax = 5.5e9
    def X(i):
        return L + pw * i / (len(years) - 1)
    def Y(v):
        return TM + ph * (1 - v / ymax)
    out = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img">']
    for gb in range(0, 6):                                   # $0-$5B gridlines
        y = Y(gb * 1e9)
        out.append(f'<line x1="{L}" y1="{y:.1f}" x2="{W-R}" y2="{y:.1f}" stroke="#D7DEDC" stroke-width="1"/>')
        out.append(f'<text x="{L-8}" y="{y+4:.1f}" text-anchor="end" class="ax">${gb}B</text>')
    for i, fy in enumerate(years):                           # x-axis year labels
        out.append(f'<text x="{X(i):.1f}" y="{H-10}" text-anchor="middle" class="ax">FY{str(fy)[2:]}</text>')
    cum = [0.0] * len(years)                                 # stacked bands
    band_tops = []
    for name, keys, color in OBLIG_BANDS:
        top = [cum[i] + val(fy, keys) for i, fy in enumerate(years)]
        pts = ([f"{X(i):.1f},{Y(top[i]):.1f}" for i in range(len(years))]
               + [f"{X(i):.1f},{Y(cum[i]):.1f}" for i in reversed(range(len(years)))])
        out.append(f'<polygon points="{" ".join(pts)}" fill="{color}" stroke="#fff" stroke-width="0.7"/>')
        band_tops.append(top)
        cum = top
    # data-point dots at each year on every band's top line
    for top in band_tops:
        for i in range(len(years)):
            out.append(f'<circle cx="{X(i):.1f}" cy="{Y(top[i]):.1f}" r="2.3" '
                       f'fill="#fff" stroke="{FOREST}" stroke-width="1"/>')
    all_keys = [k for _, ks, _ in OBLIG_BANDS for k in ks]
    total0, totalN = val(years[0], all_keys), val(years[-1], all_keys)
    # endpoint total labels (first year above stack, last year above stack)
    out.append(f'<text x="{X(0)+2:.1f}" y="{Y(total0)-7:.1f}" class="vlab">{bill(total0)}</text>')
    out.append(f'<text x="{X(len(years)-1)-2:.1f}" y="{Y(totalN)-7:.1f}" text-anchor="end" class="vlab">{bill(totalN)}</text>')
    for i, fy in enumerate(years):                           # per-year hover zones
        parts = " · ".join(f"{nm.split(' (')[0]} {bill(val(fy, ks))}" for nm, ks, _ in OBLIG_BANDS)
        tip = f"FY{fy} — Obligated total {bill(val(fy, all_keys))} · {parts}"
        out.append(f'<rect x="{X(i)-14:.1f}" y="{TM}" width="28" height="{ph}" fill="transparent" '
                   f'pointer-events="all" class="iv" data-tip="{esc(tip)}"/>')
    out.append("</svg>")
    # Scrolls rather than shrinks on a phone; 11.5px is this chart's
    # smallest label (.vlab), which is what sets how far it may scale
    # down before the wrapper takes over.
    return chart_scroll("".join(out), smallest_label=11.5)

def fig2_rows_for(year):
    """Branch + department rows for Figure 2. FY2027 carries per-department
    one-time/emergency overlays; FY2026 (tracker op/cap only) shows just
    operating + capital, since no FY2026 per-department overlay data exists."""
    b = BRANCHES_BY_FY[year]
    budget = BUD if year == 2027 else BUD26
    overlays = (year == 2027)
    j, l, o = b["jud"], b["leg"], b["oha"]
    rows = [  # branch rows first, like the original (no tracker page -> code None)
        ("Judiciary", {"operating": j["op"], "capital": j["cip"],
                       "one_time": j["one"] if overlays else 0, "emergency": 0}, None),
        ("Legislature", {"operating": l["op"], "capital": l["cip"],
                         "one_time": 0, "emergency": 0}, None),
        ("OHA", {"operating": o["op"], "capital": o["cip"],
                 "one_time": o["one"] if overlays else 0, "emergency": 0}, None),
    ]
    for d in budget["figure2_departments"]:
        if d["code"] in FIG2_COUNTY_CODES:
            continue
        rows.append((d["label"], {"operating": d["operating"], "capital": d["capital"],
                                  "one_time": ONE_TIME_BY_DEPT.get(d["code"], 0) if overlays else 0,
                                  "emergency": EMERG_BY_DEPT.get(d["code"], 0) if overlays else 0},
                     d["code"] if d["code"] in DEPT_INFO else None))
    return rows

def fig2_chart_for(year):
    return fig2_svg(fig2_rows_for(year), attrs=f' data-fig="fig2" data-fy="{year}"'
                    + ("" if year == 2027 else " hidden"))

def fig2_svg(rows, attrs=""):
    # GAP is the single biggest lever on this chart's height: it multiplies by
    # the 24 rows, and at 720 units to 7.26in one unit per row is ~0.24in on
    # the page. Page 6 has no room to spare between the chart and the folio.
    W, LEFT, RH, GAP = 720, 150, 17, 6
    maxv = 5.5e9
    plot_w = W - LEFT - 60
    H = len(rows) * (RH + GAP) + 40
    out = [f'<svg viewBox="0 0 {W} {H}" class="chart"{attrs} role="img">']
    for gx in range(0, 6):
        x = LEFT + plot_w * gx / 5.5
        out.append(f'<line x1="{x:.0f}" y1="0" x2="{x:.0f}" y2="{H-34}" stroke="#D7DEDC" stroke-width="1"/>')
        out.append(f'<text x="{x:.0f}" y="{H-18}" text-anchor="middle" class="ax">${gx}B</text>')
    y = 4
    for label, seg, code in rows:
        total = sum(seg.values())
        dept_attr = f' data-dept="{code}"' if code else ""
        out.append(f'<text x="{LEFT-8}" y="{y+RH-4}" text-anchor="end" '
                   f'class="ylab{" lk" if code else ""}"{dept_attr}>{esc(label)}</text>')
        x = LEFT
        for key in ("operating", "capital", "one_time", "emergency"):
            v = seg[key]
            if v <= 0:
                continue
            w = plot_w * v / maxv
            tip = f"{label} — {key.replace('_', '-').title()}: {short(v)}"
            if code:
                tip += " · click for tracker link"
            out.append(f'<rect x="{x:.1f}" y="{y}" width="{max(w,0.8):.1f}" height="{RH}" '
                       f'fill="{SERIES[key]}" class="iv" data-tip="{esc(tip)}"{dept_attr}/>')
            x += w
        if x + 44 > W:                        # widest bars: label inside, white
            out.append(f'<text x="{x-6:.0f}" y="{y+RH-4}" text-anchor="end" class="vlab" '
                       f'fill="#fff">{short(total)}</text>')
        else:
            out.append(f'<text x="{x+5:.0f}" y="{y+RH-4}" class="vlab">{short(total)}</text>')
        y += RH + GAP
    out.append("</svg>")
    # Scrolls rather than shrinks on a phone; 11.5px is this chart's
    # smallest label (.vlab), which is what sets how far it may scale
    # down before the wrapper takes over.
    return chart_scroll("".join(out), smallest_label=11.5)

# Each lifecycle callout owns a contiguous run of months; brackets outside the
# month ring make that span explicit rather than leaving it to proximity.
LIFECYCLE_SPANS = [
    # key, label, first month, last month, text, side  (label/text from content.md)
    (k, C.t(f"process.lifecycle.{k}.label"), m0, m1,
     C(f"process.lifecycle.{k}.text"), side)
    for k, m0, m1, side in [
        ("dec", 11, 11, "top"), ("jan", 0, 3, "right"), ("may", 4, 4, "right"),
        ("jun", 5, 5, "bottom"), ("jul", 6, 6, "bottom-left"), ("aug", 7, 8, "left"),
        ("oct", 9, 10, "left"),
    ]
]

# Geometry shared by the wheel SVG and the callout placement, so the text always
# sits at the end of its bracket stub rather than at a hand-tuned coordinate.
LC_VIEWBOX = 560            # svg user units
LC_SVG_IN = 5.8             # rendered width  (see .lifecycle-wrap svg)
LC_WRAP_IN = 6.9            # containing block width
LC_MARGIN_IN = 0.28         # svg's vertical margin inside the wrap
LC_STUB_U = 178 + 13        # bracket radius + stub length, in user units
LC_CALLOUT_IN = 1.58        # .lc width
LC_PAD_IN = 0.12            # gap past the stub end, measured along the stub
LC_EDGE_IN = 0.16           # top/bottom blocks overhang their stub by this much
# Two callouts need more reach than the rest, for reasons that are about the
# BLOCK, not the wheel — so they live here rather than in the geometry above.
# DEC hangs upward off its stub and would otherwise sit on the ring. MAY is the
# one four-line block on a diagonal stub: a left/right block is centred on its
# stub's height, so on a diagonal it is the block's top CORNER, not its edge,
# that reaches back toward the bracket — and a tall block's corner reaches far
# enough to cross it. The short diagonal blocks (AUG-SEP, OCT-NOV) do not.
LC_PAD_OVERRIDE = {"dec": 0.38, "may": 0.30}


def lifecycle_callouts():
    """Place each callout just past the end of its bracket stub.

    The offset is applied along the stub's own radial direction, not horizontally:
    a fixed x-offset leaves the diagonal callouts (MAY, AUG-SEP, OCT-NOV) visibly
    farther from their brackets than the ones due N/E/S/W.
    """
    unit = LC_SVG_IN / LC_VIEWBOX
    cx = (LC_WRAP_IN - LC_SVG_IN) / 2 + LC_SVG_IN / 2
    cy = LC_MARGIN_IN + LC_SVG_IN / 2
    out = []
    for key, lab, m0, m1, txt, side in LIFECYCLE_SPANS:
        amid = (m0 * 30 + 2 + (m1 + 1) * 30 - 2) / 2
        rad = math.radians(amid - 90)
        pad = LC_PAD_OVERRIDE.get(key, LC_PAD_IN)
        r = LC_STUB_U * unit + pad
        x, y = cx + r * math.cos(rad), cy + r * math.sin(rad)
        if side == "right":
            style = f"left:{x:.2f}in;top:{y:.2f}in"
        elif side == "left":                       # text is right-aligned to x
            style = f"left:{x - LC_CALLOUT_IN:.2f}in;top:{y:.2f}in"
        elif side == "bottom-left":
            # JUL's stub points DOWN (195 degrees), not left: hang the block
            # below the wheel like JUN's, mirrored — right-aligned to the stub.
            # Centring it on the stub's height instead, as a "left" callout,
            # lifted half the block back up under the ring, which is what put
            # its month label on the bracket.
            style = f"left:{x - LC_CALLOUT_IN + LC_EDGE_IN:.2f}in;top:{y:.2f}in"
        else:
            # top/bottom text is left-aligned, so start the block at the stub
            # rather than centring the box on it — a centred box puts its visual
            # mass (and its bold month label) to the left of the bracket.
            style = f"left:{x - LC_EDGE_IN:.2f}in;top:{y:.2f}in"
        el = f"lc.{key}"
        tag = f' data-el="{el}"' if os.environ.get("DOCSYNC_EDIT") else ""
        out.append(f'<div class="lc lc-{side}"{tag} style="{L.style(el, style)}">'
                   f'<span class="lc-mo">{lab}</span>{txt}</div>')
    return "".join(out)

def fig1_lifecycle(size=560):
    cx = cy = size / 2
    months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    ramp = ["#EAEFEA", "#D9E3DB", "#C6D6CB", "#B2C7BA", "#9EB8A8", "#89AB94",
            "#6B9E78", "#5C8A70", "#4F7865", "#44645B", "#3A5254", "#2F3E46"]
    out = [f'<svg viewBox="0 0 {size} {size}" class="chart" role="img">']
    r1, r2 = 118, 168

    def polar(r, a):
        rad = math.radians(a - 90)
        return cx + r * math.cos(rad), cy + r * math.sin(rad)
    for i, m in enumerate(months):
        a0 = i * 30
        out.append(f'<path d="{arc_path(cx, cy, r1, r2, a0 + 1, a0 + 29)}" fill="{ramp[i]}"/>')
        ang = a0 + 15
        mid = math.radians(ang - 90)
        tx, ty = cx + 143 * math.cos(mid), cy + 143 * math.sin(mid)
        rot = ang - 180 if 90 < ang < 270 else ang   # tangential, never upside down
        fill = "#fff" if i >= 8 else INK
        # Center the glyphs on (tx,ty) and rotate about that same point: a baseline
        # nudge applied before the rotation gets rotated with it, which threw the
        # flipped labels (APR-SEP, worst at MAY) off-center inside their segments.
        out.append(f'<text x="{tx:.0f}" y="{ty:.0f}" class="mo" fill="{fill}" '
                   f'text-anchor="middle" dominant-baseline="central" '
                   f'transform="rotate({rot:.0f},{tx:.0f},{ty:.0f})">{m}</text>')

    # brackets: arc over the span, inward end ticks, outward stub toward the text
    br = r2 + 10
    for _key, _lab, m0, m1, _txt, _side in LIFECYCLE_SPANS:
        a0, a1 = m0 * 30 + 2, (m1 + 1) * 30 - 2
        amid = (a0 + a1) / 2
        large = 1 if (a1 - a0) > 180 else 0
        x0, y0 = polar(br, a0); x1, y1 = polar(br, a1)
        out.append(f'<path d="M{x0:.1f},{y0:.1f} A{br},{br} 0 {large} 1 {x1:.1f},{y1:.1f}" class="brk"/>')
        for a in (a0, a1):
            ix, iy = polar(br - 6, a); ox, oy = polar(br, a)
            out.append(f'<line x1="{ix:.1f}" y1="{iy:.1f}" x2="{ox:.1f}" y2="{oy:.1f}" class="brk"/>')
        sx, sy = polar(br, amid); ex, ey = polar(br + 13, amid)
        out.append(f'<line x1="{sx:.1f}" y1="{sy:.1f}" x2="{ex:.1f}" y2="{ey:.1f}" class="brk"/>')

    # Arc spans are sized to the label that rides on them: at pr the arc length
    # must exceed the rendered text width, or textPath silently truncates.
    # The key rides along so the label can carry its own slot. A span cannot
    # live in SVG — which is why these were the one kind of text the editor
    # could never touch — but <text> takes a style attribute perfectly well.
    phases = [("process.ring.legislative", 0, 150, DARK),
              ("process.ring.planning", 150, 258, SAGE_LIGHT),
              ("process.ring.prep", 258, 360, MINT)]
    for i, (key, a0, a1, col) in enumerate(phases):
        name = esc(C.text(key))
        out.append(f'<path d="{arc_path(cx, cy, 72, 116, a0 + 1, a1 - 1)}" fill="{col}"/>')
        pr = 100
        # curved phase label
        f0, f1 = (a0 + 3, a1 - 3)
        p0 = (cx + pr * math.cos(math.radians(f0 - 90)), cy + pr * math.sin(math.radians(f0 - 90)))
        p1 = (cx + pr * math.cos(math.radians(f1 - 90)), cy + pr * math.sin(math.radians(f1 - 90)))
        large = 1 if (f1 - f0) > 180 else 0
        flip = 90 < (a0 + a1) / 2 < 270
        d = (f"M{p1[0]:.0f},{p1[1]:.0f} A{pr},{pr} 0 {large} 0 {p0[0]:.0f},{p0[1]:.0f}" if flip
             else f"M{p0[0]:.0f},{p0[1]:.0f} A{pr},{pr} 0 {large} 1 {p1[0]:.0f},{p1[1]:.0f}")
        tc = INK if is_light_bg(col) else "#fff"
        out.append(f'<defs><path id="ph{i}" d="{d}"/></defs>'
                   f'<text class="ph" fill="{tc}"{C.slot_attr(key)}>'
                   f'<textPath href="#ph{i}" startOffset="50%" '
                   f'text-anchor="middle">{name}</textPath></text>')
    out.append("</svg>")
    # Scrolls rather than shrinks on a phone; 12px is this chart's
    # smallest label (.ph), which is what sets how far it may scale
    # down before the wrapper takes over.
    #
    # The wheel is symmetric, so on a phone it is now
    # legible-but-clipped (the wrapper's edge shadows say so)
    # rather than whole at 7.2px. That is the deliberate call:
    # legibility over the at-a-glance whole.
    return chart_scroll("".join(out), smallest_label=12)

def fig6_chart():
    data = [("Lowest 20%", "Less than $21,900", 14.1), ("Second 20%", "$21,900–$44,200", 13.7),
            ("Middle 20%", "$44,200–$80,100", 14.2), ("Fourth 20%", "$80,100–$136,600", 13.4),
            ("Next 15%", "$136,600–$278,200", 11.8), ("Next 4%", "$278,200–$594,900", 10.2),
            ("Top 1%", "Over $594,900", 10.1)]
    # The SVG renders 720u across the 7.26in text column, so 1u = 0.726pt, and
    # the rendered HEIGHT is whatever H is — .chart is width:100%/height:auto.
    # Shrinking the chart therefore means shrinking H, and H is only shrinkable
    # by shortening the bars: everything below BASE is the label block (~68u:
    # quintile, then one or two range lines and their descenders) and everything
    # above the tallest bar is just the value label's ascender.
    #
    # MAXH is the 15% gridline, so every bar scales by the same factor and the
    # comparison the figure exists to make is untouched. 285 -> 230 takes the
    # figure from 3.71in to about 3.2in, with BASE moved up to keep the label
    # block and roughly 10u of headroom over the tallest value label.
    W, H, BW = 720, 320, 80
    BASE, MAXH = 252, 230          # 230u = 167pt tall for the 15% gridline
    gap = (W - 40 - 7 * BW) / 6
    out = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img">']
    for i, (q, rng, v) in enumerate(data):
        x = 20 + i * (BW + gap)
        h = (v / 15) * MAXH
        y = BASE - h
        out.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{BW}" height="{h:.0f}" fill="{SAGE}" '
                   f'class="iv" data-tip="{q} ({rng}): {v}% of income"/>')
        out.append(f'<text x="{x+BW/2:.0f}" y="{y-10:.0f}" text-anchor="middle" class="vlab b">{v}%</text>')
        # quintile name reads as the category; the income range is secondary detail
        out.append(f'<text x="{x+BW/2:.0f}" y="{BASE+24:.0f}" text-anchor="middle" class="qlab">{q}</text>')
        out.append(f'<text x="{x+BW/2:.0f}" y="{BASE+43:.0f}" text-anchor="middle" class="rlab">{rng.split("–")[0] if "–" in rng else rng}</text>')
        if "–" in rng:
            out.append(f'<text x="{x+BW/2:.0f}" y="{BASE+60:.0f}" text-anchor="middle" class="rlab">–{rng.split("–")[1]}</text>')
    out.append(f'<line x1="16" y1="{BASE}" x2="{W-16}" y2="{BASE}" stroke="{INK}" stroke-width="1"/></svg>')
    # Scrolls rather than shrinks on a phone; 14.6px is this chart's
    # smallest label (.rlab), which is what sets how far it may scale
    # down before the wrapper takes over.
    return chart_scroll("".join(out), smallest_label=14.6)

# ---------- figure data (year-parameterized for the FY26/FY27 picker) ----------
FIG3_ORDER = ["Transportation", "Formal Education", "All Others", "Economic Development", "Health"]
# A ramp, not a spread of tints. The old tail — SAGE_LIGHT/MINT/PALE — was three
# near-whites that read as one colour at the bottom of every key. Reaching for the
# darker end of the brand palette instead gives five clearly separable steps.
# Shared by Figures 3, 4 and 5, so this fixes all three keys at once. Slice labels
# reverse to white on their own (is_light_bg in pie()), so darkening is safe.
FIG3_COLORS = [FOREST, DARK, SAGE, SAGE_MID, SAGE_LIGHT]
FIG4_ORDER = ["General Funds", "Special Funds", "Federal Funds", "Other Funds"]

def fig3_slices_for(budget):
    f3 = budget["figure3_cip"]
    return [(k, f3[k], c, [f"${f3[k]/1e6:,.0f}"]) for k, c in zip(FIG3_ORDER, FIG3_COLORS)]

def fig4_slices_for(budget):
    f4 = budget["figure4_means_of_finance"]
    tot = sum(f4.values())
    return [(k, f4[k], c, [f"${f4[k]/1e9:.1f}", f"({f4[k]/tot*100:.1f}%)"])
            for k, c in zip(FIG4_ORDER, FIG3_COLORS)]

def fig5_slices_for(rev):
    get_, iit = rev["General Excise and Use Tax"], rev["Individual Income Tax"]
    tat, corp = rev["Transient Accommodations Tax"], rev["Corporate Income Tax"]
    other_tax = rev["GENERAL FUND TOTAL"] - get_ - iit - tat - corp
    return [(n, v, c, [f"${v/1e9:.2f}", f"({v/rev['GENERAL FUND TOTAL']*100:.1f}%)"])
            for (n, v), c in zip(
        [("General Excise Tax", get_), ("Individual Income Tax", iit),
         ("Transient Accommodations Tax", tat), ("All Other Taxes", other_tax),
         ("Corporate Income Tax", corp)], FIG3_COLORS)]

def cip_total_for(budget):
    return sum(budget["figure3_cip"].values())

# ---------- year-picker plumbing (Figures 2/3/4/5 + Table 1) ----------
def fy_picker(fig_id, label27="FY2027", label26="FY2026"):
    """Inline FY selector that replaces a hard-coded year in a figure caption."""
    return (f'<select class="fy-pick" data-fig="{fig_id}" aria-label="Fiscal year">'
            f'<option value="2027">{label27}</option>'
            f'<option value="2026">{label26}</option></select>')

def fy_pie_swap(fig_id, slices27, slices26, **kw):
    """Both years' pies as sibling flex items; FY2026 starts hidden."""
    return (pie(slices27, attrs=f' data-fig="{fig_id}" data-fy="2027"', **kw)
            + pie(slices26, attrs=f' data-fig="{fig_id}" data-fy="2026" hidden', **kw))

# ---------- page shells ----------
# Header glyphs for the one-time / emergency appropriation tiles (page 8): a
# warning triangle and a medical cross, echoing the printed primer. Fill is
# currentColor so they inherit the tile's text colour and flip white->charcoal
# with .card.light exactly as the title does. The triangle's exclamation is an
# even-odd cutout, so whatever colour the tile is recoloured to shows through.
WARNING_ICON = ('<svg viewBox="0 0 24 24" fill="currentColor" fill-rule="evenodd" '
                'aria-hidden="true"><path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4'
                'h-2v-4h2v4z"/></svg>')
CROSS_ICON = ('<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">'
              '<path d="M9 3h6v6h6v6h-6v6H9v-6H3V9h6z"/></svg>')

# Page 5's three spending-type tiles — the one card row that carried no icon
# while pages 8, 9 and 10 all did. GEAR for the operating budget: the machinery
# of government running day to day. SHOVEL for capital improvements, which are
# construction and nothing else. The third reuses WARNING_ICON, the same glyph
# page 8 gives One-Time & Emergency, so the two places that name that category
# are visibly the same thing rather than two unrelated symbols.
# The gear's teeth are separate rects UNDER one annulus path; the hole is
# evenodd within that single path, because fill-rule cannot punch one element
# through another.
OPERATING_ICON = (
    '<svg viewBox="0 0 32 32" fill="currentColor" fill-rule="evenodd" aria-hidden="true">'
    '<rect x="14.2" y="0.8" width="3.6" height="7" rx="1.2"/>'
    '<rect x="14.2" y="24.2" width="3.6" height="7" rx="1.2"/>'
    '<rect x="0.8" y="14.2" width="7" height="3.6" rx="1.2"/>'
    '<rect x="24.2" y="14.2" width="7" height="3.6" rx="1.2"/>'
    '<rect x="14.2" y="0.8" width="3.6" height="7" rx="1.2" transform="rotate(45 16 16)"/>'
    '<rect x="14.2" y="24.2" width="3.6" height="7" rx="1.2" transform="rotate(45 16 16)"/>'
    '<rect x="0.8" y="14.2" width="7" height="3.6" rx="1.2" transform="rotate(45 16 16)"/>'
    '<rect x="24.2" y="14.2" width="7" height="3.6" rx="1.2" transform="rotate(45 16 16)"/>'
    '<path d="M16 4.6a11.4 11.4 0 1 0 0 22.8 11.4 11.4 0 0 0 0-22.8z'
    'm0 7a4.4 4.4 0 1 1 0 8.8 4.4 4.4 0 0 1 0-8.8z"/>'
    '</svg>')
CAPITAL_ICON = (
    '<svg viewBox="0 0 32 32" fill="currentColor" aria-hidden="true">'
    '<g transform="rotate(-45 16 16)">'
    '<rect x="11.6" y="2.6" width="8.8" height="3.4" rx="1.7"/>'
    '<rect x="14.4" y="5.4" width="3.2" height="12.4"/>'
    '<path d="M10.1 17h11.8l-1.3 5a4.9 4.9 0 0 1-9.2 0z"/>'
    '</g></svg>')

# Page 9's fund tiles, in the same icon-left-of-title lockup page 8 uses.
# GENERAL — a stack of coins: the main pot, spendable on almost anything.
# SPECIAL — a padlock: money that may only be spent on one stated purpose.
# FEDERAL — a columned government building: where the grants come from.
GENERAL_FUNDS_ICON = (
    '<svg viewBox="0 0 32 32" fill="currentColor" aria-hidden="true">'
    '<ellipse cx="16" cy="8.2" rx="10" ry="3.9"/>'
    '<path d="M6 11.6c2.2 1.9 6 2.9 10 2.9s7.8-1 10-2.9V15c0 2.2-4.5 3.9-10 3.9S6 17.2 6 15z"/>'
    '<path d="M6 18.8c2.2 1.9 6 2.9 10 2.9s7.8-1 10-2.9v3.4c0 2.2-4.5 3.9-10 3.9S6 24.4 6 22.2z"/>'
    '</svg>')
SPECIAL_FUNDS_ICON = (
    '<svg viewBox="0 0 32 32" fill="currentColor" aria-hidden="true">'
    '<path d="M9.2 14V9.4a6.8 6.8 0 0 1 13.6 0V14h-3.5V9.4a3.3 3.3 0 0 0-6.6 0V14z"/>'
    '<path fill-rule="evenodd" d="M7 15.4h18a1.8 1.8 0 0 1 1.8 1.8v11a1.8 1.8 0 0 1-1.8 1.8H7'
    'a1.8 1.8 0 0 1-1.8-1.8v-11A1.8 1.8 0 0 1 7 15.4zm9 3.6a2.3 2.3 0 0 0-1.2 4.3l-.7 3.5h3.8'
    'l-.7-3.5A2.3 2.3 0 0 0 16 19z"/>'
    '</svg>')
FEDERAL_FUNDS_ICON = (
    '<svg viewBox="0 0 32 32" fill="currentColor" aria-hidden="true">'
    '<path d="M16 2.4 30 9.8V12H2V9.8z"/>'
    '<path d="M5 13.8h3.6v11.4H5zm6.6 0h3.6v11.4h-3.6zm6.6 0H21v11.4h-2.8zm6.6 0H27v11.4h-3.6z"/>'
    '<path d="M3 26.6h26v3.2H3z"/>'
    '</svg>')

# Page 10's tax tiles. Same rules as the two above: currentColor so they flip
# with the tile's text colour, and every interior detail is an even-odd CUTOUT
# (never a white shape) so a recoloured tile shows through the holes.
# GET — a grocery bag, with banknotes either side: the tax lands on everyday
# purchases, which is the point the tile's text makes.
GET_ICON = (
    '<svg viewBox="0 0 32 32" fill="currentColor" aria-hidden="true">'
    # groceries first, so the folded rim below paints over their base and they
    # read as standing IN the bag. They are spaced apart on purpose: at this
    # size two same-coloured shapes that touch merge into one blob.
    # A baguette and a bottle, standing well clear of the bag. Two tall, clearly
    # food-shaped silhouettes with a wide gap read better at 34px than three
    # crowded ones, which merge into a single blob.
    '<rect x="8.4" y="1.4" width="3.4" height="12.4" rx="1.7" transform="rotate(-11 10.1 7.6)"/>'
    '<path d="M16.8 3h2.6v2.6l1.5 2.2v6h-5.6v-6l1.5-2.2z"/>'
    # The bag carries a punched-out handle. Every handle-less version of this
    # read as a bucket or a flowerpot no matter how the rim was drawn — the
    # handle is the one shape that says "bag" on its own.
    '<path fill-rule="evenodd" d="M8.2 13.5h15.6a1.6 1.6 0 0 1 1.6 1.6v13.3a1.6 1.6 0 0 1-1.6 1.6H8.2'
    'a1.6 1.6 0 0 1-1.6-1.6V15.1a1.6 1.6 0 0 1 1.6-1.6zm3.6 3.2a4.2 4.2 0 0 0 8.4 0h-1.7'
    'a2.5 2.5 0 0 1-5 0z"/>'
    # banknotes flanking the bag, clear of it on both sides
    '<g fill-rule="evenodd">'
    '<path transform="rotate(-14 3 22)" d="M.4 20.2h5.2v3.6H.4zm2.6.8a1 1 0 1 0 0 2 1 1 0 0 0 0-2z"/>'
    '<path transform="rotate(14 29 22)" d="M26.4 20.2h5.2v3.6h-5.2zm2.6.8a1 1 0 1 0 0 2 1 1 0 0 0 0-2z"/>'
    '</g></svg>')
# IIT — bars stepping up under a rising arrow: higher incomes, higher rate.
IIT_ICON = (
    '<svg viewBox="0 0 32 32" fill="currentColor" aria-hidden="true">'
    # Bars stop at y=14 and the arrow runs above them: an arrow crossing the
    # tallest bar reads as a notch cut out of it, not as a trend line.
    '<path d="M3 24h6.4V30H3zm9.8-5h6.4v11h-6.4zm9.8-5H29v16h-6.4z"/>'
    '<path d="M3.5 12.8 24.5 3.8l1.1 2.5-21 9zM29.6 3 23.8 2.2l2.4 5.6z"/>'
    '</svg>')
# TAT — a hotel with a flag: the tile's own example, and the one readers picture.
TAT_ICON = (
    '<svg viewBox="0 0 32 32" fill="currentColor" aria-hidden="true">'
    # the hotel, flag and all, ending above the luggage line
    '<path d="M14.8.9h1.8v3.4h-1.8z"/><path d="m16.6 1.3 6 1.8-6 1.8z"/>'
    '<path fill-rule="evenodd" d="M6.4 4.3h19.2V19H6.4zm3 3.1v3.2h3.4V7.4zm5.9 0v3.2h3.4V7.4z'
    'm5.9 0v3.2h3.4V7.4zm-11.8 5.9v3.2h3.4v-3.2zm5.9 0v3.2h3.4v-3.2zm5.9 0v3.2h3.4v-3.2z"/>'
    # two suitcases at its base — separated by open gaps rather than outlines,
    # so they stay distinct whatever colour the tile is recoloured to
    '<path d="M7.5 21.6a2.9 2.9 0 0 1 5.8 0h-1.5a1.4 1.4 0 0 0-2.8 0z"/>'
    '<path fill-rule="evenodd" d="M4.3 21.9h12.2a1.4 1.4 0 0 1 1.4 1.4v5.3a1.4 1.4 0 0 1-1.4 1.4'
    'H4.3a1.4 1.4 0 0 1-1.4-1.4v-5.3a1.4 1.4 0 0 1 1.4-1.4zm-1.4 2.7v1.5h15v-1.5z"/>'
    '<path d="M20.6 23.4a2.5 2.5 0 0 1 5 0h-1.3a1.2 1.2 0 0 0-2.4 0z"/>'
    '<path fill-rule="evenodd" d="M19.6 23.7h9.5a1.3 1.3 0 0 1 1.3 1.3v3.6a1.3 1.3 0 0 1-1.3 1.3'
    'h-9.5a1.3 1.3 0 0 1-1.3-1.3V25a1.3 1.3 0 0 1 1.3-1.3zm-1.3 2.4v1.4h12.1v-1.4z"/>'
    '</svg>')


def card(title, bullets, bg, light=None, key="", icon="", icon_id="", detachable=False, min_h=None):
    """One tile. The bullets' key names it: it is already unique per card, and a
    second name for the same thing is a second thing to keep in step.

    The card rows are CSS grid, so a moved card leaves a cell its neighbours
    slide into. The spacer holds that cell — an empty grid item auto-places
    into it and the column width comes from the track, so no measurement is
    involved. The override merges into the tile's own style attribute; two
    style attributes would silently drop one.
    """
    el_id = f"card.{key}" if key else ""
    if el_id and L.refilled(el_id):
        bg = L.fill(el_id)
        # A caller passing light=True judged the colour it chose. Recolour the
        # tile and that judgment is about a colour that is no longer there —
        # keeping it puts charcoal text on a dark tile, or white on a pale one.
        # The luminance test must run again, on the colour actually being used.
        light = None
    if light is None:                       # auto: dark text on light tiles
        light = is_light_bg(fill_repr(bg))
    cls = "card light" if light else "card"
    lis = "".join(f"<li>{b}</li>" for b in bullets)
    ul = C.ul_attr(key) if key else ""
    override = L.style(el_id, "") if el_id else ""
    style = f"background:{fill_css(bg)}" + (f";{override}" if override else "")
    # A detachable tile keeps a floor height so it stays a visible panel after
    # its title and bullets are dragged out of it (an empty div would collapse
    # to nothing). In the flow, with the text still inside, the content is
    # already this tall, so it changes nothing until something is pulled out.
    if detachable and min_h:
        style += f";min-height:{min_h}in"
    tag = (L.tag(el_id) + L.fill_tag(el_id)) if el_id else ""
    # An icon, when given, sits left of the title in a flex header. Passing an
    # icon_id makes it a graphic() — a movable/resizable glyph the user can drag
    # out of the header and scale — while a plain icon (no id) stays a fixed
    # header glyph. Without any icon the heading is byte-for-byte what it always
    # was, so every other card is untouched.
    if icon:
        ico = (graphic(icon_id, icon, w=0.42, cls="card-ico")
               if icon_id else f'<span class="card-ico">{icon}</span>')
        h4_inner, h4_cls = ico + title, "card-ico-h"
    else:
        h4_inner, h4_cls = title, ""

    # A detachable card renders its title and bullets as their own movable
    # objects (data-el + ds-detachable) that START laid out inside the tile but
    # can be pulled out and placed anywhere — the pieces the user wanted to
    # separate. The TEXT still comes from content.md (title is C.t's slot span;
    # the <ul> keeps its data-slot), so it stays editable prose. The position
    # hook goes on the h4 / a wrapping div, never on the same tag as the slot —
    # two style attributes silently drop one (why the <ul> is wrapped, not
    # tagged directly). A default group (seeded in layout.json) keeps the three
    # moving as one until the user Ungroups.
    if detachable and key:
        base = key[:-len(".bullets")] if key.endswith(".bullets") else key
        title_id = base + ".title"
        tcls = (h4_cls + " ds-detachable").strip()
        head = f'{L.spacer(title_id)}<h4 class="{tcls}"{L.attr(title_id)}>{h4_inner}</h4>'
        bullets_el = (f'{L.spacer(key)}<div class="ds-detachable"{L.attr(key)}>'
                      f'<ul{ul}>{lis}</ul></div>')
    else:
        head = f'<h4 class="{h4_cls}">{h4_inner}</h4>' if h4_cls else f'<h4>{h4_inner}</h4>'
        bullets_el = f'<ul{ul}>{lis}</ul>'
    return (f'{L.spacer(el_id) if el_id else ""}'
            f'<div class="{cls}"{tag} style="{style}">'
            f'{head}{bullets_el}</div>')


def reform(n, key, accent, bg=MINT):
    """One numbered tax-reform panel (page 12).

    Same contract as card(): a movable, recolourable surface whose text stays
    in content.md. `accent` paints the number badge — the panel's own
    furniture, so a recolour of the panel background leaves it alone; only the
    body text has to re-decide its contrast, which is_light_bg does on
    whatever colour is actually in use.

    The number is furniture too, not prose: it says which of three this is, so
    it is generated rather than typed into a slot someone could renumber out
    of step with its neighbours.
    """
    el_id = f"reform.{key}"
    if L.refilled(el_id):
        bg = L.fill(el_id)
    light = is_light_bg(fill_repr(bg))
    cls = "reform" if light else "reform ondark"
    base = f"taxfair.items.{key}"
    style = f"background:{fill_css(bg)}"
    override = L.style(el_id, "")
    if override:
        style += f";{override}"
    return (
        f'{L.spacer(el_id)}<div class="{cls}"{L.tag(el_id)}{L.fill_tag(el_id)} style="{style}">'
        f'<div class="reform-head">'
        f'<span class="reform-num" style="background:{accent}">{n}</span>'
        f'<h4>{C.t(f"{base}.title")}</h4>'
        f'<span class="reform-tag">{C.t(f"{base}.tag")}</span>'
        f'</div>'
        f'{C.html(f"{base}.body")}'
        f'</div>')


def graphic(el_id, svg, w=1.5, cls=""):
    """A free-standing SVG the editor can move/resize/rotate; placement lives
    in layout.json under el_id. One implementation for every report now —
    docsync.blocks.graphic — this wrapper just supplies this report's Layout.
    (See blocks.py for the full contract: unique stable el_id, viewBox
    required, `w` is the default width until the user resizes.)"""
    from docsync.blocks import graphic as _graphic
    return _graphic(L, el_id, svg, w=w, cls=cls)


def img_el(el_id, cls, src, alt):
    """One image element, honouring replace/radius/filter/crop overrides.

    Untouched, this emits exactly the literal it replaced — attribute order is
    part of the bytes. A cropped image grows an overflow:hidden wrapper that
    carries the element id and the window's size, with the full image drawn
    inside at absolute inches (see Layout.cropped for why no pixel sizes).
    """
    src = L.img_src(el_id, src)
    css = L.img_css(el_id)
    crop = L.cropped(el_id)
    head = f'<img class="{cls}"' if cls else "<img"
    if not crop:
        return f'{head}{L.attr(el_id, css)} src="{src}" alt="{alt}">'
    inner = (f'<img src="{src}" alt="{alt}" style="position:absolute;'
             f'left:-{crop["dx"]}in;top:-{crop["dy"]}in;width:{crop["imgW"]}in;'
             f'max-width:none">')
    wcls = f"ds-cropw {cls}".strip()
    return f'<span class="{wcls}"{L.attr(el_id, css)}>{inner}</span>'


def callout_open(key):
    """The opening tag of a callout, honouring a recolour.

    Same contract as card(): the CSS paints it dark with white text, so a new
    colour must bring its own contrast decision — the class flips to onlight
    when the luminance test says so, and the footnote pills ride that class.
    Unfilled, this emits exactly the literal it replaced.
    """
    cid = f"callout.{key}"
    cls, bg = "callout", ""
    if L.refilled(cid):
        c = L.fill(cid)
        cls = "callout onlight" if is_light_bg(fill_repr(c)) else "callout"
        bg = f"background:{fill_css(c)}"
    return f'{L.spacer(cid)} <div class="{cls}"{L.attr(cid, bg)}{L.fill_tag(cid)}>'


def svg_img(el_id, src, cls, alt):
    """An <img> whose SVG can be recoloured from the editor.

    Unrecoloured it is exactly the <img> tag it always was — byte-identical
    published output. Once the editor sets layout.fill[el_id], the asset file
    is INLINED with every painted fill swapped to currentColor and the chosen
    colour on the wrapper, because no colour can cross an <img> boundary: the
    file is a separate document there, styled only by its own bytes.

    The asset is read from disk, which works everywhere this renders: the PDF
    build has the checkout, and docsync.yml stages the logo files into the
    editor's Pyodide filesystem. A monochrome mark recolours faithfully; the
    guard against surprises is check_icon_svg, the same gate icons pass — an
    asset that fails it (or is missing) falls back to the plain <img> rather
    than half-rendering.
    """
    plain = f'<img class="{cls}" src="{src}" alt="{alt}">'
    if not L.refilled(el_id):
        return plain
    try:
        raw = (HERE / "web" / src).read_text()
    except OSError:
        return plain
    m = re.search(r"<svg\b[^>]*>", raw)
    vb = re.search(r'viewBox="([^"]+)"', raw)
    if not m or not vb:
        return plain
    inner = raw[m.end():raw.rfind("</svg>")]
    try:
        check_icon_svg(inner, el_id)
    except LayoutError:
        return plain
    inner = re.sub(r'fill="(?!none)[^"]*"', 'fill="currentColor"', inner)
    color = icon_color(L.fill(el_id))
    wh = re.search(r'width="([\d.]+)"\s+height="([\d.]+)"', raw)
    size = f' width="{wh.group(1)}" height="{wh.group(2)}"' if wh else ""
    return (f'<svg class="{cls}" viewBox="{vb.group(1)}"{size} role="img" '
            f'style="color:{color}" aria-label="{alt}">{inner}</svg>')


def endnote_link(n, sid, txt, url):
    # data-el makes the entry draggable; dragging one REORDERS the list (see
    # Layout.endnote_order and the editor's reorderEndnote) rather than
    # parking it somewhere absolute, so no spacer is needed — an endnote
    # never leaves the flow. Keyed by source id, not by n: the number is
    # whatever the current order says, but the identity has to stay put.
    return (f'<li id="en{n}"{L.attr(f"endnote.{sid}")}>{txt} '
            f'<a href="{url}">{url}</a></li>')

ONE_TIME_BULLETS = C.list("onetime.cards.onetime.bullets")
EMERG_BULLETS = C.list("onetime.cards.emergency.bullets")

def table1_for(year):
    b = BRANCHES_BY_FY[year]
    e, j, l, o = b["exec"], b["jud"], b["leg"], b["oha"]
    def tot(c):
        return c["op"] + c["cip"] + c["one"] + c["emerg"]
    hidden = "" if year == 2027 else " hidden"
    # Recolours ride CSS variables on the table: the header is five cells and
    # the table has an FY2026 twin, so painting cells directly would mean
    # keeping ten of them in step. primer.css reads the vars with the designed
    # colours as fallback; text colour re-decides from the new luminance.
    t1 = []
    if L.refilled("table1.head"):
        c = L.fill("table1.head")
        t1.append(f"--th-bg:{fill_css(c)};--th-tc:{INK if is_light_bg(fill_repr(c)) else '#fff'}")
    if L.refilled("table1.total"):
        c = L.fill("table1.total")
        t1.append(f"--tot-bg:{fill_css(c)};--tot-tc:{INK if is_light_bg(fill_repr(c)) else '#fff'}")
    t1s = f' style="{";".join(t1)}"' if t1 else ""
    return f"""<table class="t1" data-fig="table1" data-fy="{year}"{hidden}{t1s}>
<thead><tr{L.fill_tag("table1.head")}><th></th><th class="lk" data-dept="__all"{C.slot_attr("table1.header.executive")}>{C("table1.header.executive")}</th><th{C.slot_attr("table1.header.judiciary")}>{C("table1.header.judiciary")}</th>
<th{C.slot_attr("table1.header.legislature")}>{C("table1.header.legislature")}</th><th{C.slot_attr("table1.header.oha")}>{C("table1.header.oha")}</th></tr></thead>
<tbody>
<tr><td>Operating Budget</td><td>{words(e['op'])}</td><td>{words(j['op'])}</td>
<td>{words(l['op'])}</td><td>{words(o['op'])}</td></tr>
<tr><td>Capital Improvement Appropriations</td><td>{words(e['cip'])}</td>
<td>{words(j['cip'])}</td><td>{words(l['cip'])}</td><td>{words(o['cip'])}</td></tr>
<tr><td>One-Time Appropriations</td><td>{words(e['one'])}</td><td>{words(j['one'])}</td>
<td>{words(l['one'])}</td><td>{words(o['one'])}</td></tr>
<tr><td>Emergency Appropriations</td><td>{words(e['emerg'])}</td><td>{words(j['emerg'])}</td>
<td>{words(l['emerg'])}</td><td>{words(o['emerg'])}</td></tr>
<tr class="total"{L.fill_tag("table1.total")}><td>Total</td>
<td>{words(tot(e))}</td><td>{words(tot(j))}</td>
<td>{words(tot(l))}</td><td>{words(tot(o))}</td></tr>
</tbody></table>"""

# Pages that accept overflow slots: a [[extra.<page>.<slug>]] key in content.md
# renders automatically at the end of that page — new prose sections can be
# added from the Google Doc alone, with no change to this file.
EXTRA_PAGES = ["basics", "process", "spent", "categories", "cip",
               "onetime", "funding", "taxes", "whopays"]

# ---------- page order ----------
# Pages are built by IDENTITY (their born ordinals 1–12) and assembled in the
# order layout.json asks for — default 1..12, so an empty override reproduces
# the report byte for byte. Everything page-keyed (shapes, text boxes, fills)
# stays keyed by identity, so reordering a page carries its own work with it.
#
# The folio and the TOC print a page's POSITION, not its identity — move a page
# and its number follows. The markup lives here, not in docsync, because "•
# BUDGET PRIMER" and the left/right alternation are this report's, not the
# engine's; docsync only computes the order.
DESIGNED_PAGES = 12
PAGE_ORDER = L.page_order(DESIGNED_PAGES)
PAGE_POS = {pid: i + 1 for i, pid in enumerate(PAGE_ORDER)}

# The editor's page rail needs the full set of designed pages and their names —
# including any currently hidden, which are absent from the rendered DOM. The
# renderer teaches it the same way it teaches everything else: a stamp emitted
# only in edit mode, so the published bytes never carry it. Labels are this
# report's business, hence here and not in docsync.
PAGE_LABELS = {1: "Cover", 2: "Contents", 3: "Budget Basics", 4: "Budget Process",
               5: "How Money Is Spent", 6: "Spending Categories",
               7: "Capital, Fixed Costs & Appropriations",
               8: "Funding the Budget", 9: "Taxes", 10: "Who Pays",
               11: "Tax Credits", 12: "Endnotes"}

def stamp_page(html, pid):
    """Tag a section with its identity, edit mode only (like data-el)."""
    if not os.environ.get("DOCSYNC_EDIT"):
        return html
    return html.replace('<section class="page', f'<section data-page="{pid}" class="page', 1)

def folio(pid):
    """The page's footer, numbered by its position. Empty for a hidden page:
    its f-string is still built, but it is never assembled."""
    n = PAGE_POS.get(pid)
    if n is None:
        return ""
    if n % 2 == 0:
        return f'<div class="folio">{n} • BUDGET PRIMER</div>'
    return f'<div class="folio r">BUDGET PRIMER • {n}</div>'

def pageno(pid):
    """A page's printed number for the table of contents. A dash if it was
    hidden — better an honest gap than a number pointing nowhere."""
    return PAGE_POS.get(pid, "—")

def blank_page(bid):
    """An empty page a blank id names — a surface for boxes, shapes and images.
    It only exists when the id appears in the order, so byte-identity holds."""
    return (f'\n<section class="page ds-blank">\n '
            f'{L.layer(bid)}{L.text_boxes(bid)}{L.tables_html(bid)}{folio(bid)}\n</section>')

pages = []

# -- page 1: cover
# The title's stacked lines are one editable slot: kept as <br> breaks so the
# design is untouched, but data-slot lets the editor edit all three lines.
# Movable like any section heading (see basics.h1 below): the <h1>/<div>
# itself carries L.attr (the drag/resize hook), a nested element carries the
# text-edit data-slot — two style-bearing attributes on one element would
# silently keep only the first (Layout.attr()'s own docstring warns of this).
# The eight shapes of the cover ribbon frame (geometry lives in .ribbon/.r1–.r8
# in primer.css, ported from the FY2025–26 cover). Emitted from one place so the
# front and back covers can never drift apart.
RIBBONS = "".join(
    f'<div class="ribbon {tone} r{i}"></div>' for i, tone in enumerate(
        ["rb-dark", "rb-dark", "rb-pale", "rb-pale",     # top
         "rb-dark", "rb-dark", "rb-pale", "rb-pale"], 1))  # bottom

_cover_title = "<br>".join(esc(l) for l in C.lines("cover.title"))
pages.append(f"""
<section class="page cover"{L.fill_attr(f"page.1")}>
 {L.layer(1)}{L.text_boxes(1)}{L.tables_html(1)}{RIBBONS}
 <div class="cover-inner">
  {L.spacer("cover.logo")}<div class="logo-lockup"{L.attr("cover.logo")}>{svg_img("cover.logo", "assets/appleseed-logo.svg", "logo-img", "Hawaiʻi Appleseed — Center for Law &amp; Economic Justice")}</div>
  {L.spacer("cover.title")}<h1 class="cover-title"{L.attr("cover.title")}>{C.slot_span("cover.title", _cover_title)}</h1>
  {L.spacer("cover.year")}<div class="cover-year"{L.attr("cover.year")}>{C.t("cover.year")}</div>
 </div>
</section>""")

# -- page 2: about / TOC
pages.append(f"""
<section class="page toc-page"{L.fill_attr(f"page.2")}>
 <div class="toc-head">
  {L.spacer("toc.logo")}<div class="logo-lockup light"{L.attr("toc.logo")}>{svg_img("toc.logo", "assets/appleseed-logo-white.svg", "logo-img", "Hawaiʻi Appleseed — Center for Law &amp; Economic Justice")}</div>
  <p class="toc-link"><a href="https://hiappleseed.org">www.hiappleseed.org</a></p>
  {L.spacer("toc.author")}<p class="toc-author"{L.attr("toc.author")}>{C.t("toc.author")}</p>
 </div>
 {C.html("toc.mission1", "mission")}
 {C.html("toc.mission2", "mission")}
 {L.spacer("toc.title")}<h2 class="toc-title"{L.attr("toc.title")}>{C.t("toc.title")}</h2>
 <div class="toc-list">
  <div><span>Budget Basics</span><span>{pageno(3)}</span></div>
  <div><span>How Money Is Spent</span><span>{pageno(5)}</span></div>
  <div><span>Funding the Budget</span><span>{pageno(8)}</span></div>
  <div><span>Endnotes</span><span>{pageno(12)}</span></div>
 </div>
 {L.spacer("toc.copyright")}<p class="copyright"{L.attr("toc.copyright")}>{C.slot_span("toc.copyright", "<br>".join(esc(l) for l in C.lines("toc.copyright")))}</p>
 {L.layer(2)}{L.text_boxes(2)}{L.tables_html(2)}{folio(2)}
</section>""")

# -- page 3: budget basics
branch_cards = [
    # the key rides along: the <ul> and the img's alt both need it, and alt
    # cannot take the tagged span C.t() returns in edit mode
    (C.t(f"basics.branch.{k}.title"), bg, img, lt, C.list(f"basics.branch.{k}.bullets"), k)
    for k, bg, img, lt in [
        ("legislature", DARK, "branch-0.jpg", True),
        ("judiciary", SAGE_MID, "branch-1.jpg", False),
        ("executive", SAGE_LIGHT, "branch-2.jpg", False),
        ("oha", MINT, "branch-3.jpg", False),
    ]
]
bc = "".join(
    f'<div class="branch">{L.spacer(f"branch.photo.{k}")}{img_el(f"branch.photo.{k}", "", f"assets/{img}", esc(C.text(f"basics.branch.{k}.title")))}'
    f'<div class="branch-card{" onlight" if is_light_bg(fill_repr(L.fill(f"branch.{k}", bg))) else ""}"'
    f'{L.tag(f"branch.{k}")}{L.fill_tag(f"branch.{k}")} style="background:{fill_css(L.fill(f"branch.{k}", bg))}">'
    f'<h4>{t}</h4><ul{C.ul_attr(f"basics.branch.{k}.bullets")}>' + "".join(f"<li>{b}</li>" for b in bl) + "</ul></div></div>"
    for t, bg, img, lt, bl, k in branch_cards)
pages.append(f"""
<section class="page"{L.fill_attr(f"page.3")}>
 {L.spacer("basics.h1")}<h1{L.attr("basics.h1")}>{C.t("basics.h1")}</h1>
 {C.html("basics.p1")}
 {C.html("basics.p2")}
 {bc}
{C.extras("basics")} {L.layer(3)}{L.text_boxes(3)}{L.tables_html(3)}{folio(3)}
</section>""")

# -- page 4: budget process
pages.append(f"""
<section class="page"{L.fill_attr(f"page.4")}>
 {L.spacer("process.h2")}<h2 class="sub"{L.attr("process.h2")}>{C.t("process.h2")}</h2>
 {C.html("process.p1")}
 {L.spacer("process.fig1.caption")}<p class="figcap"{L.attr("process.fig1.caption")}><b>Figure 1.</b> {C.t("process.fig1.caption")}</p>
 <div class="lifecycle-wrap">
  {fig1_lifecycle()}
  {lifecycle_callouts()}
 </div>
{C.extras("process")} {L.layer(4)}{L.text_boxes(4)}{L.tables_html(4)}{folio(4)}
</section>""")

# -- page 5: how money is spent
pages.append(f"""
<section class="page"{L.fill_attr(f"page.5")}>
 {L.spacer("spent.h1")}<h1{L.attr("spent.h1")}>{C.t("spent.h1")}</h1>
 {C.html("spent.p1")}
 {C.html("spent.p2")}
 <div class="cards3">
  {card(C.t("spent.cards.operating.title"), C.list("spent.cards.operating.bullets"), DARK, key="spent.cards.operating.bullets", icon=OPERATING_ICON, icon_id="spent.cards.operating.icon", detachable=True, min_h=1.83)}
  {card(C.t("spent.cards.capital.title"), C.list("spent.cards.capital.bullets"), SAGE_MID, key="spent.cards.capital.bullets", icon=CAPITAL_ICON, icon_id="spent.cards.capital.icon")}
  {card(C.t("spent.cards.onetime.title", esc=True), C.list("spent.cards.onetime.bullets"), SAGE_LIGHT, light=True, key="spent.cards.onetime.bullets", icon=WARNING_ICON, icon_id="spent.cards.onetime.icon")}
 </div>
 {C.html("spent.p3")}
 {L.spacer("spent.table1.caption")}<p class="figcap"{L.attr("spent.table1.caption")}><b>Table 1.</b> {C.t("spent.table1.caption")} {fy_picker("table1", FY_LABEL[2027], FY_LABEL[2026])}</p>
 {table1_for(2027)}
 {table1_for(2026)}
{C.extras("spent")} {L.layer(5)}{L.text_boxes(5)}{L.tables_html(5)}{folio(5)}
</section>""")

# -- page 6: figure 2
pages.append(f"""
<section class="page"{L.fill_attr(f"page.6")}>
 {L.spacer("categories.h2")}<h2 class="sub"{L.attr("categories.h2")}>{C.t("categories.h2")}</h2>
 {L.spacer("categories.h3")}<h3 class="sub2"{L.attr("categories.h3")}>{C.t("categories.h3")}</h3>
 {L.spacer("categories.fig2.caption")}<p class="figcap"{L.attr("categories.fig2.caption")}><b>Figure 2.</b> {C.t("categories.fig2.caption")} {fy_picker("fig2")}
 <span class="noprint figcap-hint">{C.t("categories.fig2.hint", esc=True)}</span></p>
 {fig2_chart_for(2027)}
 {fig2_chart_for(2026)}
 {legend([(C.t("categories.legend.operating", esc=True), SAGE),
          (C.t("categories.legend.capital", esc=True), SAGE_MID),
          (C.t("categories.legend.onetime", esc=True), DARK),
          (C.t("categories.legend.emergency", esc=True), DARKEST)])}
 {C.html("categories.fig2.note", cls="fig-note")}
 <div class="explore noprint">{C.t("categories.explore")}
  <a href="{TRACKER}#/enacted" target="_blank" rel="noopener">{C.t("categories.explore.link").replace(" →", "&nbsp;→")}</a></div>
 {C.html("categories.p1")}
{C.extras("categories")} {L.layer(6)}{L.text_boxes(6)}{L.tables_html(6)}{folio(6)}
</section>""")

# -- page 7: obligated costs + fig 3
pages.append(f"""
<section class="page"{L.fill_attr(f"page.7")}>
{callout_open("obligated")}
  <h4>{C.t("obligated.title")}</h4>
  {C.html("obligated.p1")}
 </div>
 <details class="obligated noprint">
  <summary>{C.t("obligated.summary")}</summary>
  <div class="obligated-panel">
   <p class="figcap"{C.slot_attr("obligated.panel.caption")}>{C("obligated.panel.caption")}<span class="noprint">
   {C.t("obligated.panel.hint")}</span></p>
   {fig_obligated()}
   {legend([(esc(n), c) for n, _k, c in OBLIG_BANDS])}
   {C.html("obligated.p2")}
   <p class="obligated-note">{C("obligated.panel.note").format(oblig_first=f"${OBLIG['series']['2018']['_printed_subtotal']/1e9:.2f}", oblig_last=f"${OBLIG['series']['2027']['_printed_subtotal']/1e9:.2f}")}</p>
  </div>
 </details>
 {L.spacer("cip.h3")}<h3 class="sub2"{L.attr("cip.h3")}>{C.t("cip.h3")}</h3>
 {L.spacer("cip.fig3.caption")}<p class="figcap"{L.attr("cip.fig3.caption")}><b>Figure 3.</b> {C.t("cip.fig3.caption")} {fy_picker("fig3")} ($Millions)</p>
 <div class="pie-row">{fy_pie_swap("fig3", fig3_slices_for(BUD), fig3_slices_for(BUD26), cls="pie-cip", width_in=4.05, label_pt=13.7)}{legend([(esc(n), c) for n, c in zip(FIG3_ORDER, FIG3_COLORS)])}</div>
 <p data-fig="fig3" data-fy="2027"{C.slot_attr("cip.body")}>{C("cip.body").format(fy=2027, cip_total=words(cip_total_for(BUD)))}</p>
 <p data-fig="fig3" data-fy="2026" hidden{C.slot_attr("cip.body")}>{C("cip.body").format(fy=2026, cip_total=words(cip_total_for(BUD26)))}</p>
{L.spacer("onetime.h3")}<h3 class="sub2"{L.attr("onetime.h3")}>{C.t("onetime.h3")}</h3>
 <ul class="approp-brief">{"".join(f"<li>{b}</li>" for b in ONE_TIME_BULLETS[:3])}</ul>
 {C.html("onetime.brief", "approp-note")}
 <details class="obligated approp noprint">
  <summary>{C.t("onetime.summary")}</summary>
  <div class="cards2">
   {card(C.t("onetime.cards.onetime.title"), ONE_TIME_BULLETS, DARK, key="onetime.cards.onetime.bullets", icon=WARNING_ICON, icon_id="onetime.cards.onetime.icon")}
   {card(C.t("onetime.cards.emergency.title"), EMERG_BULLETS, DARKEST, key="onetime.cards.emergency.bullets", icon=CROSS_ICON, icon_id="onetime.cards.emergency.icon")}
  </div>
 </details>
{C.extras("cip")}{C.extras("onetime")} {L.layer(7)}{L.text_boxes(7)}{L.tables_html(7)}{folio(7)}
</section>""")

# -- page 8: funding the budget
pages.append(f"""
<section class="page"{L.fill_attr(f"page.8")}>
 {L.spacer("funding.h1")}<h1{L.attr("funding.h1")}>{C.t("funding.h1")}</h1>
 {L.spacer("funding.fig4.caption")}<p class="figcap"{L.attr("funding.fig4.caption")}><b>Figure 4.</b> {C.t("funding.fig4.caption")} {fy_picker("fig4")} {C.t("funding.fig4.caption.suffix")}</p>
 <div class="pie-row">{fy_pie_swap("fig4", fig4_slices_for(BUD), fig4_slices_for(BUD26), cls="pie-mof", width_in=4.65, label_pt=14.6)}{legend([(esc(n), c) for n, c in zip(FIG4_ORDER, FIG3_COLORS)])}</div>
 {C.html("funding.fig4.note", cls="fig-note")}
 {C.html("funding.p1")}
 <div class="cards3">
  {card(C.t("funding.cards.general.title"), C.list("funding.cards.general.bullets"), DARK, key="funding.cards.general.bullets", icon=GENERAL_FUNDS_ICON, icon_id="funding.cards.general.icon")}
  {card(C.t("funding.cards.special.title"), C.list("funding.cards.special.bullets"), SAGE_MID, key="funding.cards.special.bullets", icon=SPECIAL_FUNDS_ICON, icon_id="funding.cards.special.icon")}
  {card(C.t("funding.cards.federal.title"), C.list("funding.cards.federal.bullets"), SAGE_LIGHT, light=True, key="funding.cards.federal.bullets", icon=FEDERAL_FUNDS_ICON, icon_id="funding.cards.federal.icon")}
 </div>
{C.extras("funding")} {L.layer(8)}{L.text_boxes(8)}{L.tables_html(8)}{folio(8)}
</section>""")

# -- page 9: taxes
pages.append(f"""
<section class="page"{L.fill_attr(f"page.9")}>
 {L.spacer("taxes.h2")}<h2 class="sub"{L.attr("taxes.h2")}>{C.t("taxes.h2")}</h2>
 {L.spacer("taxes.fig5.caption")}<p class="figcap"{L.attr("taxes.fig5.caption")}><b>Figure 5.</b> {C.t("taxes.fig5.caption")} {fy_picker("fig5")} {C.t("taxes.fig5.caption.suffix")}</p>
 <div class="pie-row">{fy_pie_swap("fig5", fig5_slices_for(REV), fig5_slices_for(REV26), cls="pie-tax", width_in=4.80, label_pt=13.1)}{legend([(esc(n), c) for (n, _v, c, _l) in fig5_slices_for(REV)])}</div>
 <div class="cards3">
  {card(C.t("taxes.cards.get.title"), C.list("taxes.cards.get.bullets"), DARK, key="taxes.cards.get.bullets", icon=GET_ICON, icon_id="taxes.cards.get.icon")}
  {card(C.t("taxes.cards.iit.title"), C.list("taxes.cards.iit.bullets"), SAGE_MID, key="taxes.cards.iit.bullets", icon=IIT_ICON, icon_id="taxes.cards.iit.icon")}
  {card(C.t("taxes.cards.tat.title"), C.list("taxes.cards.tat.bullets"), SAGE_LIGHT, light=True, key="taxes.cards.tat.bullets", icon=TAT_ICON, icon_id="taxes.cards.tat.icon")}
 </div>
{C.extras("taxes")} {L.layer(9)}{L.text_boxes(9)}{L.tables_html(9)}{folio(9)}
</section>""")

# -- page 10: who pays
pages.append(f"""
<section class="page"{L.fill_attr(f"page.10")}>
 {L.spacer("whopays.h3")}<h3 class="sub2"{L.attr("whopays.h3")}>{C.t("whopays.h3")}</h3>
 <p class="figcap"><b>Figure 6.</b> {C("whopays.fig6.caption")}</p>
 {fig6_chart()}
 {C.html("whopays.p1")}
{C.extras("whopays")} {L.layer(10)}{L.text_boxes(10)}{L.tables_html(10)}{folio(10)}
</section>""")

# -- page 11: tax credits (the callout was on page 11; moved to its own page)
# The callout keeps its identity ("callout.whopays"), so any recolour/position
# it carried follows it here — everything page-keyed stays by identity. Its
# [^tax-credits] footnote sits right where it did in reading order (last of
# page 11 -> first of page 12), so endnote numbering is unchanged.
pages.append(f"""
<section class="page"{L.fill_attr(f"page.11")}>
 {L.spacer("taxfair.h2")}<h2 class="sub"{L.attr("taxfair.h2")}>{C.t("taxfair.h2")}</h2>
 {C.html("taxfair.intro", cls="taxfair-intro")}
 <div class="reforms">
  {reform(1, "capgains", DARK)}
  {reform(2, "conveyance", SAGE)}
  {reform(3, "loopholes", FOREST)}
 </div>
{callout_open("whopays")}
  <h4>{C.t("whopays.callout.title")}</h4>
  {C.html("whopays.callout.p1")}
  {C.html("whopays.callout.p2")}
 </div>
 {L.layer(11)}{L.text_boxes(11)}{L.tables_html(11)}{folio(11)}
</section>""")

# -- page 12: endnotes --------------------------------------------------------
# Footnote refs live in the prose as stable [^id] tokens. Resolve them across the
# assembled body FIRST so numbering follows the page ORDER, then build the
# endnotes page from the order that produced.
#
# One page, three columns (.endnotes in primer.css) — the two-page split is
# gone. The list is a product of resolving the very body it sits in, so the
# page is assembled as a shell holding a placeholder, the whole ordered body is
# resolved, and the placeholder is filled with the numbered list after.
ENDNOTES_SLOT = "<!--ds-endnotes-->"
by_id = {i + 1: html for i, html in enumerate(pages)}   # designed pages 1..12
by_id[12] = f"""
<section class="page"{L.fill_attr("page.12")}>
 {L.spacer("endnotes.h1")}<h1{L.attr("endnotes.h1")}>{C.t("endnotes.h1")}</h1>
 <ol class="endnotes">{ENDNOTES_SLOT}</ol>
 {L.layer(12)}{L.text_boxes(12)}{L.tables_html(12)}{folio(12)}
</section>"""
for _bid in L.blank_ids():
    by_id[_bid] = blank_page(_bid)

_assembled = "".join(stamp_page(by_id[pid], pid) for pid in PAGE_ORDER)
# Endnotes are numbered by first appearance, unless the editor has dragged
# one past another on the Endnotes page — then layout.json carries the order
# it should read in. Seeded BEFORE resolve(), which numbers refs as it walks
# and so cannot be reordered after the fact.
C.fn.order_by(L.endnote_order(), C.fn.cited(_assembled))
body = C.fn.resolve(_assembled)

missing_src = C.fn.unused()
# A source with no citation is a HARD error only when PUBLISHING. While editing
# (DOCSYNC_EDIT), it is almost always a normal in-between state — you cut a
# sentence to move it elsewhere, and for those few seconds the source it cited
# has nothing pointing at it. Crashing the live preview over that made a routine
# move feel like a "critical error" and blocked it. In edit mode the source is
# simply left unrendered (it reappears the moment you paste the text back), and
# a note prints; publish/export/CI still refuse, so a source that stays orphaned
# — a citation typo, a genuinely dangling def — is still caught before it ships.
if missing_src and os.environ.get("DOCSYNC_EDIT"):
    print("  draft: source(s) not cited yet — normal while moving text: "
          + ", ".join(missing_src))
elif missing_src:
    srcs = ", ".join(f"[{s}]" for s in missing_src)
    hidden = [p for p in range(1, DESIGNED_PAGES + 1) if p not in PAGE_POS]
    if hidden:
        # The common cause once pages can be hidden: the page that cited these
        # sources is no longer in the report. Name it, and the fix.
        names = ", ".join(f"{PAGE_LABELS[p]} (page {p})" for p in hidden)
        raise ContentError(
            f"hiding {names} left these sources with nothing citing them: {srcs}. "
            f"Show that page again, or remove the sources from the doc.")
    raise ContentError(
        "content.md declares sources never cited in the prose: " + srcs
        + " — if you're moving the text that cites one, finish the move (paste it "
        + "back in); this blocks publishing only, not the live preview.")

entries = list(C.fn.endnotes_with_ids())
notes = C.fn.endnotes()
# Split by count, first half on page 13, the rest on page 14. A qualifying
# entry earlier in the doc always lands before one later, so the split point
# is just "how many", independent of how the two halves are laid out.
en_all = "".join(endnote_link(i + 1, sid, t, u) for i, (sid, t, u) in enumerate(entries))

def linkify_footnotes(markup):
    """Turn every <sup>N</sup> marker into a clickable ref the JS can pop.

    Handles multi-note markers like <sup>4&thinsp;5</sup>. The href still points
    at the endnote anchor, so the marker works without JS and in print.
    """
    def repl(m):
        inner = m.group(1)
        nums = re.findall(r"\d+", inner)
        if not nums:
            return m.group(0)
        out = []
        for n in nums:
            i = int(n)
            if 1 <= i <= len(notes):
                out.append(f'<a class="fn" href="#en{i}" data-fn="{i}">{n}</a>')
            else:
                out.append(n)
        return "<sup>" + "&thinsp;".join(out) + "</sup>"
    return re.sub(r"<sup>(.*?)</sup>", repl, markup, flags=re.S)

# The endnotes page was assembled as a shell holding a placeholder; fill it
# now that the numbering it depends on exists. The list carries no footnote
# markers, so this substitution is safe after resolve.
body = body.replace(ENDNOTES_SLOT, en_all)

# Teach the editor's rail every designed page and its name (edit mode only, so
# the published bytes are untouched). Hidden pages are absent from the DOM; this
# is how the rail still lists them.
if os.environ.get("DOCSYNC_EDIT"):
    body += ('<script type="application/json" id="ds-pagemeta">'
             + json.dumps([{"id": i, "label": PAGE_LABELS[i]}
                           for i in range(1, DESIGNED_PAGES + 1)])
             + "</script>")

# A few slots are built into a string before they reach the page (a caption the
# renderer slices, a label inside SVG). A style on those does nothing at all,
# and silence is the one thing this project does not do.
unstyleable = L.unknown_text_keys(C.styleable())
if unstyleable:
    raise LayoutError(
        "layout.json styles text that cannot carry a style: "
        + ", ".join(unstyleable)
        + "\n  Only slots rendered as a paragraph, a span or a list can be "
          "styled — these reach the page already built into a string.")

out_of_bounds = L.check_bounds()
if out_of_bounds:
    raise LayoutError("layout.json puts content off the page:\n  "
                      + "\n  ".join(out_of_bounds))

unused = C.unused_keys()
if unused:
    msg = "content.md has keys the report never uses: " + ", ".join(unused)
    if any(k.startswith("extra.") for k in unused):
        msg += ("\n  extra.* slots must be named extra.<page>.<slug> where "
                "<page> is one of: " + ", ".join(EXTRA_PAGES))
    raise ContentError(msg)

# The editor previews the report in an iframe assigned via srcdoc, so this
# stylesheet is an ORDINARY browser subresource load — it does not go through
# the editor's own cache-busted fetches, and primer.css is not in docsync.yml's
# editor.engine list either, so nothing in the live-reload path ever re-fetches
# it. Without a stamp the browser keeps the copy it already has: every other
# live edit lands instantly while a CSS edit needs a HARD refresh. Stamped with
# the file's mtime so it changes exactly when the file does (a per-render stamp
# would refetch on every drag), and ONLY in edit mode — the published bytes are
# byte-identical to before this existed.
_css_href = "primer.css"
if os.environ.get("DOCSYNC_EDIT"):
    try:
        _css_href += f"?cb={int((HERE / 'web' / 'primer.css').stat().st_mtime)}"
    except OSError:
        pass                      # no stylesheet on disk yet: ship the bare href

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- Unlisted: reachable only via direct link while in review. Remove once ready to publish. -->
<meta name="robots" content="noindex, nofollow">
<title>Hawaiʻi Budget Primer FY2026–27 — Hawaiʻi Appleseed</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
{L.font_link()}
<!-- The cover display face, requested separately because font_link() only emits
     the engine's brand fonts plus whatever layout.json styles ask for, and this
     family is not in the editor's allowlist (docsync/layout.py FONTS). Stands in
     for Encorpada Classic, the licensed serif on the FY2025-26 cover: matched on
     stem weight, hairline contrast and set width, and unlike the Bodoni family
     it carries a real ʻokina (U+02BB). -->
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Serif+Display:wght@600&display=swap">
<link rel="stylesheet" href="{_css_href}">
</head>
<body{' class="ds-bleed"' if os.environ.get("DOCSYNC_MARKS") else ''}>
{chart_scroll_css()}
<div class="toolbar noprint">
 <span>Hawaiʻi Budget Primer FY2026–27</span>
 <span class="tb-actions">
  <a class="tb-link" href="{TRACKER}" target="_blank" rel="noopener">Budget Tracker ↗</a>
  <!-- Two controls, one of which is showing (primer.css, .tb-print/.tb-pdf).
       On a desktop the button prints the live page, so a PDF always reflects
       the newest edit. A phone gets the built file instead: iOS Safari lays a
       print job out at the VIEWPORT width rather than the paper, so the fixed
       8.5x11in sheets and every inch-positioned element spilled out of the
       print page box and the reader got blank sheets with fragments strewn
       over them. Relative href — the PDF is published beside this page by
       `make -C report2027 pdf-pub`; bundle_embed.py rewrites it for the
       Squarespace bundle, where nothing is relative to /primer/. -->
  <button class="tb-print" onclick="window.print()">Download PDF</button>
  <a class="tb-pdf" href="Budget-Primer-FY2026-27.pdf">Download PDF</a>
  <!-- The same report as a page you can copy words out of, for anyone quoting
       it or pulling figures into slides. Built by tools/build_text_page.py and
       published beside this page by `make -C report2027 pub`; relative href,
       so bundle_embed.py rewrites it for the Squarespace bundle the same way
       the PDF above is rewritten. -->
  <a class="tb-link" href="text.html">Plain text</a>
 </span>
</div>
{linkify_footnotes(body)}
<div id="tip" class="noprint"></div>
<script>window.PRIMER_NOTES = {json.dumps([{"t": t, "u": u} for t, u in notes], separators=(",", ":"))};
window.PRIMER_FY_SPAN = {json.dumps(HIST_FY_SPAN)};
window.PRIMER_LINKS = {json.dumps({
    **DEPT_INFO,
    "__all": {"name": "Hawaiʻi Budget Tracker",
              "blurb": "Interactive companion to this primer: every executive department and program in Act 175, governor's request vs. enacted, veto changes, positions, and historical trends back a decade.",
              "operating": EXEC_OP, "capital": EXEC_CIP,
              "hist": [round(t["total_nominal"] / 1e6) for t in _hist["totals_by_fy"]],
              "url": TRACKER + "#/enacted"},
}, separators=(",", ":"))};</script>
<script src="primer.js"></script>
</body>
</html>"""

_OUT.write_text(html)
print(f"wrote {_OUT} ({len(html):,} bytes, {len(pages)} pages)")
