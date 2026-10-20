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

   **The other half, threading between frames — CLOSED 2026-10-19.** It was
   filed under "Not coming" on the belief that it needed a measured text
   engine. It does not: it needs the browser's layout to be READ after it
   happens, which is what ANCHOR_JS already does. A box says
   `next: "<box id>"`; the FIRST frame of a thread holds the whole story's
   markdown and the frames after it hold none (refused at load if they do,
   since their words would be lost). Every frame is exactly its `h` —
   `height`, not the `min-height` a lone box gets — because a frame that
   grows never runs out of room; threading a box without one gives it its
   current rendered height, so nothing moves.

   `layout.THREAD_JS`, emitted once and only when something is threaded,
   puts each story back in its first frame and pours it forward: in each
   frame, the first block whose bottom passes the frame's content edge is
   split at the first LINE that does not fit (a binary search over word
   starts, measured with Ranges), and that block's rest and everything after
   it move on. A paragraph splits by `Range.extractContents`, so a link or
   bold run across the break is carried intact on both sides; a list moves
   whole items and an `<ol>` keeps counting with `start`. A continuation is
   `ds-cont`: no first-line indent, no space before. A last frame that still
   overflows gets `data-overset`, which the editor draws as InDesign's red
   `+` and `audit()` reports as `overset`; the words are NOT hidden — they
   spill past the frame, and the fit check still catches anything that runs
   off the sheet. No script, or a phone (where mobile_css puts every frame
   back in the flow): the story stays in its first frame, which grows.

   Editing: a click in ANY frame opens the whole story in the first frame,
   growing while it is open (`__dsThreadGather`), and it pours back through
   the frames when the edit commits — InDesign edits in place, this edits
   the story, which is the one real difference. Right-click a box: **Thread
   text into…** arms the next click (Esc cancels; the clicked box's own
   words join the end of the story, as InDesign merges stories), and **Break
   thread after this frame** cuts the chain (the empty frames after it go;
   what no longer fits is overset). Deleting, duplicating, copying and
   pasting a frame keep the thread whole (`unlinkFrame`, `standaloneCopy`);
   dragging a frame's edge to nothing leaves it at the smallest height
   rather than turning it back into a growing box. Pilot: `thread(a, b)`,
   `unthread(id)`, both batchable; `setBoxText` on the first frame retexts
   the whole story.

   Refused at load, each with its reason: a `next` naming no box, a frame
   continuing into itself, two frames continuing into one, a loop, a frame
   with no `h`, a threaded frame that also anchors, wraps, acts or sets
   columns (thread more frames instead), and any master box (a shared
   layout cannot thread). Every existing render stays byte-identical.

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

   **Contour wrap — CLOSED 2026-10-19.** `wrapShape` on a wrapped object:
   `box` (the rectangle, as before), `contour` (the picture's own outline,
   CSS `shape-outside` read from its transparency — a cut-out photo, a
   logo) or `ellipse` (a round portrait). Its own runtime, `WRAPSHAPE_JS`,
   emitted only when something uses it, so the anchor runtime every
   anchoring report ships kept its bytes; it runs after the anchors float the
   object. A picture with no transparency, or from another origin, falls back
   to the rectangle by itself. In the Position pop and the Box panel, an
   Outline/Ellipse row under the wrap sides; pilot `wrap(id, side, shape)`.

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

7. **Master pages. — CLOSED 2026-09-18.** `layout.masters` is a map of
   named masters, each a set of `boxes` and `shapes`; `layout.pageMasters`
   says which master a page uses. The engine draws a master's items on every
   page that uses it as page instances — id `<item>@<page>`, `{page}` in a
   box's words as that page's number (its place in the order, so a blank page
   numbers itself). A master's items go through exactly the checks a page's
   own do, plus three refusals: no anchor or wrap (the page places them, not a
   paragraph), no `act` (a button copied onto every page is a button nobody
   meant), and no `@` in an id. A layout without masters renders byte-for-byte.

   The editor resolves `text.folio@3` to the master's ONE box, so a drag, a
   retext or a restyle made on any page lands on every page that uses the
   master — which is what a master is for, and the strip says so ("on shared
   layout Section", and the type bar's chip). Made from a page: the strip's
   picker (page mode) offers **+ Share this page's layout…**, which MOVES the
   page's own boxes and shapes into the master; other pages take it from the
   same picker; right-click a page for **Detach shared layout to this page**
   (own, numbered copies) and a master item for **Remove from shared layout**.
   Delete on a master item refuses and says why. A duplicated page keeps its
   master; a deleted blank drops its entry. Pilot: `master(page, name)`,
   `masterFrom(page, name)`, `detachMaster(page)`.

   **The person sees "shared layout", not "master" (2026-09-21).** The word
   was InDesign's until 2020, when InDesign itself renamed it to "parent
   page" — and neither reads as anything to somebody who has not used
   InDesign, which is who lays out these reports. The picker now reads
   *No shared layout / Shared layout: Section / + Share this page's layout…*
   and carries a tooltip saying what one does. Only the STRINGS changed:
   `layout.masters`, `layout.pageMasters`, the `@` ids, the pilot verbs and
   every identifier keep the old word, so parity with InDesign's model is
   unaffected. Rename strings here, never keys.

   Master shapes move too (2026-09-18): the thirty-odd shape lookups now go
   through one `shapeOf()`, which resolves `rule@3` to the master's one shape,
   so a drag on any page lands on every page — the same rule as its boxes.
   **Both follow-ups CLOSED 2026-10-19.** Tables are shared-layout items:
   `masters.<name>.tables`, drawn on every page that uses the layout as
   `table.<id>@<page>`, one table edited from any page; Share this page's
   layout takes the page's placed tables, detaching copies them back, and a
   flow table stays in its text. And **per-item overrides**:
   `masterOverrides["<item>@<page>"]` holds only the keys changed on that
   page (null removes one), so the rest still follows the layout. In the
   editor, right-click a shared-layout item ▸ **Change on this page only**,
   and an overridden instance is a Proxy that reads the page's keys first
   and writes only to them — every existing edit path lands on that page
   without knowing it. **Back to the shared layout** undoes it; pilot
   `masterOverride(id, on)`. Tables also became ANCHORS, which needed a
   bug fixed on the way: moving a placed table wrote a stray `positions{}`
   entry nothing reads, so `place()` on a table did nothing at all. The
   placer now treats a placed table like a text box (its own x/y), and
   `anchorable` lets placed tables follow a paragraph. The primer's
   own designed pages are the renderer's, as before: a master dresses blank
   and template pages, which is where the copying was.

## Tier 3 — real, but not what is stopping anyone

8. **Named layers. — CLOSED 2026-10-19.** `layout.layers`, in stacking
   order (first at the back): `{name, hidden?, locked?}`, and any shape,
   box, table or placed piece may say `layer`. Each layer lifts its objects
   100 up the stack, so a whole layer sits above the ones before it; a
   hidden layer is not drawn at all (editor, page and PDF, as InDesign); a
   locked one is refused by everything that reads `isLocked` — drag,
   place, align, remove. Objects on no layer are exactly what they were. In
   the Layers panel, a Document layers section: show/hide, lock, put the
   selection on a layer, delete (its things stay, on no layer), + New layer.
   Pilot `defineLayer(name, {hidden, locked, rename})`, `toLayer(ids,
   name)`, `dropLayer(name)`.
9. **Baseline grid. — CLOSED 2026-10-19.** `layout.grid = {increment (pt),
   start (in)}`; a text style says `grid: true`. CSS has no snap-to-baseline,
   so `GRID_JS` (emitted only when a grid exists and some style sits on it)
   gives each such paragraph a line-height that is a whole number of
   increments, measures its first baseline with a zero-size probe, and pads
   it down to the next grid line — in reading order, after threads pour, so
   each frame's text snaps where it landed. Paragraph panel: Align to grid,
   the grid's increment and start, and Show the grid (an editor overlay).
   Pilot `setGrid({increment, start})`.
10. **Table headers that repeat across a break, and cell styles. —
    RECLASSIFIED 2026-09-17.** A placed table is pinned by inch to one page
    and never breaks, so a repeating `<thead>` has nothing to repeat across;
    the case the item described is a report renderer's own flowed table,
    which is that report's business. Cell styles exist as per-cell
    overrides (`cells`), and a table style (2) now carries the grid's look.
    Nothing left here that stops anyone.
11. **Print production — bleed, slug, spot colour, preflight, package.** The
    deliverable here is HTML plus a Chrome-printed PDF, so most of this is
    genuinely out of scope. **Bleed — CLOSED 2026-10-19, for every
    report.** The "Bleed & crop marks (PDF)" box sends `marks` to
    `/__export`, which sets `DOCSYNC_MARKS=1` for that one render. The
    engine cannot know a report's own page padding, so it emits `BLEED_JS`
    with an `@page` sized from the sheet (+0.25in): each sheet grows 0.125in
    a side, its padding by the same so the words keep their place against
    the trim, every placed thing and the drawing layer shift by 0.125in (a
    placement is measured from the sheet's corner, which padding does not
    move; anchored things are re-measured), and eight hairlines mark the
    trim inside the sheet. A renderer that does this itself (report2027's
    `.ds-bleed`) is left alone; a pageless report has no sheet to bleed. A
    normal build carries none of it.
12. **Blend modes. — CLOSED 2026-09-17**, on the way past (2): `blend` on a
    shape, box or table, in the transparency pop, which is what a blend is a
    generalisation of. `normal` is accepted so an object can undo the blend
    its style gave it.

## Not coming, and why

- **A measured, InDesign-faithful text engine.** The whole design is that the
  browser sets the type and the same markup serves the preview, the published
  page, the phone and the PDF. A second text engine would have to agree with
  CSS exactly or the preview would stop being the thing.
  (Threading, once filed here as needing one, turned out not to — see (4).
  The browser still sets every line; THREAD_JS only reads where they fell.)
- **Anything that only makes sense on paper** — printer's marks, colour
  separation, ink limits. These documents are read on a screen first.

## The order to do them in

Done: (1) text styles, (2) object styles, (3) find and change, (4)'s cheap half
(columns in a box), (5) text wrap, (6) anchored objects, (7) master pages,
(12) blend modes; (10) turned out not to be a gap. Every one kept the same
shape: opt-in keys, every existing render byte-identical (checked across all
twelve bindings in both modes on every commit), one resolver or one runtime
string shared by the page and the editor rather than two that could drift.

What is left is Tier 3: (9) a baseline grid, which waits on threading; (11)
bleed beyond report2027, only if another report goes to a printer. (8) is
back. Threading between frames is done (4). The next real work on this editor is not a gap
in this list — it is the follow-ups each closed item named: tables as master
items and as anchors, per-item master overrides, `shape-outside` if a wrapped
image ever needs a contour.

## The stylesheet from a pilot — 2026-09-18

The first of those follow-ups, and the one that mattered most: driving the
editor through `docsync.api` is the DEFAULT way this thing is meant to be
changed, and the pilot could not see a style, let alone set one. So "make
every pull-quote match" was a thing a person could do from the panel and an
agent could not do at all — it would build the look by hand, once per quote,
which is precisely what a stylesheet exists to stop.

- **`inventory().styles`** reports both stylesheets with `wornBy` on each,
  because the first question anyone asks of a style is how much would move if
  it changed. Each element and slot also says what it wears (`use`,
  `textStyle`), so "already dressed" can be told from "looks the same by
  hand" — the two want opposite next moves. `styles()` returns the same thing
  alone. It travels over the hub relay too.
- **`dress(id, name | null)`** puts an object style on a shape, box or table,
  or takes it off and keeps the look. Refuses a style written for another
  kind, naming both.
- **`defineStyle({scope, name, …})`** creates or redefines — one verb, because
  redefining is defining a name already taken and everything wearing it
  follows either way. Keys given REPLACE the style's own, so a style cannot
  accumulate a look nobody asked for over several calls.
- **`dropStyle(scope, name)`** unlinks every wearer first and leaves them the
  look. A `use` naming a style that no longer exists is refused at load, so
  the alternative is not one fewer style — it is a document that will not
  open. Refused while another style inherits from it.
- **`setStyle`** now refuses a `use` that names no text style, for the same
  reason: writing one produced a document that failed to build several steps
  away from the call that did it.

Two things worth keeping. **`_pilotStyleHosts()` is the one list of everything
that can wear a style, master items included.** They live in `layout.masters`,
not `layout.boxes`, so a count taken off the page stores under-reports and a
drop that skipped them breaks the load — one list, so counting, unlinking and
dropping cannot disagree. And **the verbs are batchable, with `defineStyle`
deferring its dependants' validation** the way a `@ref` already did: an op
cannot be validated against a style an earlier op in the same batch has not
created yet, and "define a style and put it on all twelve" is one undo. That
is the whole argument for a stylesheet, and a batch that refused it would have
been the feature not working.

The MCP tool description had also drifted — it named neither these nor the
master and anchor verbs that landed the night before, and an MCP client can
only call what that string lists.

## What is still missing — scoped 2026-10-19

A second pass, a month after the first, ranked by the same two questions. It
was scoped by reading `layout.py`, `edit.html` and the renderers, not by
running the editor, so each item names what the code has and lacks; check
the claim before building on it. "Size" is a rough guess at the work: S is
an evening, M a day or two, L longer.

Since the first pass the editor has also gained things this file never
listed, so they are not gaps: group/ungroup (⌘⇧G), lock, an eyedropper,
copy and paste style (the format painter), ruler guides, gradient fills on
shapes, PNG-per-page and HTML export, footnote and endnote chips, and
bleed and crop marks for report2027 (see (11)).

### Tier 1 — redone on every edit, or no way to say it

13. **Overset text — NOT A GAP (corrected 2026-10-19).** Scoped above as
    missing; it is not. A box's `h` is a min-height, so a box grows with its
    words rather than hiding them, and the one place words CAN be lost is
    the sheet's edge, which `fitCheck()` already measures on every render:
    red hatching where the PDF cuts, a `!` on the page chip, `⚠ N pages cut
    in print` in the bar, `fitReport()` on every pilot reply, and Save
    refusing while anything is cut. That is InDesign's red `+`, at the only
    edge that clips. What it does not do is tell you a box grew into the
    thing under it, which is an overlap and not an overset, and would cry
    wolf on every deliberate overlay.
14. **Paragraph spacing and indents. — CLOSED 2026-10-19.** Text-style
    keys in inches: `spaceBefore`, `spaceAfter`, `indent` (first line),
    `hang` (a hanging indent: the first line starts `hang` left of the
    rest), `indentLeft`, `indentRight`, each 0 to 3. They act on
    PARAGRAPHS — the slot when it is one (C.html's `<p>`), the paragraphs
    inside it when it is a wrapper (a text box, a card), and a heading's
    inline span made a block. Never the wrapper itself: a text box's style
    sits on the absolutely placed box, and a margin there would move it. So
    they ride as custom properties (`--ds-sb`, `--ds-sa`, `--ds-il`,
    `--ds-ir`, `--ds-ti`), and `SPACE_CSS` applies them, emitted once and only
    when a style asks. Its rules are `!important`, the strength an inline
    style has: the person asked for this paragraph's spacing, and a report's
    own `.page p.lead{margin}` would otherwise out-rank an attribute
    selector and quietly win. In the Paragraph panel under "Spacing and
    indents"; a pilot sets them with `setStyle` or `defineStyle`. InDesign
    drops space-before at the top of a frame; this does not, which is the
    one visible difference.
15. **Numbers in tables line up. — CLOSED 2026-10-19.** The figures half
    came with (22): `figures: "tabular"` on a table's style. The column
    half: `colAlign` on a placed table, one entry per column, each `left`,
    `center`, `right`, `decimal` or null, and a cell's own `align` still
    beats it. `decimal` is InDesign's decimal tab. CSS has no
    `text-align:"."` that any browser implements, so `decimal_cell()` splits
    a plain figure at the point: the whole part right-aligned against a
    fixed-width box (`min-width` in `ch`, sized to the column's widest tail)
    holding the point, its digits and any suffix. Every point in the column
    then sits at the same x whatever the font, and a figure with no point
    lines its units up with the rest. A cell of words, markdown or a dash is
    left whole and right-aligned, and so is the header. Decimal cells get
    tabular figures. In the editor it is on the cell's right-click menu
    (Align column left / Centre / right / on the decimal point; choosing the
    current one clears it). Insert, delete and move column keep `colAlign`
    in step, the way they keep `colw`. There is no pilot table verb, so a
    pilot writes `colAlign` through announce-then-edit, like the rest of a
    table's look.

16. **An image's alt text. — CLOSED 2026-10-19.** Insert image writes
    `![<file name>](path)` because the file name is all it knows, and a
    designed figure's `data-desc` was already editable (right-click ▸
    Description (for screen readers)…). What had no way in was an image
    that came through MARKDOWN: Insert image's box, or a picture in a
    paragraph. Its words live in that markdown, so that is what is
    rewritten, never the `<img>`, which is the render's echo of it.

    - **The menu.** Right-clicking such an image offers the same item, with
      the same name, as a designed figure. Over a file name the field opens
      EMPTY and says what is there now, so the file name is not offered as
      words to keep. Empty is allowed and means decoration (a screen reader
      skips it); brackets and newlines are stripped, because they would end
      the alt or the image. Every `![…](src)` of that picture in that place
      takes the words: the same picture twice in one box is one picture.
    - **The pilot.** `describeImage(host, words, src?)`, batchable: `host` is
      a text box id or a slot key; `src` picks one when the host carries
      several, and the refusal lists them. `audit()` reports each image
      still described by its file name as `alt-is-filename`, with the call
      that fixes it.
    - **The check.** `docsync.check`'s `alt-text` WARNS on an `<img>` whose
      alt is a file name: an image extension, or the src's own base name.
      An empty alt is never flagged. `alt_is_filename()` in `check.py` and
      `altIsFilename()` in `edit.html` are one rule twice; keep them equal.
      All 29 checked files were clean when it landed.

### Tier 2 — real gaps with a workaround — ALL CLOSED 2026-10-19

Built in the order (20)+(22), (21), (19)+(18), (17). Every one is opt-in,
and a layout that uses none of them builds byte-for-byte what it did: all 15
bound outputs were built with the engine before and after and hashed equal.
Each thing that needs more than an inline style rides out ONCE per document
and only when something asks for it (`_type_once`, `_vars_once`,
`_char_once` in `layer()`, beside `_anchor_once`).

17. **Character styles inside a paragraph. — CLOSED.** `[these words]{.Name}`
    puts the named text style on a run. content.py's `CHAR_STYLE_RE` makes it
    `<span class="ds-cs" data-cs="Name">`, which names the style and carries
    no CSS; `Layout._char_once` emits one `.ds-cs[data-cs=…]` rule per style
    a run wears, so a redefine moves every run. A run takes only the
    letterform keys (`CHAR_KEYS`: face, size, weight, italic, underline,
    colour, tracking, case, effect, figures, small caps, ligatures) —
    alignment, leading, keep, drop cap and list are the paragraph's.
    In the editor the words toolbar has **Aa▾**: a style on the selection,
    or "No style" off it, splitting a run like un-bolding part of bold
    (`applyCharStyle`). The rule is injected live (`liveCharRule`) so the
    words change before the edit commits. Runs do not nest; a run holding a
    `]` of the person's own keeps its words and drops the style rather than
    the reverse. `styles()` counts runs in `wornBy` (and `runs`), and
    `dropStyle` unwraps them, since a run cannot carry a look of its own.
    One grammar change on the way: link text may no longer contain `[`, in
    content.py and the editor alike, or a link inside a run started at the
    run's bracket. No bound report had such a link.
    A name with no style behind it renders as the paragraph's words, with no
    rule — not refused at load, because the run is in content.md and the
    stylesheet is in layout.json, and a draft must not fail to build over
    a stale name.
18. **Contents and page references. — CLOSED.** `{contents}` in a box is a
    list of every h1/h2 after the box's own page, with the page each is on,
    leader dots, and links (a heading with no id is given one).
    `{pageof:<slot key>}` is the page that slot landed on, "?" when nothing
    carries the key. Both are filled after layout by `layout.VARS_JS`, the
    same measure-then-write pattern as `ANCHOR_JS`, which the editor
    evaluates after each render (`fillVars`). A referenced slot gets the
    `data-anc-host` hook in both modes (`Content.page_ref_hosts`), because
    `data-slot` is edit-only. Text menu: **Insert ▸** for the tokens and
    **Page reference ▸**, which lists the document's headings by their words
    and writes the key, so a retitled heading keeps its references.
    What it does not do: pick which headings count (it is h1/h2, the shape
    every bound report uses), or number a contents list differently from the
    sheet order.
19. **Text variables. — CLOSED.** `{page}` `{pages}` `{section}` (the last
    h1/h2 on or before this page — a running header) `{title}` (the
    document's `<title>`) `{date}` (the build date, the one the engine knows
    itself; `DOCSYNC_DATE` pins it). They work in any slot's or box's words,
    not only on a master. While typing, each is an atomic chip, so an edit
    gives back the token and never the number it shows; `VAR_RE` lists the
    names, so a renderer's own `{fy}` is left alone. A master's `{page}` is
    still replaced in Python, as before, so those bytes did not move.
20. **Keep options, hyphenation, line breaking. — CLOSED.** Text-style keys:
    `wrap` (`balance` for headings, `pretty` for paragraphs), `hyphenate`,
    `keep` (`together` = break-inside:avoid, `next` = break-after:avoid, for
    the PDF). Every renderer already declares `lang="en"`, which hyphenation
    needs. `wrap` joins `align` and `dropCap` in `_BOX_KEYS`: on an inline
    slot they mean nothing, so the slot is given inline-block at full width,
    as align always did.
21. **Drop caps and list styling. — CLOSED.** `dropCap: 2–5` is
    `initial-letter` on the first letter of the slot or of its first
    paragraph. `list: {marker, color, indent}` sets the marker (disc, circle,
    square, dash, numbers, letters, roman, none), its colour and the list's
    indent. A pseudo-element and the UA sheet's own `list-style-type` on
    every `<ul>` are both out of an inline style's reach, so these ride as
    custom properties (`--ds-cap`, `--ds-lt`, `--ds-mk`, `--ds-li`) and
    `TYPE_CSS` applies them.
22. **OpenType features. — CLOSED**, with (15)'s figures half: `figures`
    (tabular, tabular-lining, lining, oldstyle, tabular-oldstyle,
    proportional), `smallCaps` (`small`, or `all` for acronyms), and
    `ligatures: false`.

    All of (20)–(22) are in one **Paragraph** panel (¶ on the type bar),
    which reads the RESOLVED style, so a slot wearing Body shows Body's
    settings, and writes through `setStyle` like every other type control;
    a pilot sets the same keys with `setStyle` or `defineStyle`.

(15)'s figures half came with (22); its column half and (14) closed the
same day — see Tier 1.

### Tier 3 — drawing and production, not what is stopping anyone

23. **Drawing tools. — Pen, Pathfinder and skew CLOSED 2026-10-19.**
    A `path` shape: `points` `{x, y, in?, out?}` as fractions of its own
    box, so it moves, resizes and snaps like any shape. The **Pen** in the
    Shapes panel: click to place points, drag to pull a curve, Enter or the
    first point closes it, Esc finishes it open. Double-click a path (or
    right-click ▸ Edit points) to drag its points; Alt-click removes one.
    **Pathfinder** (right-click with two or more simple shapes selected):
    a `compound` of `parts` with `op` union / subtract / intersect /
    exclude, drawn by SVG — one nonzero path, a mask cutting the front
    shapes out of the back one, a clip of the back by each front, one
    even-odd path. **Release compound** gives the parts back. **Skew**
    (Shear): `skew` in degrees (±60) on shapes, boxes, tables and placed
    pieces, folded into the same transform as rotation and flip; a ⧄ field
    beside the rotation in the Position pop. Pilot `addPath`, `pathfinder`,
    `release`, `skew`. Said here: a union or exclude with a stroke strokes
    each part's outline (the geometry is SVG's fill rule, not a computed
    outline); a part keeps its box but not a rotation, so rotated or skewed
    shapes must be straightened before they combine; points edit only on an
    unrotated path. Polygons, stars and corner options closed by (41); text
    on a path is (74).
24. **Step and repeat, feather and glow. — CLOSED 2026-10-19 by (38) and
    (39).**
25. **Image fitting presets. — CLOSED 2026-10-19 by (30).**
26. **No reusable object library.** Now (50). Images reuse through the project's
    assets, and templates carry the house style. A saved callout, figure
    frame or stat card that can be dropped into any report is not offered.
    `style_guide()`'s ready dicts are most of it. **M.**
27. **Preflight is split up. — CLOSED 2026-10-19 by (47).** `docsync.check` is already a good preflight
    (legibility floor, citations, bounds, dead text), but it runs from the
    shell and the editor shows parts of it in separate chips. One panel
    listing it all, with (13) and (16) added and a low-resolution image
    warning for print. **M.**

### Already on this list, still open

All closed 2026-10-19: contour wrap (5), tables on shared layouts and as
anchors and per-item overrides (7), named layers (8), the baseline grid (9),
bleed for every report (11), and pen paths, Pathfinder and skew (23). Each
kept every bound report's published bytes identical — checked by building
all 15 outputs with the engine before and after; the one first attempt that
did not (contour wrap inside the anchor runtime) was moved into its own.

### Not worth doing here

Data merge, IDML or InDesign-file import, books (several documents as one),
an index, CMYK, spot colour, and the rest of the paper-only list. None
fits documents that are read on a screen first and drafted one at a time.
An IDML import would matter only if a back catalogue of InDesign reports
needed moving in, and that would be a one-off converter, not an editor
feature.

### The order

Done: all of Tier 1 — (14), (15), (16) — and all of Tier 2, (17)–(22); (13)
turned out not to be a gap. Every one kept every existing render
byte-identical, checked by building all 15 bound outputs with the engine
before and after. What is left is Tier 3, drawing and production, and the
standing follow-ups listed under "Already on this list".

## Against InDesign itself — read 2026-10-19

A third pass, read off the application rather than from memory: InDesign
2026 (21.6)'s whole command surface through its own scripting —
`app.menuActions` (3,488 actions, each with the menu area it lives in), the
main menu tree, and `app.panels` (70 panels) — then checked against Cider's
code. Read-only; nothing in InDesign was changed. Ranked by the same two
questions as the rest of this file. Numbering continues from (27).

### Worth building — these land on the reports made here

28. **Paragraph rules, borders and shading. — CLOSED 2026-10-19.** A rule
    over a heading was a placed shape, which stayed where it was put while
    the words moved. Text-style keys now: `ruleAbove` / `ruleBelow`
    `{color, weight (pt, 0.25–12), offset (in, the gap to the text, 0–1),
    length (in; the column's width when absent)}`, `shade` (a fill behind
    the paragraph), `paraBorder` `{color, weight (pt), sides}` (all, top,
    bottom, left, right, or topbottom for a pull-out), and `paraPad` (inches
    of padding inside the shading and border). They work the way the
    spacing keys (14) do: custom properties on the slot, and `PARA_CSS`,
    emitted once and only when a style asks, applies them to the paragraphs
    at or inside it, never to a placed box. A rule is the paragraph's own
    `::before` / `::after`, so it needs no element and goes wherever the
    paragraph goes; shading, border and padding are `!important` for the
    same reason spacing is. In the Paragraph panel under "Rules, border and
    shading"; a weight of "none" removes a rule or border with its colour
    and gap. A named style carries them like any other key.
    Not done, and said so: InDesign merges the shading of consecutive
    paragraphs into one panel; here each paragraph is shaded on its own,
    with the gap between them showing. A rule's width cannot follow the
    width of the TEXT on its line (InDesign's "Text" width), only the
    column or a set length — CSS cannot measure a line from a pseudo-element.
    A rule is never drawn on a list item: rxkids draws its benefit bullets
    with `li::before`, and a rule there would replace them. A report whose
    own stylesheet put `::before`/`::after` on a PARAGRAPH would lose that
    decoration to a rule; checked, none bound today does.
29. **Nested and GREP styles. — CLOSED 2026-10-19.** Two text-style keys,
    both naming a character style (17), so a redefine still moves every
    run they make:
    - `grep: [{pattern, style, case?}]` — that style on every match of the
      pattern in the text wearing this style (`case: false` ignores case).
    - `nested: [{style, through}]` — styles from the start of each
      paragraph, in order, each running through a delimiter (":", "—",
      any short string), `"sentence"`, or `"words:N"`.

    Matching words is a fact about the text, and the slot, box and table
    renderers each make their text differently, so `text_css` carries the
    rules as one custom property (`--ds-gs`, base64 JSON) and
    `layout.GREP_JS`, emitted once and only when a rule exists, wraps each
    match after render in the span a hand-made run uses (`span.ds-cs`) plus
    `ds-auto`. That class is what keeps the markdown clean: the editor never
    writes an automatic run back (`nodeToMdInline`), and the text editor
    opens from the markdown, so while typing the words are plain and the
    formatting returns when the edit commits. Each paragraph is matched once
    (`data-ds-g`), within its own text, inside footnote chips, variables
    and decimal cells never; a match across a bold run is wrapped piece by
    piece so no markup is restructured. Threads call it on the gathered
    story before pouring (`THREAD_JS`), so a run crossing a frame break is
    carried with its words. The editor runs it after each render, before
    the threads (`applyAuto`).

    Refused at load: a rule naming no text style, a pattern that does not
    compile, one that matches the empty string (it would style empty
    space), a construct Python's `re` has and the page's `RegExp` does not
    (`(?P`, `(?#`, `\A`, `\Z`, inline flags), a `through` that is not a
    short delimiter, "sentence" or "words:N", unknown keys, and more than
    12 rules a style.

    In the Paragraph panel under "Automatic formatting": pick a style, pick
    what it goes on — numbers and amounts, percentages, words in quotes,
    "Source:"/"Note:", bill and Act numbers, the start of a paragraph through
    a colon or dash, the first sentence, the first three words, or a
    pattern of your own — and Add. Each rule is listed with ✕ to remove it;
    a rule that comes from a named style says to redefine it there. Without
    a script (a feed reader) the words are simply unformatted; the PDF runs
    it.

30. **Frame fitting for images. — CLOSED 2026-10-19.** InDesign's fitting
    commands, as the CSS that already does them: `fit` is `cover` (Fill
    Frame Proportionally), `contain` (Fit Content Proportionally), `fill`
    (Fit Content to Frame — stretches) or `none` (Center Content, actual
    size); `fitAlign` is the reference point (`center`, `top`, `bottom`,
    `left`, `right`, and the four corners). The browser applies
    `object-fit` on every layout, so a resized frame refits itself —
    InDesign's Auto-Fit is what CSS does anyway, and the re-crop by hand on
    every resize is gone.

    Two kinds of image take it. An **inserted image** is a text box whose
    markdown is one picture: `fit` on the box makes it exactly its `h` (the
    `ds-fitbox` class and `FIT_CSS`, emitted once and only when used, size
    the picture to it). A **designed image** keeps `fit` in `layout.img`
    beside its flip, tone and corners, through `img_css`: a placed image's
    frame is its placement, and one still in the flow gets a frame height
    `h` in `layout.img` so it can be fitted without being pinned.

    Fitting never moves a frame: the first fit fixes the frame at the size
    it has, then fits the picture into it. **Fit frame to content** goes
    the other way — the frame's height becomes what the picture's own shape
    makes it at the frame's width. Crop and fit are two ways to frame one
    image, so a document may use one (refused at load if both); choosing a
    crop takes fitting off. Also refused: fit on a box with words in it, a
    fitted box with no `h`, an unknown mode or anchor, an anchor with no fit.

    In the editor, right-click an image ▸ **Fitting ▸**: the four modes, Fit
    frame to content, and — once fitted — a 3 × 3 grid to anchor the
    picture and Clear fitting. Pilot: `fit(id, mode|null, align?)`
    (batchable) and `fitFrame(id)`. InDesign's Content-Aware Fit is an AI
    crop and is not here.

31. **Special characters and breaks. — CLOSED 2026-10-19.** White space
    and hyphens are the plain Unicode characters, in the file and on the
    page: nonbreaking space and hyphen, em, en, thin, hair and figure
    spaces, and the discretionary hyphen — so "$1.2 million" keeps together
    and nothing about rendering changed. They are invisible, which is the
    problem InDesign's Show Hidden Characters solves; here the text editor
    shows each as a small chip (`ds-spchip`) while typing, and gives back
    exactly the character it found. Breaks are tokens in the variables
    grammar (19), so they are the same atomic chips:
    `{break:line}` (a `<br>`, for a heading or an address),
    `{break:optional}` (`<wbr>`, where a line may break and nowhere else),
    `{break:column}` (its paragraph starts the next column of a box with
    columns — `break-before`, via `BREAK_CSS`, emitted once when used) and
    `{break:frame}` (its paragraph starts the next frame of a thread —
    `THREAD_JS` moves it on). Right-click in the words ▸ **Insert
    special ▸** offers all of them.

    Two differences from InDesign, both said here. A column or frame break
    moves its whole paragraph, not the words after the character: splitting
    a paragraph would change the words in the file. And there is no page
    break — a page here is a fixed sheet, and a break inside one would split
    the sheet in the PDF; `{break:page}` is left as typed.

    On the way: an insert from the text menu (a chip, an endnote) whose
    caret sat BETWEEN paragraphs — the select-all a box opens with,
    collapsed — landed beside the paragraphs, where it serialized as a
    paragraph of its own. `insertNodeAt` now steps into the block beside it.

32. **Tab stops with leaders. — CLOSED 2026-10-19.** A text-style key,
    `tabs: [{at, align, leader}]`: `at` in inches from the text's left edge,
    `align` left, right, center or decimal, `leader` none or one of
    `. - _ ·`. A tab typed in the words is a real tab character in the
    markdown. `text_css` carries the stops as one custom property
    (`--ds-tabs`, a word a stop), and `TABS_JS`, emitted once and only when
    a style sets stops, turns each tab into an inline-block whose width runs
    to the next stop — less the words after it for right, half of them for
    center, up to their decimal point for decimal — and fills it with as
    many leader characters as fit. Past the last stop the default stops
    every half inch take over. The editor runs it after each render, before
    the threads pour. Paragraph panel ▸ Tab stops (one stop a line,
    "4.5 right ."); Tab types a tab in text that has stops, and still
    indents a bullet. Not done: InDesign's ruler over the frame for dragging
    stops; a right-aligned segment that itself wraps measures as its whole
    width.
33. **Table structure. — CLOSED 2026-10-19.** Keys on a placed table, all
    optional, so a table without them keeps its markup:
    - `merges: [[row, col, rows, cols]]` — a merged cell's top-left and its
      span (rowspan/colspan), refused if smaller than two cells, off the
      table, or overlapping another. Merging joins the words into the
      top-left cell. Insert, delete and move row/column keep merges, row
      heights and header/footer counts in step (a line inside a merge grows
      or shrinks it; moving a line splits a merge across it).
    - `headerRows` / `footerRows` — how many; `header: true` is still one.
      Either key wraps the rows in `<thead>` / `<tbody>` / `<tfoot>` for a
      reader and the tagged PDF. Footer rows are bold, `footerFill` their
      fill.
    - `inset` — cell padding in inches, one or `[top, right, bottom, left]`
      (a cell may carry its own); `valign` top / middle / bottom (likewise).
    - `rowh` — one minimum height a row, inches or null.
    - `bandStroke: {w, color, style}` — a rule under every other body row,
      the rows `band` tints.
    Text to table: a text box's lines become rows, split on tabs (or `|`,
    or commas), and the table takes the box's place. Right-click a cell ▸
    Merge cells / Unmerge cells / Table options…; right-click a text box ▸
    Convert text to table. Pilot: `setTable`, `mergeCells`,
    `unmergeCells`, `textToTable`. Not done: splitting a cell into two
    (merge, then add a row or column), diagonal lines, a table that breaks
    across frames repeating its header.
34. **Accessible PDF out. — CLOSED 2026-10-19.** Bookmarks: the PDF export
    asks Chrome for a document outline built from the headings
    (`generateDocumentOutline`, `--generate-pdf-document-outline` on the
    one-shot path). Tagged PDF: Chrome already tags from the page's
    accessibility tree, and the export now asks for it explicitly
    (`generateTaggedPDF`, `--export-tagged-pdf`) so it stays on. Reading
    order (the Articles panel): `layout.readingOrder: {page: [ids]}` — those
    objects are read first, in that order, the rest of the page after. The
    listed elements are stamped `data-ro`; `ORDER_JS`, emitted once and only
    when an order is set, makes them the page's `aria-owns`, which changes
    the accessibility tree and so the tag order — paint order and layout
    are untouched. Checked in a printed PDF: an image listed first is the
    first structure element. Right-click a page ▸ Reading order… (one id a
    line), pilot `setReadingOrder(page, ids|null)`. Shapes are not in it:
    they are decoration in one SVG a layer, and a reader skips them.
35. **Track changes. — ALREADY THERE.** The hub's Suggesting mode is this:
    an edit made while suggesting becomes a suggestion thread with the old
    and new words, and Accept / Reject (and Accept all) apply or drop it.
    What it lacks against InDesign is local mode — suggestions live in the
    hub's store, so a report edited on disk only has history.
36. **Margins, columns and guide grids. — CLOSED 2026-10-19.**
    `layout.margins: {top, bottom, left, right, columns, gutter}` (inches;
    1–12 columns), or a master's own `margins` for the pages using it. Like
    ruler guides they are the editor's alone — validated at load
    (`_check_margins`), never read by the renderer, so they change no
    published byte. Drawn with the guides, the margin box magenta and the
    column gutters violet, and snapped to while guides show. Create Guides
    adds rows and columns of ruler guides with a gutter, fitted to the
    margins or the page. Right-click a page ▸ Margins and columns… /
    Create guides…; pilot `setMargins(m|null, master?)`,
    `createGuides({rows, columns, gutter, fit})`. Not done: margins do not
    push text; a text box is still placed by hand.
37. **Find/Change past plain text. — CLOSED 2026-10-19.** The Find panel
    has a GREP box: the needle is a regular expression and the change may
    say `$1`, `$&`, `$$`. Plain mode now treats a `$` in the change as a
    dollar sign (it was being read as a pattern). Find Format: "In text
    styled" limits the search to slots, boxes and tables wearing a named
    style. Change Format: "Change to style" wraps each change in that
    character style (an empty change keeps the words). Find Font: the
    panel lists every family the report sets or shows, with how many
    places, and swaps one for another everywhere — the stylesheet, slots,
    boxes, tables, and designed text showing that family without setting
    it (the slot gets its own font). A bad pattern says so in the panel.
    Pilot: `findChange({find, change, grep, cased, whole, inStyle,
    applyStyle})`, `fontsInUse()`, `replaceFont(from, to)`.

### Smaller wins

38. **Placement. — CLOSED 2026-10-19.** Paste in Place (⌥⇧⌘V, and the
    right-click menu): the copy's own x/y, on the page in view. Step and
    Repeat (⌥⌘U, right-click): count and offsets, one undo step; pilot
    `stepRepeat(ids, count, dx, dy)`. Transform Again (⌥⌘3): the last move
    — drag, nudge, X/Y field, `place()` — or rotation, done again to what is
    selected now. Select next object above / below: ⌥] / ⌥[ (⌘] / ⌘[ move
    in the stack; these walk it), and in the right-click menu.
39. **Effects. — CLOSED 2026-10-19.** `glow` (outer glow), `innerShadow`,
    `innerGlow` and `feather` on boxes, tables and placed elements; a
    shape takes an outer glow (as an SVG drop-shadow filter). Merged with
    the drop shadow into one box-shadow; feather is a mask of two edge
    gradients. In the effects pop.
40. **Named swatches with tints. — CLOSED 2026-10-19.**
    `layout.swatches: {name: hex}`; any colour key may say `"@Name"` or
    `"@Name 40%"`, resolved at load, so redefining a swatch recolours every
    use. Dropping a swatch writes its colour into each use first. In every
    colour picker; pilot `defineSwatch(name, hex|null)`.
41. **Corner options and polygons. — CLOSED 2026-10-19.** Shape kinds
    `polygon` (`sides` 3–12) and `star` (`points` 3–24, `inner` radius),
    and `corner: bevel | inset` with `r` on a rectangle. They drag, resize,
    and go into Pathfinder. Right-click ▸ Shape options….
42. **QR codes. — CLOSED 2026-10-19.** Shape kind `qr` with `data` (up to
    213 bytes): `docsync/qr.py`, a pure-Python encoder (byte mode, level M,
    versions 1–10) so it runs in the browser through Pyodide too; drawn as
    crisp SVG named "QR code: <link>" for a reader. Checked module for
    module against an independent encoder, and decoded. Shapes panel ▸ QR
    code; right-click ▸ QR code link….
43. **Live captions. — CLOSED 2026-10-19.** `{caption:<id>}` in any words
    is the picture's description: an inserted image's alt text, or a
    designed image's `<id>.desc` slot — so editing the description edits
    the caption. Right-click an image ▸ Add a caption.
44. **Links panel. — CLOSED 2026-10-19.** A toolbar button: every picture
    the report places, by file — where it is used, pixel size, effective
    resolution at its printed size (under 150 ppi flagged), and missing
    (did not load). Go to; Relink… swaps the file everywhere it is used:
    a replaced image's source, box and slot markdown, and a designed
    picture (pinned into `layout.img`, as Replace image does). Pilot
    `links()`, `relink(old, new)`.
45. **Conditional text. — CLOSED 2026-10-19.** `[words]{?name}` marks a
    run; `print` and `web` are built in (shown only in the PDF, only on
    screen), and `layout.conditions: {name: false}` hides any other. In the
    editor a conditional run has a dashed underline. Right-click in the
    words ▸ Show only in ▸; pilot `setCondition(name, on)`.
46. **Character controls. — CLOSED 2026-10-19.** Text-style keys `strike`,
    `shift` (baseline shift, points) and `features` (OpenType tags: frac,
    ordn, sups, subs, salt, swsh, dlig, zero, case, onum, lnum, titl,
    ss01–ss20). In the Paragraph panel, and allowed in a character style.

### Already listed, or not worth it

Already above: pen and path tools, Pathfinder (23), named layers (8),
baseline grid (9), contour wrap. Not worth doing here, for the reasons under
"Not worth doing here": Book and Index, Data Merge, separations, trapping
and colour profiles, InCopy, EPUB, interactive forms and buttons, and the
generative-AI commands. Spell check and Flex Layout: see "After the gap
list" below.

## After the gap list — scoped 2026-10-19

With 1–46 closed, a fourth pass: InDesign's menus and panels read again
(the same 3,488 actions), each candidate checked against Cider's code
rather than memory. Of forty candidates, six turned out to be here already,
nineteen partly, fifteen not at all. Ranked by the same two questions, with
the same sizes as Tier 3 (**S** a day or less, **M** a few, **L** a week or
more). Numbering continues from (46).

Already here, and now said: spell check while typing is the browser's own
and is on in every text editor (`spellcheck="false"` only on inputs like
the hex field and the GREP box); links in the exported PDF are clickable,
endnote markers included; object styles cover shapes, boxes and tables;
Present mode, the source .zip and the PNG-per-page .zip downloads, and the
Google Doc import exist, and were never listed here.

### Worth building — these land on the reports made here

47. **One Preflight panel. — CLOSED 2026-10-19** (folds in 27). A toolbar
    button with a count — red when any error, amber for warnings only —
    worked out a beat after each render; the panel lists every problem by
    kind, and a click goes to it (selects it, or scrolls to its page). It
    gathers `audit()` (print overflow, off the sheet, overlap, covering the
    text, a rule through letters, an orphaned group member, an uncited
    source, a file-name description, overset, text that cannot move), the
    Links panel's missing and low-resolution pictures, and three checks of
    its own: a picture with no `alt` at all; text under WCAG AA contrast
    (4.5:1, 3:1 for large text) against what is actually painted behind it
    — read off the paint stack, so a placed shape under white words counts,
    and worked out from the element's own ancestors and the placed shapes
    below it where a page is scrolled out of view; a picture or gradient
    behind the words is skipped, not guessed — and a web font that failed
    to load. Errors are what print or publish would get wrong (overflow,
    overset, uncited, missing picture, failed font); the rest are warnings.
    Pilot `preflight()` — `{issues, errors, counts}`, each issue with a
    severity. About 15 ms on a 30-page report. Not in it yet:
    `docsync.check`'s legibility floor, which needs the engine's own
    measure.
48. **Footnotes at the foot of the page.** Cider makes endnotes only
    (`Footnotes` in content.py). A brief or testimony often wants the note
    on the page it supports. Same markers and sources, a per-report switch
    (`notes: "foot" | "end"`), notes set in a reserved band at the bottom
    of the page they are cited on, overflow carried to the next page; and
    the switch is the conversion both ways. The hard part is measuring the
    band after layout — a runtime like THREAD_JS. **L.**
49. **Text frame options. — CLOSED 2026-10-19.** Two keys on a text box
    (and in a box object style): `vAlign` top / middle / bottom / justify
    places the words in a box taller than they are — `align-content` on
    the box, so the paragraphs lay out exactly as before; only justify
    (spread the paragraphs over the height) uses a flex column.
    `firstBaseline` leading / ascent / cap / x sets where the first line
    sits against the top: the browser's own half-leading, or trimmed to the
    ascender, cap height or x-height with `text-box`. A box's `h` is still
    a minimum, so these need a box with room to spare. In the box's style
    pop. Not done: a fixed first-baseline offset in inches.
50. **A library of reusable pieces** (was 26). Select a callout, stat card
    or figure frame ▸ Save to library; drop it into any report from the
    library panel, with its text styles and swatches brought along. Lives
    beside the templates (a library project the editor reads), so the hub
    and local mode share it. **M.**
51. **Quick Apply.** One box (⌘↩) that finds any text style, character
    style, swatch, command or pilot verb by typing a few letters and applies
    it to the selection — InDesign's fastest path, and the editor's menus
    are deep. **M.**
52. **Hyperlinks panel. — CLOSED 2026-10-19.** Every link in the report —
    in slots, boxes and table cells, and the sources list — by address, with
    where each is used and its words. **Check links** sends the addresses to
    the local server (`/__linkcheck`: HEAD, GET where a site refuses HEAD,
    a browser User-Agent, eight at a time, eight seconds each), because a
    page cannot fetch another site; broken ones sort to the top, a site that
    refuses a script (401/403/429) is "unverified" rather than broken, and a
    redirect says where it moves to. A link to `#…` is checked against the
    page's own ids. **Change…** rewrites an address everywhere it is
    written, sources included. **Link bare addresses** turns an address
    typed into words into a link to itself, leaving ones already linked
    alone. Pilot `hyperlinks()`, `checkLinks()`, `changeLink(old, new)`,
    `linkBareUrls()`. On the hub there is no check yet — it needs the
    hub's worker to do the fetching.
53. **What tables still lacked. — CLOSED 2026-10-19.**
    - **Split cell**, horizontally and vertically: a merged cell gives its
      grid back; a plain one gains a row under it (or a column beside it,
      half its width) while every other cell in its row (column) spans
      both, so only this cell is seen to split — InDesign's behaviour.
    - **Header columns**: `headerCols`, the first N cells of every row are
      `th`, with the header fill and colour.
    - **Alternating columns**: `bandCols` (a fill on every other body
      column) and `bandColStroke {w, color, style}` (a rule down them), the
      column twins of `band` and `bandStroke`.
    - **Distribute rows evenly**: the selected rows (or all) get the average
      of their measured heights as `rowh`, a minimum, so a row whose words
      need more keeps it.
    - **Table to text**: a text box of tab-separated lines in the table's
      place — Text to table's inverse.
    - **Named cell styles**: `layout.cellStyles {name: {fill, color, align,
      valign, bold, italic, inset}}`, worn by `use` in a cell's override;
      the cell's own keys still win, and a redefine moves every cell
      wearing it. Dropping one writes its look into those cells first, so
      nothing on the page changes.
    - **A picture in a cell**: it already worked — a cell is markdown, and
      `![…](…)` renders as an inline image; now tested.
    Right-click a cell ▸ Split cell horizontally / vertically, Distribute
    rows evenly, Cell style ▸ (a style, No cell style, Save this cell's
    look as…), Convert table to text; Table options… gains header columns
    and the alternating column fill and stroke. Pilot `splitCell`,
    `tableToText`, `defineCellStyle`, `cellStyle`, and `setTable` takes
    the new keys.
54. **Page numbering sections. — CLOSED 2026-10-19.** `pages.sections:
    [{from, start, style, prefix}]`: from the `from`th sheet in reading
    order, pages count from `start` in decimal, lower- or upper-roman, or
    lower- or upper-alpha, with `prefix` before the number. Sheets before
    the first section keep their plain place. `{page}`, `{pageof}`, the
    contents list, a shared layout's `{page}` and the page strip all follow;
    `{pages}` stays the number of sheets. The variables runtime is compiled
    with the sections only when there are some (`vars_js`), so a report
    without them keeps VARS_JS byte for byte; `page_label` is the engine's
    twin of the runtime's. Reordering pages keeps the sections. Right-click
    a page ▸ Numbering and section…; pilot `setPageSection(sheet,
    {start, style, prefix} | null)`. A section is pinned to a sheet number,
    not to a page, so moving the page it starts on does not move it.
55. **Numbered lists past one paragraph block.** Restart or continue
    numbering, a list continuing across boxes that are not threaded, and
    multi-level numbering (1, 1.1, a.) — recommendations lists in policy
    reports are numbered and often split by a chart. `list.marker` already
    has decimal, alpha and roman. **M.**
56. **Widows, orphans and word spacing. — CLOSED 2026-10-19.** Text-style
    keys `widows` and `orphans` (1–9 lines; CSS honours them across columns,
    threaded frames and PDF pages) and `wordSpacing` (em added between
    words, −0.5 to 2), all inherited, so set on a slot or box they reach
    every paragraph in it. In the Paragraph panel. Not done, and said so:
    InDesign's minimum / desired / maximum spacing for justified text —
    CSS has one word-spacing value, not a range.
57. **Smarter arranging.** Align to a **key object** (the last one
    clicked stays still), distribute with an **exact gap**, and smart
    guides that show **equal spacing** while dragging. `alignSel` and
    `distributeSel` do the rest already. **S–M.**
58. **Paste Into — a picture inside a shape.** An image framed by an
    ellipse, polygon, star or path: the shape becomes the mask (SVG
    `clipPath`, which Pathfinder already writes). Headshots in circles are
    drawn by CSS radius today, and nothing else is possible. **M.**
59. **Linked text. — CLOSED 2026-10-19.** `{text:<slot key>}` or
    `{text:text.N}` in any slot, box or table cell repeats that slot's or
    box's first paragraph, as one line, written in at every render — so the
    key figure in the summary and the body cannot drift apart. A link to
    nothing stays as typed, so it shows; a link inside the repeated words
    is not followed (no chains, so no loops). The published page carries
    the words alone; in the editor they are marked (`span.ds-linked`,
    a teal underline) and, while typing, the token is a chip showing the
    start of the words it repeats. Right-click in the words ▸ Insert
    variable ▸ Linked text…. Edit the words at their source; there is no
    out-of-date state to manage, because there is no copy.
60. **Text variables of your own. — CLOSED 2026-10-19.**
    `layout.variables: {name: text}` (a name of letters, digits, - and _;
    one line of text, up to 200 characters, no variable inside another) and
    `{var:name}` in any slot, box or table cell: written into the words at
    render, as a caption is, so the page carries plain text. One nobody
    defined is left as typed, so it shows. In the text editor it is a chip
    showing its text. Right-click in the words ▸ Insert variable ▸ (and
    Define variables…, one per line, "name = text"); pilot
    `setVariable(name, text | null)`. Not done: `{file}` and `{modified}` —
    the engine runs in the browser too, where a file's date means nothing.
61. **Editorial notes. — CLOSED 2026-10-19.** A note is an HTML comment in
    a slot's block of content.md — what writers already leave there, which
    the renderer strips and the editor already kept through edits; they were
    only invisible. A **Notes** button (with a count) lists every note by
    slot, plus the document's own above the first slot; Go to, Edit…,
    Delete. Each slot with notes carries a small mark in the editor (its
    notes as the tooltip; a click opens the list) — never in print, on the
    web or while presenting. Right-click any text ▸ Add a note…. A note's
    slot words are untouched by adding, editing or deleting it. Pilot
    `notes()`, `setNotes(key, [texts])`. Not done: a note anchored to a
    word inside the slot (the comment sits after the words), and notes on
    text boxes, whose words live in layout.json.
62. **Track changes in local mode.** Suggesting (35) needs the hub's store.
    Locally: a change recorded as `{++inserted++}` / `{--deleted--}` marks
    in content.md (CriticMarkup), drawn as insert and strike, Accept and
    Reject per change and for all, and refused at build so a marked draft
    cannot publish by accident. **L.**
63. **Story editor.** Every slot and box of a report as one plain column of
    markdown in reading order (34), edited fast without the page — for the
    rewrite pass where layout is a distraction. Writes back through
    `writeSlot` and box `md`. **M.**

### Smaller wins

64. Named **gradient swatches**, and a gradient feather (a fade to clear,
    not just the edge feather of 39). Gradients exist; naming them does not.
65. **Eyedropper for looks.** The colour eyedropper picks colour only, and
    the format painter misses some keys (a shape's blend and glow, a box's
    padding, radius and border) and never works on a run of text.
66. **Change case, placeholder text, paste without formatting. — CLOSED
    2026-10-19.** Right-click in the words ▸ Change case ▸ UPPERCASE,
    lowercase, Title Case (small words kept small) or Sentence case rewrites
    the selected words themselves; formatting inside the selection goes, as
    typing over it would. Right-click a text box ▸ Fill with placeholder
    text, about enough to fill its frame. ⇧⌘V while typing pastes the
    clipboard's plain text, no bold, italic or links.
67. A **Glyphs panel**: every character in a font, searchable, with the
    recent ones kept — for arrows, fractions, ʻokina and kahakō letters.
68. **Rotate and flip. — CLOSED 2026-10-19.** Right-click ▸ Rotate and
    flip ▸ 90° either way, 180°, flip horizontal or vertical, on the
    selection; one undo step; a quarter turn is what Transform Again
    repeats. `flip` (h, v, hv) is now a key on shapes, boxes and tables as
    well as placed pieces and images, folded into the one transform each
    already has (`css_turn`, `svg_turn`). Pilot `flip(id, f)`.
69. An **undo history** list to jump back through, beside the version
    history.
70. **Show hidden characters and text threads. — CLOSED 2026-10-19.** The
    ¶ button (⌥⌘I) puts a ¶ at the end of every paragraph (¤ in a table
    cell), outlines tabs, labels column and frame breaks, and draws a dashed
    line from each threaded frame to the next — "continues on page N" where
    the next frame is on another page. Editor-only marks, redrawn after each
    render while the view is on; remembered between sessions.
71. **Export** JPEG as well as PNG, export only the selection, and a
    package .zip that includes the fonts and the rendered pages.
72. A **measure readout**: the distance to the next object while dragging
    or hovering with ⌥, as Figma shows it.
73. **Spell check for Hawaiian.** The browser checks spelling but flags
    every Hawaiian word; the browser takes no custom dictionary from a page,
    so this would be a project word list checked by the engine and drawn
    as a quiet underline. Worth it only if the false flags get in the way.
74. **Text on a path** (from 23): SVG `textPath` along a path shape. Rare
    in reports.

### Not worth doing here, and why

Different page sizes in one document and facing-page spreads (one sheet
size is what the PDF and the phone layout both assume; a fold-out is a
separate document); even/odd page breaks (sheets are fixed, and blank pages
are already placed by hand in the page strip); Flex Layout (still a design
question — the renderers' own grids space cards today); clipping paths and
background removal (an image tool's job, done before the picture comes
in); maths typesetting (no report here has needed an equation — revisit if
one does, with KaTeX); custom keyboard shortcuts and saved workspaces;
Liquid Layout and alternate layouts (the mobile reflow is the alternate
layout).

### The order

(49), (54), (56) and (60) closed first, 2026-10-19 — each kept every
bound report's published bytes identical — and then (47) and (52), which
are the editor's alone and touch no published byte; then (59) and (53);
then (61), (66), (68) and (70). Next: (80), (77) and (75) from the pass
below, then (57), (64) and (82). (48) and (62) are the large ones, worth
it when a report asks for footnotes or a local review round.

## A fifth pass: InDesign's panel menus — scoped 2026-10-19

The 766 panel-menu commands (paragraph, character, object, table and cell
styles, swatches, pages, layers, links, hyperlinks, align, transform,
library), each checked against the code. Most of InDesign's panel work is
here by now; what is not, ranked the same way. Numbering continues from
(74).

### Worth building

75. **Load styles from another report.** Every report re-makes the house
    look by hand. Load text styles, character styles, object styles, cell
    styles, swatches and variables from another project (pick which), with
    a clash shown before anything is overwritten. The staff hub makes this
    a list of the reports you can open. **S–M.**
76. **Style housekeeping.** Select and delete unused styles (text, object,
    cell, swatches), duplicate a style, and an **override highlighter**:
    text whose own keys differ from the style it wears gets a dashed
    outline, with Clear overrides — the way to find why one heading looks
    off. **S.**
77. **Type from the keyboard.** ⇧⌘> / ⇧⌘< size, ⌥← / ⌥→ tracking, ⌥↑ / ⌥↓
    leading, ⌥⇧↑ / ⌥⇧↓ baseline shift, on the selected text — InDesign's
    muscle memory, and much faster than the panel for fitting copy. **S.**
78. **Span columns.** A heading over both columns of a two-column box:
    `{span}` at the start of a paragraph sets `column-span: all` on it.
    Today a multi-column box can only start its columns at the top. **S.**
79. **Default styles for new things.** A report's default text style for
    new text boxes, and its default object style for new boxes, shapes and
    tables — so what is added already looks like the report. **S.**
80. **Colours in use.** List every colour the report uses (fills, text,
    strokes, borders, charts); make swatches from the unnamed ones in one
    go (Add Unnamed Colors); select everything wearing a colour (Find this
    Color); and replace one colour with another everywhere (Merge
    Swatches). The brand-cleanup pass. **S–M.**
81. **Clear transformations.** One command that puts rotation, skew, flip
    and scale back to none. **S.**
82. **Internal links and cross-reference text.** Link words to another
    place in the report (a slot, a heading), clickable in the PDF and on
    the web; and `{titleof:<slot>}`, the heading's own words, so "see
    *Who pays* on page {pageof:…}" follows a renamed heading too. **S–M.**
83. **Export some pages.** A PDF of a page range or the current page, for
    sending one section; the export takes one page as PNG today, not a
    range as PDF. **S.**
84. **Tabs and slides for the web.** InDesign's Object States: one frame
    holding several states, switched by buttons — a tabbed explainer on the
    web, the states one after another in print. RxKids' tabbed page was
    built by hand in its renderer; this would make it a thing any report can
    have. **M–L.**
85. **Layer housekeeping.** Merge layers, delete unused layers, select
    everything on a layer. **S.**

### Not worth doing here

Style groups and style packs (a report has a dozen styles, not hundreds);
export tags (the HTML is already semantic); Book, Index, Data Merge (a
renderer loop makes a set of district fact sheets better than a merge
would); InCopy assignments; page transitions; ink manager and trapping;
alternate and liquid layouts (the phone reflow is the alternate layout).
