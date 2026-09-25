"""Vendor the engine into every consumer repo — automatically.

    python3 -m docsync.vendor            # sync all consumers in vendor.yml
    python3 -m docsync.vendor --dry-run  # show what would change
    python3 -m docsync.vendor --force    # overwrite even a dirty consumer
    python3 -m docsync.vendor --no-commit

This repo is the canonical home of the editor engine; consumer repos
(~/BudgetPrimerFinal, and any future report repo) carry vendored COPIES so
each stays self-contained for CI, other machines and madison. The copies
used to be synced by hand — "fix here, remember to copy" — which is exactly
the kind of accounting that rots. Now:

- Every consumer ALSO gets a permission guard in its own .claude/settings.json
  (permissions.ask on docsync/** and the EXTRA paths below) — an AI session
  working on that report defaults to driving the editor's UI for content
  changes (see the consumer's own CLAUDE.md) and has to ask before editing
  engine code directly. This is added and kept up to date the same way the
  code is: automatically, on every vendor run, to every consumer in
  vendor.yml/vendor.local.yml — including ones added after this was written.
  Merged in, never overwritten: existing permissions are left alone.

- WHAT the engine is needs no manifest: every file git tracks under
  docsync/ plus report2027/tools/serve.py. Add a module to the package and
  it vendors itself.
- WHO consumes it is one line per repo in vendor.yml.
- WHEN is automatic: .githooks/post-commit runs this after any commit that
  touches an engine path (install once per clone:
  git config core.hooksPath .githooks).

Per consumer this script: refuses if the consumer's engine paths are dirty
(engine edits must never originate in a consumer — fix here first;
--force overrides), copies the canonical files, restages every editor
binding in the consumer's own docsync.yml, and commits JUST the engine
paths there (a local commit only — deploying is still the consumer's own
explicit Push). It never starts or stops anyone's dev server; if the
consumer's live server is running, it reminds you to relaunch the app.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from docsync.stage import _origin_slug

ROOT = Path(__file__).resolve().parent.parent
VENDOR_YML = ROOT / "vendor.yml"
# Per-machine additions, gitignored — see consumers().
VENDOR_LOCAL = ROOT / "vendor.local.yml"

# Engine paths, relative to either repo root. serve.py rides along because
# consumers run the live server from their own checkout.
EXTRA = ["report2027/tools/serve.py",
         # The GitHub App's public client id. serve.py reads it from ITS OWN
         # repo root (SELF_ROOT), and the live report's server runs from the
         # consumer — without this ride-along, Connect GitHub works in this
         # repo and silently falls back to paste-a-token everywhere else.
         "github-app.json"]


def restage_cmd(bid: str, slug: str | None) -> list[str]:
    """The command that re-stages one binding of a CONSUMER, run with the
    consumer as cwd. Its repo is passed explicitly, and that is load-bearing:
    docsync.stage's own fallback is the origin of the checkout the running
    module was imported from (ROOT), which is right when a repo stages itself
    and wrong the moment one repo stages another. Left to the fallback, a
    vendor whose import resolved to primer-editor's docsync — a worktree, a
    PYTHONPATH, the consumer's package mid-rewrite under --force — stamped the
    Budget Primer's manifest with Hawaii-Appleseed/primer-editor, and the hub
    then named a DIFFERENT collab room for it (2026-09-18). No origin means no
    flag: the manifest's own record, then the editor's ask, stand as before."""
    cmd = [sys.executable, "-m", "docsync.stage", "--id", bid]
    if slug:
        cmd += ["--repo", slug]
    return cmd


def _sh(args: list[str], cwd: Path) -> str:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          check=True).stdout


def _read_at_head(rel: str) -> bytes | None:
    """Bytes of a tracked file as of the last commit — not the working tree.

    Vendoring reads from HEAD, not disk, so an unrelated uncommitted edit
    elsewhere in the repo (someone else's in-progress work on a different
    engine file) can never ride along into a consumer. None means the path
    has no committed version yet (e.g. staged but never committed), which is
    treated the same as "doesn't exist" — nothing to vendor.
    """
    r = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=ROOT,
                       capture_output=True, check=False)
    return r.stdout if r.returncode == 0 else None


def engine_files() -> list[str]:
    """Every tracked file under docsync/ (the package IS the manifest)."""
    files = [f for f in _sh(["git", "ls-files", "docsync/"], ROOT).splitlines()
             if f and not f.endswith((".pyc",))]
    return files + EXTRA


def consumers() -> list[Path]:
    """Every repo this engine syncs into: vendor.yml, plus vendor.local.yml if
    it exists. The split matters because vendor.yml is TRACKED — a consumer
    listed there travels to everyone who clones, and their post-commit hook
    would then try to write into a checkout they do not have. So the tracked
    file stays empty by default and your own machine's consumers live in
    vendor.local.yml, which is gitignored."""
    import yaml                                       # noqa: PLC0415
    entries = []
    for f in (VENDOR_YML, VENDOR_LOCAL):
        if not f.exists():
            continue
        data = yaml.safe_load(f.read_text()) or {}
        entries.extend(data.get("consumers") or [])
    if not entries:
        return []
    out = []
    for c in entries:
        p = Path(str(c["path"] if isinstance(c, dict) else c)).expanduser()
        if not (p / "docsync.yml").exists():
            print(f"  skipping {p}: no docsync.yml (not a docsync repo)",
                  file=sys.stderr)
            continue
        out.append(p)
    return out


def dirty_engine_paths(repo: Path) -> list[str]:
    out = _sh(["git", "status", "--porcelain", "--",
               "docsync", *EXTRA], repo)
    return [l for l in out.splitlines() if l.strip()]


def stage_ids(repo: Path) -> list[str]:
    """Editor bindings in the CONSUMER's registry, read with its own code."""
    code = ("import sys; sys.path.insert(0, '.');"
            "from docsync.registry import load_registry;"
            "print('\\n'.join(b.id for b in load_registry() if b.editor))")
    try:
        return [l for l in _sh([sys.executable, "-c", code], repo).splitlines() if l]
    except subprocess.CalledProcessError as e:
        print(f"  could not read {repo.name}/docsync.yml: {e.stderr.strip()}",
              file=sys.stderr)
        return []


def _ask_patterns(extra: list[str]) -> list[str]:
    """Edit(...)/Write(...) patterns for the engine paths a consumer should
    confirm before touching directly: docsync/ itself, plus the directory
    each EXTRA file lives in (report2027/tools/serve.py -> report2027/tools/**).
    Derived rather than hard-coded so it stays correct if EXTRA ever grows."""
    dirs = {"docsync"} | {str(Path(rel).parent) for rel in extra}
    patterns = []
    for d in sorted(dirs):
        if d == ".":
            # A root-level EXTRA (github-app.json) guards ITSELF, not the
            # whole repo — "Edit(./**)" would make the consumer confirm every
            # file it owns, which is the opposite of a targeted backstop.
            patterns += [f"Edit({rel})" for rel in extra if str(Path(rel).parent) == "."]
            patterns += [f"Write({rel})" for rel in extra if str(Path(rel).parent) == "."]
            continue
        patterns += [f"Edit({d}/**)", f"Write({d}/**)"]
    return patterns


def ensure_permission_guard(repo: Path, extra: list[str], *, dry: bool) -> bool:
    """Make editing the engine directly ask first, in this consumer.

    `ask`, not `deny`: an engine change is sometimes exactly what is wanted
    (this very script is proof — it lives under docsync/), and `ask` lets it
    through with one confirmation instead of requiring settings.json to be
    hand-edited first to allow it. The default this backstops is "drive the
    editor's UI for content changes," stated in the consumer's own CLAUDE.md;
    this is what happens on the rare occasion an AI session reaches for a file
    edit instead — asked once, not silently blocked and not silently allowed.

    Merges into whatever is already there. Only ever ADDS missing patterns to
    permissions.ask; nothing existing is removed, reordered or overwritten.
    Returns whether anything changed."""
    settings_path = repo / ".claude" / "settings.json"
    try:
        cur = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    except json.JSONDecodeError:
        print(f"  {settings_path.relative_to(repo)} is not valid JSON — leaving it alone")
        return False
    ask = cur.setdefault("permissions", {}).setdefault("ask", [])
    added = [p for p in _ask_patterns(extra) if p not in ask]
    if not added:
        return False
    if dry:
        print(f"  would guard engine edits in .claude/settings.json: {', '.join(added)}")
        return True
    ask.extend(added)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(cur, indent=2) + "\n")
    print(f"  guarded engine edits in .claude/settings.json: {', '.join(added)}")
    return True


def vendor_one(repo: Path, files: list[str], *, dry: bool, force: bool,
               commit: bool) -> bool:
    print(f"\n{repo}")
    dirty = dirty_engine_paths(repo)
    if dirty and not force:
        print("  REFUSING: engine paths are dirty in the consumer — engine "
              "changes must be made in Cider, not here. Reconcile "
              "(or --force to overwrite):")
        for l in dirty[:10]:
            print(f"    {l}")
        return False

    changed = []
    for rel in files:
        src_bytes = _read_at_head(rel)
        if src_bytes is None:
            continue
        dst = repo / rel
        if dst.exists() and dst.read_bytes() == src_bytes:
            continue
        changed.append(rel)
        if not dry:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src_bytes)

    # Checked every run, independent of whether the engine files themselves
    # changed — an already-in-sync consumer still needs this the first time,
    # and a consumer added to vendor.yml after this was written gets it the
    # very next time vendor runs, with no extra step.
    guarded = ensure_permission_guard(repo, EXTRA, dry=dry)

    if not changed and not guarded:
        print("  already in sync")
        return True
    for rel in changed:
        print(f"  {'would copy' if dry else 'copied'}  {rel}")
    if dry:
        return True

    if changed:
        ids = stage_ids(repo)
        slug = _origin_slug(repo)
        for bid in ids:
            r = subprocess.run(restage_cmd(bid, slug), cwd=repo)
            if r.returncode != 0:
                print(f"  STAGE FAILED for '{bid}' — fix and re-run "
                      f"python3 -m docsync.stage --id {bid} in {repo}",
                      file=sys.stderr)
                return False

    if commit:
        # engine paths plus the restaged editor bundles (each binding's
        # editor dir lives under docs/ by convention; add skips the unchanged)
        paths = ["docsync", *EXTRA] + (["docs"] if (repo / "docs").is_dir() else [])
        if guarded:
            paths.append(".claude")
        subprocess.run(["git", "add", "--", *paths], cwd=repo, capture_output=True)
        bits = ([f"Files: {', '.join(changed)}"] if changed else []) \
             + (["Added a permissions.ask guard on engine paths in "
                 ".claude/settings.json"] if guarded else [])
        msg = ("vendor engine from Cider\n\n"
               "Automated copy by python3 -m docsync.vendor (Cider "
               "is the engine's canonical home; see its CLAUDE.md). "
               + " ".join(bits))
        r = subprocess.run(["git", "commit", "-m", msg], cwd=repo,
                           capture_output=True, text=True)
        if r.returncode == 0:
            print(f"  committed in {repo.name} (local only — Push stays yours)")
        else:
            print("  nothing new to commit" if "nothing" in r.stdout
                  else f"  commit failed: {r.stderr.strip()}")

    if any(rel.endswith(("serve.py", "edit.html")) for rel in changed):
        print("  NOTE: if this repo's live editor server is running, relaunch "
              "its app to pick up the new engine (never restart it from here).")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Vendor the engine to consumers.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="overwrite a consumer whose engine paths are dirty")
    ap.add_argument("--no-commit", action="store_true",
                    help="copy + restage but leave the consumer uncommitted")
    args = ap.parse_args()

    files = engine_files()
    ok = True
    reps = consumers()
    if not reps:
        return 0
    for repo in reps:
        ok = vendor_one(repo, files, dry=args.dry_run, force=args.force,
                        commit=not args.no_commit) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
