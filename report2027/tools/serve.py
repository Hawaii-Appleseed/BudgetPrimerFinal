#!/usr/bin/env python3
"""Local live-editing server for docsync reports — project-aware.

    make -C report2027 live          # http://localhost:8010/primer/start.html

The loop this exists for: edit a report — by hand in content.md / layout.json,
or by asking Claude to — and see it in the browser in about a second. No
commit, no CI wait. Save in the draft editor writes the files and commits
LOCALLY; Push is a separate, explicit step that sends it to GitHub, so a save
can never surprise-trigger a deploy or a GitHub Actions run.

Local-first: the files on disk are the single source of truth. The editor
served from here reads and writes THEM (not the GitHub API), and a watcher
rebuilds each project's preview whenever its own files change — so your edits
and Claude's land in the same place and show up together. Stdlib only, except
PyYAML for docsync.yml (already required by docsync.registry).

One process, many projects, possibly many repos: this repo's own docsync.yml
(rxkids, demo-report, and this repo's own budget-primer test fixture) is
always available; `docs/primer/projects.json` — the same registry start.html
reads for its grid — can additionally name a project whose real files live in
a DIFFERENT repo on disk (an optional "local_root"), so a live server always
launched from here can still edit, say, the real Budget Primer content that
lives in ~/BudgetPrimerFinal. Everything below is keyed by project id; there
is no more one global ROOT/CONTENT/LAYOUT — see PROJECTS.
"""
from __future__ import annotations

import base64
import contextlib
import functools
import glob
import importlib.util
import io
import json
import re
import mimetypes
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import hashlib
import threading
import time
import webbrowser
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SELF_ROOT = Path(__file__).resolve().parents[2]   # this repo — wherever serve.py lives


def _engine_sig() -> str:
    """A fingerprint of the server's OWN code, as it currently sits on disk.

    A long-lived server keeps running whatever it imported at launch. Update it,
    vendor a new engine over it, switch branches — the process does not care,
    and nothing in the UI said so. That is not theoretical: a server booted
    before serve.py grew /__pilot answered `unknown endpoint` to a client whose
    editor had the feature, with no way to tell that the SERVER was the old one.
    Compared against the value taken at boot, a difference means the code on
    disk has moved on and only a relaunch will pick it up.
    """
    h = hashlib.sha256()
    files = [Path(__file__)] + sorted((SELF_ROOT / "docsync").glob("*.py"))
    for f in files:
        try:
            h.update(f.read_bytes())
        except OSError:          # deleted mid-read: that is itself a change
            h.update(b"\0")
    return h.hexdigest()[:16]
DOCS = SELF_ROOT / "docs"           # shared UI + any project staged directly under this repo
# The report is a Chrome-printed PDF; export reuses the same engine. Override
# with CHROME_BIN on a non-mac host.
CHROME = os.environ.get("CHROME_BIN") or \
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = int(os.environ.get("PRIMER_PORT", "8010"))


# ---- project registry: which report lives in which repo -------------------
def _load_bindings(repo_root: Path) -> dict:
    """Import repo_root's OWN vendored docsync.registry so every path resolves
    against THAT repo, not this one. Each repo carries its own copy of
    docsync/registry.py (see CLAUDE.md's vendoring rule); its module-level
    ROOT is baked to wherever that file physically lives, so importing it
    from its real location — rather than reading its yml with OUR copy — is
    what makes a foreign repo's Binding.content etc. come out correct."""
    reg_file = repo_root / "docsync" / "registry.py"
    if not reg_file.is_file():
        return {}
    mod_name = f"_docsync_registry_{abs(hash(str(repo_root)))}"
    spec = importlib.util.spec_from_file_location(mod_name, reg_file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    try:
        return {b.id: b for b in mod.load_registry()}
    except mod.RegistryError as e:
        print(f"  ({repo_root.name}: {e})")
        return {}


def _load_projects() -> dict:
    """id -> {root, binding}. The registry (projects.json) always lives in,
    and is read from, THIS repo (see the comment in start.html) — the one
    list of every project this server's grid shows, whatever repo each
    project's files actually live in (an entry's optional "local_root" names
    it; absent means it lives right here, alongside serve.py itself)."""
    projects = {}
    self_bindings = _load_bindings(SELF_ROOT)
    reg_path = DOCS / "primer" / "projects.json"
    registry = json.loads(reg_path.read_text()) if reg_path.is_file() else {}
    for pid, entry in registry.items():
        root = Path(entry["local_root"]).expanduser() if entry.get("local_root") else SELF_ROOT
        bindings = self_bindings if root == SELF_ROOT else _load_bindings(root)
        b = bindings.get(pid)
        if b is None:
            print(f"  (projects.json lists '{pid}' but {root}/docsync.yml has no such binding — skipping)")
            continue
        projects[pid] = {"root": root, "binding": b}
    # A binding in THIS repo's own docsync.yml that projects.json doesn't
    # mention yet still gets served (so a freshly staged report works before
    # anyone remembers to register it) — it just won't appear on the grid.
    for pid, b in self_bindings.items():
        projects.setdefault(pid, {"root": SELF_ROOT, "binding": b})
    return projects


PROJECTS = _load_projects()

# ---- GitHub sign-in (device flow, proxied) ---------------------------------
# GitHub's login endpoints refuse cross-origin BROWSER calls, which is why the
# hosted editor needs a relay worker. This server has no such problem: it can
# forward the two device-flow calls itself, so a LOCAL editor signs in with no
# worker, no Cloudflare account, nothing deployed. The client id is the one
# per-organisation registration (docs/primer/OAUTH_SETUP.md step 1 — the app,
# not the relay); it is public by design. Overridable bases so the test suite
# can stand in for GitHub without network.
def _gh_client_default() -> str:
    """The GitHub App's public client id.

    Tracked in github-app.json rather than left to an environment variable:
    the id is public by design, and every colleague's install needs the same
    one — an env var set on one machine helps nobody else. The environment
    still wins, so a test (or a second App) can override it.
    """
    try:
        return json.loads((SELF_ROOT / "github-app.json").read_text()).get("client_id", "")
    except (OSError, json.JSONDecodeError):
        return ""


GH_CLIENT = os.environ.get("PRIMER_GH_CLIENT") or _gh_client_default()
GH_BASE = os.environ.get("PRIMER_GH_BASE", "https://github.com")
GH_API = os.environ.get("PRIMER_GH_API", "https://api.github.com")
# The token the SERVER pushes with, once someone signs in. One file, account-
# level (a token is a person, not a project), never committed (.gitignore).
TOKEN_FILE = SELF_ROOT / ".primer-github-token"


def _gh_token() -> dict:
    try:
        return json.loads(TOKEN_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _gh_http(url: str, body: dict | None = None, token: str = "") -> dict:
    """One JSON round-trip to GitHub (or the test stand-in). urllib, not a
    dependency: two endpoints do not justify a package."""
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode() or "{}")
DEFAULT_PROJECT = "budget-primer" if "budget-primer" in PROJECTS else next(iter(PROJECTS), None)

# Foreign-repo projects: nothing under THIS server's docs/ tree can reach
# another repo's files on disk, so each gets its own reserved URL prefix,
# mapped straight to that project's own staged editor dir (wherever THAT
# repo's docsync.yml editor.dir put it — e.g. BudgetPrimerFinal/docs/primer).
EXTERNAL_MOUNTS = {
    pid: p["binding"].editor.dir
    for pid, p in PROJECTS.items()
    if p["root"] != SELF_ROOT and p["binding"].editor
}
# The inverse, for SELF-rooted projects: which docs/<name> belongs to which
# project id, so a preview page's live-reload banner (and __ping/__events)
# knows which project's build state to show.
SELF_DIR_TO_PID = {
    p["binding"].editor.dir.name: pid
    for pid, p in PROJECTS.items()
    if p["root"] == SELF_ROOT and p["binding"].editor
}


def _register_project(pid: str, root: Path, binding) -> None:
    """Bring a project to life in a RUNNING server — the counterpart of the
    import-time loaders above, kept in step with all four structures they
    fill. The watcher picks the new project up on its next pass (it re-reads
    PROJECTS each sweep), and the start-page registry cache is dropped so the
    grid shows it immediately."""
    PROJECTS[pid] = {"root": root, "binding": binding}
    STATE[pid] = ProjectState()
    if binding.editor:
        if root != SELF_ROOT:
            EXTERNAL_MOUNTS[pid] = binding.editor.dir
        else:
            SELF_DIR_TO_PID[binding.editor.dir.name] = pid
    _default_registry.cache_clear()


@functools.lru_cache(maxsize=1)
def _default_registry() -> dict:
    """The editor's start-page registry, derived from what this server serves.
    Merged UNDER any on-disk projects.json — see do_GET. `base` is the staged
    editor dir relative to docs/primer/, which is what start.html opens; a
    project living in another repo is reached through its reserved mount.

    Cached: it depends only on PROJECTS, which is fixed at import, and it shells
    out to `git remote get-url` once per project. Recomputing it per request
    made the start page's registry fetch slow enough to still be in flight when
    the page was first scripted, which reads as an EMPTY project list rather
    than a slow one."""
    out = {}
    for pid, p in sorted(PROJECTS.items()):
        e = p["binding"].editor
        if not e:
            continue
        base = (f"../_repo-{pid}" if p["root"] != SELF_ROOT
                else "." if e.dir.name == "primer" else f"../{e.dir.name}")
        entry = {"name": pid.replace("-", " ").title(), "base": base}
        repo = _origin_of(p["root"])
        if repo:
            entry["repo"] = repo
        out[pid] = entry
    return out


def _origin_of(root: Path) -> str | None:
    """'owner/name' for a checkout's origin, so a synthesised registry points
    Save/Push at the repo the files actually came from."""
    r = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"],
                       capture_output=True, text=True)
    url = r.stdout.strip().removesuffix(".git")
    m = re.search(r"[:/]([^/:]+/[^/]+)$", url) if url else None
    return m.group(1) if m else None


def _watch_patterns(root: Path, b) -> list[str]:
    """Everything a rebuild of this ONE project should react to: its content/
    layout/renderer/engine files (already absolute — resolved by whichever
    repo's registry produced this Binding), any extra globs docsync.yml named
    under `watch:`, and the shared engine code (docsync/*.py, edit.html) that
    lives alongside it in the SAME repo."""
    pats = [str(b.content)]
    e = b.editor
    if e:
        if e.layout:
            pats.append(str(e.layout))
        pats.append(str(e.render))
        pats.extend(str(f) for f in e.engine)
    pats.extend(str(root / w) for w in b.watch)
    pats.append(str(root / "docsync" / "*.py"))
    pats.append(str(root / "docsync" / "editor" / "edit.html"))
    return pats


class ProjectState:
    """One project's rebuild state — a separate lock/condition per project so
    a slow build in one report never blocks another's Save/Push/watch."""

    def __init__(self):
        self.version = 0
        self.error = None
        self.mtimes: dict[str, float] = {}
        self.lock = threading.Lock()
        self.cond = threading.Condition()
        # ---- pilot relay (see _pilot). All THREE fields live under self.cond,
        # deliberately: the SSE loop already waits on that condition, so a
        # queued op wakes it with no second lock to order against.
        self.pilot_pending: list[dict] = []   # queued, not yet handed to a tab
        self.pilot_results: dict[str, dict] = {}   # op id -> what the tab returned
        self.pilot_n = 0                      # op-id counter


STATE = {pid: ProjectState() for pid in PROJECTS}

# ---- host-state lock -------------------------------------------------------
# docsync.yml and docs/primer/projects.json are read-modify-written from more
# than one process at once: this server (scaffold/adopt/connect), a second
# server (the user's app while a test run's own server is up), and the test
# suite's cleanup rewrites. Two concurrent read-modify-writes silently lose
# one of the updates — which is exactly the docsync.yml scaffold race that
# left zz-spec-* bindings committed three separate times before it was
# understood as a pattern. A directory is the one primitive every platform
# creates atomically, so mkdir IS the lock; the pid inside lets a waiter
# steal from a holder that died without releasing. The test suite's
# fixtures/host-state.js takes the SAME directory, which is what makes the
# guarantee cross-process rather than per-process politeness.
HOST_LOCK = Path(tempfile.gettempdir()) / "docsync-host-state.lock"


@contextlib.contextmanager
def _host_lock(timeout: float = 30.0):
    deadline = time.time() + timeout
    while True:
        try:
            HOST_LOCK.mkdir()
            (HOST_LOCK / "pid").write_text(str(os.getpid()))
            break
        except FileExistsError:
            pid = 0
            try:
                pid = int((HOST_LOCK / "pid").read_text() or 0)
            except (OSError, ValueError):
                pass
            if pid:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:      # holder died mid-hold: steal
                    shutil.rmtree(HOST_LOCK, ignore_errors=True)
                    continue
                except PermissionError:
                    pass                        # alive, someone else's — wait
            if time.time() > deadline:
                # Held beyond any plausible critical section (they are file
                # writes, milliseconds): a wedged lock must not brick every
                # scaffold and adopt on the machine. Steal and press on.
                shutil.rmtree(HOST_LOCK, ignore_errors=True)
                continue
            time.sleep(0.02)
    try:
        yield
    finally:
        shutil.rmtree(HOST_LOCK, ignore_errors=True)


# /__inventory's addressable-id list, keyed (project, build version). Learning
# the ids costs a whole edit-mode render, and they cannot change without a
# rebuild — so one entry, replaced whenever the version moves, is the entire
# cache anyone needs.
INVENTORY_IDS: dict = {}


def _snapshot(patterns: list[str]) -> dict:
    out = {}
    for pat in patterns:
        for p in glob.glob(pat):
            try:
                out[p] = os.path.getmtime(p)
            except OSError:
                pass
    return out


def _ahead(root: Path) -> int:
    """Commits sitting on this project's repo that origin does not have yet —
    what a Push would send. 0 whenever there is nothing to push, including if
    HEAD has no upstream (a detached checkout) rather than raising."""
    r = subprocess.run(["git", "-C", str(root), "rev-list", "--count", "@{u}..HEAD"],
                        capture_output=True, text=True)
    try:
        return int(r.stdout.strip())
    except ValueError:
        return 0


# ---- report-content updates (the OTHER repo) -------------------------------
# tools/selfupdate.py keeps the EDITOR current. A report that lives in its own
# repo — the live Budget Primer on a colleague's machine — had no equivalent:
# new content pushed by someone else was invisible until their next Push was
# rejected with "fetch first". Same contract as the editor's update, so there
# is one idea to learn rather than two: check in the background, tell the
# person, never apply anything under a running editor.
CONTENT_POLL = 5 * 60
# root(str) -> {"behind", "can", "why", "log"}. Keyed by ROOT, not project:
# several projects can share one checkout, and it is the checkout that is
# behind. SELF_ROOT is deliberately absent — selfupdate owns that one, and two
# mechanisms fast-forwarding the same checkout is how you get a merge nobody
# asked for.
CONTENT: dict[str, dict] = {}


def _content_roots() -> set:
    return {p["root"] for p in PROJECTS.values() if p["root"] != SELF_ROOT}


# ---- will a Push actually work? --------------------------------------------
# A Push that CANNOT succeed used to be discoverable exactly one way: press it
# and read the wreck. A global Git LFS pre-push hook (core.hooksPath, a tool
# not on a launchd-started server's minimal PATH) silently refused every push
# from this editor for SIX DAYS on one machine — 45 commits piled up behind a
# button that looked perfectly ready, and nothing anywhere said so.
#
# `git push --dry-run` is the honest question, because it runs the SAME
# machinery the real push does: the hooks, the credential helper, the remote's
# accept/reject. It transfers no objects and moves no refs. Answering it costs
# a network round trip, so it rides the background poll rather than the ping,
# and only when there is something to push — a clean tree asks nobody anything.
# root(str) -> {"ok", "why", "branch", "deploy", "checked"}
PUSH_HEALTH: dict[str, dict] = {}
# The one background job here that touches the NETWORK. Off for the test
# suite (playwright.config.js), which must not depend on GitHub being
# reachable — and which would otherwise see a real answer for a real
# checkout bleed into specs that mock every other endpoint.
PUSH_PROBE = os.environ.get('PRIMER_PUSH_PROBE', '1') != '0'


def _push_health(root: Path, deploy: str) -> dict:
    """Would a Push from this checkout succeed, and where would it land?"""
    out = {"ok": True, "why": "", "branch": "", "deploy": deploy,
           "deployAhead": 0, "checked": time.time()}
    try:
        out["branch"] = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except RuntimeError:
        pass
    if not _ahead(root):
        return out                       # nothing to push: nothing to warn about
    # How far the DEPLOY branch would move. Pushing HEAD:<deploy> is what
    # actually publishes, and when HEAD is not the deploy branch that number
    # is the whole story a person needs before pressing (see _push).
    try:
        out["deployAhead"] = int(_git(
            root, "rev-list", "--count", f"origin/{deploy}..HEAD").strip() or 0)
    except (RuntimeError, ValueError):
        pass
    try:
        _git(root, "push", "--dry-run", "origin", "HEAD", timeout=60)
    except RuntimeError as e:
        msg = str(e)
        out["ok"] = False
        # Name the causes worth naming; anything else travels verbatim, since
        # a git message the person can paste beats a guess about it.
        if "git-lfs" in msg or "git lfs" in msg:
            out["why"] = ("a Git LFS pre-push hook is refusing the push — git-lfs "
                          "is not on this server's PATH. Relaunch the editor app; "
                          "if it persists, install git-lfs or remove the hook "
                          "named in the error.")
        elif "rejected" in msg or "fetch first" in msg:
            out["why"] = ("the remote has commits this checkout does not — "
                          "reconcile before pushing")
        elif re.search(r"authenticat|credential|denied|403", msg, re.I):
            out["why"] = ("this computer has no usable GitHub credential — "
                          "File ▸ Connect GitHub sets it up")
        else:
            out["why"] = msg.strip().splitlines()[-1][:200] if msg.strip() else "push would fail"
    return out


def _content_status(root: Path) -> dict:
    """How far behind origin this checkout is, and whether it can safely
    catch up. Fetch first — `behind` is meaningless against a stale remote."""
    blank = {"behind": 0, "can": False, "why": "", "log": []}
    if not (root / ".git").exists():
        return dict(blank, why="not a git checkout")
    try:
        _git(root, "fetch", "--quiet", "origin", timeout=60)
    except RuntimeError as e:
        return dict(blank, why=f"could not reach GitHub: {str(e)[:120]}")
    try:
        behind = int(_git(root, "rev-list", "--count", "HEAD..@{u}").strip() or 0)
    except (RuntimeError, ValueError):
        return dict(blank, why="no upstream branch to compare with")
    if not behind:
        return blank
    log = [l for l in _git(root, "log", "--format=%s", "-5", "HEAD..@{u}"
                           ).splitlines() if l.strip()]
    ahead = _ahead(root)
    dirty = bool(_git(root, "status", "--porcelain", "--untracked-files=no").strip())
    # Fast-forward ONLY. Unsaved edits or unpushed commits of their own mean a
    # real merge, which is a decision — and one a person mid-edit should not
    # discover as a side effect of a button labelled "update".
    if dirty:
        why = "you have unsaved changes on disk — Save or discard them first"
    elif ahead:
        why = f"you have {ahead} commit{'s' if ahead > 1 else ''} not pushed yet — Push first"
    else:
        why = ""
    return {"behind": behind, "can": not (dirty or ahead), "why": why, "log": log}


def content_watcher():
    """Catch up at startup, then report every CONTENT_POLL.

    The FIRST pass applies a safe fast-forward, the rest only offer. That
    split is the whole point: server startup IS app launch — no editor is
    open yet, so nobody is mid-sentence — and it mirrors what selfupdate.py
    already does for the editor's own code at exactly the same moment. Once
    someone is working, an update arriving under them is theirs to accept,
    which is what the button is for.

    Safe means fast-forward only, and _content_status already refused when
    the person has uncommitted work or unpushed commits.
    """
    first = True
    while True:
        for root in _content_roots():
            try:
                st = _content_status(root)
                if first and st["behind"] and st["can"]:
                    try:
                        _git(root, "merge", "--ff-only", "@{u}")
                        print(f"  [{root.name}] updated — {st['behind']} change"
                              f"{'s' if st['behind'] > 1 else ''} from GitHub")
                        st = _content_status(root)
                        for pid, p in PROJECTS.items():
                            if p["root"] == root:
                                rebuild(pid, "content-launch")
                    except RuntimeError as e:
                        print(f"  [{root.name}] could not update: {str(e)[:120]}")
                CONTENT[str(root)] = st
                # Would a Push work? Asked here, on the same slow loop, so the
                # answer is already waiting when someone looks at the button —
                # rather than being discovered by pressing it. Only when there
                # IS something to push; _push_health returns early otherwise.
                # NOT on the first pass. That one runs at server startup, when
                # the person is waiting for an editor — and this asks the
                # network, which is exactly the wrong moment for a background
                # nicety to cost a round trip. (It also kept the test suite's
                # own server phoning GitHub mid-run, and a real answer for a
                # real checkout then leaked into specs that mock everything
                # else.) One poll cycle of latency is nothing against a
                # condition that went unnoticed for six days.
                dep = next((p2["binding"].editor.branch or "main"
                            for p2 in PROJECTS.values()
                            if p2["root"] == root and p2["binding"].editor), "main")
                h = {} if (first or not PUSH_PROBE) else _push_health(root, dep)
                if not h:
                    continue
                was = PUSH_HEALTH.get(str(root), {})
                PUSH_HEALTH[str(root)] = h
                if not h["ok"] and was.get("why") != h["why"]:
                    print(f"  [{root.name}] PUSH WOULD FAIL: {h['why']}")
            except Exception as e:                   # noqa: BLE001 — never die
                print(f"  content check error for {root} (continuing): {e!r}")
        first = False
        time.sleep(CONTENT_POLL)


def rebuild(pid: str, reason: str = "") -> None:
    """Run this ONE project's build command; record its version and any
    error, then wake everyone watching it. Serialised per-project, so a save
    and the watcher never build the same report over each other — but two
    DIFFERENT projects rebuild independently and concurrently."""
    p = PROJECTS.get(pid)
    if p is None:
        return
    st, root, b = STATE[pid], p["root"], p["binding"]
    with st.lock:
        if b.build:
            # Run through a shell, not shlex.split — the registry documents
            # `build` as "a shell command", and rxkids/demo-report's actually
            # ARE compound ones (render && re-stage the editor); a plain
            # split-and-exec would pass "&&" as a literal argv token instead
            # of chaining, silently skipping the second command.
            r = subprocess.run(b.build, shell=True, cwd=str(root),
                                capture_output=True, text=True)
            ok, output = r.returncode == 0, r.stdout + r.stderr
        else:
            ok, output = True, ""
        # Guarantee the live editor's staged copy is fresh after ANY rebuild —
        # regardless of whether THIS project's own `build:` remembers to
        # re-stage. budget-primer's Makefile chain does; rxkids/demo-report
        # originally didn't, which silently left the editor showing stale
        # content while `build` "succeeded". Idempotent (docsync.stage just
        # copies declared files) and safe to run unconditionally: it preserves
        # whatever --repo a human already set (see stage.py), so this can't
        # un-link a project from the repo it pushes to. This is what makes the
        # live-reload loop correct for EVERY docsync.yml-registered project —
        # one freshly authored here, or one pointed at via a projects.json
        # local_root — without relying on each one's own build: string.
        if ok and b.editor:
            sr = subprocess.run(["python3", "-m", "docsync.stage", "--id", pid],
                                 cwd=str(root), capture_output=True, text=True)
            if sr.returncode != 0:
                ok, output = False, output + "\n[re-stage]\n" + sr.stdout + sr.stderr
        st.mtimes = _snapshot(_watch_patterns(root, b))   # AFTER the build, so
        if ok:                                            # its own writes don't
            st.version += 1                               # look like a fresh change
            st.error = None
            print(f"  [{pid}] rebuilt ({reason or 'change'}) -> v{st.version}")
        else:
            st.error = output.strip()[-3000:]
            print(f"  [{pid}] BUILD FAILED ({reason}):\n{st.error}")
    with st.cond:
        st.cond.notify_all()


# When the watcher last completed a pass. A dead watcher is the worst failure
# this server has: it keeps serving perfectly, so everything LOOKS fine while
# every edit silently stops reaching the browser — engine changes included,
# which then surface far away as a stale validator rejecting new values. This
# is published in /__ping so the editor can say so instead of going quiet.
WATCH_BEAT = [time.time()]

# ---- editor-window lifecycle ------------------------------------------------
# The launcher nohups this server and exits; nothing ever stopped it when the
# last editor window closed, so it outlived its window by days — running ever
# staler code (see BOOT_SIG) and holding the port. The windows now say hello
# (on load and pageshow) and goodbye (a pagehide beacon), and when the last
# one has been gone for PRIMER_LINGER seconds the server exits on its own, so
# the next app-open always boots the current code. 0 disables — the Playwright
# suite does, because its pages come and go constantly by design.
#
# A hello with no goodbye (a force-quit browser sends no pagehide) would pin
# the server forever, hence the TTL: live tabs re-hello every 30 minutes, so
# an entry older than 2 hours is a ghost. And a server nobody ever opened a
# window on (make live before the browser starts) is left alone until the
# first hello — exit is armed by presence, never by absence alone.
CLIENTS: dict[str, float] = {}          # editor tab id -> last hello
# Armed at the /__hello ENDPOINT, not by the reaper observing CLIENTS: the
# reaper samples every 5s, so a window that opened and closed inside one tick
# was never seen at all and the exit stayed disarmed — the spec caught it.
EVER_HELLO = [False]
CLIENT_TTL = 2 * 3600
LINGER = int(os.environ.get("PRIMER_LINGER", "120"))
SERVERS: list = []                       # filled by main(); reaper shuts these down


def _close_own_windows() -> bool:
    """Close every Chrome window showing THIS server, macOS only.

    The quit path's other half. The AppleScript is the launcher's
    focus_existing() walk — window by window, tab by tab, matched on the URL
    prefix — ending in `close` instead of `activate`, so it rides the same
    Automation grant the launcher already prompted for. Matching on the tab
    URL rather than "the window the request came from" is deliberate: quit
    stops the server for EVERY window, so every window goes.

    Closing the TAB (not the window) handles both shapes: an --app window is
    its tab, so the window follows; and if the editor is ever a plain tab in
    a browsing window (the no-Chrome `open` fallback after Chrome was later
    installed), only the editor's tab goes, not the person's other tabs.

    Best-effort by design. Permission declined, Chrome absent, tab already
    closed — each just returns False, and the page's own status line (which
    never assumes the close worked) tells the person to close the window.
    """
    if sys.platform != "darwin":
        return False
    script = """
on run argv
  set closed to false
  tell application "System Events"
    if not (exists process "Google Chrome") then return "no"
  end tell
  tell application "Google Chrome"
    repeat with w in windows
      set doomed to {}
      repeat with t in tabs of w
        set u to URL of t
        repeat with target in argv
          if u starts with (target as text) then
            set end of doomed to t
            exit repeat
          end if
        end repeat
      end repeat
      repeat with t in doomed
        close t
        set closed to true
      end repeat
    end repeat
  end tell
  if closed then return "yes"
  return "no"
end run
"""
    try:
        r = subprocess.run(
            # Every origin a window of this server can carry: the launcher
            # opens localhost, _new_window accepts 127.0.0.1 and [::1] too.
            ["osascript", "-", f"http://localhost:{PORT}/",
             f"http://127.0.0.1:{PORT}/", f"http://[::1]:{PORT}/"],
            input=script, capture_output=True, text=True, timeout=6)
        return r.returncode == 0 and "yes" in (r.stdout or "")
    except (OSError, subprocess.SubprocessError):
        return False


def _idle_reaper():
    gone_since = None
    while True:
        time.sleep(5)
        now = time.time()
        for cid, t in list(CLIENTS.items()):
            if now - t > CLIENT_TTL:
                CLIENTS.pop(cid, None)
        if CLIENTS:
            gone_since = None
            continue
        if not EVER_HELLO[0]:
            continue
        # Never exit under a build/Save/Push — st.lock is held through those.
        if any(st.lock.locked() for st in STATE.values()):
            gone_since = None
            continue
        gone_since = gone_since or now
        if now - gone_since >= LINGER:
            print(f"  last editor window gone {LINGER}s — exiting so the next "
                  "open starts on current code")
            for srv in SERVERS:
                threading.Thread(target=srv.shutdown, daemon=True).start()
            return
# What the server's code looked like when this process started. Compared
# against the live value on every ping — see _engine_sig().
BOOT_SIG = _engine_sig()

# ---- staying current --------------------------------------------------------
# The app is distributed as a real checkout that fast-forwards itself, and it
# used to do that ONLY at launch — so anyone who leaves the editor open (which
# is the normal way to use it) ran whatever was current the day they last
# quit. This polls in the background and publishes the result; the editor shows
# it and can ask for it to be applied. Applying restarts this process, because
# the update includes this file.
UPDATE_POLL = 20 * 60
UPDATE = {"behind": 0, "can": False, "log": [], "sha": "", "date": "", "why": ""}
UPDATER = SELF_ROOT / "tools" / "selfupdate.py"


def _run_updater(*flags: str) -> dict:
    """Run the updater and read its JSON back. Every git decision lives there,
    not here — one of those decisions living in two places is how the last
    three bugs in this file happened."""
    if not UPDATER.is_file():
        return {"ok": False, "why": "no updater in this checkout"}
    try:
        r = subprocess.run([sys.executable, str(UPDATER), "--json", *flags],
                           cwd=str(SELF_ROOT), capture_output=True, text=True, timeout=180)
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as e:
        return {"ok": False, "why": f"updater failed: {e}"}


def _update_status(apply: bool = False) -> dict:
    """Ask the updater what it sees, or (apply=True) have it act. Kept in the
    updater rather than reimplemented here: it already knows every way an
    update can be unsafe, and one of those decisions living in two places is
    how the last three bugs in this file happened."""
    if not UPDATER.is_file():
        return dict(UPDATE, why="no updater in this checkout")
    cmd = [sys.executable, str(UPDATER), "--json"] + ([] if apply else ["--check"])
    try:
        r = subprocess.run(cmd, cwd=str(SELF_ROOT), capture_output=True,
                           text=True, timeout=180)
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as e:
        return dict(UPDATE, why=f"update check failed: {e}")


def update_watcher():
    """One check at startup so the version is known immediately, then every
    UPDATE_POLL. Never applies anything on its own — a running editor holds
    unsaved work, and only the person at the keyboard can say when."""
    while True:
        try:
            UPDATE.update(_update_status())
        except Exception as e:                       # noqa: BLE001 — never die
            print(f"  update check error (continuing): {e!r}")
        time.sleep(UPDATE_POLL)


def _update_payload() -> dict:
    """What the editor needs to show a version and, when there is one, an
    offer. Trimmed: the full status carries fields only the CLI uses."""
    return {"behind": UPDATE.get("behind", 0), "can": bool(UPDATE.get("can")),
            "log": UPDATE.get("log", [])[:5], "why": UPDATE.get("why", ""),
            "sha": UPDATE.get("sha", ""), "date": UPDATE.get("date", ""),
            "rollback": UPDATE.get("rollback", "")}


def watcher():
    patterns = {pid: _watch_patterns(p["root"], p["binding"]) for pid, p in PROJECTS.items()}
    for pid in PROJECTS:
        STATE[pid].mtimes = _snapshot(patterns[pid])
    while True:
        time.sleep(0.4)
        WATCH_BEAT[0] = time.time()
        # A project registered while the server runs (/__scaffold, /__adopt)
        # starts being watched on the next pass — its patterns just join in.
        for pid, p in PROJECTS.items():
            if pid not in patterns:
                patterns[pid] = _watch_patterns(p["root"], p["binding"])
                STATE[pid].mtimes = _snapshot(patterns[pid])
        for pid in list(PROJECTS):
            # One bad pass must never end the loop. An exception here used to
            # kill the thread outright and take live-reload with it for the
            # life of the process — observed after two days' uptime, with the
            # version frozen and no file change producing a rebuild.
            try:
                now = _snapshot(patterns[pid])
                if now != STATE[pid].mtimes:
                    rebuild(pid, "watch")
            except Exception as e:
                # Re-baseline so the same change does not re-fire every 0.4s.
                try:
                    STATE[pid].mtimes = _snapshot(patterns[pid])
                except Exception:
                    pass
                print(f"  [{pid}] watch error (continuing): {e!r}")


# ---- the server -------------------------------------------------------------
# %%PID%% is replaced per-response with the project id the served page belongs
# to (or "" when none applies, e.g. start.html) — every fetch/localStorage key
# below is scoped by it so two different projects' preview tabs, open at once,
# never elect a shared leader or react to each other's rebuilds.
RELOAD_JS = """
<script>
(function(){
  var PID = %%PID%%;
  var QS = PID ? ('?project=' + encodeURIComponent(PID)) : '';
  var last;
  var banner;
  function show(msg){
    if(!banner){ banner=document.createElement('div');
      banner.style.cssText='position:fixed;left:0;right:0;bottom:0;z-index:2147483647;'
        +'background:#8a2b1e;color:#fff;font:12px/1.5 ui-monospace,monospace;'
        +'white-space:pre-wrap;padding:10px 14px;max-height:45vh;overflow:auto;'
        +'box-shadow:0 -6px 24px rgba(0,0,0,.3)';
      document.body.appendChild(banner); }
    banner.textContent='build failed — the preview is the last good version\\n\\n'+msg;
  }
  function clear(){ if(banner){ banner.remove(); banner=null; } }
  function apply(d){
    if(!d) return;
    if(d.error){ show(d.error); return; }
    clear();
    if(last===undefined){ last=d.v; return; }
    if(d.v!==last) location.reload();
  }
  // ONE connection for ALL preview tabs OF THE SAME PROJECT. Each tab opening
  // its own SSE piled them up against Chromium's six-per-origin cap, and
  // stale tabs then deadlocked the whole origin. The tabs elect a single
  // LEADER (a heartbeat lock in localStorage) that holds the only /__events
  // stream and relays each event to the rest through localStorage — a
  // `storage` event fires in every OTHER tab. Followers hold no connection.
  var LK='primer-preview-leader:'+PID, EV='primer-preview-event:'+PID;
  var HB=2000, STALE=6000;
  var me=Math.random().toString(36).slice(2)+Date.now().toString(36);
  var es=null, leading=false, hb=0, evN=0;
  function relay(d){ try{ localStorage.setItem(EV, JSON.stringify({v:d.v, error:d.error, _n:(++evN)+'.'+Date.now()})); }catch(e){} }
  function setLock(){ try{ localStorage.setItem(LK, JSON.stringify({id:me,t:Date.now()})); }catch(e){} }
  function readLock(){ try{ return JSON.parse(localStorage.getItem(LK)||'null'); }catch(e){ return null; } }
  function onmsg(e){ var d={}; try{ d=JSON.parse(e.data||'{}'); }catch(x){} apply(d); relay(d); }
  function openStream(){ if(es) return; try{ es=new EventSource('/__events'+QS); es.onmessage=onmsg; }catch(e){} }
  function stopLeading(){ leading=false; if(es){ es.close(); es=null; } if(hb){ clearInterval(hb); hb=0; } }
  function startLeading(){ setLock(); if(leading) return; leading=true; openStream();
    hb=setInterval(function(){ setLock();
      fetch('/__ping'+QS,{cache:'no-store'}).then(function(r){return r.json();}).then(function(d){ apply(d); relay(d); }).catch(function(){}); }, HB); }
  function elect(){ if(document.hidden){ stopLeading(); return; }
    var lk=readLock();
    if(!lk || Date.now()-lk.t>=STALE || lk.id===me) startLeading(); else stopLeading(); }
  function catchUpOnce(){ fetch('/__ping'+QS,{cache:'no-store'}).then(function(r){return r.json();}).then(apply).catch(function(){}); }
  window.addEventListener('storage', function(e){ if(e.key===EV && !leading && e.newValue){ try{ apply(JSON.parse(e.newValue)); }catch(x){} } });
  document.addEventListener('visibilitychange', function(){ elect(); if(!document.hidden && !leading) catchUpOnce(); });
  window.addEventListener('pagehide', function(){ var lk=readLock(); if(lk && lk.id===me){ try{ localStorage.removeItem(LK); }catch(e){} } stopLeading(); });
  elect(); setInterval(elect, HB);
  if(!document.hidden) catchUpOnce();
})();
</script>
"""


class Handler(SimpleHTTPRequestHandler):
    # HTTP/1.1, so the event stream stays a persistent connection. Under the
    # 1.0 default a browser treats the SSE response as a finished short reply
    # and never receives another event — which looked like "I had to reload".
    protocol_version = "HTTP/1.1"

    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(DOCS), **k)

    def end_headers(self):
        # Never cache. A rebuild replaces edit.html and the engine files on
        # disk; without this the browser reuses the copy it loaded first and a
        # reload silently shows stale code — exactly the kind of ghost that
        # wastes an afternoon.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *a):        # quiet: only the rebuild lines matter
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, code, ctype, data, filename):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        clean = parsed.path
        pid = (parse_qs(parsed.query).get("project") or [None])[0]
        if clean == "/__ping":
            return self._json(200, self._ping_payload(pid))
        if clean == "/__templates":
            # What "+ New report" can start from, beyond the blank canvas.
            # Read via the same sys.path dance _scaffold uses, so the answer
            # comes from THIS checkout's engine, not whichever docsync module
            # was imported first.
            sys.path.insert(0, str(SELF_ROOT))
            try:
                from docsync.templates import listing
                return self._json(200, {"ok": True, "templates": listing()})
            except Exception as e:               # noqa: BLE001 — report it
                return self._json(200, {"ok": False, "error": str(e),
                                        "templates": []})
            finally:
                sys.path.remove(str(SELF_ROOT))
        if clean == "/__oauth/status":
            t = _gh_token()
            return self._json(200, {"ok": True, "connected": bool(t.get("token")),
                                    "login": t.get("login", ""),
                                    "client_id": GH_CLIENT})
        if clean == "/__events":
            return self._sse(pid)
        if clean == "/__inventory":
            q = parse_qs(parsed.query)
            return self._inventory_payload(
                pid, (q.get("elements") or ["1"])[0] != "0")
        # projects.json is per-machine and untracked, so a fresh clone has
        # none — and the start page then showed "No reports yet" over a server
        # that was happily serving four of them, with "+ New report" disabled
        # because that needs a repo it had no way to know. Synthesise the file
        # from what this process ACTUALLY serves.
        #
        # The on-disk file is an OVERRIDE LAYER, not a whitelist. It used to win
        # outright whenever it existed, which meant a file listing 2 of 5
        # bindings silently hid the other three — the same "serving it but not
        # showing it" bug as above, just harder to spot because the grid looked
        # populated rather than empty.
        #
        # The merge is a UNION, deliberately: every disk entry survives even if
        # this server cannot resolve its binding (a foreign repo whose checkout
        # is missing is still in the user's list, and adopting that slug must
        # still be refused as a duplicate), and every served project appears
        # even if the disk file forgot it.
        if clean.endswith("/projects.json"):
            registry = _default_registry()
            disk = DOCS / clean.lstrip("/")
            if disk.is_file():
                try:
                    overrides = json.loads(disk.read_text())
                except json.JSONDecodeError as exc:
                    print(f"  (projects.json is not valid JSON — {exc}; "
                          f"serving the synthesised registry)")
                    overrides = {}
                for pid, entry in overrides.items():
                    registry[pid] = {**registry.get(pid, {}), **entry}
            return self._json(200, registry)
        for mpid, mount_dir in EXTERNAL_MOUNTS.items():
            prefix = f"/_repo-{mpid}/"
            if clean == prefix[:-1] or clean.startswith(prefix):
                rel = clean[len(prefix):] or "index.html"
                return self._serve_mounted(mount_dir, rel, mpid)
        # Inject the live-reloader into served pages — except the editor, which
        # handles change events itself so it can keep your unsaved edits.
        if clean.endswith(".html") and not clean.endswith("edit.html"):
            return self._inject(clean)
        return super().do_GET()

    def _ping_payload(self, pid: str | None) -> dict:
        if pid not in STATE:
            return {"ok": True, "v": 0, "ahead": 0, "project": pid}
        st = STATE[pid]
        # Stamp WHOSE numbers these are. Tabs relay live events to each other
        # through localStorage, and the key alone used to be the only thing
        # keeping two projects apart — a tab with no project id fell into a
        # shared bucket and applied another project's counts as its own (a Push
        # button flipping between two repos' commit counts). A payload that says
        # who it belongs to can be checked instead of trusted.
        payload = {"ok": True, "v": st.version, "ahead": _ahead(PROJECTS[pid]["root"]),
                   "project": pid, "update": _update_payload(),
                   "content": CONTENT.get(str(PROJECTS[pid]["root"]), {}),
                   # Whether a Push would actually succeed, and where it would
                   # land — both answerable BEFORE the button is pressed. Empty
                   # until the background poll has looked once.
                   "push": PUSH_HEALTH.get(str(PROJECTS[pid]["root"]), {}),
                   "watchAge": round(time.time() - WATCH_BEAT[0], 1),
                   # True once the code on disk has moved past what this
                   # process is running. Only a relaunch fixes it, so the
                   # editor says exactly that rather than leaving the person
                   # to wonder why a shipped fix has no effect.
                   "serverStale": _engine_sig() != BOOT_SIG,
                   # Only ADVERTISED here, never handed out (see _sse). The
                   # leader's own 2s poll is what covers an SSE reconnect gap;
                   # a follower reading this simply never claims.
                   "pilotWaiting": len(st.pilot_pending)}
        if st.error:
            payload["error"] = st.error
        return payload

    # ---- inventory: the document as data, without a browser -----------------
    # The editor's own docsync.api.inventory() is the richer answer — it
    # MEASURES the live DOM, so it knows where every designed element actually
    # paints. This is the headless twin, for a CI check, a script, or an MCP
    # client with no browser at all: everything the FILES know, exactly, and
    # nothing guessed.
    #
    # What that boundary means in practice, and it is worth stating plainly
    # rather than letting a caller discover it: layout.json holds geometry only
    # for things somebody PLACED (shapes, boxes, tables, anything ever dragged).
    # A designed element sitting where its renderer put it has no stored
    # coordinates — its position exists only once a browser has laid the page
    # out — so `placed` covers the first kind and `elements` lists the ids of
    # both without claiming geometry for the second. Geometry for an unmoved
    # designed element needs the editor API; everything else is here.
    def _inventory_payload(self, pid, with_elements=True):
        pid = pid or DEFAULT_PROJECT
        p = PROJECTS.get(pid)
        if p is None:
            return self._json(404, {"ok": False, "error": f"unknown project '{pid}'",
                                    "projects": sorted(PROJECTS)})
        root, b = p["root"], p["binding"]
        st = STATE[pid]
        # The registry resolves every path against its own repo root at load,
        # so these are already absolute — including for a project mounted from
        # another checkout via projects.json's local_root.
        try:
            source = b.content.read_text()
        except OSError as e:
            return self._json(200, {"ok": False, "error": f"cannot read content: {e}"})
        layout = {}
        lay_path = b.editor.layout if (b.editor and b.editor.layout) else None
        if lay_path:
            try:
                layout = json.loads(lay_path.read_text() or "{}")
            except (OSError, json.JSONDecodeError) as e:
                return self._json(200, {"ok": False, "error": f"cannot read layout: {e}"})

        # Slots, parsed the way the editor's own slotRe does: a [[key]] on its
        # own line owns everything up to the next one. Full markdown, not the
        # 80-character snippet the browser inventory carries — a headless
        # caller has no second call to go and fetch the rest with.
        blocks = re.split(r"^\[\[([^\]]+)\]\]\s*$", source, flags=re.M)
        raw = {}
        for i in range(1, len(blocks) - 1, 2):
            raw[blocks[i]] = blocks[i + 1].strip()
        sources_block = raw.pop("sources", "")
        slots = [{"key": k, "md": v} for k, v in raw.items()]

        # Sources, with how many times each is actually cited — an uncited
        # source blocks publishing, so "which are unused" is exactly the
        # question a CI check wants to ask and it is one regex away here.
        sources = []
        for line in sources_block.splitlines():
            m = re.match(r"^\[([^\]]+)\]:\s*(.*?)\s+—\s+(\S+)\s*$", line.strip())
            if m:
                sid = m.group(1)
                sources.append({"id": sid, "text": m.group(2), "url": m.group(3),
                                "cites": len(re.findall(
                                    r"\[\^" + re.escape(sid) + r"\]", source))})

        # The sheet the report is actually on. layout.json carries a `page`
        # only once File ▸ Resize has written one, so an untouched report has
        # none — and reporting null there would be answering "what size is
        # this?" with a shrug. Fall back to what the binding says it was BUILT
        # at, which is the size in force until an override exists.
        page = layout.get("page") or {}
        built_w, built_h = (b.editor.page if b.editor else (None, None))
        pageless = "h" in page and page.get("h") is None
        page_out = {"w": page.get("w") if page.get("w") is not None else built_w,
                    "h": None if pageless else
                         (page.get("h") if page.get("h") is not None else built_h),
                    "pageless": pageless,
                    "overridden": bool(page)}
        pages = layout.get("pages") or {}
        placed = {
            "shapes": layout.get("shapes") or [],
            "boxes": layout.get("boxes") or [],
            "tables": layout.get("tables") or [],
            "positions": layout.get("positions") or {},
        }
        out = {
            "ok": True, "project": pid, "root": str(root),
            "version": st.version, "error": st.error,
            "ahead": _ahead(root),
            "page": page_out,
            # EMPTY means "no override" — the renderer's own designed order,
            # 1..N. The real sequence needs the page count, which only a render
            # knows, so it rides along with `elements` when that is asked for.
            "pageOrder": pages.get("order") or [],
            "blanks": [x.get("id") for x in (pages.get("blanks") or [])],
            "slots": slots, "sources": sources, "placed": placed,
            "fill": layout.get("fill") or {},
            "hidden": layout.get("hidden") or [],
            "locked": layout.get("locked") or [],
            "groups": layout.get("groups") or [],
            "note": "geometry here is what layout.json stores (placed objects only); "
                    "an unmoved designed element's position needs the editor's "
                    "docsync.api.inventory(), which measures the rendered page",
        }
        if with_elements:
            out["elements"] = self._addressable_ids(pid, root, b, st)
        return self._json(200, out)

    def _addressable_ids(self, pid, root, b, st):
        """Every id the editor could address, discovered by rendering the
        report in EDIT mode and reading its hooks back.

        The published build stamps no data-el/data-slot at all (Layout.attr
        and Content.slot_attr gate them behind DOCSYNC_EDIT), so the ids
        simply are not in the normal output — a render is the only way to
        learn them without a browser. Cached against the build version, so a
        caller polling this endpoint pays for one render per rebuild rather
        than one per request.
        """
        if not (b.editor and b.editor.render):
            return {"ok": False, "error": "this project has no editor render"}
        key = (pid, st.version)
        hit = INVENTORY_IDS.get(key)
        if hit is not None:
            return hit
        work = Path(tempfile.mkdtemp(prefix="primer-inv-"))
        out_html = work / "inventory.html"
        try:
            env = dict(os.environ)
            env["DOCSYNC_EDIT"] = "1"          # the whole point: stamp the hooks
            env["DOCSYNC_OUT"] = str(out_html)
            r = subprocess.run(["python3", str(b.editor.render)], cwd=str(root),
                               env=env, capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                return {"ok": False,
                        "error": "edit-mode render failed:\n"
                                 + (r.stdout + r.stderr).strip()[-800:]}
            html = out_html.read_text(errors="replace")
        except (OSError, subprocess.SubprocessError) as e:
            return {"ok": False, "error": f"edit-mode render failed: {e}"}
        finally:
            shutil.rmtree(work, ignore_errors=True)
        found = {
            "ok": True,
            "els": sorted(set(re.findall(r'data-el="([^"]+)"', html))),
            "shapes": sorted(set(re.findall(r'data-shape="([^"]+)"', html))),
            "slots": sorted(set(re.findall(r'data-slot="([^"]+)"', html))),
            "fills": sorted(set(re.findall(r'data-fill="([^"]+)"', html))),
            # How many sheets the renderer draws — the other half of pageOrder,
            # which is empty whenever nobody has reordered anything. With this,
            # a caller can read the real sequence as 1..pageCount.
            "pageCount": len(re.findall(r'<section[^>]*class="[^"]*\bpage\b', html)),
        }
        INVENTORY_IDS.clear()          # one build's worth is all anyone needs
        INVENTORY_IDS[key] = found
        return found

    def _reload_script(self, pid: str | None) -> str:
        return RELOAD_JS.replace("%%PID%%", json.dumps(pid or ""))

    def _inject(self, clean):
        f = DOCS / clean.lstrip("/")
        if not f.is_file():
            return super().do_GET()
        html = f.read_text(errors="replace")
        pid = SELF_DIR_TO_PID.get(clean.lstrip("/").split("/", 1)[0])
        # rpartition, not the first "</body>": start.html's new-report flow
        # builds a whole starter report as a JS template literal, which
        # contains its OWN literal "</body>" long before the page's real
        # closing tag. Replacing the first occurrence spliced a live
        # <script> block into the middle of that JS string and corrupted the
        # page's syntax ("Unexpected end of input") — the last occurrence is
        # always the actual closing tag.
        script = self._reload_script(pid)
        if "</body>" in html:
            head, sep, tail = html.rpartition("</body>")
            html = head + script + sep + tail
        else:
            html = html + script
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_mounted(self, base_dir: Path, rel_path: str, pid: str):
        """Serve a file from ANOTHER repo's staged editor dir, mounted at
        /_repo-<id>/ — the counterpart to SimpleHTTPRequestHandler's default
        serving of THIS repo's own DOCS tree."""
        try:
            f = (base_dir / rel_path).resolve()
            f.relative_to(base_dir.resolve())    # guard against a ../ escape
        except (ValueError, OSError):
            return self._json(404, {"ok": False, "error": "not found"})
        if not f.is_file():
            return self._json(404, {"ok": False, "error": "not found"})
        if f.suffix == ".html" and f.name != "edit.html":
            html = f.read_text(errors="replace")
            script = self._reload_script(pid)
            if "</body>" in html:
                head, sep, tail = html.rpartition("</body>")
                html = head + script + sep + tail
            else:
                html += script
            body, ctype = html.encode(), "text/html; charset=utf-8"
        else:
            body = f.read_bytes()
            ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, pid: str | None):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        st = STATE.get(pid)
        root = PROJECTS[pid]["root"] if pid in PROJECTS else None
        seen = None
        # A stream LEASE, not a lifetime. A zombie holder — a discarded tab or
        # a hung worker context the browser never quite kills — can sit on an
        # open stream forever, ACKing heartbeats, pinning one of the browser's
        # six per-origin sockets. Six of those and the origin is dead: every
        # new load queues behind sockets that never free ("localhost won't
        # load", server perfectly healthy). Ending the response after a bounded
        # window returns the socket no matter what; a LIVE client's EventSource
        # auto-reconnects in ~3s (and the leader's poll covers the gap), while
        # a zombie's held socket simply expires. Starvation becomes transient
        # instead of permanent — self-healing regardless of client bugs.
        deadline = time.time() + 45
        try:
            if st is None:
                time.sleep(min(45, deadline - time.time()))
                return
            while time.time() < deadline:
                with st.cond:
                    st.cond.wait_for(lambda: st.version != seen or st.error is not None
                                      or bool(st.pilot_pending),
                                      timeout=min(20, max(0.1, deadline - time.time())))
                    v, err = st.version, st.error
                # A heartbeat keeps proxies from closing an idle stream. ahead
                # rides along so a Save elsewhere (another tab, another Claude
                # session) updates every open editor's Push button too.
                payload = {"v": v, "ahead": _ahead(root), "project": pid,
                           "update": _update_payload(),
                           "push": PUSH_HEALTH.get(str(root), {}),
                           "watchAge": round(time.time() - WATCH_BEAT[0], 1)}
                if err:
                    payload["error"] = err
                # ADVERTISE pilot ops; never hand them out here. A stream is
                # not proof of a live tab: a closed tab's stream lingers until
                # its 45s lease expires or its next write fails, so delivering
                # into "whichever stream wakes first" posts the op to a corpse
                # and the caller times out for no reason. The tab claims over
                # HTTP instead (POST /__pilot/claim) — a request only a living
                # tab can make, and an atomic pop, so exactly one gets them.
                with st.cond:
                    payload["pilotWaiting"] = len(st.pilot_pending)
                self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
                self.wfile.flush()
                seen = v
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        path = self.path.split("?")[0]
        if path in ("/__hello", "/__bye"):
            # sendBeacon bodies are plain text (the tab's own id), not JSON —
            # and /__bye must stay parse-proof: it is fired during pagehide,
            # where a rejected beacon cannot be retried.
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            cid = (raw or b"").decode("utf-8", "replace").strip()[:64] or "anon"
            if path == "/__hello":
                CLIENTS[cid] = time.time()
                EVER_HELLO[0] = True
            else:
                CLIENTS.pop(cid, None)
            return self._json(200, {"ok": True, "clients": len(CLIENTS)})
        if path not in ("/__save", "/__push", "/__export", "/__upload",
                        "/__update", "/__rollback", "/__window", "/__quit",
                        "/__scaffold", "/__adopt", "/__connect", "/__pull",
                        "/__pilot", "/__pilot/claim", "/__pilot/result",
                        "/__oauth/device/code", "/__oauth/device/token",
                        "/__oauth/save"):
            return self._json(404, {"ok": False, "error": "unknown endpoint"})
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError as e:
            return self._json(400, {"ok": False, "error": f"bad request: {e}"})
        # PRIMER_TEST_SAFE: the Playwright suite's guard against real commits,
        # pushes, exports and uploads, moved SERVER-side. It used to be four
        # context.route() blocks in the test fixture — but one registered route
        # turns on full request interception for the context, and Firefox's
        # interception pipe wedges streaming the ~30MB Pyodide fetch on a
        # second boot: every multi-boot spec then timed out at first render
        # while the app itself was fine. Answering here needs no interception
        # at all, and it also closes the fixture's documented gap — a spec
        # POSTing through Playwright's `request` fixture bypassed the browser
        # routes entirely and hit the real endpoint. Response shapes mirror
        # the old mocks exactly; specs assert on them.
        if os.environ.get("PRIMER_TEST_SAFE") == "1":
            # The suite shares ONE server across every parallel worker, so a
            # spec that exercised the real quit would take the whole run down
            # with it — every other spec failing on a dead port, far from the
            # cause. Refused here; specs assert on the request instead.
            if path == "/__quit":
                return self._json(200, {"ok": False, "error":
                    "blocked in tests — the suite's server stays up"})
            if path in ("/__save", "/__push"):
                return self._json(200, {"ok": True, "ahead": 0, "message":
                    "blocked in tests — no real save/push happens here"})
            if path == "/__pull":
                return self._json(200, {"ok": False, "error":
                    "blocked in tests — no real repo is updated here"})
            if path == "/__upload":
                name = str(req.get("name") or "upload.png")
                return self._json(200, {"ok": True, "src": f"assets/{name}",
                                        "path": f"report2027/web/assets/{name}"})
            if path == "/__export":
                body = b"%PDF-1.4 fake export for tests"
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
        if path == "/__window":
            return self._new_window(req)
        if path == "/__quit":
            return self._quit()
        if path == "/__pull":
            return self._pull_content(req)
        if path == "/__scaffold":
            return self._scaffold(req)
        if path == "/__connect":
            return self._connect(req)
        if path == "/__adopt":
            return self._adopt(req)
        if path.startswith("/__oauth/"):
            return self._oauth(path, req)
        if path == "/__update":
            return self._apply_update()              # writes its own response
        if path == "/__rollback":
            return self._rollback()                  # writes its own response
        if path == "/__export":
            return self._export(req)                 # writes its own response
        if path == "/__pilot":
            return self._pilot(req)
        if path == "/__pilot/claim":
            return self._pilot_claim(req)
        if path == "/__pilot/result":
            return self._pilot_result(req)
        pid = req.get("project") or DEFAULT_PROJECT
        if pid not in PROJECTS:
            return self._json(200, {"ok": False, "error": f"unknown project '{pid}'"})
        if path == "/__upload":
            try:
                return self._json(200, {"ok": True, **self._upload(pid, req)})
            except Exception as e:                   # noqa: BLE001 — report it
                return self._json(200, {"ok": False, "error": str(e)})
        try:
            msg = (self._push(pid, bool(req.get("deploy")))
                   if path == "/__push" else self._save(pid, req))
            return self._json(200, {"ok": True, "message": msg, "v": STATE[pid].version,
                                     "project": pid,
                                     "ahead": _ahead(PROJECTS[pid]["root"])})
        except Exception as e:                       # noqa: BLE001 — report it
            # A push that needs the person to SEE where it lands is not a
            # failure — it is a question, and the editor asks it as one rather
            # than showing a refusal they cannot act on.
            txt = str(e)
            if txt.startswith("NEEDS_CONFIRM|"):
                return self._json(200, {"ok": False, "needsConfirm": True,
                                         "error": txt.split("|", 1)[1],
                                         "project": pid,
                                         "ahead": _ahead(PROJECTS[pid]["root"])})
            return self._json(200, {"ok": False, "error": txt,
                                     "project": pid,
                                     "ahead": _ahead(PROJECTS[pid]["root"])})

    def _pull_content(self, req):
        """Fast-forward a report's own repo to what is on GitHub, then rebuild
        every project living in it.

        Only ever a fast-forward, and only when _content_status already said
        it could be: this re-checks rather than trusting the button, because
        the person may have typed something in the seconds since.
        """
        pid = req.get("project") or DEFAULT_PROJECT
        if pid not in PROJECTS:
            return self._json(200, {"ok": False, "error": f"unknown project '{pid}'"})
        root = PROJECTS[pid]["root"]
        if root == SELF_ROOT:
            return self._json(200, {"ok": False, "error":
                "this report lives in the editor's own repo — the version chip "
                "in the toolbar updates it"})
        st = _content_status(root)
        CONTENT[str(root)] = st
        if not st["behind"]:
            return self._json(200, {"ok": True, "message": "already up to date"})
        if not st["can"]:
            return self._json(200, {"ok": False, "error": st["why"] or "cannot fast-forward"})
        try:
            _git(root, "merge", "--ff-only", "@{u}")
        except RuntimeError as e:
            return self._json(200, {"ok": False, "error": f"could not update: {str(e)[:200]}"})
        CONTENT[str(root)] = _content_status(root)
        n = 0
        for other, p in PROJECTS.items():
            if p["root"] == root:
                rebuild(other, "content-pull")
                n += 1
        return self._json(200, {"ok": True,
                                "message": f"updated — {st['behind']} change"
                                           f"{'s' if st['behind'] > 1 else ''} from GitHub",
                                "rebuilt": n})

    def _scaffold(self, req):
        """A blank local project, from the start page's "+ New report" —
        no GitHub, no token, no repo access. docsync.new writes the files
        and the docsync.yml binding; this registers it in the RUNNING server
        and builds it, so "Create" lands in a working editor."""
        slug = str(req.get("slug") or "").strip()
        name = str(req.get("name") or "").strip()
        template = str(req.get("template") or "").strip()
        size = req.get("size") or {}
        try:
            w = float(size.get("w", 8.5))
            h = float(size.get("h", 11.0))
        except (TypeError, ValueError):
            return self._json(200, {"ok": False, "error": "page size must be numbers"})
        sys.path.insert(0, str(SELF_ROOT))
        try:
            from docsync.new import NewProjectError, create
            # create() read-appends docsync.yml; under the host lock so two
            # concurrent scaffolds (parallel test workers, two tabs) cannot
            # lose each other's binding.
            with _host_lock():
                create(slug, name, w, h, root=SELF_ROOT, template=template)
        except NewProjectError as e:
            return self._json(200, {"ok": False, "error": str(e)})
        except Exception as e:                   # noqa: BLE001 — report it
            return self._json(200, {"ok": False, "error": f"scaffold failed: {e}"})
        finally:
            sys.path.remove(str(SELF_ROOT))
        binding = _load_bindings(SELF_ROOT).get(slug)
        if binding is None:
            return self._json(200, {"ok": False,
                                    "error": "created, but the binding did not read back"})
        _register_project(slug, SELF_ROOT, binding)
        rebuild(slug, "scaffold")
        err = STATE[slug].error
        if err:
            return self._json(200, {"ok": False, "error": f"created, but the first build failed: {err}"})
        return self._json(200, {"ok": True, "slug": slug})

    def _adopt(self, req):
        """Register a docsync repo ALREADY ON THIS DISK — the "add the live
        report later" path. Nothing is written into the adopted repo; this
        host's projects.json gains entries and the running server mounts
        them. Every binding the repo declares comes in: half a repo is not a
        useful adoption."""
        raw = str(req.get("root") or "").strip()
        if not raw:
            return self._json(200, {"ok": False, "error": "name the folder the repo lives in"})
        root = Path(raw).expanduser()
        if not (root / "docsync.yml").is_file():
            return self._json(200, {"ok": False,
                                    "error": f"{root} has no docsync.yml — not a docsync repo"})
        try:
            bindings = _load_bindings(root.resolve())
        except Exception as e:                   # noqa: BLE001 — report it
            return self._json(200, {"ok": False, "error": f"could not read its registry: {e}"})
        if not bindings:
            return self._json(200, {"ok": False, "error": "its docsync.yml has no bindings"})
        root = root.resolve()
        # The repo the editor's Push sends to, learned from the clone itself.
        origin = ""
        try:
            url = _git(root, "remote", "get-url", "origin").strip()
            m = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?$", url)
            origin = m.group(1) if m else ""
        except Exception:
            pass
        reg = DOCS / "primer" / "projects.json"
        added = []
        already = []          # served from THIS same checkout — adopt is a no-op
        # The registry read-modify-write sits under the host lock; the
        # rebuilds happen AFTER release — a build takes seconds, and holding
        # a cross-process lock through one would serialise every scaffold and
        # cleanup on the machine behind it for no correctness gain.
        with _host_lock():
            try:
                registry = json.loads(reg.read_text()) if reg.is_file() else {}
            except json.JSONDecodeError:
                registry = {}
            for pid, b in bindings.items():
                if not b.editor:
                    continue
                cur = PROJECTS.get(pid)
                if cur is not None:
                    # Already serving this id — three cases, three answers.
                    # From THIS same checkout: adopting is a no-op, and a
                    # no-op should open the project rather than scold
                    # (below). From a DIFFERENT checkout still on disk: a
                    # genuine conflict, refused as ever. But a registration
                    # whose root has VANISHED — the folder deleted since it
                    # was adopted — is a memory of a project, not a project,
                    # and it used to block re-adopting the id until someone
                    # restarted the server: nothing could remove it, and
                    # everything that touched it (ping, rebuild, the grid)
                    # just errored. Evict the ghost and let the new checkout
                    # have the name.
                    if cur["root"] == root:
                        already.append(pid)
                        continue
                    if cur["root"].exists():
                        continue
                    PROJECTS.pop(pid, None)
                    STATE.pop(pid, None)
                    EXTERNAL_MOUNTS.pop(pid, None)
                    CONTENT.pop(str(cur["root"]), None)
                    _default_registry.cache_clear()
                _register_project(pid, root, b)
                registry[pid] = {"name": pid.replace("-", " ").title(),
                                 "base": f"../_repo-{pid}",
                                 **({"repo": origin} if origin else {}),
                                 "local_root": str(root)}
                added.append(pid)
            if added:
                reg.parent.mkdir(parents=True, exist_ok=True)
                # Atomic: a reader that lands mid-write sees the OLD file,
                # never a truncated one — a partial read parsed as {} made
                # the start page forget every project for one load.
                tmp = reg.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(registry, indent=2) + "\n")
                tmp.replace(reg)
        if not added:
            if already:
                # Everything this repo declares is already being served from
                # this very folder — adopting it again is a no-op, and a no-op
                # is not an error. Answer with the projects so the start page
                # opens one, exactly as a successful adoption would; erroring
                # here just told the person "you can't have what you already
                # have" and left them stranded on the grid.
                return self._json(200, {"ok": True, "added": already,
                                        "message": "already in your list — opening"})
            return self._json(200, {"ok": False,
                                    "error": "every project there is already served "
                                             "from a different checkout"})
        for pid in added:
            rebuild(pid, "adopt")
        return self._json(200, {"ok": True, "added": added})

    def _connect(self, req):
        """Point a project at a GitHub repo: manifest, registry, git remote.

        The repo a project pushes to is recorded in THREE places that have to
        agree — the staged manifest.json the editor reads, docs/primer's
        projects.json the start page reads, and the checkout's own `origin`
        that git actually pushes to. Setting one and not the others is how a
        project ends up claiming a repo it cannot push to, so this does all
        three or reports which one refused.

        Creating the repo on GitHub is the browser's job (it holds the token
        and can call the API directly); by the time this is called the repo
        exists and only the wiring is left.
        """
        pid = str(req.get("project") or "").strip()
        slug = str(req.get("repo") or "").strip().removesuffix(".git")
        if pid not in PROJECTS:
            return self._json(200, {"ok": False, "error": f"unknown project '{pid}'"})
        # A segment of "." or ".." matches a naive [\w.-]+ and would build a
        # nonsense remote (https://github.com/../evil.git). Neither is a legal
        # GitHub name, so require at least one alphanumeric per segment.
        if not re.fullmatch(r"(?=[^/]*[A-Za-z0-9])[\w.-]+/(?=[^/]*[A-Za-z0-9])[\w.-]+", slug):
            return self._json(200, {"ok": False,
                                    "error": f"'{slug}' is not an owner/name repo slug"})
        p = PROJECTS[pid]
        root, b = p["root"], p["binding"]
        if not b.editor:
            return self._json(200, {"ok": False, "error": f"'{pid}' has no editor"})

        # git remote: add when absent, and only RETARGET when asked, so a
        # checkout that already pushes somewhere is never silently redirected.
        remote_note = "left as it was"
        try:
            current = _git(root, "remote", "get-url", "origin").strip()
        except Exception:
            current = ""
        want = f"https://github.com/{slug}.git"
        try:
            if not current:
                _git(root, "remote", "add", "origin", want)
                remote_note = f"origin set to {slug}"
            elif req.get("retarget") and _slug_of(current) != slug:
                _git(root, "remote", "set-url", "origin", want)
                remote_note = f"origin retargeted to {slug}"
            elif _slug_of(current) != slug:
                return self._json(200, {"ok": False, "error":
                    f"this checkout already pushes to {_slug_of(current) or current}. "
                    f"Choose that repo, or confirm the change to point it at {slug}.",
                    "needs_retarget": True, "current": _slug_of(current)})
        except Exception as e:                       # noqa: BLE001 — report it
            return self._json(200, {"ok": False, "error": f"could not set the remote: {e}"})

        # manifest.json — what the editor reads on boot
        man = b.editor.dir / "engine" / "manifest.json"
        try:
            m = json.loads(man.read_text())
            m["repo"] = slug
            tmp = man.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(m, indent=2) + "\n")
            tmp.replace(man)
        except (OSError, json.JSONDecodeError) as e:
            return self._json(200, {"ok": False, "error": f"could not write the manifest: {e}"})

        # projects.json — what the start page reads. Under the host lock:
        # this is a read-modify-write of a file /__adopt and the test
        # suite's cleanup also read-modify-write, and unlocked concurrent
        # writers silently lose one side's update.
        reg = DOCS / "primer" / "projects.json"
        with _host_lock():
            try:
                registry = json.loads(reg.read_text()) if reg.is_file() else {}
            except json.JSONDecodeError:
                registry = {}
            entry = registry.get(pid) or dict(_default_registry().get(pid) or {})
            entry["repo"] = slug
            registry[pid] = entry
            reg.parent.mkdir(parents=True, exist_ok=True)
            tmp = reg.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(registry, indent=2) + "\n")
            tmp.replace(reg)
        _default_registry.cache_clear()
        return self._json(200, {"ok": True, "repo": slug, "remote": remote_note})

    def _oauth(self, path, req):
        """The device flow, proxied — and the token, kept for git.

        /device/code and /device/token forward to GitHub with the server's
        client id filled in (the browser cannot call GitHub's login endpoints
        cross-origin; this server can). /save verifies whatever token the
        browser ends up with — signed in or pasted — against the API and
        keeps it in TOKEN_FILE, which is what lets THIS SERVER push over
        https without the keychain ever having been set up."""
        if path == "/__oauth/save":
            tok = str(req.get("token") or "").strip()
            if not tok:
                return self._json(200, {"ok": False, "error": "no token in the request"})
            try:
                who = _gh_http(GH_API + "/user", token=tok)
            except Exception as e:               # noqa: BLE001 — report it
                return self._json(200, {"ok": False, "error": f"GitHub did not accept it: {e}"})
            login = who.get("login") or ""
            if not login:
                return self._json(200, {"ok": False, "error": "token works but names no user"})
            TOKEN_FILE.write_text(json.dumps({"token": tok, "login": login}) + "\n")
            os.chmod(TOKEN_FILE, 0o600)
            return self._json(200, {"ok": True, "login": login})
        client = str(req.get("client_id") or "") or GH_CLIENT
        if not client:
            return self._json(200, {"ok": False, "error":
                "no GitHub App client id configured — see docs/primer/OAUTH_SETUP.md "
                "step 1, then set PRIMER_GH_CLIENT (or the manifest's oauth block)"})
        target = (GH_BASE + "/login/device/code" if path.endswith("/device/code")
                  else GH_BASE + "/login/oauth/access_token")
        body = {k: v for k, v in req.items() if k != "client_id"}
        body["client_id"] = client
        if path.endswith("/device/code"):
            body.setdefault("scope", "repo")
        try:
            return self._json(200, _gh_http(target, body))
        except Exception as e:                   # noqa: BLE001 — report it
            return self._json(200, {"error": "relay_unreachable", "detail": str(e)})

    def _new_window(self, req):
        """A SECOND editor window on this same server.

        The launcher deliberately raises its existing window rather than adding
        one — every extra window is a full Pyodide boot, and a Dock icon that
        stacked them up was a real annoyance. But wanting two at once is also
        real: two projects side by side, or the same report on two pages. So
        the extra window is asked for explicitly, from the editor's File menu,
        and this is what opens it.

        A `--app` window via `open`, the same shape the launcher makes, so the
        second window is not a lesser browser-tab version of the first. Falls
        back to the default browser when Chrome is absent, exactly as the
        launcher does. One server serves them all; nothing here starts another.
        """
        # The URL comes from the client because the client is the one that
        # knows it: which docs/<dir> or /_repo-<pid> mount a project is served
        # under is already worked out in the browser's own location, and
        # re-deriving it here would be a second copy of that mapping to keep in
        # step. Checked against THIS server's own origin — nothing else is
        # openable — and passed as a list, never a shell string.
        url = str(req.get("url") or "")
        allowed = (f"http://localhost:{PORT}/", f"http://127.0.0.1:{PORT}/",
                   f"http://[::1]:{PORT}/")
        if not url.startswith(allowed) or any(c in url for c in '"\'\\ \t\n'):
            return self._json(200, {"ok": False,
                                    "error": "url must be a plain path on this server"})
        chrome = "/Applications/Google Chrome.app"
        try:
            if sys.platform == "darwin" and os.path.isdir(chrome):
                # -n: a new window even though Chrome is already running. This
                # is the one place that IS what we want.
                subprocess.Popen(["open", "-na", "Google Chrome",
                                  "--args", f"--app={url}"])
            else:
                webbrowser.open(url)
        except Exception as e:                       # noqa: BLE001 — report it
            return self._json(200, {"ok": False, "error": str(e)})
        return self._json(200, {"ok": True, "url": url})

    def _apply_update(self):
        """Take the update, then restart into it.

        The update includes THIS FILE, so there is no version of this that
        does not end in a restart — a process cannot swap out its own running
        code. The response goes out first and the restart happens a moment
        later on another thread, because execv never returns and a client
        waiting on a reply it will never get looks exactly like a crash.

        Nothing here decides whether the update is safe; selfupdate.py does,
        and refuses in every case it cannot handle without losing work."""
        st = _update_status(apply=True)
        UPDATE.update(st)
        applied = bool(st.get("applied"))
        self._json(200, {"ok": applied, "restarting": applied,
                         "why": st.get("why", ""), "sha": st.get("sha", ""),
                         "behind": st.get("behind", 0)})
        if not applied:
            return
        print("  updated — restarting into the new version")
        self._restart_soon()

    def _rollback(self):
        """Go back to the version in use before the last update, and restart.

        Updates arrive on their own now, so a bad one reaches everybody at
        once — and the person it reaches has no git at their fingertips and,
        in the worst case, an app that will not start. This is the way back,
        and it is deliberately reachable from inside the editor rather than
        only from a terminal."""
        r = _run_updater("--rollback")
        UPDATE.update(_update_status())
        self._json(200, {"ok": bool(r.get("ok")), "restarting": bool(r.get("ok")),
                         "why": r.get("why", ""), "sha": r.get("sha", "")})
        if r.get("ok"):
            print(f"  rolled back to {r.get('sha')} — restarting")
            self._restart_soon()

    def _quit(self):
        """Stop the server NOW, from the editor's File menu.

        The idle exit already stops it PRIMER_LINGER seconds after the last
        window closes, so this is not about tidiness — it is about the two
        cases waiting does not cover. A force-quit browser sends no pagehide,
        so its window never says goodbye and the ghost pins the server until
        the CLIENT_TTL sweep hours later; and a server that has gone wrong
        (a wedged build, a watcher that died) is exactly when you want it
        stopped on purpose rather than in two minutes.

        Same shutdown the reaper uses, on another thread a beat later, for the
        same reason the restart is deferred: a client waiting on a reply that
        never comes looks like a crash. Localhost-only, like every other
        endpoint here — and it destroys nothing, since the next open boots a
        fresh server and every Save is already on disk.
        """
        self._json(200, {"ok": True, "stopping": True})
        print("  quit asked for from the editor — stopping")

        def go():
            time.sleep(0.4)
            # Close the editor's own windows before the server goes. The page
            # cannot do this for itself: a --app window opened by `open` is not
            # script-opened, so Chrome refuses its window.close() — which left
            # a live-looking window over a dead server, the exact state this
            # row exists to avoid. The launcher already walks Chrome's windows
            # by URL to RAISE one (focus_existing); this is the same walk with
            # `close` at the end, so the Automation grant it prompted for is
            # the one this uses. Every failure mode — permission declined,
            # Chrome absent, tab already closed — just leaves the window for
            # the person, with the editor's status line saying to close it.
            _close_own_windows()
            for srv in SERVERS:
                threading.Thread(target=srv.shutdown, daemon=True).start()
        threading.Thread(target=go, daemon=True).start()

    def _restart_soon(self):
        """execv on another thread, a beat after the response is on the wire.
        execv never returns, and a client waiting on a reply it will never get
        looks exactly like a crash."""
        def go():
            time.sleep(0.4)
            try:
                os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve())])
            except OSError as e:
                print(f"  restart failed ({e}) — quit and reopen the app")
        threading.Thread(target=go, daemon=True).start()

    def _upload(self, pid: str, req) -> dict:
        """Write an uploaded image into the project's assets dir, on disk.

        The file itself IS the persistence — it lands in the checkout next to
        everything else the project owns, and the next Save's path-scoped
        commit picks the assets dir up along with content/layout (see _save).
        Committing here instead would make an upload alone create history,
        which Save never does for any other edit.
        """
        b = PROJECTS[pid]["binding"]
        name = re.sub(r"[^a-z0-9.]+", "-", str(req.get("name") or "")
                      .lower()).strip("-")
        if not name or "." not in name:
            raise RuntimeError(f"bad image name {req.get('name')!r}")
        try:
            data = base64.b64decode(req.get("data") or "", validate=True)
        except Exception as e:
            raise RuntimeError(f"bad image data: {e}") from e
        if not data or len(data) > 20 * 1024 * 1024:
            raise RuntimeError("image is empty or over 20MB")
        assets = _assets_dir(b)
        assets.mkdir(parents=True, exist_ok=True)
        (assets / name).write_bytes(data)
        # The prose/box path is relative to the project's built page.
        out_dir = (b.editor.out if b.editor else b.content).parent
        src = os.path.relpath(assets / name, out_dir)
        return {"src": src, "path": str((assets / name).relative_to(
            PROJECTS[pid]["root"]))}

    # ---- export: render the editor's CURRENT draft to a file and stream it ---
    # The editor posts its in-memory content + layout, so you download exactly
    # what is on screen — unsaved edits and all — without a Save (which commits)
    # and without touching content.md / layout.json. render_report.py takes the
    # draft from temp files via DOCSYNC_CONTENT/LAYOUT and writes a throwaway
    # HTML next to the project's own published output (so its relative
    # css/js/assets links resolve); Chrome then prints or screenshots it
    # exactly as the project's own `make pdf`-equivalent would.
    # ---- pilot relay: drive the open editor over HTTP ----------------------
    # An AI pilot's slowest step was never the editor — render() is ~50ms — it
    # was the TRANSPORT: one browser-extension eval per verb, each a full model
    # round trip, each needing the tab open and fronted. This relays a verb to
    # the editor tab that already holds the live stream, so the same call is a
    # curl away: no extension, no JS-string escaping, no tab focus.
    #
    # It is NOT the out-of-band write /__inventory refuses to be. The op runs
    # INSIDE the open editor through window.docsync.api — the document stays
    # in memory, pushHistory() still runs first (⌘Z undoes a relayed op like a
    # human's), and render() validates it like any other edit. Nothing here
    # touches files behind the editor's back.
    #
    # Exactly-once by construction: ops are drained by the SSE writer, and the
    # SSE stream is held by exactly ONE tab (the leader election in edit.html).
    # A dispatched op is never re-queued — if the tab dies mid-op the caller
    # gets a timeout, which is honest, where a retry would risk applying the
    # edit twice.
    def _pilot(self, req):
        pid = req.get("project") or DEFAULT_PROJECT
        if pid not in STATE:
            return self._json(400, {"ok": False, "error": f"unknown project '{pid}'"})
        verb = req.get("verb")
        if not verb or not isinstance(verb, str):
            return self._json(400, {"ok": False, "error": "verb is required"})
        args = req.get("args", [])
        if not isinstance(args, list):
            args = [args]
        try:
            wait = float(req.get("timeout", 30))
        except (TypeError, ValueError):
            wait = 30.0
        wait = max(1.0, min(120.0, wait))
        st = STATE[pid]
        # `tab` aims the op at ONE editor (docsync.api.status().tab). Leader
        # election gives one claimant per browser profile, but two profiles —
        # or two automated browser contexts — with the same project open each
        # elect their own, and an untargeted op then goes to whichever claims
        # first. Naming a tab removes that ambiguity; omitting it is right for
        # the ordinary one-browser case.
        tab = req.get("tab")
        with st.cond:
            st.pilot_n += 1
            op_id = f"op{st.pilot_n}"
            st.pilot_pending.append({"id": op_id, "verb": verb, "args": args,
                                     "tab": tab})
            st.cond.notify_all()          # wake the SSE writer holding the stream
            got = st.cond.wait_for(lambda: op_id in st.pilot_results, timeout=wait)
            if got:
                return self._json(200, {"ok": True, "id": op_id,
                                        "result": st.pilot_results.pop(op_id)})
            # Undelivered vs delivered-but-unanswered are different problems.
            still_queued = any(o["id"] == op_id for o in st.pilot_pending)
            st.pilot_pending = [o for o in st.pilot_pending if o["id"] != op_id]
        if still_queued:
            if tab:
                return self._json(200, {"ok": False, "error":
                    f"no editor tab '{tab}' claimed the op — that tab is closed "
                    "or is not the one holding the live stream"})
            return self._json(200, {"ok": False, "error":
                "no editor is listening — open the report's edit.html "
                "(a tab must hold the live stream for a pilot op to run)"})
        return self._json(200, {"ok": False, "error":
            f"the editor took the op but did not answer within {wait:g}s"})

    # The claim: an atomic pop, so however many tabs (or zombie streams) heard
    # the advertisement, exactly one caller carries the ops away. Only the
    # LEADER tab claims — not for exactly-once (this pop already guarantees
    # that) but for COHERENCE: each tab holds its own in-memory document, so
    # ops split across two tabs would edit two different drafts.
    def _pilot_claim(self, req):
        pid = req.get("project") or DEFAULT_PROJECT
        if pid not in STATE:
            return self._json(400, {"ok": False, "error": f"unknown project '{pid}'"})
        tab = req.get("tab")
        st = STATE[pid]
        with st.cond:
            # An op naming a tab is only ever handed to that tab; an untargeted
            # one goes to whoever claims first. Both are an atomic pop, so no
            # op is handed out twice however many tabs are asking.
            mine = [o for o in st.pilot_pending if o.get("tab") in (None, tab)]
            if mine:
                taken = {o["id"] for o in mine}
                st.pilot_pending = [o for o in st.pilot_pending
                                    if o["id"] not in taken]
        return self._json(200, {"ok": True, "ops": mine})

    def _pilot_result(self, req):
        pid = req.get("project") or DEFAULT_PROJECT
        if pid not in STATE:
            return self._json(400, {"ok": False, "error": f"unknown project '{pid}'"})
        op_id = req.get("id")
        if not op_id:
            return self._json(400, {"ok": False, "error": "id is required"})
        st = STATE[pid]
        with st.cond:
            st.pilot_results[op_id] = req.get("result")
            st.cond.notify_all()
        return self._json(200, {"ok": True})

    def _export(self, req):
        pid = req.get("project") or DEFAULT_PROJECT
        p = PROJECTS.get(pid)
        if p is None:
            return self._json(400, {"ok": False, "error": f"unknown project '{pid}'"})
        root, b = p["root"], p["binding"]
        if not b.editor:
            return self._json(400, {"ok": False, "error": f"'{pid}' has no editor"})
        fmt = (req.get("fmt") or "pdf").lower()
        if fmt not in ("pdf", "png"):
            return self._json(400, {"ok": False, "error": "fmt must be pdf or png"})
        # Shape-check page/scale BEFORE the build. Everything below this costs a
        # full render plus a headless Chrome; a malformed argument should not.
        try:
            scale = float(req.get("scale", 2))
        except (TypeError, ValueError):
            return self._json(400, {"ok": False, "error": "scale must be a number"})
        scale = max(0.1, min(4.0, scale))
        want_page = req.get("page")
        if want_page is not None:
            try:
                want_page = int(want_page)
            except (TypeError, ValueError):
                return self._json(400, {"ok": False, "error": "page must be a number"})
            if want_page < 1:
                return self._json(400, {"ok": False, "error": "page starts at 1"})
        content, layout = req.get("content"), req.get("layout")
        if content is None or layout is None:
            return self._json(400, {"ok": False, "error": "content and layout required"})
        marks = bool(req.get("marks"))
        work = Path(tempfile.mkdtemp(prefix="primer-exp-"))
        token = work.name.rsplit("-", 1)[-1]
        out_html = b.editor.out.parent / f"__export-{token}.html"   # must live
        try:                                                        # beside the
            (work / "content.md").write_text(content)                # project's own
            (work / "layout.json").write_text(layout)                 # output for
            env = dict(os.environ)                                    # its assets
            env["DOCSYNC_CONTENT"] = str(work / "content.md")
            env["DOCSYNC_LAYOUT"] = str(work / "layout.json")
            env["DOCSYNC_OUT"] = str(out_html)
            env.pop("DOCSYNC_EDIT", None)             # publish mode, not edit mode
            if marks and fmt == "pdf":
                env["DOCSYNC_MARKS"] = "1"
            r = subprocess.run(["python3", str(b.editor.render)], cwd=str(root),
                               env=env, capture_output=True, text=True)
            if r.returncode != 0:
                tail = (r.stdout + r.stderr).strip()[-2000:]
                return self._json(200, {"ok": False,
                                        "error": "the draft does not build:\n" + tail})
            if fmt == "pdf":
                data = self._chrome_pdf(out_html)
                return self._bytes(200, "application/pdf", data, f"{pid}.pdf")
            npages = out_html.read_text().count('<section class="page')
            base = out_html.resolve().as_uri()
            # ONE page, at whatever resolution was asked for: the cheap visual
            # check. A pilot deciding "does this look right" wants ~50KB at
            # scale 0.25, not a multi-megabyte zip of the whole document.
            if want_page is not None:
                n = want_page
                if n > max(npages, 1):
                    return self._json(400, {"ok": False,
                        "error": f"page {n} is outside 1..{max(npages, 1)}"})
                one = self._chrome_png_one(base, n, scale)
                if one is None:
                    return self._json(200, {"ok": False,
                        "error": "Chrome produced no PNG within the time limit."})
                return self._bytes(200, "image/png", one, f"{pid}-page-{n:02d}.png")
            data = self._chrome_png_zip(out_html, npages, pid, scale)
            return self._bytes(200, "application/zip", data, f"{pid}-pages.zip")
        except Exception as e:                        # noqa: BLE001 — report it
            return self._json(200, {"ok": False, "error": str(e)})
        finally:
            try:
                out_html.unlink()
            except OSError:
                pass
            shutil.rmtree(work, ignore_errors=True)

    def _chrome_capture(self, args, out_file, deadline=50.0, settle=2.0) -> bool:
        """Run headless Chrome and return once out_file is fully written.

        This build of Chrome writes the PDF/PNG in a few seconds but then does
        NOT exit under --headless=new, so waiting on the process (subprocess.run)
        hangs forever. We watch the OUTPUT instead: once its size holds steady
        it is done, and we kill the whole process group — main plus the gpu/
        renderer helpers — so nothing lingers between exports."""
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
        start, last, held = time.time(), -1, 0.0
        try:
            while time.time() - start < deadline:
                if proc.poll() is not None:       # some versions do self-exit
                    break
                time.sleep(0.4)
                sz = out_file.stat().st_size if out_file.exists() else -1
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
        return out_file.exists() and out_file.stat().st_size > 0

    def _chrome_pdf(self, out_html) -> bytes:
        prof = Path(tempfile.mkdtemp(prefix="primer-chrome-"))
        pdf = prof / "out.pdf"
        try:
            if not self._chrome_capture(
                [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
                 "--no-first-run", f"--user-data-dir={prof}",
                 "--virtual-time-budget=12000", "--no-pdf-header-footer",
                 f"--print-to-pdf={pdf}", out_html.resolve().as_uri()],
                pdf, deadline=50, settle=2.0):
                raise RuntimeError("Chrome produced no PDF within the time limit.")
            return pdf.read_bytes()
        finally:
            shutil.rmtree(prof, ignore_errors=True)

    def _chrome_png_one(self, base: str, i: int, scale: float = 2.0) -> bytes | None:
        """One page as PNG. 816x1056 css px = 8.5x11in; scale is the device
        pixel ratio — 2 for a crisp raster, 0.25 for a ~50KB thumbnail a pilot
        can glance at instead of paying for a full screenshot round trip. A
        fresh profile per page: Chrome is killed, not exited, so a reused
        profile can still hold a lock."""
        prof = Path(tempfile.mkdtemp(prefix="primer-chrome-"))
        png = prof / f"page-{i}.png"
        try:
            if self._chrome_capture(
                [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
                 "--no-first-run", f"--user-data-dir={prof}",
                 "--virtual-time-budget=8000", "--hide-scrollbars",
                 f"--force-device-scale-factor={scale:g}", "--window-size=816,1056",
                 f"--screenshot={png}", f"{base}?only={i}"],
                png, deadline=30, settle=1.0):
                return png.read_bytes()
            return None
        finally:
            shutil.rmtree(prof, ignore_errors=True)

    def _chrome_png_zip(self, out_html, npages, slug: str, scale: float = 2.0) -> bytes:
        # One screenshot per page via primer.js's ?only=N isolation.
        base = out_html.resolve().as_uri()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for i in range(1, max(npages, 1) + 1):
                data = self._chrome_png_one(base, i, scale)
                if data:
                    z.writestr(f"{slug}-page-{i:02d}.png", data)
        return buf.getvalue()

    def _save(self, pid: str, req) -> str:
        """Write what changed to disk, rebuild, and commit LOCALLY. Never
        pushes on its own — that is a separate, explicit action (_push) the
        editor's Push button asks for, so a save can never surprise-trigger a
        GitHub Actions run (build.yml watches for pushes to main) or race a
        push you were not ready to make yet."""
        p = PROJECTS[pid]
        root, b = p["root"], p["binding"]
        targets = []
        if req.get("content") is not None:
            targets.append((b.content, req["content"]))
        if req.get("layout") is not None and b.editor and b.editor.layout:
            targets.append((b.editor.layout, req["layout"]))
        if not targets:
            return "nothing to save"

        # Remember what was on disk BEFORE touching it. The build runs against
        # the written files, so a draft that does not build has already
        # overwritten the author's work by the time we find out — and the error
        # said "nothing was saved", which was simply false: the previous
        # content.md was gone, recoverable only from git, and only if it had
        # been committed. Keeping the old bytes lets that promise be true.
        previous = [(path, path.read_text() if path.exists() else None)
                    for path, _ in targets]
        wrote = []
        try:
            for path, text in targets:
                _write_atomic(path, text)
                wrote.append(path.name)
            rebuild(pid, "save")                      # so the built page is current
            if STATE[pid].error:
                raise RuntimeError(
                    "the draft does not build — nothing was saved:\n" + STATE[pid].error)
        except Exception:
            # Put every file back exactly as it was, then rebuild so the served
            # page matches the restored source rather than the rejected draft.
            for path, old in previous:
                if old is not None:
                    _write_atomic(path, old)
                elif path.exists():
                    path.unlink()
            rebuild(pid, "save-rollback")
            raise
        # Commit ONLY these paths — never whatever else happens to be staged.
        # A path-scoped commit ignores the rest of the index, so an unrelated
        # `git add` elsewhere can never ride along on a Save.
        paths = [str(b.content.relative_to(root))]
        if b.editor and b.editor.layout:
            paths.append(str(b.editor.layout.relative_to(root)))
        paths.extend(b.outputs)
        if b.editor:
            paths.append(str(b.editor.dir.relative_to(root)))
        # Uploaded images (_upload writes them to disk without committing)
        # ride the Save that uses them, like every other edit. A path-scoped
        # commit only takes tracked files, so a NEW image must be added first
        # — still scoped to the assets dir, nothing else can ride along.
        assets = _assets_dir(b)
        if assets.exists():
            rel = str(assets.relative_to(root))
            _git(root, "add", "--", rel)
            paths.append(rel)
        if subprocess.run(["git", "-C", str(root), "diff", "--quiet", "HEAD", "--",
                           *paths]).returncode == 0:
            return "already up to date"
        _git(root, "commit", "-m", f"{pid}: edit from the live editor (" + ", ".join(wrote) + ")",
             "--", *paths)
        return "saved locally — Push when you're ready to publish"

    def _push(self, pid: str, req_ok: bool = False) -> str:
        """Send whatever is committed locally (one Save, or several) to
        GitHub: the current branch, and fast-forward the deploy branch to
        match. Two separate remote refs, so either can independently reject a
        non-fast-forward — most commonly build.yml's own bot commit landing on
        the deploy branch between saves — and that failure is reported plainly
        rather than as raw git stderr, since the fix (ask for a reconcile) is
        the same every time."""
        p = PROJECTS[pid]
        root = p["root"]
        branch = (p["binding"].editor.branch if p["binding"].editor else "main") or "main"
        # A signed-in token (File > Connect GitHub) authenticates https pushes
        # without the keychain ever having been set up — the exact machine a
        # colleague's fresh install is. Inline -c, nothing persisted: an SSH
        # remote, or a machine whose keychain already works, is untouched.
        auth = []
        tok = _gh_token().get("token")
        if tok:
            try:
                origin_url = _git(root, "remote", "get-url", "origin").strip()
            except RuntimeError:
                origin_url = ""
            if origin_url.startswith("https://github.com/"):
                auth = ["-c", "url.https://x-access-token:" + tok
                        + "@github.com/.insteadOf=https://github.com/"]
        # WHERE this lands, said out loud. `branch` is the deploy branch — the
        # one that builds and publishes — and it defaults to main when a
        # binding does not name one. On a checkout whose current branch is
        # something else, one press fast-forwards the deploy branch to match
        # HEAD, which can be a great many commits: a 45-commit publish looked
        # exactly like a 1-commit one. Deploying across branches is a real
        # decision, so it is confirmed rather than assumed — `deploy: true` in
        # the request is the person having seen the number and said yes.
        here = ""
        try:
            here = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
        except RuntimeError:
            pass
        n = 0
        try:
            n = int(_git(root, "rev-list", "--count",
                         f"origin/{branch}..HEAD").strip() or 0)
        except (RuntimeError, ValueError):
            pass
        if here and here != branch and n > 1 and not req_ok:
            raise RuntimeError(
                f"NEEDS_CONFIRM|this would publish {n} commit"
                f"{'s' if n > 1 else ''} by fast-forwarding '{branch}' "
                f"(the branch that builds and deploys) to match '{here}'")
        try:
            _git(root, *auth, "push", "origin", "HEAD")              # this branch
            _git(root, *auth, "push", "origin", f"HEAD:{branch}")    # -> deploy
        except RuntimeError as e:
            if "git-lfs" in str(e) or "git lfs" in str(e):
                raise RuntimeError(
                    "a Git LFS pre-push hook refused the push — git-lfs is not on "
                    "this server's PATH. Relaunch the editor app; if it persists, "
                    "install git-lfs or remove the hook the error names.") from e
            if "rejected" in str(e) or "fetch first" in str(e):
                raise RuntimeError(
                    "push rejected — the remote has commits this machine doesn't "
                    "(often build.yml's own rebuild). Ask Claude to reconcile it, "
                    "or run: git fetch && git merge origin/" + branch) from e
            raise
        # The health answer is stale the moment a push succeeds; let the next
        # poll re-ask rather than leave a warning standing over a clean tree.
        PUSH_HEALTH.pop(str(root), None)
        where = f" — '{branch}' now matches '{here}'" if here and here != branch else ""
        return f"pushed{where} — GitHub Pages deploys in about a minute"


def _assets_dir(b) -> Path:
    """Where a project's uploaded images live: the binding's own assets dir
    when it names one, else an assets/ folder beside the built page — the
    place a relative "assets/…" src in the page resolves to."""
    if b.editor and b.editor.assets:
        return b.editor.assets
    out = b.editor.out if b.editor else b.content
    return out.parent / "assets"


def _tool_path() -> str:
    """PATH for the git subprocesses, widened to where tools actually install.

    A launcher-started server is reparented to launchd and inherits its
    minimal PATH — /usr/bin:/bin:/usr/sbin:/sbin — which has none of the
    places a Mac puts user-installed binaries. git itself is in /usr/bin so
    everything LOOKS fine, right up until git shells out to something that
    isn't: a Git LFS pre-push hook is the one that bites, because it is
    installed globally via core.hooksPath and then refuses the push of any
    repo, LFS or not, with "'git-lfs' was not found on your path". The same
    push from a terminal works, which makes it read as a server bug rather
    than an environment one.

    Appended, never prepended: a PATH the user really set (running serve.py
    from a shell) keeps deciding which git and which tools win.
    """
    seen = os.environ.get("PATH", "").split(os.pathsep)
    extra = [str(Path.home() / ".local" / "bin"), "/opt/homebrew/bin",
             "/usr/local/bin", "/opt/local/bin"]
    return os.pathsep.join(seen + [p for p in extra if p and p not in seen])


def _slug_of(url: str) -> str:
    """'owner/name' from either git URL form, or '' when it is neither."""
    m = re.search(r"[:/]([\w.-]+/[\w.-]+?)(?:\.git)?$", (url or "").strip())
    return m.group(1) if m else ""


def _write_atomic(path: Path, text: str) -> None:
    """Replace a file's contents in one step, or not at all.

    path.write_text() truncates the target and then writes into it, so a crash,
    a full disk or a killed server between those two moments leaves the file
    empty or half-written — and for this server that file is the author's whole
    document. Writing a sibling temp file and renaming makes the swap atomic on
    POSIX: readers see either all the old bytes or all the new ones, never a
    torn middle. The temp file is a sibling, not /tmp, because os.replace is
    only atomic within a filesystem.
    """
    tmp = path.with_name(path.name + ".tmp-save")
    try:
        tmp.write_text(text)
        os.replace(tmp, path)
    finally:
        # A failure before the rename leaves the temp behind; never leave litter
        # next to a report's source.
        try:
            tmp.unlink()
        except OSError:
            pass


def _git(root: Path, *args, timeout=45):
    # A detached dev server has no terminal and no GUI session to answer a
    # credential prompt, so a git that decides to ASK — a first push before the
    # keychain has cached anything, an unreachable remote — would hang the push
    # AND the editor's spinner forever. Force non-interactive and cap the wait:
    # a missing credential or dead network now fails in seconds with a message
    # you can act on. A credential already in the keychain is still used without
    # a prompt, so a push that worked keeps working.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0",
           "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=10",
           "PATH": _tool_path()}
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                           text=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"git {args[0]} timed out after {timeout}s — the remote didn't answer, "
            "or a credential prompt had no terminal to answer it. Run the push once "
            "in your own terminal so the keychain caches the credential, then retry.")
    if r.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed:\n{(r.stdout + r.stderr).strip()[-1500:]}")
    # The output, for the callers that read one (remote get-url); the many
    # that only care about success/failure ignore it, as they always have.
    return r.stdout


class _IPv6Server(ThreadingHTTPServer):
    # macOS resolves `localhost` to BOTH 127.0.0.1 and ::1, and per RFC 6724 a
    # browser tries ::1 FIRST. Binding IPv4 only left ::1:PORT dead, so Chrome/
    # Safari stalled on the IPv6 attempt while curl (which picked IPv4) worked —
    # the classic "curl loads it, the browser spins" split. We listen on BOTH
    # loopback addresses so it doesn't matter which the browser picks.
    address_family = socket.AF_INET6


def _serve_forever(servers):
    """serve_forever on the first socket in the calling thread, the rest in
    daemon threads. All share one Handler, so a Save/Push on either family hits
    the same build state."""
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    servers[0].serve_forever()


def main():
    if not PROJECTS:
        print("  no projects found — check docsync.yml and docs/primer/projects.json")
    for pid in PROJECTS:
        rebuild(pid, "startup")
    threading.Thread(target=watcher, daemon=True).start()
    threading.Thread(target=update_watcher, daemon=True).start()
    threading.Thread(target=content_watcher, daemon=True).start()
    # Two loopback listeners (IPv4 + IPv6), NOT a dual-stack `::` bind — the save/
    # push/export endpoints must stay off the LAN. If IPv6 loopback is somehow
    # unavailable, fall back to IPv4-only rather than refuse to start.
    try:
        servers = [ThreadingHTTPServer(("127.0.0.1", PORT), Handler)]
    except OSError as e:
        # Almost always a server already running — a second `make live`, or one
        # left behind by a closed terminal. The bare traceback that used to
        # come out of here named none of that.
        import errno
        if e.errno != errno.EADDRINUSE:
            raise
        print(f"\n  Port {PORT} is already in use.\n"
              f"    If the editor is already running, just open "
              f"http://localhost:{PORT}/primer/start.html\n"
              f"    Otherwise, stop the process holding it:\n"
              f"      lsof -nP -iTCP:{PORT} -sTCP:LISTEN\n"
              f"    or start this one somewhere else:\n"
              f"      PRIMER_PORT=8011 make -C report2027 live\n")
        return 1
    try:
        servers.append(_IPv6Server(("::1", PORT), Handler))
    except OSError as e:
        print(f"  (IPv6 loopback unavailable: {e}; serving IPv4 only)")
    url = f"http://localhost:{PORT}/primer/start.html"
    print(f"\n  Primer editor — live at {url}")
    for pid, p in sorted(PROJECTS.items()):
        print(f"    {pid} -> {p['root']}")
    if os.environ.get("PRIMER_OPEN", "1") == "1":
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    SERVERS[:] = servers
    if LINGER > 0:
        threading.Thread(target=_idle_reaper, daemon=True).start()
    try:
        _serve_forever(servers)
    except KeyboardInterrupt:
        print("\n  stopped")


if __name__ == "__main__":
    # SystemExit, not a bare call: main() returns 1 when the port is
    # taken, and `make live` must fail rather than report success.
    raise SystemExit(main())
