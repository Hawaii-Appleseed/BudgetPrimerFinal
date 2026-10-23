#!/usr/bin/env python3
"""Every bound report, built by the base tree and by this one, byte for byte.

    python3 -m docsync.build_parity                   # working tree vs HEAD
    python3 -m docsync.build_parity --base origin/main

docsync/CHART_PARITY.md asks one thing of the chart engine: *a published
report must not move because the engine grew.* chart_parity holds it for
every chart both engines can draw; this holds it for the pages. Every
binding in docsync.yml is built twice — by the base tree's renderer, content,
layout and engine, and by this tree's — with the date pinned (DOCSYNC_DATE),
and the two pages are compared byte for byte.

A page that moved is one of three things, and only the last is a failure:

  - its OWN files changed between base and here (its content, layout,
    renderer, assets, data): that diff is the explanation;
  - docsync/BUILD_PARITY.md changed in the same range: an engine change
    that moves a page says which pages and why there, the way a chart that
    moved is accounted for in CHART_PARITY.md;
  - neither: the engine moved a published report and nothing says why.

Exit 0 for the first two, 1 for the third — and for a report this tree can
no longer build. A bare run measures the working tree's uncommitted work
against HEAD; CI runs it against the commit a push or pull request started
from (editor-tests.yml, the `parity` job).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PINNED_DATE = "2026-01-01"
RECORD = "docsync/BUILD_PARITY.md"


def _git(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def bindings(tree: Path) -> dict[str, dict]:
    """id → {render, out, own} for every binding with a renderer, read from
    the tree's own docsync.yml, paths relative to the tree as the file holds
    them. `own` is what counts as the report's own: its renderer, content,
    layout, outputs, watched globs, engine data, assets, and the folders the
    first three live in."""
    import yaml
    reg = tree / "docsync.yml"
    if not reg.is_file():
        return {}
    data = yaml.safe_load(reg.read_text(encoding="utf-8")) or {}
    out: dict[str, dict] = {}
    for b in data.get("bindings") or []:
        ed = b.get("editor") or {}
        if not (b.get("id") and ed.get("render") and ed.get("out")):
            continue
        own = {ed["render"], ed["out"], b.get("content") or ""}
        own |= {str(Path(p).parent) for p in list(own) if p}
        own |= set(b.get("outputs") or []) | set(b.get("watch") or [])
        own |= set(ed.get("engine") or [])
        own |= {ed[k] for k in ("layout", "assets") if ed.get(k)}
        out[b["id"]] = {"render": ed["render"], "out": ed["out"],
                        "own": sorted(p for p in own if p and p != ".")}
    return out


def is_own(path: str, own: list[str]) -> bool:
    """Is `path` one of the report's own files: listed, under a listed
    folder, or matching a listed glob?"""
    for o in own:
        if any(ch in o for ch in "*?["):
            if fnmatch(path, o):
                return True
        elif path == o or path.startswith(o.rstrip("/") + "/"):
            return True
    return False


def changed_since(base: str) -> set[str]:
    """Every file that differs between `base` and the working tree, untracked
    ones included — except the stager's copies under docs/, which are
    nobody's edit."""
    files = set(_git("diff", "--name-only", base).stdout.splitlines())
    files |= {f for f in _git("ls-files", "--others", "--exclude-standard").stdout.splitlines()
              if not f.startswith("docs/")}
    return {f for f in files if f}


def build(tree: Path, render: str, out: Path, date: str) -> str | None:
    """Run the tree's renderer into `out`. None when it built; else why not."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("DOCSYNC_EDIT", "DOCSYNC_SLOTLOG", "DOCSYNC_LAYOUT", "DOCSYNC_OUT")}
    env["DOCSYNC_OUT"] = str(out)
    env["DOCSYNC_DATE"] = date
    try:
        r = subprocess.run([sys.executable, str(tree / render)], env=env, cwd=tree,
                           capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return "timed out after 300s"
    if r.returncode or not out.is_file():
        tail = (r.stderr.strip() or r.stdout.strip()).splitlines()
        return tail[-1] if tail else f"exited {r.returncode} with no output"
    return None


def checkout(rev: str):
    """The base tree, as a throwaway git worktree: (path, sha, drop)."""
    sha = _git("rev-parse", "--verify", f"{rev}^{{commit}}").stdout.strip()
    if not sha:
        raise SystemExit(f"build_parity: no such commit: {rev}")
    tmp = Path(tempfile.mkdtemp(prefix="docsync-parity-"))
    tree = tmp / "base"
    r = _git("worktree", "add", "--detach", str(tree), sha)
    if r.returncode:
        shutil.rmtree(tmp, ignore_errors=True)
        raise SystemExit(f"build_parity: could not check out {rev}:\n{r.stderr.strip()}")

    def drop():
        _git("worktree", "remove", "--force", str(tree))
        shutil.rmtree(tmp, ignore_errors=True)
    return tree, sha, drop


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="HEAD",
                    help="the commit to compare against (default HEAD)")
    ap.add_argument("--show", type=int, default=5,
                    help="how many moved pages to excerpt (default 5)")
    ap.add_argument("--date", default=PINNED_DATE,
                    help=f"the DOCSYNC_DATE both builds get (default {PINNED_DATE})")
    ap.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1),
                    help="renderers to run at once (default 4)")
    a = ap.parse_args()
    from .chart_parity import _first_diff                     # noqa: PLC0415

    tree, sha, drop = checkout(a.base)
    try:
        here, there = bindings(ROOT), bindings(tree)
        changed = changed_since(sha)
        accounted = RECORD in changed
        failed = moved = shown = 0
        with tempfile.TemporaryDirectory() as td_, ThreadPoolExecutor(a.jobs) as pool:
            td = Path(td_)
            jobs = {}
            for bid, b in here.items():
                jobs[bid, "here"] = pool.submit(build, ROOT, b["render"], td / f"{bid}.here.html", a.date)
                if bid in there:
                    jobs[bid, "base"] = pool.submit(build, tree, there[bid]["render"], td / f"{bid}.base.html", a.date)
            built = {k: f.result() for k, f in jobs.items()}
            for bid, b in sorted(here.items()):
                err = built[bid, "here"]
                if err:
                    print(f"FAIL  {bid}: does not build here ({err})")
                    failed += 1
                    continue
                if bid not in there:
                    print(f"new   {bid}: not bound at {a.base}, nothing to compare")
                    continue
                if built[bid, "base"]:
                    print(f"skip  {bid}: did not build at {a.base} ({built[bid, 'base']})")
                    continue
                was = (td / f"{bid}.base.html").read_bytes()
                now = (td / f"{bid}.here.html").read_bytes()
                if was == now:
                    print(f"ok    {bid} ({len(now):,} bytes, identical)")
                    continue
                moved += 1
                mine = sorted(p for p in changed if is_own(p, b["own"]))
                if mine:
                    more = f" and {len(mine) - 3} more" if len(mine) > 3 else ""
                    print(f"moved {bid}: its own files changed ({', '.join(mine[:3])}{more}) — expected")
                elif accounted:
                    print(f"moved {bid}: the engine moved it, and {RECORD} changed with it — accounted for")
                else:
                    print(f"MOVED {bid}: the engine moved it and nothing says why — "
                          f"name it, and the reason, in {RECORD}")
                    failed += 1
                if shown < a.show:
                    shown += 1
                    print(_first_diff(was.decode("utf-8", "replace"), now.decode("utf-8", "replace")))
        print(f"{len(here)} reports built against {a.base} ({sha[:9]}) with the date pinned to {a.date}: "
              f"{moved} moved, {failed} unexplained or unbuilt")
        return 1 if failed else 0
    finally:
        drop()


if __name__ == "__main__":
    sys.exit(main())
