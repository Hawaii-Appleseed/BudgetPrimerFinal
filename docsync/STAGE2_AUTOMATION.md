# How much of STAGE TWO can be automated?

STAGE ONE (make a static page *openable* in the draft editor) is now fully
automatic: `python3 -m docsync.scaffold page.html --id my-page`. STAGE TWO
(make its pieces *editable*) was done by hand for `projects/rxkids`. This
note scopes which parts of that hand work could become scripts, in order of
payoff, and which parts genuinely need a human or an AI session.

## Automatable — worth building

### 1. Auto-slot proposal (`docsync.propose`) — DONE
`python3 -m docsync.propose --id my-page` (after scaffold) walks
`original.html` and mechanically wires every substantial text leaf as an
editable slot: the element keeps its tag and styling and gains `data-slot`
via `C.slot_attr` (no wrapper), its text moves to `content.md` under a
generated key (`ha-hero-lead.p-1` — CSS classes make surprisingly good
names). Headings rejected for styled children get the **fragment pass**:
their simple `<span>` children slot with a relaxed length threshold and the
bare text runs between them get generated wrapper spans, so a line like
"Advancing *economic justice* for and with…" is fully editable in three
pieces without touching the styled markup. Inline b/i/em/a round-trip
through the markdown layer; free-standing `<img>`s (no style attr) get
movable `data-el` hooks.

Proven on the Hawaii Appleseed mission page (`projects/our-mission`):
47 slots, hero + values + practices + footer all editable, publish build
byte-clean (zero markers, zero data-slot). What still needs the AI/hand
pass: renames, pruning chrome, repeated-widget restructuring, citations.

### 2. Page-height trim (fully automatic)
Render, measure the content's real bottom in headless Playwright (already a
dev dependency), write the trimmed height back to `render_report.py` +
`docsync.yml`. Removes the "generous 160in default" wart and the class of
stale page-cut warnings entirely.

### 3. Reveal-animation neutraliser (DONE — in the scaffold)
The generated renderer now injects, in edit mode only, a generic
`opacity:1 !important` override for `[class*="reveal"/"fade"/"animate"]` and
`[data-aos]`. This was the #1 stage-one blocker on rxkids (everything below
the hero was invisible). Pages with *JS-driven* behaviour — image cyclers,
carousels — still need a hand-written edit-mode override.

### 4. Movable images/SVGs (semi-automatic, ship behind a flag)
Free-standing `<img>` and inline `<svg>` elements are safe to auto-wrap with
`data-el` hooks (`L.attr`) — that's mechanical. **Flow text blocks are not**:
giving arbitrary paragraphs `data-el` means a drag absolutises them and their
siblings reflow underneath, wrecking the page. Auto-wire images only.

## NOT worth automating — needs judgment

- **Repeated-widget restructuring** (the rxkids benefits tabs → a loop over
  structured slots): detecting "these six blocks are one component" and
  synthesising the loop is real program synthesis. An AI session does this
  well; a script does it wrong confidently.
- **Citations/footnotes**: source pages carry no citation semantics; mapping
  superscripts and links to `[^source-id]` definitions is editorial.
- **Page-specific JS overrides**: what an image cycler or carousel should do
  in edit mode (freeze? show frame 1? expose each frame?) is a per-page call.
- **Grouping/detachables**: which visuals move together vs independently is
  design intent, unrecoverable from markup.

## Colored background sections — shorten/lengthen like Canva

The ask: grab the bottom edge of a colored band (the hero gradient, the dark
"Join us" section) and make the colored area taller or shorter, the way a
Canva back-layer rectangle works. Two viable models, in build order:

### B — resizable section heights — DONE
The background stays glued to its section; the section's height is a layout
override. `docsync.propose` finds `section`/`header`/`footer` tags whose own
CSS class carries a `background` declaration and stamps them with a `⟦B:key⟧`
marker; the renderer substitutes `L.sec(key)` — `data-sec` in edit mode, a
`min-height:<h>in` style in both modes when `layout.json` has a `sections`
override. In the editor (`wireSections` in `edit.html`), every `[data-sec]`
band grows a bottom-edge grip (hover pill, ns-resize cursor): dragging down
stretches the band and writes `layout.sections[id] = {h}`, dragging back to
the content's natural height clears the override entirely, so an untouched
page stays untouched. Text keeps flowing — the band can never end
mid-paragraph. Guarded by `tests/editor/section-resize.spec.js`; proven on
the mission page (hero / five-section / footer bands).

### A later — detach to a true back-layer shape (the full Canva model)
An explicit "detach background" action on a section: strip the background
declaration from the section, emit a full-width rect into `layout.json`
`shapes` seeded with the section's rendered geometry, drawn BEHIND the flow
content (SVG layer with negative z-index inside the `.page` stacking
context — static flow content paints above negative-z siblings, so this
works without touching the page's own markup). The user then moves/resizes
the colored rect completely independently, Canva-style.
- Costs: needs rendered geometry to seed the rect (headless measure or the
  editor does it live at detach time — the editor is the right place); once
  detached, the band no longer follows content reflow — that's inherent to
  the model and should be a deliberate user action, not a default.
- Gradients can be carried (SVG gradients); photo backgrounds should refuse
  detach in v1.

B is built; A remains the escape hatch for full freedom — opt-in per
section, reusing the existing shapes/groups pipeline — if stretching alone
ever proves insufficient.

## The realistic pipeline

```
scaffold (script, seconds)        → openable, everything visible   [DONE]
propose (script, seconds)         → every paragraph editable       [DONE]
AI pass (one session, minutes)    → prune chrome, rename, restructure
                                    widgets, wire citations, JS overrides
```

The scripts compress the AI session from "read and rebuild the whole page"
(rxkids took a full session) to "review a working proposal" — but the last
pass can't be eliminated, because it *is* the editorial decision of what the
page's owner should be able to touch.
