"""docsync.yml -> Binding objects.

The registry is the single place that says which doc feeds which file. Adding a
report to the sync is a registry entry, not new code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "docsync.yml"

_ID_RE = re.compile(r"^[a-z0-9-]+$")
MODES = ("slots", "fragment")


class RegistryError(RuntimeError):
    pass


@dataclass
class Editor:
    """What the draft editor needs to run a report in the browser.

    The editor runs the report's OWN renderer via Pyodide, so it has to be told
    which renderer, what that renderer reads, and the shape of a page. Naming
    those here is what keeps one editor able to serve any report instead of one
    editor per report.
    """
    render: Path                      # the renderer's entry point
    engine: list[Path]                # everything else the renderer reads
    out: Path                         # where the renderer writes its HTML
    dir: Path                         # published dir; the editor is staged here
    page: tuple[float, float] = (8.5, 11.0)     # inches, w x h
    margins: tuple[float, float] = (0.62, 0.75)  # side, top — snap lines
    assets: Path | None = None        # uploaded images land here
    palette: list = None              # the report's own colours, offered first
    layout: Path | None = None        # the overrides file, if the report has one
    branch: str = "main"              # the deploy branch drafts fork from / publish to


@dataclass
class Binding:
    id: str
    content: Path              # the committed source the report reads
    doc: str = ""              # a Google Doc id, if this binding syncs to one
    mode: str = "slots"        # slots | fragment (only meaningful with a doc)
    build: str = ""            # shell command to rebuild after an edit
    target: Path | None = None  # fragment mode: page to inject into
    anchor: str = "docsync"    # fragment mode: <!-- docsync:start|end -->
    pr: bool = False           # open a PR instead of committing to main
    outputs: list[str] = field(default_factory=list)  # extra paths to commit
    # Extra glob patterns (relative to ROOT) the local live server should
    # watch for changes, beyond content/layout/editor.render/editor.engine —
    # a report's own CSS/JS or data files the renderer doesn't read directly
    # but a rebuild should still react to.
    watch: list[str] = field(default_factory=list)
    editor: Editor | None = None       # None: this binding has no draft editor
    # How docsync.check treats editability findings (dead text / frozen prose
    # in the edit-mode draft). "strict": they are ERRORS and fail CI — the
    # generators write this into every new project, so an ingestion cannot
    # ship uneditable text silently. "warn": legacy default for bindings that
    # predate the check. "wip" (kept as written, behaves like warn): a
    # scaffolded page parked between STAGE ONE and STAGE TWO — greppable
    # intent, flipped to strict when the wiring pass is done.
    editability: str = "warn"
    # Exact visible strings a strict binding accepts as deliberately inert
    # (page-number chrome, a data-frozen label). Each entry is a reviewable
    # decision in the diff; anything not listed still fails.
    editability_ok: list[str] = field(default_factory=list)

    @property
    def state_file(self) -> Path:
        return ROOT / "docsync" / ".state" / f"{self.id}.json"

    def __str__(self) -> str:
        return f"{self.id} ({self.mode})"


def _require(d: dict, key: str, where: str):
    if key not in d or d[key] in (None, ""):
        raise RegistryError(f"{where}: missing required key '{key}'")
    return d[key]


def load_registry(path: Path = REGISTRY) -> list[Binding]:
    try:
        import yaml                                   # noqa: PLC0415
    except ImportError as e:
        raise RegistryError("pyyaml is required: pip install pyyaml") from e

    if not path.exists():
        raise RegistryError(f"{path} does not exist")
    data = yaml.safe_load(path.read_text()) or {}
    raw = data.get("bindings") or []
    if not raw:
        raise RegistryError(f"{path}: no bindings defined")

    out, seen = [], set()
    for i, b in enumerate(raw):
        where = f"{path.name}: binding #{i + 1}"
        bid = _require(b, "id", where)
        if not _ID_RE.match(bid):
            raise RegistryError(f"{where}: id '{bid}' must be lowercase kebab-case")
        if bid in seen:
            raise RegistryError(f"{where}: duplicate id '{bid}'")
        seen.add(bid)

        # Doc-only fields are optional now that a binding can be local-only.
        mode = b.get("mode", "slots")
        if mode not in MODES:
            raise RegistryError(
                f"{where}: mode '{mode}' must be one of {', '.join(MODES)}")

        target = b.get("target")
        if mode == "fragment" and not target:
            raise RegistryError(f"{where}: fragment mode requires 'target'")

        edi = str(b.get("editability", "warn"))
        if edi not in ("warn", "strict", "wip"):
            raise RegistryError(
                f"{where}: editability '{edi}' must be warn, strict or wip")
        edi_ok = b.get("editability_ok") or []
        if not all(isinstance(s, str) for s in edi_ok):
            raise RegistryError(f"{where}: editability_ok must be strings")

        out.append(Binding(
            id=bid,
            doc=b.get("doc", ""),
            mode=mode,
            content=ROOT / _require(b, "content", where),
            build=b.get("build", ""),
            target=ROOT / target if target else None,
            anchor=b.get("anchor", "docsync"),
            pr=bool(b.get("pr", False)),
            outputs=list(b.get("outputs") or []),
            watch=list(b.get("watch") or []),
            editor=_editor(b.get("editor"), where),
            editability=edi,
            editability_ok=[str(s) for s in edi_ok],
        ))
    return out


def _editor(e: dict | None, where: str) -> Editor | None:
    if not e:
        return None
    page = e.get("page") or [8.5, 11.0]
    margins = e.get("margins") or [0.62, 0.75]
    if len(page) != 2 or len(margins) != 2:
        raise RegistryError(f"{where}: editor 'page' and 'margins' are two numbers each")
    return Editor(
        render=ROOT / _require(e, "render", where + " editor"),
        engine=[ROOT / f for f in (e.get("engine") or [])],
        out=ROOT / _require(e, "out", where + " editor"),
        dir=ROOT / _require(e, "dir", where + " editor"),
        page=(float(page[0]), float(page[1])),
        margins=(float(margins[0]), float(margins[1])),
        assets=ROOT / e["assets"] if e.get("assets") else None,
        palette=list(e.get("palette") or []),
        layout=ROOT / e["layout"] if e.get("layout") else None,
        branch=e.get("branch") or "main",
    )


def get(binding_id: str, path: Path = REGISTRY) -> Binding:
    for b in load_registry(path):
        if b.id == binding_id:
            return b
    raise RegistryError(f"no binding with id '{binding_id}' in {path.name}")
