#!/usr/bin/env python3
"""Every chart the COMMITTED engine can draw, drawn by both engines, compared.

    python3 -m docsync.chart_parity            # working tree vs HEAD
    python3 -m docsync.chart_parity --base v1  # ...vs any other commit

The property `docsync/CHART_PARITY.md` asks us to keep: *a published report
must not move because the engine grew.* Every gap the chart engine closes is
supposed to be opt-in, or to fire only where the old behaviour was wrong — and
"supposed to be" is not a thing anyone can hold in their head across a
thousand-line change. So this draws every combination of type, dataset, label
set, flag and box size the OLD engine could express, with both engines, and
reports what moved.

It is not a pass/fail gate, because some differences ARE the fix: a bar that
used to be drawn at height zero while its label read -45, a legend whose names
overlapped, a row label that ran off the left of the drawing. It exits non-zero
so a bare run is noisy, prints one diff per differing shape so you can see
WHICH pixel moved, and leaves the judgment where it belongs. What it makes
impossible is finding out later.

The old engine is read straight out of git, so there is nothing to keep in
step: add a chart type or a flag below and both sides get it — the old engine
simply refuses the ones it has never heard of, which is why the sweep is built
from ITS list of types, not the working tree's.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Datasets chosen for the edges that have actually broken: a negative (the bug
# the signed axis exists for), a budget-sized number (where the format matters),
# a fraction under 1 (where tick precision collapses), and more categories than
# a small box can hold.
DATA = {
    "pos": [{"name": "A", "data": [12, 19, 8]}],
    "neg": [{"name": "chg", "data": [120, -45, 60]}],
    "two": [{"name": "A", "data": [5, 9, 3]}, {"name": "B", "data": [2, 6, 7]}],
    "big": [{"name": "$", "data": [2.44e9, 1.1e9, 3.7e9]}],
    "tiny": [{"name": "p", "data": [0.2, 0.55, 0.31]}],
    "wide": [{"name": "x", "data": [1, 2, 3, 4, 5, 6, 7, 8]}],
}
LABELS = {
    "short": ["A", "B", "C"],
    "long": ["Lowest 20%", "Second 20%", "Middle 20%"],
    "break": ["Lowest 20%\n$0-$21,900", "Second 20%\n$21,900-$44,200",
              "Middle 20%\n$44,200-$71,300"],
    "eight": [f"Bin {i}" for i in range(8)],
}
FLAGS = [
    {},
    {"legend": True},
    {"values": True},
    {"legend": True, "values": True},
    {"grid": False},
    {"title": "A figure"},
    {"tips": True},
    {"wrapLabels": True},
    {"axisMin": -50, "axisMax": 200},
    {"axisTicks": 4},
    {"format": {"prefix": "$", "scale": "B", "decimals": 2}},
    {"format": {"suffix": "%"}, "labelFormat": {"decimals": 1}},
    {"sliceLabel": "both", "values": True},
    {"colors": ["#FF0000", "#00FF00", "#0000FF"]},
    {"titleColor": "#111111", "labelColor": "#222222",
     "axisColor": "#333333", "gridColor": "#444444", "title": "Ink"},
]
# A page-width figure, a full page, a thumbnail, and a tall narrow column —
# the box is what drives every floor and shrink-to-fit decision in the engine.
BOXES = [(0, 0, 4, 2.5), (1, 1, 6.5, 4), (0.5, 0.5, 2.2, 1.4), (0, 0, 3.6, 9)]
ANIM = {"kind": "bars", "duration": 0.8, "delay": 0.1}


def _load_base(rev: str):
    """The engine as of `rev`, importable alongside the working tree's.

    Its relative imports are rewritten to absolute ones so the two copies
    share `docsync.content` rather than needing a whole second package.
    """
    src = subprocess.run(["git", "-C", str(ROOT), "show",
                          f"{rev}:docsync/layout.py"],
                         capture_output=True, text=True, check=True).stdout
    src = re.sub(r"^from \.(\w+) import", r"from docsync.\1 import",
                 src, flags=re.M)
    tmp = Path(tempfile.mkdtemp()) / "layout_base.py"
    tmp.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("layout_base", tmp)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["layout_base"] = mod
    spec.loader.exec_module(mod)
    return mod


def _first_diff(a: str, b: str) -> str:
    for i, (ca, cb) in enumerate(zip(a, b)):
        if ca != cb:
            lo = max(0, i - 90)
            return f"    was: …{a[lo:i + 90]}…\n    now: …{b[lo:i + 90]}…"
    return f"    same to {min(len(a), len(b))} chars, then lengths differ"


def sweep(base, new, show: int):
    bad = n = 0
    shown = 0
    for ctype in base.CHART_TYPES:
        for dname, series in DATA.items():
            for lname, labels in LABELS.items():
                for flags in FLAGS:
                    for box in BOXES:
                        c = dict(flags, type=ctype, labels=list(labels),
                                 series=[dict(s) for s in series])
                        a = base.chart_svg(dict(c), *box)
                        b = new.chart_svg(dict(c), *box)
                        n += 1
                        if a == b:
                            continue
                        bad += 1
                        if shown < show:
                            shown += 1
                            print(f"  {ctype} / {dname} / {lname} / {flags} "
                                  f"/ {box}")
                            print(_first_diff(a, b))
    # The animated path bakes per-bar timing INTO the rects, so it is a
    # different code path through the same drawing and gets its own pass.
    for ctype in base.CHART_TYPES:
        c = {"type": ctype, "labels": LABELS["short"], "series": DATA["two"],
             "legend": True, "values": True}
        n += 1
        if base.chart_svg(dict(c), 0, 0, 4, 2.5, anim=ANIM) \
                != new.chart_svg(dict(c), 0, 0, 4, 2.5, anim=ANIM):
            bad += 1
            print(f"  anim / {ctype}")
    return n, bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="HEAD",
                    help="the commit to compare against (default HEAD)")
    ap.add_argument("--show", type=int, default=5,
                    help="how many differing renders to print (default 5)")
    a = ap.parse_args()
    sys.path.insert(0, str(ROOT))
    from docsync import layout as new                       # noqa: E402
    n, bad = sweep(_load_base(a.base), new, a.show)
    print(f"{n} renders compared against {a.base}, {bad} differ")
    if bad:
        print("Each one is either a fix or a regression. Say which, in "
              "docsync/CHART_PARITY.md.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
