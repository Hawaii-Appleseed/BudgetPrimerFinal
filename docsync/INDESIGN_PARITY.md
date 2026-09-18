# The editor, against InDesign

Scoped 2026-09-17, after `CHART_PARITY.md`'s chart gaps were closed. That file
is the chart half and stays the chart half; this one is everything else — the
editor as a page-layout tool.

## Why this file exists, and how it ranks

Not by InDesign's menu. By **what makes someone leave**: either open InDesign
instead, or hand-edit `layout.json` / the renderer, which is the local version
of the same defeat — and which is exactly how the primer ended up with six
frozen figures.

Two questions decide the rank:

1. **Does the work have to be redone every time something changes?** A thing
   you do once and keep is an inconvenience. A thing you redo on every edit is
   why people stop using the tool.
2. **Is there any way to say it at all?** A gap with a workaround costs time.
   A gap with none costs the document.

The chart pass is the pattern to copy: name the smallest thing that closes the
gap, make it opt-in, keep every existing render byte-identical, and say in the
doc what moved and why.

## What is already here

Worth stating, so the gaps below are read against the right baseline. Placed
objects with drag/resize/rotate/opacity/z-order and shadows; text boxes
carrying markdown; tables with borders, fills and per-cell overrides; charts
(see `CHART_PARITY.md`); icons and images with crop; per-page fills; page
resize including pageless; entrance animations; a topic-colour palette per
project; templates with colour schemes; guides, snapping, align and
distribute; a real undo stack shared with live collaboration; version
history, comments and share on the hub; PDF out through headless Chrome;
a Squarespace export that freezes the design at one width.

That is a lot of InDesign's *object* model. What is missing is nearly all of
its *text* model, and the whole idea of a named, redefinable style.

## Tier 1 — the ones that make work get redone

1. **Paragraph and character styles. — CLOSED 2026-09-17.**
   `layout.textStyles` is a map of named styles. A slot, a text box or a table
   says `use: "<name>"`; a style may say `from: "<name>"` and inherit. One
   rule decides everything: **the nearer the author, the stronger** — a slot's
   own keys beat the style it wears, and a style's own keys beat the one it
   comes from. So a style is a starting point and never a cage, which is what
   makes people adopt a stylesheet rather than work around it.

   `resolve_style()` in `layout.py` is the whole of it, and the three places a
   style becomes CSS — slot, box, table — all go through `Layout.styled_as()`,
   so they cannot disagree about what wearing a style means. The editor
   mirrors the resolution ONLY to show what the page does: the type toolbar
   reads the resolved style, so a slot wearing Body shows Body's font rather
   than an empty dropdown.

   Four things worth keeping:

   - **The resolved style is what clears the legibility floor.** Wearing a
     style is not a way under `MIN_TEXT_PX`; nor is overriding one down to it.
   - **`font_link()` resolves.** A slot that says only `use: "Heading"` names
     no font of its own, and the family would have been faked in the published
     page while looking right in the editor, which loads every family.
   - **Saving a style makes the slot it came from WEAR it**, with nothing left
     over. Otherwise the one slot that defined a style is the one slot a
     redefine can never move.
   - **A slot may wear a style and still disagree with it**, and the picker
     says so — InDesign marks that state with a `+` and something has to, or
     "redefine Body" looks broken on the one slot that was overriding it.

   In the toolbar, where InDesign's control bar puts it: the style picker,
   then Save as a style / Redefine / Apply to matching / Unlink. "Apply to
   matching" finds every other piece of text whose resolved CSS is already
   identical and points it at the style — the cheap way to adopt a stylesheet
   in a document that was styled by hand.

2. **Object styles. — CLOSED 2026-09-17.**
   `layout.objectStyles` is a map of named looks, each saying which `kind` it
   dresses — `shape`, `box` or `table` — because re-scoping confirmed the
   worry below: the three are three key sets, and a style any of them could
   wear would be a style that could say nothing checkable. `OBJECT_STYLE_KEYS`
   in `layout.py` is the whole list per kind; a key not on it (geometry,
   identity, content) is refused where the style is defined, with the list in
   the message. An object says `use: "<name>"`; a style may say `from` (same
   kind only). `resolve_object()` is the rule — the nearer the author, the
   stronger — with one line more than text: a box's `style` (its TEXT style)
   merges one level down, so a box that says `size: 14` over a style that says
   `style: {use: "Body"}` is a 14px Body and not a lost Body.

   The three renderers each go through `Layout.dressed()`, so they cannot
   disagree about what wearing a style means; an object wearing nothing is
   returned as itself, which is what keeps every existing render byte-for-byte
   (checked across all twelve bindings, published and edit mode). The
   validator checks the DRESSED object, so a style cannot hand a shape a value
   the shape could not carry, and a style's own keys are checked where it is
   defined, so a bad style fails on load and not on the first thing to wear it.

   Because a pull-quote style with no padding and no rule is not a pull-quote
   style, text boxes grew the look keys they were missing, all opt-in:
   `pad` (inches; one, two or four), `radius` (px), `border` (the table's
   border spec, with `sides` = all or ONE edge — the left rule), and — see (4)
   — `cols`/`gap`. And every kind took `blend` (`mix-blend-mode`), which
   closes (12) on the way past.

   In the editor: a shape or table gets the picker at the head of the arrange
   strip, where the text picker leads the type bar, with the same four verbs
   (Save as a style / Redefine / Apply to matching / Unlink) built by ONE
   function for both places. A text box is text — one click puts the type bar
   in hand — so it dresses through a **Box** button there: the picker and
   verbs, then background, border (colour, weight, edge), padding, corners and
   columns. Every control reads the dressed object and writes the object
   itself, so a slider moved on a box wearing a style creates an override and
   the picker marks it `+`. The strip's border, transparency and effects pops
   read the dressed object too, and "none"/"remove" on something the style
   gave the object writes an explicit `null`/`normal` rather than a silent
   nothing, because a deletion has to beat the style it is deleting against.

   Not done, and said so: the table panel's presets still write keys onto the
   table rather than offering to save a style — the strip's picker covers a
   table, so this is a convenience and not a gap. There is no pilot verb yet
   for wearing or defining a style; the JSON is one `use` key, so a pilot can
   write it through the announce-then-edit route.

3. **Find and Change. — CLOSED 2026-09-17.** ⌘F in either document (the
   chrome and the report iframe are separate documents and a key pressed over
   the page never reaches the other), or File ▸ Find & Change. Match case and
   whole words; every hit listed in context and grouped by where it is,
   because "38 hits" is a count and not something anyone would press Change
   all against; clicking a slot's hit scrolls the page to it. Change all is
   one `pushHistory()` and one render, so it is one ⌘Z.

   `findTargets()` is the one list of the three places words live — a
   content.md slot, a text box's own `md`, a table's cells — so a find cannot
   quietly miss a third of the document. It searches the AUTHORED text, not
   the rendered HTML, because that is what a replace writes back; a word split
   across a bold marker is not found, which is the honest answer rather than a
   replace that corrupts the markup.

   Deliberately not InDesign's GREP find/change, and not styles-as-criteria.
   Plain text across the whole document is the thing people actually reach
   for, and it was entirely absent.

## Tier 2 — the ones with no way to say it at all

4. **Text that flows between frames, and columns inside one.** A box is one
   frame, and text that overruns it simply overruns. **The cheap half is
   CLOSED 2026-09-17:** a box says `cols: 2` (1–4) and `gap` (inches), CSS
   sets the columns, the words flow between them as they are edited, and a
   phone gets one column back (`mobile_css` emits the release only when some
   box asked for columns). That is the common two-column page with no flow
   machinery at all; it lives in the Box panel with the rest of the box's
   look, and an object style can carry it.

   The other half — threading between frames — is still the most *InDesign*
   thing on the list and the most work: it needs a measured text layout, which
   the engine deliberately does not have (it hands text to the browser and
   lets CSS set it). Scoped separately, and honestly, as a large piece; see
   "Not coming".

5. **Text wrap around an object. — CLOSED 2026-09-17**, the rectangular
   case, which is the case. `wrap: "left" | "right"` (and `wrapPad`, inches)
   on an ANCHORED box or designed element: the same runtime that positions
   anchors moves the object to just before its paragraph in the same flow, as
   a CSS float, and the paragraph's lines shorten around it — the browser's
   own wrap, no measuring. A previous sibling, never a child, so the
   paragraph's markup stays exactly what content.md says and editing it in
   place can never swallow the figure. Wrap without an anchor is refused at
   load: text flows around what belongs somewhere. A wrapped object's place
   IS its paragraph's, so it refuses drags and `place()` (both say to turn
   wrap off first), and a phone gets the flow back (`float:none`). In the
   editor: a Text wrap row in the Position pop (designed elements) and the
   Box panel (text boxes), shown once the object is anchored; pilot
   `wrap(id, side)` / `wrap(id, null)`.

   InDesign's contour-from-alpha wrap (`shape-outside` from the image) is not
   here and is not what anyone here needs; the CSS exists if it ever is.

6. **Anchored objects. — CLOSED 2026-09-17**, for text boxes and designed
   elements. `anchor: {to: <slot>, dy, edge}` on a box or a `positions` entry:
   the object follows the slot it names, `dy` inches under its top (or, with
   `edge: "bottom"`, under its bottom — a slot of several paragraphs has ONE
   top and ONE bottom, so "under this text" means under all of it). Measured,
   not computed: the engine hands text to CSS and cannot know where a
   paragraph ends, so `ANCHOR_JS` — one string in `layout.py`, emitted once
   per document and only when something is anchored — asks the layout that
   just happened and writes `top`. It runs at parse, load, fonts-ready and
   resize; the phone release rule (`top:auto !important`) beats it, so an
   anchored object reads in order there like any other. `content.py` stamps
   the followed slot with `data-anc-host` in BOTH modes, because `data-slot`
   is edit-only and the published page needs a hook.

   The editor runs that same string after every render (an imported page
   node never runs its scripts), before it measures anything, so there is one
   implementation. Anchoring never moves the thing: the Anchor button on the
   type bar (a text box) or the arrange strip (a designed element) ARMS the
   next click, the click names the paragraph, and `dy` is whatever distance
   the object already had. A drag keeps the paragraph and changes the
   distance (`anchorRebase` in the placer's one write path). Releasing writes
   the object's current place back as a plain `y`. Pilot: `anchor(id, key,
   {edge})` / `anchor(id, null)`, a single verb with its own history step.

   Not shapes: the shape layer is the page's own SVG drawing and `top` means
   nothing in a viewBox. Not tables yet: the table drag is its own path. Both
   said by the verb and by the hidden button.

7. **Master pages.** Templates seed a new document, and page 4 of the report
   template is a "replicable section page" you copy. Nothing propagates
   afterwards: change the running footer and you change it on every page by
   hand. Distinct from (1) only in what it applies to.

## Tier 3 — real, but not what is stopping anyone

8. **Named layers.** The panel exists and is deliberately switched off
   (`#layers { display:none !important }` — "hidden for now", one line to
   bring back). What it offers is per-page restacking, not InDesign's named
   layers with lock and hide across the document.
9. **Baseline grid.** Nothing aligns type across columns or facing pages.
   Matters once (4) exists; before that there is nothing to align to.
10. **Table headers that repeat across a break, and cell styles. —
    RECLASSIFIED 2026-09-17.** A placed table is pinned by inch to one page
    and never breaks, so a repeating `<thead>` has nothing to repeat across;
    the case the item described is a report renderer's own flowed table,
    which is that report's business. Cell styles exist as per-cell
    overrides (`cells`), and a table style (2) now carries the grid's look.
    Nothing left here that stops anyone.
11. **Print production — bleed, slug, spot colour, preflight, package.** The
    deliverable here is HTML plus a Chrome-printed PDF, so most of this is
    genuinely out of scope. Bleed is the exception and only for a report that
    is actually going to a printer.
12. **Blend modes. — CLOSED 2026-09-17**, on the way past (2): `blend` on a
    shape, box or table, in the transparency pop, which is what a blend is a
    generalisation of. `normal` is accepted so an object can undo the blend
    its style gave it.

## Not coming, and why

- **A measured, InDesign-faithful text engine.** The whole design is that the
  browser sets the type and the same markup serves the preview, the published
  page, the phone and the PDF. A second text engine would have to agree with
  CSS exactly or the preview would stop being the thing.
- **Anything that only makes sense on paper** — printer's marks, colour
  separation, ink limits. These documents are read on a screen first.

## The order to do them in

(1), (2), (3), (5), (6) and (12) are done, (4)'s cheap half is done and (10)
turned out not to be a gap. The re-scope of (2) was right to insist on: it
took three key sets, three checkers and two places in the UI — but ONE
resolver and ONE verbs builder, which is what kept it a day's work rather
than a week's. (6) and (5) went the same way: one runtime string, run by the
page and by the editor, rather than two positioners that would have drifted —
and wrap turned out to be four lines of that string once an object knew what
paragraph it belonged to.

What is left, and in what order: **(7) master pages**, which is (2) pointed
at page furniture — a named set of boxes/shapes a page `use`s, so the running
footer changes in one place; scope it against the report templates' section
page, which is the thing people copy today. **(8)** if the layers panel is
wanted back (`#layers { display:none !important }` is the one line). **(9)**
waits on threading, which is not coming; **(11)** only if a report actually
goes to a printer.
