"""Publish a project's page to a public site that lives in ANOTHER repository.

A Cider project is saved into the checkout the editor runs from. A website
(the Estimates pages, a one-pager that is not on the staff hub) lives in a
different repo and is deployed from there — so the editor's own Push cannot
reach it. A binding that declares a `publish:` block closes that gap:

    publish:
      repo: Hawaii-Appleseed/Census-Forecaster     # owner/name, a git URL, or a local path
      branch: main                                 # the branch the site deploys from
      command: python3 projects/my-page/publish_site.py --site "$CIDER_SITE/site" --write
      paths: [site/my-page/index.html]             # the only files a publish may change
      when: save                                   # save | push  (default push)
      url: https://hawaii-appleseed.github.io/Census-Forecaster/my-page/

When the trigger fires the engine

  1. keeps its OWN clone of the site repo (~/.cider/publish/…), reset to the
     remote branch every time — never the person's working checkout, which may
     be on another branch or hold unfinished work;
  2. runs `command` from the Cider checkout with CIDER_SITE pointing at that
     clone, so the command writes the page(s) into it;
  3. stages ONLY `paths`, commits, and pushes the branch. Nothing else the
     command touched is committed, and an unchanged page commits nothing.

The remote's own deploy (GitHub Pages, a workflow) does the rest. A publish
that fails never undoes the Save that triggered it: the work is still saved
locally and the message says the public site was not updated, and why.

The command is whatever turns the project into the site's page. Nothing here
knows what a page looks like, so a new kind of site needs a new command, not a
new engine.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


@dataclass
class Result:
    ok: bool
    changed: bool
    message: str
    sha: str = ""
    url: str = ""


def home() -> Path:
    """Where the engine's clones of public-site repos live."""
    return Path(os.environ.get("CIDER_PUBLISH_HOME") or Path.home() / ".cider" / "publish")


def _origin(repo: str) -> str:
    """owner/name -> its GitHub URL; an existing directory or a URL as written."""
    local = Path(os.path.expanduser(repo))
    if local.is_dir():
        return str(local)
    if re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        return f"https://github.com/{repo}.git"
    return repo


def clone_dir(repo: str, branch: str) -> Path:
    return home() / (re.sub(r"[^A-Za-z0-9._-]+", "_", repo.strip("/")) + "@" + branch)


def _git(cwd: Path, *args: str, auth: list[str] = (), path_env: str = "", timeout: int = 90) -> str:
    # Never ask for a credential: a detached server has nobody to answer, and a
    # prompt would hang the Save. A missing credential fails in seconds, named.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0",
           "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=10"}
    if path_env:
        env["PATH"] = path_env
    try:
        r = subprocess.run(["git", *auth, "-C", str(cwd), *args], capture_output=True,
                           text=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git {args[0]} timed out after {timeout}s") from None
    if r.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {(r.stdout + r.stderr).strip()[-800:]}")
    return r.stdout


def _fresh_clone(cfg, auth, path_env) -> Path:
    """The engine's clone, identical to the remote branch (its own, so a hard
    reset loses nothing of anyone's)."""
    d = clone_dir(cfg.repo, cfg.branch)
    if not (d / ".git").exists():
        d.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            ["git", *auth, "clone", "--quiet", "--branch", cfg.branch, _origin(cfg.repo), str(d)],
            capture_output=True, text=True, timeout=300,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **({"PATH": path_env} if path_env else {})})
        if r.returncode != 0:
            raise RuntimeError(f"could not clone {cfg.repo}: {(r.stdout + r.stderr).strip()[-600:]}")
    else:
        _git(d, "fetch", "--quiet", "origin", cfg.branch, auth=auth, path_env=path_env)
        _git(d, "checkout", "--quiet", "-B", cfg.branch, f"origin/{cfg.branch}", path_env=path_env)
        _git(d, "reset", "--quiet", "--hard", f"origin/{cfg.branch}", path_env=path_env)
        _git(d, "clean", "--quiet", "-fd", path_env=path_env)
    # A machine with no git identity (a fresh install, CI) still has to be able
    # to commit; one with an identity is left alone.
    try:
        _git(d, "config", "user.email", path_env=path_env)
    except RuntimeError:
        _git(d, "config", "user.name", "Cider", path_env=path_env)
        _git(d, "config", "user.email", "cider@users.noreply.github.com", path_env=path_env)
    return d


def _run_command(cfg, root: Path, site: Path, pid: str, path_env: str) -> None:
    env = {**os.environ, "CIDER_SITE": str(site), "CIDER_PUBLISH_PROJECT": pid}
    if path_env:
        env["PATH"] = path_env
    r = subprocess.run(cfg.command, shell=True, cwd=str(root), env=env,
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        tail = (r.stderr.strip() or r.stdout.strip()).splitlines()[-6:]
        raise RuntimeError("the publish command failed: " + " | ".join(tail))


def _attempt(cfg, root, pid, trigger, auth, path_env) -> Result:
    site = _fresh_clone(cfg, auth, path_env)
    _run_command(cfg, root, site, pid, path_env)
    missing = [p for p in cfg.paths if not (site / p).exists()]
    if missing:
        raise RuntimeError("the publish command did not write: " + ", ".join(missing))
    _git(site, "add", "--", *cfg.paths, path_env=path_env)
    if subprocess.run(["git", "-C", str(site), "diff", "--cached", "--quiet"]).returncode == 0:
        return Result(True, False, f"the public site already matches ({cfg.repo})", url=cfg.url)
    msg = cfg.message or f"{pid}: publish from Cider ({trigger})"
    _git(site, "commit", "--quiet", "-m", msg, "--", *cfg.paths, path_env=path_env)
    sha = _git(site, "rev-parse", "--short", "HEAD", path_env=path_env).strip()
    _git(site, "push", "--quiet", "origin", f"HEAD:{cfg.branch}", auth=auth, path_env=path_env)
    return Result(True, True, f"published to {cfg.repo} ({sha})", sha=sha, url=cfg.url)


def run(cfg, root: Path, pid: str, trigger: str, *, auth: list[str] = (), path_env: str = "") -> Result:
    """Publish once. Returns a Result either way; never raises."""
    d = str(clone_dir(cfg.repo, cfg.branch))
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(d, threading.Lock())
    with lock:
        try:
            try:
                return _attempt(cfg, root, pid, trigger, auth, path_env)
            except RuntimeError as e:
                # Someone else pushed between our fetch and our push: take the
                # new tip and make the page again, once.
                if "rejected" in str(e) or "fetch first" in str(e):
                    return _attempt(cfg, root, pid, trigger, auth, path_env)
                raise
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            return Result(False, False, str(e))


# ---- the hub's route: a GitHub Actions job (see .github/workflows/publish-sites.yml)

def _overlay(root: Path, binding, ref: str) -> list[str]:
    """Take the project's own source files — and only those — from `ref`.

    The staff hub's Publish commits what the editor saved (content, layout,
    assets) to a branch `hub/<project>` of this repository. The job runs this
    repo's TRUSTED code from the default branch and overlays just those files
    from the hub branch: the branch also carries a docsync.yml, and a command in
    it must never be run with a publish credential in the environment.
    """
    ed = binding.editor
    wanted = [binding.content]
    if ed and ed.layout:
        wanted.append(ed.layout)
    if ed:
        wanted.append(ed.assets or (ed.out.parent / "assets"))
    took = []
    for p in wanted:
        rel = os.path.relpath(p, root)
        if rel.startswith(".."):
            raise RuntimeError(f"{binding.id}: {p} is outside the repository")
        if _git(root, "ls-tree", "-r", "--name-only", ref, "--", rel).strip():
            _git(root, "checkout", ref, "--", rel)
            took.append(rel)
    return took


def config_for(root: Path, binding, ref: str = ""):
    """What this project publishes to: its `publish:` block, or the website it was
    made in the editor (docsync/website.py) — read from `ref`'s layout.json when
    given, since that is where the hub's Publish put the choice. Never the
    branch's docsync.yml: the sites a page may choose are the default branch's."""
    from docsync import website                     # noqa: PLC0415
    layout = None
    ed = binding.editor
    if ref and ed and ed.layout and not binding.publish:
        rel = os.path.relpath(ed.layout, root)
        try:
            import json                                  # noqa: PLC0415
            layout = json.loads(_git(root, "show", f"{ref}:{rel}"))
        except (RuntimeError, ValueError):
            layout = None
    return website.publish_for(binding, layout)


def _version_of(root: Path, ref: str) -> str:
    """The hub version a `hub/<id>` commit was exported from (collab/src/export.js
    writes it into the commit message)."""
    try:
        m = re.search(r"hub version (\S+)", _git(root, "log", "-1", "--format=%s", ref))
        return m.group(1) if m else ""
    except RuntimeError:
        return ""


def _report(base: str, key: str, room: str, payload: dict) -> str:
    """Tell the Worker what happened, so the hub can show it (collab/src/index.js,
    POST /site/<room>). Never fatal: the page is already published or not."""
    import json
    import urllib.request
    try:
        req = urllib.request.Request(f"{base.rstrip('/')}/site/{room}", method="POST",
                                     data=json.dumps(payload).encode(),
                                     headers={"content-type": "application/json", "x-export-key": key,
                                              # Cloudflare's bot check refuses urllib's default
                                              # Python-urllib/x.y with a 403 (error 1010) before
                                              # the Worker ever sees the request.
                                              "user-agent": "cider-publish (docsync.publish)"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return f"reported to the hub ({r.status})"
    except Exception as e:  # noqa: BLE001 — a report that fails must not fail the publish
        return f"could not report to the hub: {e}"


def doctor(pid: str, *, hub_dir: Path | None = None, token_env: str = "SITE_PUBLISH_TOKEN") -> int:
    """Check, from here, everything a project needs to publish — and say what to do about each gap."""
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from docsync.registry import RegistryError, get
    bad = 0

    def line(ok, msg, fix=""):
        nonlocal bad
        mark = "ok  " if ok is True else "FIX " if ok is False else "note"
        bad += ok is False
        print(f"  {mark} {msg}" + (f"\n       -> {fix}" if fix and ok is not True else ""))

    print(f"publish doctor: {pid}")
    try:
        b = get(pid)
    except RegistryError as e:
        line(False, str(e), "add the project to docsync.yml (docsync.scaffold does)")
        return 1
    from docsync.website import WebsiteError, publish_for     # noqa: PLC0415
    try:
        cfg = publish_for(b)
    except WebsiteError as e:
        line(False, f"its website choice is not usable: {e}", "choose again from File ▸ Publish as a website…")
        return 1
    if cfg is None:
        line(False, "the binding has no publish: block and is not a website",
             "choose File ▸ Publish as a website… in the editor, or add a publish: block")
        return 1
    line(True, f"publish: {cfg.repo}@{cfg.branch}, paths {', '.join(cfg.paths)}, home {cfg.home}"
               + (f", when {cfg.when}" if cfg.home == "local" else ""))
    ed = b.editor
    line(bool(ed and ed.assets), "the editor block names an assets: path (uploaded images land inside the project)",
         "add `assets: projects/<id>/assets` under editor:")
    line(_has_name(root, pid), "the binding has a name: (docsync.hub skips a project without one)",
         "add `name: \"...\"` to the binding")
    # docsync.website takes the clone from $CIDER_SITE itself (its --site default).
    if "$CIDER_SITE" not in cfg.command and "docsync.website" not in cfg.command:
        line(False, "the command never mentions $CIDER_SITE, so it cannot be writing into the site clone",
             "write the page under \"$CIDER_SITE/<path>\"")
    origin = _origin(cfg.repo)
    token = os.environ.get(token_env, "")
    auth = (["-c", f"url.https://x-access-token:{token}@github.com/.insteadOf=https://github.com/"] if token else [])
    try:
        out = _git(root, "ls-remote", "--heads", origin, cfg.branch, auth=auth, timeout=30)
        line(bool(out.strip()), f"{cfg.repo} has a branch {cfg.branch}", "check `repo:` and `branch:`")
    except RuntimeError as e:
        line(False, f"cannot reach {cfg.repo}: {str(e)[-160:]}", f"set {token_env} (a token that can read it), or check the repo name")
    wf = root / ".github" / "workflows" / "publish-sites.yml"
    line(wf.exists(), "the hub route's workflow exists (.github/workflows/publish-sites.yml)", "restore it from the hub-publish branch")
    owner = origin.split("/")[-2] if origin.startswith("https://") else ""
    have = None
    if owner:
        try:
            names = subprocess.run(
                ["gh", "secret", "list", "--org", owner, "--json", "name", "-q", ".[].name"],
                capture_output=True, text=True, timeout=30)
            if names.returncode == 0:
                have = set(names.stdout.split())
        except (OSError, subprocess.SubprocessError):
            have = None
    if have is None:
        line(None, f"cannot check from here: the organization secrets PUBLISH_APP_ID and PUBLISH_APP_PRIVATE_KEY "
                   f"(GitHub > {owner or 'the org'} > Settings > Secrets and variables > Actions)")
    else:
        missing = [n for n in ("PUBLISH_APP_ID", "PUBLISH_APP_PRIVATE_KEY") if n not in have]
        line(not missing, f"the organization secrets PUBLISH_APP_ID and PUBLISH_APP_PRIVATE_KEY exist on {owner}",
             "run ~/scripts/cider-publisher-secrets.sh, or add them under the organization's Actions secrets")
    hub = hub_dir or Path.home() / "staff-updates-internal"
    pj = hub / "primer" / "projects.json"
    if pj.exists():
        import json
        try:
            listed = pid in (json.loads(pj.read_text()).get("projects") or json.loads(pj.read_text()))
        except (ValueError, AttributeError):
            listed = pid in pj.read_text()
        line(listed, f"the staff hub lists {pid}", "run `python3 -m docsync.hub` from the main checkout, review and push the hub")
    else:
        line(None, f"no hub checkout at {hub} — cannot check that {pid} is vendored there")
    print("all set" if not bad else f"{bad} thing(s) to fix")
    return 1 if bad else 0


def _has_name(root: Path, pid: str) -> bool:
    import yaml
    for b in (yaml.safe_load((root / "docsync.yml").read_text()) or {}).get("bindings") or []:
        if b.get("id") == pid:
            return bool(b.get("name"))
    return False


def github_target(repo: str) -> tuple[str, str]:
    """(owner, name) when `repo` is a GitHub repository, else ("", "") — a local
    path or another host has no installation token to mint."""
    m = re.fullmatch(r"(?:https://github\.com/)?([\w.-]+)/([\w.-]+?)(?:\.git)?/?", repo.strip())
    if not m or Path(os.path.expanduser(repo)).is_dir():
        return "", ""
    return m.group(1), m.group(2)


CALLER_WORKFLOW = """name: Publish sites

# Publishes this repository's website projects (a `publish:` block in docsync.yml)
# when the staff hub's Publish writes a hub/<project> branch. The work is in
# Cider's reusable workflow; this file only calls it. Needs the organization
# secrets PUBLISH_APP_ID and PUBLISH_APP_PRIVATE_KEY (docsync/publish.py explains).

on:
  push:
    branches: ["hub/**"]
  workflow_dispatch:
    inputs:
      project:
        description: "Project id to publish from its hub branch (hub/<id>)"
        required: true

jobs:
  publish:
    uses: Hawaii-Appleseed/Cider/.github/workflows/publish-sites.yml@main
    with:
      project: ${{ inputs.project }}
    secrets: inherit
    permissions:
      contents: read
      statuses: write
"""


def init_workflow(repo_root: Path) -> Path:
    """Write the caller workflow into a repository that vendors the engine."""
    dest = repo_root / ".github" / "workflows" / "publish-sites.yml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(CALLER_WORKFLOW)
    return dest


def main(argv: list[str] | None = None) -> int:
    """`python3 -m docsync.publish --hub-branch hub/<id>` — publish one project
    now, from what the hub saved. Exit 0 also when the project has nothing to
    publish to (the hub exports every project; most are not websites)."""
    import argparse
    ap = argparse.ArgumentParser(prog="python3 -m docsync.publish", description=main.__doc__)
    ap.add_argument("--id", help="the project's binding id")
    ap.add_argument("--hub-branch", help="hub/<id>, the branch the hub's Publish wrote")
    ap.add_argument("--ref", help="take the project's source files from this ref (default origin/<hub-branch>)")
    ap.add_argument("--site-repo", help="publish to this repository instead of the binding's (tests, forks)")
    ap.add_argument("--token-env", default="SITE_PUBLISH_TOKEN",
                    help="the environment variable holding a token that can push to the site repo")
    ap.add_argument("--report-to", default=os.environ.get("HUB_REPORT_URL", ""),
                    help="the collab Worker's URL: tell it the result (needs HUB_EXPORT_KEY)")
    ap.add_argument("--doctor", action="store_true", help="check the project's publish setup and say what is missing")
    ap.add_argument("--print-target", action="store_true",
                    help="write the site repository's owner and name to $GITHUB_OUTPUT (the workflow mints its token from them)")
    ap.add_argument("--init-workflow", action="store_true",
                    help="write .github/workflows/publish-sites.yml here, calling Cider's reusable workflow")
    ap.add_argument("--pull-hub", action="store_true",
                    help="copy the hub's latest content for this project into the working tree (to move its home "
                         "back to local); review and commit it yourself")
    a = ap.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    if a.init_workflow:
        print("wrote", init_workflow(root))
        return 0
    pid = a.id or (a.hub_branch or "").removeprefix("hub/")
    if not pid:
        ap.error("--id or --hub-branch is required")
    if a.doctor:
        return doctor(pid, token_env=a.token_env)
    sys.path.insert(0, str(root))
    from docsync.registry import RegistryError, get
    try:
        b = get(pid)
    except RegistryError as e:
        print(f"::notice::{e} — nothing to publish")
        return 0
    from docsync.website import WebsiteError        # noqa: PLC0415
    ref = a.ref or (f"origin/{a.hub_branch}" if a.hub_branch else "")
    try:
        cfg = config_for(root, b, ref)
    except WebsiteError as e:
        print(f"::error::{pid}: {e}")
        cfg = None
        if not a.print_target:
            return 1
    if a.print_target:
        owner, name = github_target(a.site_repo or cfg.repo) if cfg and cfg.home == "hub" else ("", "")
        out = os.environ.get("GITHUB_OUTPUT")
        text = f"owner={owner}\nname={name}\n"
        if out:
            with open(out, "a") as f:
                f.write(text)
        print(text, end="")
        return 0
    if a.pull_hub:
        took = _overlay(root, b, a.ref or f"origin/hub/{pid}")
        print("took from the hub branch:", ", ".join(took) or "(nothing there)")
        print("review with `git diff`, commit, then set `home: local` in the publish: block")
        return 0
    if cfg is None:
        print(f"::notice::{pid} has no publish: block and is not a website — nothing to publish")
        return 0
    if cfg.home != "hub":
        print(f"::notice::{pid} is edited locally (home: local), not on the hub — not published from the hub branch")
        return 0
    if a.site_repo:
        cfg.repo = a.site_repo
    version = _version_of(root, ref) if ref else ""
    try:
        if ref:
            print("source files from", ref + ":", ", ".join(_overlay(root, b, ref)) or "(none)")
        token = os.environ.get(a.token_env, "")
        if not token and not Path(os.path.expanduser(cfg.repo)).is_dir():
            raise RuntimeError(
                f"{a.token_env} is not set — add a token that can push to {cfg.repo} "
                f"as that repository secret (Settings > Secrets and variables > Actions)")
        auth = (["-c", f"url.https://x-access-token:{token}@github.com/.insteadOf=https://github.com/"]
                if token else [])
        r = run(cfg, root, pid, "hub", auth=auth)
    except RuntimeError as e:
        r = Result(False, False, str(e))
    text = r.message + (f" — {r.url}" if r.ok and r.url else "")
    print(("::notice::" if r.ok else "::error::") + text)
    note = ""
    key = os.environ.get("HUB_EXPORT_KEY", "")
    repo_env = os.environ.get("GITHUB_REPOSITORY", "")
    if a.report_to and key and repo_env:
        room = f"{repo_env.replace('/', '~')}~{pid}"
        note = _report(a.report_to, key, room, {"version": version, "ok": r.ok, "changed": r.changed,
                                                "message": r.message, "url": r.url, "sha": r.sha})
        print(note)
    for path, body in ((os.environ.get("GITHUB_STEP_SUMMARY"), f"**{pid}**: {text}\n"),
                       (os.environ.get("GITHUB_OUTPUT"), "".join(
                           f"{k}={v}\n" for k, v in (("ok", str(r.ok).lower()), ("url", r.url if r.ok else ""),
                                                      ("message", r.message.replace("\n", " ")[:200]))))):
        if path:
            with open(path, "a") as f:
                f.write(body)
    return 0 if r.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
