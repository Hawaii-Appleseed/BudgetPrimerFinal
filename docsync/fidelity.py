"""Does a Cider-published page look like the page it replaces?

    python3 -m docsync.fidelity CURRENT.html CANDIDATE.html

Both files are rendered in a real browser at a desktop and a phone width — the
candidate from a sibling temp file, so the page's own relative assets and
stylesheets resolve exactly as they will once published — and compared two ways:

  STYLE   every distinct text style (font, size, weight, colour, ...) and where
          it sits (the nearest classed ancestor) must be present on both sides.
          Editing words never changes this; a bold that lost its colour, an
          italic that went upright, or a wrapper that a CSS rule now catches does.
  PIXELS  when the visible text is identical, the two screenshots must match
          (a handful of anti-aliasing pixels are tolerated). When the text
          differs — someone edited it — only STYLE applies, plus a check that
          neither width scrolls sideways more than the current page does.

`check()` returns a list of problems (empty = faithful). It needs node and the
`playwright` npm package (primer-editor's node_modules has it); with either
missing it returns None and says why, so a machine that can't look fails open
and loud instead of blocking a publish nobody can verify.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = Path(__file__).with_name("fidelity_probe.js")
WIDTHS = (1280, 390)
# Anti-aliasing noise only: fraction of pixels allowed to differ when the text is the same.
PIXEL_TOLERANCE = 0.0001


def _probe(url: str, width: int, shot: Path) -> dict:
    r = subprocess.run(["node", str(PROBE), url, str(width), str(shot)], capture_output=True,
                       text=True, timeout=120, env={**os.environ, "CIDER_ROOT": str(ROOT)})
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip()[-400:])
    return json.loads(r.stdout.strip().splitlines()[-1])


def _pixel_fraction(a: Path, b: Path) -> float:
    from PIL import Image, ImageChops
    ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    if ia.size != ib.size:
        return 1.0
    diff = ImageChops.difference(ia, ib).convert("L").point(lambda v: 255 if v > 30 else 0)
    return sum(1 for v in diff.getdata() if v) / (ia.size[0] * ia.size[1])


def check(current: Path, candidate_html: str) -> list[str] | None:
    """Problems with `candidate_html` as a replacement for the page at `current`."""
    if not shutil.which("node"):
        print("fidelity: node is not installed — the look was NOT verified", file=sys.stderr)
        return None
    cand = current.with_name(f".{current.stem}.fidelity{current.suffix}")
    problems: list[str] = []
    try:
        cand.write_text(candidate_html, encoding="utf-8")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            for w in WIDTHS:
                a = _probe(current.resolve().as_uri(), w, td / f"a{w}.png")
                b = _probe(cand.resolve().as_uri(), w, td / f"b{w}.png")
                for s in sorted(set(a["styles"]) - set(b["styles"])):
                    problems.append(f"{w}px: style lost — {s}")
                for s in sorted(set(b["styles"]) - set(a["styles"])):
                    problems.append(f"{w}px: style appeared — {s}")
                if b["scrollWidth"] > max(a["scrollWidth"], w):
                    problems.append(f"{w}px: page now scrolls sideways ({b['scrollWidth']}px wide)")
                if a["text"] == b["text"]:
                    frac = _pixel_fraction(td / f"a{w}.png", td / f"b{w}.png")
                    if frac > PIXEL_TOLERANCE:
                        problems.append(f"{w}px: same text but {frac:.4%} of pixels differ")
    except (RuntimeError, OSError, subprocess.SubprocessError, ImportError) as e:
        print(f"fidelity: could not render — {e}", file=sys.stderr)
        return None
    finally:
        cand.unlink(missing_ok=True)
    return problems


def main(argv: list[str] | None = None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) != 2:
        raise SystemExit(__doc__)
    probs = check(Path(a[0]), Path(a[1]).read_text(encoding="utf-8"))
    if probs is None:
        return 2
    for p in probs:
        print("  " + p)
    print("faithful" if not probs else f"{len(probs)} difference(s)")
    return 1 if probs else 0


if __name__ == "__main__":
    raise SystemExit(main())
