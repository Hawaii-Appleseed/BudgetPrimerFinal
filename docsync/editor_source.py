"""The editor, put together: docsync/editor/edit.html with its parts inlined.

The editor is written as several files (docsync/editor/edit.html and the areas
under docsync/editor/parts/) and served as ONE: every place that hands the
editor to a browser — `docsync.stage` (docs/<id>/edit.html, which serve.py
serves), `docsync.hub` (the staff hub's primer/edit.html) — writes
`assembled()`, never the raw edit.html. So what a browser runs is byte for
byte what the single file was before the split: same globals, same hoisting,
one script, one stylesheet, nothing new for the service worker, the cache or
the hub's version check to know about.

A part is pulled in by a line that holds nothing but an include, written as a
comment of the language it sits in:

    //@include parts/pdf.js          (inside the <script>)
    /*@include parts/editor.css*/    (inside the <style>)

The whole line, its newline included, is replaced by the file's bytes, so a
part ends with a newline like any other line. Paths are relative to
docsync/editor/. A part may not include another (one level keeps the line map
simple), and a part named twice or missing is an error, not a silent gap.

tools/editor-source.cjs is the same rule for the Node tools (ratchets,
typecheck, the test picker); keep the two equal — test_docsync.py checks that
they agree byte for byte.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

EDITOR_DIR = Path(__file__).resolve().parent / "editor"
EDITOR = EDITOR_DIR / "edit.html"
PARTS = EDITOR_DIR / "parts"

INCLUDE = re.compile(r"^[ \t]*(?://|/\*)@include ([\w./-]+)(?:\*/)?[ \t]*(?:\r?\n|\Z)", re.M)


class AssembleError(ValueError):
    pass


def segments(text: str, read: Callable[[str], str | None]) -> list[tuple[str, str]]:
    """edit.html's text cut into [(file, text)] in order: the pieces of
    edit.html itself (file "edit.html") and each part where it is included."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    at = 0
    for m in INCLUDE.finditer(text):
        name = m.group(1)
        if not name.startswith("parts/") or ".." in name:
            raise AssembleError(f"edit.html: an include must name a file under parts/: {name}")
        if name in seen:
            raise AssembleError(f"edit.html: {name} is included twice")
        seen.add(name)
        body = read(name)
        if body is None:
            raise AssembleError(f"edit.html includes {name}, which does not exist")
        if INCLUDE.search(body):
            raise AssembleError(f"{name}: a part may not include another")
        if body and not body.endswith("\n"):
            raise AssembleError(f"{name}: a part must end with a newline")
        out.append(("edit.html", text[at:m.start()]))
        out.append((name, body))
        at = m.end()
    out.append(("edit.html", text[at:]))
    return [s for s in out if s[1] or s[0] != "edit.html"]


def assemble(text: str, read: Callable[[str], str | None]) -> str:
    return "".join(body for _, body in segments(text, read))


def _read_dir(d: Path) -> Callable[[str], str | None]:
    def read(name: str) -> str | None:
        p = d / name
        return p.read_text(encoding="utf-8") if p.is_file() else None
    return read


def assembled(editor: Path = EDITOR) -> str:
    """The editor a browser gets, from the files on disk."""
    return assemble(editor.read_text(encoding="utf-8"), _read_dir(editor.parent))


def assembled_bytes(editor: Path = EDITOR) -> bytes:
    return assembled(editor).encode("utf-8")


def sources(editor: Path = EDITOR) -> list[Path]:
    """Every file the assembled editor is made of: edit.html and its parts."""
    files = [editor]
    files += sorted(p for p in (editor.parent / "parts").glob("*") if p.is_file())
    return files


if __name__ == "__main__":                     # python3 -m docsync.editor_source > out.html
    import sys
    sys.stdout.write(assembled())
