#!/usr/bin/env python3
"""Vendor the draft editor into the staff hub — `/primer/` on staff-updates-internal.

    python3 -m docsync.hub                  # into the hub named in vendor.local.yml
    python3 -m docsync.hub --into ~/staff-updates-internal
    python3 -m docsync.hub --dry-run        # say what would change, write nothing

The hub (Hawaii-Appleseed/staff-updates-internal — a Cloudflare Pages site
behind Cloudflare Access) is where staff already sign in, and most of them
have no GitHub account. Served from THERE, the editor inherits that session:
its collaboration socket goes to the hub's own `/api/collab/<room>` door
(`functions/api/collab/[room].js`), which authenticates the request itself and
hands the Durable Object the verdict — no GitHub token, no ticket. That only
works on one origin (the Access cookie is first-party to the hub's hostname),
which is why the editor has to live at a path on the hub rather than link out
to GitHub Pages. See collab/README.md, "Served from the staff hub".

What lands in `<hub>/primer/`:

    edit.html            the editor — ONE copy, for every project
    collab-client.js     its collaboration client
    projects.json        the registry: id -> {name, base, repo, collab: {path}}
    icons/, manifest.webmanifest   the tab icon
    <id>/engine/…        each project's renderer, the files it reads, its manifest
    <id>/assets/…        each project's images
    index.html           the hub's own list page — hand-maintained THERE; never written here

Sources: every binding with an `editor:` block in this repo's docsync.yml, then
every consumer in vendor.yml / vendor.local.yml (each staged by its own vendored
`docsync.stage`, so the copy that ships is the one that repo's build produces).
A binding may say `hub: false` to stay off the hub — this repo's own
`budget-primer` does, because it is the test fixture and the live report is the
consumer's. A `name:` on a binding is the title the hub's list shows.

The manifest's `repo` is canonicalised to the source checkout's origin. A room
is named `owner~repo~project`, and the hub serves only the Hawaii-Appleseed
spellings (`assets/collab.js` allowedRepos), so a manifest still saying
`dtomkatsu/…` would be refused at the door — and even if it were not, it would
be a DIFFERENT ROOM from the one a colleague is in.

Nothing here commits. The hub is a website: a push there is a deploy, and that
stays a person's explicit act. The engine's post-commit hook runs this after
`docsync.vendor` so the hub's working tree is always current; review it and
commit there when it is time to ship.
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

from docsync.registry import ROOT, RegistryError, load_registry  # noqa: E402
from docsync.stage import COLLAB_CLIENT, EDITOR, _origin_slug, stage  # noqa: E402
from docsync.vendor import VENDOR_LOCAL, VENDOR_YML, consumers  # noqa: E402

# The hub's front door onto the rooms, and the route that says who is signed
# in — both paths on the hub's own origin (functions/api/collab/[room].js and
# functions/api/me.js there). The editor reads these off its registry entry.
COLLAB_DOOR = {"path": "/api/collab", "me": "/api/me"}
# The editor links these beside itself (a 404 per page load otherwise).
SHELL_EXTRAS = ("icons",)
WEBMANIFEST = {
    "name": "Report editor — Hawaiʻi Appleseed staff",
    "short_name": "Editor",
    "start_url": "./index.html",
    "scope": "./",
    "display": "standalone",
    "background_color": "#2F3E46",
    "theme_color": "#2F3E46",
    "icons": [
        {"src": "icons/icon.svg", "sizes": "any", "type": "image/svg+xml"},
        {"src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
    ],
}


def hub_dir(explicit: str | None) -> Path | None:
    """`--into`, else the `hub:` key of vendor.local.yml / vendor.yml. None
    when nothing names one — the post-commit hook runs this everywhere the
    engine is committed, and a clone with no hub beside it has nothing to do."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    import yaml                                       # noqa: PLC0415
    for f in (VENDOR_LOCAL, VENDOR_YML):
        if not f.exists():
            continue
        data = yaml.safe_load(f.read_text()) or {}
        hub = data.get("hub")
        if isinstance(hub, dict):
            hub = hub.get("path")
        if hub:
            return Path(str(hub)).expanduser().resolve()
    return None


def humanise(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.split("-"))


def _raw_bindings(repo: Path) -> list[dict]:
    """The registry as written — for the keys the Binding dataclass does not
    carry (`name`, `hub`) and for a CONSUMER, whose paths must resolve against
    its own root, not this one's (load_registry binds them to ROOT)."""
    import yaml                                       # noqa: PLC0415
    data = yaml.safe_load((repo / "docsync.yml").read_text()) or {}
    return [b for b in (data.get("bindings") or []) if isinstance(b, dict)]


def _local_names() -> dict[str, str]:
    """Display names from this machine's projects.json, when it has them."""
    try:
        reg = json.loads((ROOT / "docs" / "primer" / "projects.json").read_text())
        return {k: v.get("name") for k, v in reg.items() if isinstance(v, dict) and v.get("name")}
    except (OSError, json.JSONDecodeError, AttributeError):
        return {}


def sources(*, dry: bool) -> list[dict]:
    """Every project the hub gets, staged fresh: {id, name, dir, repo}."""
    names = _local_names()
    out: dict[str, dict] = {}

    def add(bid: str, raw: dict, staged: Path, repo: str | None, where: str):
        if raw.get("hub") is False:
            print(f"  {bid}: hub: false — skipped")
            return
        if not (staged / "engine" / "manifest.json").is_file():
            print(f"  {bid}: nothing staged at {staged} — skipped", file=sys.stderr)
            return
        if not repo:
            print(f"  {bid}: {where} has no origin remote, so no room name — skipped",
                  file=sys.stderr)
            return
        if bid in out:
            print(f"  {bid}: also in {out[bid]['where']} — {where} wins")
        out[bid] = {"id": bid, "dir": staged, "repo": repo, "where": where,
                    "name": raw.get("name") or names.get(bid) or humanise(bid)}

    # This repo: the registry proper, staged in-process.
    print(f"{ROOT}")
    raw_here = {b.get("id"): b for b in _raw_bindings(ROOT)}
    for b in load_registry():
        if not b.editor:
            continue
        raw = raw_here.get(b.id, {})
        if raw.get("hub") is False:
            print(f"  {b.id}: hub: false — skipped")
            continue
        if not dry:
            stage(b)
        add(b.id, raw, b.editor.dir, _origin_slug(ROOT), ROOT.name)

    # Consumers: their own vendored stage, in their own checkout.
    for repo in consumers():
        print(f"{repo}")
        slug = _origin_slug(repo)
        for raw in _raw_bindings(repo):
            bid, e = raw.get("id"), raw.get("editor") or {}
            if not bid or not e.get("dir"):
                continue
            if raw.get("hub") is False:
                print(f"  {bid}: hub: false — skipped")
                continue
            if not dry:
                r = subprocess.run([sys.executable, "-m", "docsync.stage", "--id", bid],
                                   cwd=repo, capture_output=True, text=True)
                if r.returncode != 0:
                    print(f"  {bid}: stage failed in {repo.name} — skipped\n{r.stderr}",
                          file=sys.stderr)
                    continue
            add(bid, raw, repo / e["dir"], slug, repo.name)
    return list(out.values())


# --------------------------------------------------------------- the copy

def _sync_tree(src: Path, dst: Path, changed: list[str], *, dry: bool,
               skip: tuple[str, ...] = ()) -> None:
    """Make dst hold exactly src: copy what differs, delete what is gone.
    `skip` names files (relative) that are written separately."""
    want = {p.relative_to(src) for p in src.rglob("*")
            if p.is_file() and str(p.relative_to(src)) not in skip}
    for rel in sorted(want):
        s, d = src / rel, dst / rel
        if d.is_file() and d.read_bytes() == s.read_bytes():
            continue
        changed.append(str(d))
        if not dry:
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
    if dst.is_dir():
        for p in sorted(dst.rglob("*"), reverse=True):
            if p.is_file() and p.relative_to(dst) not in want and str(p.relative_to(dst)) not in skip:
                changed.append(f"- {p}")
                if not dry:
                    p.unlink()
            elif p.is_dir() and not any(p.iterdir()) and not dry:
                p.rmdir()


def _write(dst: Path, data: bytes, changed: list[str], *, dry: bool) -> None:
    if dst.is_file() and dst.read_bytes() == data:
        return
    changed.append(str(dst))
    if not dry:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)


def vendor(hub: Path, projects: list[dict], *, dry: bool) -> list[str]:
    primer = hub / "primer"
    changed: list[str] = []

    # The editor and its client, once.
    _write(primer / "edit.html", EDITOR.read_bytes(), changed, dry=dry)
    if COLLAB_CLIENT.is_file():
        _write(primer / "collab-client.js", COLLAB_CLIENT.read_bytes(), changed, dry=dry)
    for extra in SHELL_EXTRAS:
        src = ROOT / "docs" / "primer" / extra
        if src.is_dir():
            _sync_tree(src, primer / extra, changed, dry=dry)
    _write(primer / "manifest.webmanifest",
           (json.dumps(WEBMANIFEST, indent=2, ensure_ascii=False) + "\n").encode(),
           changed, dry=dry)

    # Each project: engine + assets, with a canonical manifest.
    registry: dict[str, dict] = {}
    for p in projects:
        pid, sdir = p["id"], p["dir"]
        pdir = primer / pid
        _sync_tree(sdir / "engine", pdir / "engine", changed, dry=dry, skip=("manifest.json",))
        m = json.loads((sdir / "engine" / "manifest.json").read_text())
        m["repo"] = p["repo"]
        _write(pdir / "engine" / "manifest.json",
               (json.dumps(m, indent=2) + "\n").encode(), changed, dry=dry)
        if (sdir / "assets").is_dir():
            _sync_tree(sdir / "assets", pdir / "assets", changed, dry=dry)
        elif (pdir / "assets").is_dir() and not dry:
            shutil.rmtree(pdir / "assets")
        registry[pid] = {"name": p["name"], "base": pid, "repo": p["repo"],
                         "collab": dict(COLLAB_DOOR)}

    # A project that left the registry leaves the hub. Only directories this
    # script made (they hold an engine/manifest.json) — never index.html or
    # anything else the hub keeps beside them.
    if primer.is_dir():
        for d in sorted(primer.iterdir()):
            if d.is_dir() and d.name not in registry and (d / "engine" / "manifest.json").is_file():
                changed.append(f"- {d}/")
                if not dry:
                    shutil.rmtree(d)

    _write(primer / "projects.json",
           (json.dumps(registry, indent=2, ensure_ascii=False) + "\n").encode(),
           changed, dry=dry)
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description="Vendor the editor into the staff hub.")
    ap.add_argument("--into", help="the hub checkout (default: `hub:` in vendor.local.yml)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    hub = hub_dir(a.into)
    if hub is None:
        print("no hub configured (add `hub: ~/staff-updates-internal` to "
              "vendor.local.yml, or pass --into) — nothing to do")
        return 0
    if not (hub / "functions" / "api" / "collab").is_dir():
        print(f"{hub} has no functions/api/collab — not the staff hub?", file=sys.stderr)
        return 2

    try:
        projects = sources(dry=a.dry_run)
    except RegistryError as err:
        print(f"registry error: {err}", file=sys.stderr)
        return 2
    if not projects:
        print("no projects to vendor", file=sys.stderr)
        return 1

    changed = vendor(hub, projects, dry=a.dry_run)
    verb = "would change" if a.dry_run else "changed"
    print(f"\n{hub / 'primer'}: {len(projects)} project(s) — "
          + ", ".join(p["id"] for p in projects))
    if not changed:
        print("  already in sync")
    else:
        for c in changed[:40]:
            print(f"  {verb}  {re.sub(str(hub) + '/', '', c)}")
        if len(changed) > 40:
            print(f"  … and {len(changed) - 40} more")
        if not a.dry_run:
            print("  review and commit in the hub — a push there deploys.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
