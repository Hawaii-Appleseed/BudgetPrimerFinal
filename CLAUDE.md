# Working on the Budget Primer

> Deeper playbook: the **`report-editor` skill** (`.claude/skills/report-editor/`)
> — load it when adding or restyling anything on a report page. This file is the
> always-on summary.

## Default to driving the editor UI, not editing files

When asked for a visual/content change — move, resize, restyle, retext,
recolor an element, swap an image, adjust a chart — **drive the draft editor
in the browser** (open it, click, drag, type) instead of editing
`report2027/content.md` / `report2027/layout.json` or any other file
directly. A drag in the editor writes `layout.json` and nothing else, and
Save is a local commit reviewed before Push — the browser session has no
reach into the renderer or engine code even by accident.

Only edit repo files directly when the instruction is explicitly about the
files or the code — "edit content.md", "fix the renderer", "update
render_report.py", or similar. If a request is ambiguous ("make the intro
shorter"), prefer the UI. This matters more here than in most repos: an
engine-code edit is unusual (this repo mostly just vendors `docsync/` from
`~/primer-editor`, see below), so a direct file edit is more likely to be a
content change that should have gone through the editor instead.

## Adding SVGs / graphics the editor can move & resize

**Any SVG, diagram, icon-lockup, or decorative graphic you add to the report
MUST be emitted through `graphic()` in `report2027/tools/render_report.py`** —
never as a bare `<svg>` in the markup. A bare `<svg>` is frozen: the draft
editor can't see it, so the user can't reposition or resize it. `graphic()`
gives it the same `data-el` hook an image has, so the editor moves, resizes
(proportionally, from any corner), rotates, and layers it, with the placement
remembered in `report2027/layout.json`.

```python
# el_id: unique + stable (survives across rebuilds; it's the layout.json key).
# The SVG MUST carry a viewBox so it scales to the wrapper width. w = default
# width in inches before the user sizes it.
{graphic("spent.flowchart", '<svg viewBox="0 0 200 120" ...>...</svg>', w=2.4)}
```

- Give each graphic a **unique, stable `el_id`** (`<page>.<name>`). Renaming it
  orphans whatever position the user dragged it to.
- The SVG **needs a `viewBox`**; use `fill`/`stroke` colours (it can be
  recoloured from the editor's colour tools since it's inline).
- Small header/inline glyphs that belong INSIDE another element (e.g. a card
  title icon) are not graphics — leave those inline. `graphic()` is for
  free-standing things the user should be able to move on their own.

**New visuals are SEPARATE primitives, grouped — never fused.** A labelled
box = a `shapes` rect + a `boxes` text box in layout.json + a `groups` entry
(`["<shapeId>", "text.<boxId>"]`) so they move together until the user hits
Ungroup (⌘⇧G). Never bake sentences inside an SVG or fuse text + background
into one div — the user must be able to detach the text. Full schema and
rules: the `report-editor` skill.

The mechanism, if you need to extend it: `graphic()` wraps the SVG in
`<span class="ds-graphic" data-el=...>`; `.ds-graphic` CSS in
`report2027/web/primer.css` makes the SVG fill the wrapper; the editor's
`isGraphic()` in `docsync/editor/edit.html` gives `.ds-graphic` corner
handles. `tests/editor/graphic.spec.js` (in the primer-editor repo) guards it.

## Downstream consumer: capitol-quest

Commits that touch `docs/js/departments_act175_fy2027.json` or
`summary_stats_act175_fy2027.json` fire `.git/hooks/post-commit`
(untracked; installed by `~/capitol-quest/tools/sync_primer.py
--install-hook`), which mirrors both files into `~/capitol-quest`,
rebuilds its budget.json, and auto-commits there. Log:
`~/.config/capitol-quest-sync.log`. After recloning this repo, re-run
the installer to restore the hook.

## Repo layout & the editor

- The live editor / engine (`docsync/`, `report2027/tools/serve.py`,
  `edit.html`) is **vendored from the separate `~/primer-editor` repo, and the
  vendoring is AUTOMATIC**: primer-editor's post-commit hook runs
  `python3 -m docsync.vendor`, which copies the engine here, restages
  `docs/primer`, and makes a local commit. So: NEVER edit engine files in this
  repo — the next vendor run refuses while they're dirty, and your change
  would be orphaned anyway. Fix engine/editor/tooling bugs in
  `~/primer-editor` and commit there; the copy arrives on its own. Report
  *content* (`content.md`, `layout.json`, the numbers, page-specific markup
  like the appropriation-card icons) is owned HERE and never flows back.
- **Never launch the live server yourself.** "Budget Primer Editor.app" owns
  it; a server started any other way can't reach the keychain and its Push
  hangs. If it needs a restart, ask the user to relaunch the app.
- `make -C report2027 pub` is the ONE build command (used by the live server,
  Save, and CI). `report2027/web/index.html` and `docs/primer/` are generated —
  never hand-edit them.
- Save commits locally; Push sends the current branch AND fast-forwards `main`
  (the deploy branch), so **every Push publishes to the live site**.
