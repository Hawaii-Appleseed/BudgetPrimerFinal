#!/usr/bin/env python3
"""Render web/index.html to dist/Budget-Primer-FY2026-27.pdf with headless Chrome.

A one-line `chrome --print-to-pdf` in the Makefile is what this replaces, and
the reason it could not stay one line is that this build of Chrome writes the
PDF in a few seconds and then does NOT exit under --headless=new. `make pdf`
hung forever waiting on it. serve.py hit the same wall for its export endpoint
and solved it by watching the OUTPUT rather than the process; this is that
approach, kept here so the command line build and the live server agree.

  python3 tools/build_pdf.py [--out PATH] [--chrome PATH]

Exits non-zero with a readable message when no Chrome can be found, so CI says
what is wrong instead of producing a zero-byte PDF.
"""
import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

# Mac first because that is where this report is edited; the Linux names are
# for CI. Anything else: pass --chrome.
CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome-stable", "google-chrome", "chromium-browser", "chromium",
]


def find_chrome(explicit: str | None) -> str | None:
    for c in ([explicit] if explicit else []) + CANDIDATES:
        if not c:
            continue
        p = shutil.which(c) or (c if os.access(c, os.X_OK) else None)
        if p:
            return p
    return None


def capture(args: list[str], out: Path, deadline: float = 90.0,
            settle: float = 2.5) -> bool:
    """Run Chrome and return once `out` has stopped growing.

    start_new_session so the whole group — main process plus the gpu and
    renderer helpers — can be signalled together; a stray helper left running
    holds the profile directory open and the next build inherits its mess.
    """
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    start, last, held = time.time(), -1, 0.0
    try:
        while time.time() - start < deadline:
            if proc.poll() is not None:          # some versions do self-exit
                break
            time.sleep(0.4)
            sz = out.stat().st_size if out.exists() else -1
            if sz > 0 and sz == last:
                held += 0.4
                if held >= settle:
                    break
            else:
                held = 0.0
            last = sz
    finally:
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
    return out.exists() and out.stat().st_size > 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "dist" / "Budget-Primer-FY2026-27.pdf"))
    ap.add_argument("--chrome", default=os.environ.get("CHROME") or None)
    ap.add_argument("--html", default=str(HERE / "web" / "index.html"))
    a = ap.parse_args()

    chrome = find_chrome(a.chrome)
    if not chrome:
        print("build_pdf: no Chrome or Chromium found — pass --chrome PATH "
              "or set CHROME=", file=sys.stderr)
        return 1
    src = Path(a.html)
    if not src.exists():
        print(f"build_pdf: {src} does not exist — run `make render` first",
              file=sys.stderr)
        return 1

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()                    # so a stale file can never look fresh
    prof = Path(tempfile.mkdtemp(prefix="primer-pdf-"))
    try:
        ok = capture([
            chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
            "--no-first-run", f"--user-data-dir={prof}",
            # The webfonts and the charts want a moment; virtual time lets the
            # page reach its settled state without a wall-clock guess.
            "--virtual-time-budget=12000", "--no-pdf-header-footer",
            f"--print-to-pdf={out}", src.resolve().as_uri(),
        ], out)
    finally:
        shutil.rmtree(prof, ignore_errors=True)

    if not ok:
        print("build_pdf: Chrome produced no PDF", file=sys.stderr)
        return 1
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
