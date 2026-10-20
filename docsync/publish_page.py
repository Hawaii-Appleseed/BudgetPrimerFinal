"""Turn a scaffolded project back into the web page it came from.

    python3 -m docsync.publish_page --id my-page --shell site/my-page/index.html \\
        --out site/my-page/index.html

A project made by `docsync.scaffold` wraps a page's body in the editor's fixed
sheet and keeps only its inline styles, so its own build is a poor thing to put
on a public site: the fonts, stylesheet links and responsive layout belong to the
original page's `<head>`. This renders the project (with the document's text,
tables and citations as the editor left them), takes what is inside the sheet,
and puts it back inside a SHELL — the site's existing page, or the project's own
original.html — so the published page keeps the head it always had.

It is the usual `command:` of a binding's `publish:` block (docsync/publish.py),
and it has no idea which site it is writing to.

    --shell FILE       the page whose <head> and <body> tag are kept
                       (default: the project's original.html)
    --out FILE         where the page is written (default: the shell's own path)
    --env K=V          an environment variable for the render (repeatable) — the
                       way a renderer is told to draw the site's own markup
    --swap-asset FILE=>URL
                       the project carries an image inline (a data: URI) so the
                       editor can show it; write FILE's bytes' data URI back as
                       URL (repeatable)
    --replace OLD=>NEW  a plain text swap on the result (repeatable)
    --no-fidelity      skip docsync.fidelity, which renders the result next to the
                       page it replaces and refuses a change in how it looks

Without --write it only says whether the result differs from --out.
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_SHEET = re.compile(r'<section class="page"[^>]*>(.*)</section>\s*</body>', re.S)
# The engine closes every sheet with the rules that stack its placed objects on a
# phone. A published page has no placed objects of the editor's kind.
_SHEET_CSS = re.compile(r"\s*<style>@media screen and \(max-width:[\d.]+in\).*?</style>\s*\Z", re.S)
_BODY = re.compile(r"(<body[^>]*>)(.*)(</body>)", re.S)


def sheet_inner(built: str) -> str:
    """What is inside the project's built sheet."""
    m = _SHEET.search(built)
    if not m:
        raise ValueError('the render has no <section class="page"> to take the page from')
    return _SHEET_CSS.sub("", m.group(1))


# The text engine writes **bold** / *italic* as <b>/<i>; a site's own stylesheet
# nearly always styles <strong>/<em> (the page this came from used them), so the
# published page says what the site's CSS expects.
# Matched as pairs, so the close of an <i class="icon"> is never mistaken for one.
_TAGS = [(re.compile(r"<b>(.*?)</b>", re.S), r"<strong>\1</strong>"),
         (re.compile(r"<i>(.*?)</i>", re.S), r"<em>\1</em>")]


def semantic_tags(html: str) -> str:
    """<b>/<i> (no attributes) -> <strong>/<em>."""
    for rx, new in _TAGS:
        html = rx.sub(new, html)
    return html


def swap_asset(html: str, data: bytes, url: str) -> str:
    """Write an inline data: URI for `data` back as `url`."""
    return re.sub(r"data:[\w/+.-]+;base64," + re.escape(base64.b64encode(data).decode()), url, html)


def into_shell(shell: str, inner: str) -> str:
    """The shell with its <body> contents replaced; its <head> and the <body> tag stay."""
    m = _BODY.search(shell)
    if not m:
        raise ValueError("the shell has no <body>")
    return shell[:m.start(2)] + "\n" + inner.strip("\n") + "\n" + shell[m.end(2):]


def render(binding, env: dict[str, str]) -> str:
    """The binding's PUBLISH build (never the edit-mode draft)."""
    ed = binding.editor
    if ed is None or not ed.render:
        raise ValueError(f"{binding.id} has no editor.render to build")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "page.html"
        full = {**os.environ, **env, "DOCSYNC_OUT": str(out)}
        full.pop("DOCSYNC_EDIT", None)
        r = subprocess.run([sys.executable, str(ed.render)], cwd=str(ROOT), env=full,
                           capture_output=True, text=True)
        if r.returncode or not out.exists():
            raise RuntimeError("the render failed: " + (r.stderr.strip() or r.stdout.strip())[-600:])
        return out.read_text(encoding="utf-8")


def _pairs(items: list[str], what: str) -> list[tuple[str, str]]:
    out = []
    for it in items:
        if what != "--env" and "=>" in it:
            a, b = it.split("=>", 1)
        elif what == "--env" and "=" in it:
            a, b = it.split("=", 1)
        else:
            raise SystemExit(f"{what} wants {'K=V' if what == '--env' else 'OLD=>NEW'}, got {it!r}")
        out.append((a, b))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", required=True, help="the project's binding id in docsync.yml")
    ap.add_argument("--shell", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--env", action="append", default=[], metavar="K=V")
    ap.add_argument("--swap-asset", action="append", default=[], metavar="FILE=>URL")
    ap.add_argument("--replace", action="append", default=[], metavar="OLD=>NEW")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--no-fidelity", action="store_true",
                    help="skip the look-alike check against the page being replaced")
    a = ap.parse_args(argv)

    sys.path.insert(0, str(ROOT))
    from docsync.registry import get
    b = get(a.id)
    shell_path = a.shell or (b.editor.render.parent / "original.html")
    out_path = a.out or shell_path
    inner = semantic_tags(sheet_inner(render(b, dict(_pairs(a.env, "--env")))))
    for f, url in _pairs(a.swap_asset, "--swap-asset"):
        inner = swap_asset(inner, Path(f).read_bytes(), url)
    page = into_shell(shell_path.read_text(encoding="utf-8"), inner)
    for old, new in _pairs(a.replace, "--replace"):
        page = page.replace(old, new)
    current = out_path.read_text(encoding="utf-8") if out_path.exists() else None
    if page == current:
        print(f"no change: {out_path} already matches the project")
        return 0
    print(f"{out_path}: {len(current or ''):,} -> {len(page):,} bytes")
    if current is not None and not a.no_fidelity:
        from docsync import fidelity
        problems = fidelity.check(out_path, page)
        if problems:
            print("the published page would not look like the one it replaces:", file=sys.stderr)
            for p in problems:
                print("  " + p, file=sys.stderr)
            print("fix the cause, or pass --no-fidelity if the change is deliberate", file=sys.stderr)
            return 1
        print("fidelity: looks like the page it replaces" if problems == [] else "fidelity: not checked")
    if a.write:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(page, encoding="utf-8")
        print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
