#!/usr/bin/env python3
"""Stage the draft editor next to a report.

    python3 -m docsync.stage --id budget-primer

The editor runs the report's OWN renderer in the browser, so it needs that
renderer, whatever the renderer reads, and a description of the report. All of
that is copied to `<dir>/engine/` and described by `<dir>/engine/manifest.json`,
and the editor is copied in beside it.

Two things this buys, and they are the point:

* **One editor, any report.** The editor knows nothing about the Budget Primer.
  It reads the manifest sitting next to it and renders whatever it names. A
  second report is a second entry in docsync.yml, not a second editor.
* **Same origin.** Fetching the renderer from raw.githubusercontent let its CDN
  hand out a copy several commits old — a page that renders fine and is quietly
  wrong. Staged here, the engine can only ever be the one that shipped with the
  editor it is running in.

Everything written here is a build artifact, like the rendered HTML. Never edit
it; edit `docsync/editor/edit.html` and re-stage.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docsync.registry import ROOT, Binding, RegistryError, get, load_registry  # noqa: E402

EDITOR = Path(__file__).resolve().parent / "editor" / "edit.html"
COLLAB_CLIENT = EDITOR.parent / "collab-client.js"
# Every docsync module a project renderer may import, copied into Pyodide's
# filesystem. Unlike vendor.py — where the package genuinely IS the manifest —
# this list is explicit, so a new shared module has to be added here too or the
# draft build dies with ModuleNotFoundError while the published page is fine.
PACKAGE = ("content.py", "normalise.py", "layout.py", "blocks.py", "okina.py")


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


def manifest(b: Binding) -> dict:
    """What the editor needs to know, in the editor's own terms.

    Paths are given twice: the URL to fetch (relative to the editor's page) and
    the path the renderer expects inside Pyodide's filesystem. The renderer is
    written for a repo checkout, so the browser has to rebuild one.
    """
    e = b.editor
    files = {"engine/docsync/" + n: "docsync/" + n for n in PACKAGE}
    files["engine/" + rel(e.render)] = rel(e.render)
    files["engine/" + rel(b.content)] = rel(b.content)
    if e.layout:
        files["engine/" + rel(e.layout)] = rel(e.layout)
    for f in e.engine:
        files["engine/" + rel(f)] = rel(f)
    return {
        "id": b.id,
        "repo": None,               # filled by --repo, or the editor asks
        "branch": e.branch,         # deploy branch drafts fork from / publish to
        "files": files,
        "render": rel(e.render),
        "content": rel(b.content),
        "layout": rel(e.layout) if e.layout else None,
        "out": rel(e.out),
        "assets": rel(e.assets) if e.assets else None,
        "page": {"w": e.page[0], "h": e.page[1]},
        "margins": {"side": e.margins[0], "top": e.margins[1]},
        "palette": e.palette or [],
        # The images already IN the project — a template's logos, past
        # uploads — so Insert image can offer them without a trip through
        # the file picker. Names only; they resolve as "assets/<name>"
        # relative to the editor's page like every other asset src.
        "images": _asset_images(b),
    }


IMAGE_EXTS = (".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp")


def _asset_images(b: Binding) -> list[str]:
    """Image filenames in the project's assets dir, the same dir /__upload
    writes into: editor.assets when the binding names one, else assets/
    beside the built page."""
    e = b.editor
    adir = e.assets if e.assets else e.out.parent / "assets"
    if not adir.is_dir():
        return []
    return sorted(p.name for p in adir.iterdir()
                  if p.suffix.lower() in IMAGE_EXTS and p.is_file())


def _origin_slug(root) -> str | None:
    """'owner/name' for a checkout's origin, or None when it has none. Accepts
    either URL form git hands out: git@host:owner/name.git and
    https://host/owner/name(.git)."""
    try:
        url = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not url:
        return None
    url = url.removesuffix(".git")
    m = re.search(r"[:/]([^/:]+/[^/]+)$", url)
    return m.group(1) if m else None


def stage(b: Binding, repo: str = "") -> None:
    if not b.editor:
        raise RegistryError(f"'{b.id}' has no editor: block in docsync.yml")
    e = b.editor
    eng = e.dir / "engine"
    m = manifest(b)
    existing = eng / "manifest.json"
    if repo:
        m["repo"] = repo
    elif existing.is_file():
        # Re-staging (every rebuild, not just the first `--repo` adoption)
        # must not silently forget which repo a project pushes to — manifest()
        # always starts from repo: None, so without this a build that re-stages
        # without repeating --repo would blank it out on every single rebuild.
        try:
            m["repo"] = json.loads(existing.read_text()).get("repo") or None
        except (json.JSONDecodeError, OSError):
            pass
    if not m.get("repo"):
        # Nothing chose one, so default to THIS checkout's own origin. Without
        # it a fresh clone inherits whatever repo was staged into the committed
        # manifest — i.e. someone else's — and the new owner's very first Push
        # aims at a repo that is not theirs. Deriving it means the editor
        # points at your own remote from the first build, and it still falls
        # back to None (the editor asks) when there is no origin at all.
        m["repo"] = _origin_slug(ROOT)

    for url, src in m["files"].items():
        dst = e.dir / url
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / src, dst)

    # A package needs an __init__; the real one imports the registry (pyyaml),
    # which the renderer never needs and Pyodide would have to install.
    (eng / "docsync" / "__init__.py").write_text("")
    (eng / "manifest.json").write_text(json.dumps(m, indent=2) + "\n")
    shutil.copy2(EDITOR, e.dir / "edit.html")
    # The real-time collaboration client (collab/build-client.mjs bundles it
    # from collab/client/session.mjs). The editor import()s it beside itself
    # only when a project has a collab relay configured, so it costs nothing
    # to stage everywhere and saves a "where does this file come from" hunt.
    if COLLAB_CLIENT.is_file():
        shutil.copy2(COLLAB_CLIENT, e.dir / "collab-client.js")
    # The assets themselves, staged beside the editor: the iframe resolves a
    # box's "assets/…" src against the editor's page, and for a scaffolded
    # project nothing else put the files there — a template's logo (or any
    # upload, after the reload that empties localImages) came back 404.
    # Reports whose own build already fills <dir>/assets (the primer's make
    # pub) just get the same bytes copied again.
    adir = e.assets if e.assets else e.out.parent / "assets"
    if adir.is_dir():
        shutil.copytree(adir, e.dir / "assets", dirs_exist_ok=True)
    print(f"  staged {len(m['files'])} files + editor -> {rel(e.dir)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", help="only this binding (default: every one with an editor)")
    ap.add_argument("--repo", default="", help="owner/name the editor saves to")
    a = ap.parse_args()
    try:
        bindings = [get(a.id)] if a.id else [b for b in load_registry() if b.editor]
    except RegistryError as err:
        print(f"registry error: {err}", file=sys.stderr)
        return 2
    if not bindings:
        print("no bindings have an editor: block", file=sys.stderr)
        return 0
    for b in bindings:
        print(f"{b.id}:")
        stage(b, a.repo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
