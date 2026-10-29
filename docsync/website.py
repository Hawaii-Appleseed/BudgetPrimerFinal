"""A page made into a website by choosing it in the editor, not by writing config.

The editor's File ▸ Publish as a website… writes one small record into the
project's layout.json:

    "website": {"site": "reports", "slug": "act-163-credits", "home": "hub"}

`site` names an entry in the `sites:` list of docsync.yml — the websites Cider
publishes to, each declared ONCE (its repository, branch, folder and address).
Nothing about the page needs declaring: the publish settings are derived here
(publish_for), and the page is built by this module's own recipe, so it works
the same from the editor app's Save and from the staff hub's Publish.

That split is the security model, and it is why the record holds no command.
The hub's publish job (.github/workflows/publish-sites.yml) runs the DEFAULT
branch's code and takes only the project's content, layout and assets from the
hub branch — a branch's own docsync.yml never runs with the publish token in
the environment. A website record is data that job can trust: it can only pick
a site the default branch already lists and an address that passes SLUG_RE;
what runs is always `python3 -m docsync.website`.

    python3 -m docsync.website --id <project> [--write] [--no-fidelity]

builds the page into $CIDER_SITE/<folder>/<slug>/ (the publish engine's own
clone of the site; docsync/publish.py sets CIDER_SITE). Without --write it only
says what would change.

How the page is built:
  * a report made in Cider: its own publish build, which is already a whole
    web page — written as it is;
  * a page brought in from another site (docsync.scaffold left original.html):
    the editor's text put back into that page's own shell (publish_page.py).
Every file the page loads relatively (hub.page_refs) is copied beside it, and a
reference that leaves the page's folder (`../`) or cannot be found refuses the
publish, naming the file — a page that paints without its stylesheet is worse
than one not yet published. The address is claimed with a `.cider-page` file
naming the project, so two projects can never write the same address.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "docsync.yml"

# A web address segment: lowercase words joined by hyphens, 2-64 characters.
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])$")
SITE_RE = re.compile(r"^[a-z0-9-]+$")
MARKER = ".cider-page"


@dataclass
class Site:
    name: str          # the key in docsync.yml's `sites:`
    title: str         # what the editor's dialog shows
    repo: str          # owner/name, a git URL, or a local path (tests)
    branch: str
    folder: str        # inside the repo; "" for the root
    url: str           # the site's address, ending in /


class WebsiteError(RuntimeError):
    pass


def load_sites(path: Path = REGISTRY) -> dict[str, Site]:
    """docsync.yml's `sites:` — {} when it has none."""
    import yaml                                       # noqa: PLC0415
    data = yaml.safe_load(path.read_text()) or {}
    raw = data.get("sites") or {}
    if not isinstance(raw, dict):
        raise WebsiteError(f"{path.name}: sites must be a mapping of name -> site")
    out = {}
    for name, s in raw.items():
        where = f"{path.name} sites.{name}"
        if not SITE_RE.match(str(name)) or not isinstance(s, dict):
            raise WebsiteError(f"{where}: a site is a kebab-case name with repo/url under it")
        folder = str(s.get("folder") or "").strip("/")
        if ".." in Path(folder).parts or folder.startswith(".git"):
            raise WebsiteError(f"{where}: folder must be inside the repository")
        for k in ("repo", "url"):
            if not s.get(k):
                raise WebsiteError(f"{where}: missing '{k}'")
        url = str(s["url"])
        out[str(name)] = Site(name=str(name), title=str(s.get("title") or name), repo=str(s["repo"]),
                              branch=str(s.get("branch") or "main"), folder=folder,
                              url=url if url.endswith("/") else url + "/")
    return out


def designation(layout: dict | None, sites: dict[str, Site]) -> dict | None:
    """The layout's website record, checked against the sites this repo lists.
    None when the page is not a website; WebsiteError when the record is bad."""
    w = (layout or {}).get("website")
    if not w:
        return None
    if not isinstance(w, dict):
        raise WebsiteError("layout.json website must be {site, slug, home}")
    site, slug, home = str(w.get("site") or ""), str(w.get("slug") or ""), str(w.get("home") or "local")
    if site not in sites:
        raise WebsiteError(f"website site '{site}' is not one of this repository's sites "
                           f"({', '.join(sorted(sites)) or 'none listed'})")
    if not SLUG_RE.match(slug):
        raise WebsiteError(f"website address '{slug}' must be lowercase words joined by hyphens")
    if home not in ("local", "hub"):
        raise WebsiteError(f"website home '{home}' must be local or hub")
    return {"site": sites[site], "slug": slug, "home": home}


def designation_from_publish(binding, sites: dict[str, Site]) -> dict | None:
    """The target a binding's own `publish:` block names, when that block runs THIS command.

    A project whose docsync.yml binding has an explicit `publish:` block (command
    `python3 -m docsync.website …`) cannot be given a layout record from the editor,
    which hides File ▸ Publish as a website… for any page that has a block of its own.
    So a saved document that never got the record (a hub room is seeded once and
    takes no key from a deployment) would fail every publish with "not set up as a
    website". The block already says which site, address and home, so use it."""
    pub = getattr(binding, "publish", None)
    if not pub or "docsync.website" not in (pub.command or "") or not pub.paths:
        return None
    for site in sites.values():
        if site.repo != pub.repo or site.branch != pub.branch:
            continue
        path = str(pub.paths[0]).strip("/")
        slug = path[len(site.folder) + 1:] if site.folder and path.startswith(site.folder + "/") else path
        if SLUG_RE.match(slug):
            return {"site": site, "slug": slug, "home": pub.home or "local"}
    return None


def read_layout(binding) -> dict | None:
    ed = binding.editor
    if not ed or not ed.layout or not Path(ed.layout).is_file():
        return None
    try:
        return json.loads(Path(ed.layout).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def target_path(d: dict) -> str:
    """Where the page lives inside the site repository."""
    return f"{d['site'].folder}/{d['slug']}" if d["site"].folder else d["slug"]


def publish_for(binding, layout: dict | None = None, sites: dict[str, Site] | None = None):
    """The Publish settings for a project: its explicit `publish:` block if it has
    one, else the ones its website record implies, else None."""
    if getattr(binding, "publish", None):
        return binding.publish
    from docsync.registry import Publish          # noqa: PLC0415
    lay = read_layout(binding) if layout is None else layout
    if not (lay or {}).get("website"):
        return None
    d = designation(lay, load_sites() if sites is None else sites)
    path = target_path(d)
    return Publish(repo=d["site"].repo, branch=d["site"].branch,
                   command=f"python3 -m docsync.website --id {binding.id} --write",
                   paths=[path], when="save", home=d["home"],
                   message=f"{d['slug']}: published from Cider ({binding.id})",
                   url=d["site"].url + d["slug"] + "/")


# ---------------------------------------------------------------- the recipe

def build_page(binding) -> str:
    """The page as the site will serve it."""
    from docsync.publish_page import into_shell, render, semantic_tags, sheet_inner   # noqa: PLC0415
    built = render(binding, {})
    shell = binding.editor.render.parent / "original.html"
    if shell.is_file():
        try:
            page = into_shell(shell.read_text(encoding="utf-8"), semantic_tags(sheet_inner(built)))
        except ValueError:
            return built    # a renderer that draws the whole page itself (rxkids): its build IS the page
        return carry_font_links(page, built)
    return built


_FONT_LINK = re.compile(r"""<link\b[^>]*\bhref\s*=\s*["'](?:https?:)?//fonts\.g(?:oogleapis|static)\.com(?:/[^"']*)?["'][^>]*>""", re.I)


def carry_font_links(page: str, built: str) -> str:
    """The shell page with the web-font <link>s its renderer added and the shell
    never had (tfc-2027-priorities names Manrope and Poppins and its original
    loaded neither, so the published page fell back to Helvetica). The shell's
    own links are kept as they are; a renderer's that match one are not added
    twice. Inserted before the shell's first <style>, where a font stylesheet
    belongs, else at the end of <head>."""
    head_end = built.lower().find("</head>")
    have = {m.group(0) for m in _FONT_LINK.finditer(page)}
    new = [m.group(0) for m in _FONT_LINK.finditer(built[:head_end] if head_end >= 0 else built)
           if m.group(0) not in have]
    if not new:
        return page
    links = "\n".join(dict.fromkeys(new))
    m = re.search(r"<style\b", page, re.I) or re.search(r"</head>", page, re.I)
    if not m:
        return page
    return page[:m.start()] + links + "\n" + page[m.start():]


# Script dependencies hub.page_refs does not look for: an import map's targets
# and a module's static or dynamic imports (the Mission page loads three.js that
# way, and published without it).
_JS_REFS = (re.compile(r"""["']imports["']\s*:\s*\{([^}]*)\}""", re.S),
            re.compile(r"""\b(?:from|import)\s*\(?\s*["'](\.{1,2}/[^"']+\.m?js)["']"""))


def script_refs(html: str) -> set[str]:
    out = set()
    m = _JS_REFS[0].search(html)
    if m:
        out |= {v for v in re.findall(r""":\s*["']([^"']+\.m?js)["']""", m.group(1))
                if not re.match(r"^(?:[a-z][a-z0-9+.-]*:|//|/)", v, re.I)}
    out |= set(_JS_REFS[1].findall(html))
    return {r[2:] if r.startswith("./") else r for r in out}


def page_files(binding, html: str) -> dict[str, Path]:
    """Every file the page loads relatively -> where to copy it from."""
    from docsync.hub import page_refs                  # noqa: PLC0415
    ed = binding.editor
    look = [Path(ed.out).parent, Path(ed.render).parent]
    if ed.assets:
        look.append(Path(ed.assets).parent)
    out, problems = {}, []
    refs = {r[2:] if r.startswith("./") else r for r in page_refs(html)} | script_refs(html)
    for ref in sorted(refs):
        if ".." in Path(ref).parts:
            problems.append(f"{ref} — outside the page's folder, which a website address cannot reach")
            continue
        src = next((d / ref for d in look if (d / ref).is_file()), None)
        if src is None:
            problems.append(f"{ref} — linked by the page, found nowhere to copy from")
        else:
            out[ref] = src
    if problems:
        raise WebsiteError("the page loads files it could not take with it:\n  " + "\n  ".join(problems))
    return out


def publish(binding, site_root: Path, *, write: bool, fidelity: bool = True,
            sites: dict[str, Site] | None = None) -> str:
    sites = load_sites() if sites is None else sites
    d = designation(read_layout(binding), sites) or designation_from_publish(binding, sites)
    if d is None:
        raise WebsiteError(f"{binding.id} is not set up as a website (File ▸ Publish as a website…)")
    dest = site_root / target_path(d)
    owner = (dest / MARKER).read_text().strip() if (dest / MARKER).is_file() else ""
    if owner and owner != binding.id:
        raise WebsiteError(f"the address {d['site'].url}{d['slug']}/ is already {owner}'s — choose another")
    if dest.exists() and not owner and any(dest.iterdir()):
        raise WebsiteError(f"{target_path(d)}/ in {d['site'].repo} holds files Cider did not put there — "
                           "choose another address")
    page = build_page(binding)
    files = page_files(binding, page)
    current = dest / "index.html"
    if current.is_file() and current.read_text(encoding="utf-8") == page and all(
            (dest / r).is_file() and (dest / r).read_bytes() == s.read_bytes() for r, s in files.items()):
        return f"no change: {d['site'].url}{d['slug']}/ already matches the project"
    if fidelity and current.is_file():
        from docsync import fidelity as fid           # noqa: PLC0415
        problems = fid.check(current, page)
        if problems:
            raise WebsiteError("the published page would not look like the one it replaces:\n  "
                               + "\n  ".join(problems)
                               + "\nfix the cause, or pass --no-fidelity if the change is deliberate")
    if not write:
        return f"would publish {d['site'].url}{d['slug']}/ ({len(files)} file(s) beside the page)"
    if dest.exists():
        shutil.rmtree(dest)                           # the whole address is the page's: no leftovers
    dest.mkdir(parents=True)
    (dest / "index.html").write_text(page, encoding="utf-8")
    for ref, src in files.items():
        (dest / ref).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest / ref)
    (dest / MARKER).write_text(binding.id + "\n")
    return f"wrote {d['site'].url}{d['slug']}/"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m docsync.website", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", required=True)
    ap.add_argument("--site", type=Path, default=os.environ.get("CIDER_SITE"),
                    help="the site repository's checkout (default $CIDER_SITE)")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--no-fidelity", action="store_true")
    a = ap.parse_args(argv)
    if not a.site:
        ap.error("--site or $CIDER_SITE is required")
    sys.path.insert(0, str(ROOT))
    from docsync.registry import get                  # noqa: PLC0415
    try:
        print(publish(get(a.id), Path(a.site), write=a.write, fidelity=not a.no_fidelity))
    except WebsiteError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
