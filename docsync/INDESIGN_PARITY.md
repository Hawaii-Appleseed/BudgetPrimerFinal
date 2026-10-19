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
   Deliberately not: tables are not master items, and there is no per-page
   override of one item short of detaching the whole master. The primer's
   own designed pages are the renderer's, as before: a master dresses blank
   and template pages, which is where the copying was.

## Tier 3 — real, but not what is stopping anyone

8. **Named layers. — The panel is back, 2026-09-18.** Per-page restacking of
   the page's objects, master items included. Still not InDesign's named
   layers with lock and hide across the document; nothing here needs those.
9. **Baseline grid.** Nothing aligns type across columns or facing pages.
   It waited on (4), and (4) is now whole — threaded frames side by side are
   exactly where lines that do not line up show.
10. **Table headers that repeat across a break, and cell styles. —
    RECLASSIFIED 2026-09-17.** A placed table is pinned by inch to one page
    and never breaks, so a repeating `<thead>` has nothing to repeat across;
    the case the item described is a report renderer's own flowed table,
    which is that report's business. Cell styles exist as per-cell
    overrides (`cells`), and a table style (2) now carries the grid's look.
    Nothing left here that stops anyone.
11. **Print production — bleed, slug, spot colour, preflight, package.** The
    deliverable here is HTML plus a Chrome-printed PDF, so most of this is
    genuinely out of scope. **Bleed is half here (corrected 2026-10-19):**
    the Download menu's "Bleed & crop marks (PDF)" box sends `marks` to
    `/__export`, which sets `DOCSYNC_MARKS=1` for that one render, and
    report2027's renderer puts `.ds-bleed` on `<body>` so `primer.css` grows
    the sheet 0.125in a side and draws the trim ticks. That is report2027
    only, at a hard-coded 8.5 × 11: no other renderer reads `DOCSYNC_MARKS`,
    so for every other project the box is offered and does nothing. The
    missing half is moving the bleed CSS into the engine (sized from the
    page, not 8.5 × 11), or hiding the box where the renderer cannot honour
    it.
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

23. **Drawing tools stop at preset shapes.** Shape kinds are rect,
    ellipse, line, triangle, arrow, icon and chart. There is no pen or
    freeform path, no polygon or star with N sides, no pathfinder
    (combine or subtract), no corner options beyond radius, no skew, no
    text on a path. The layer would need a `path` kind with a `d` string
    and a node editor. Most report art is placed SVG, so this is the
    least-felt item here. **L.**
24. **No step and repeat or grid duplicate**, and no feather or outer
    glow. Duplicate exists, one at a time. **S** each.
25. **No image fitting presets.** Crop exists. Fit content to frame, fill
    proportionally and centre are not offered as commands. **S.**
26. **No reusable object library.** Images reuse through the project's
    assets, and templates carry the house style. A saved callout, figure
    frame or stat card that can be dropped into any report is not offered.
    `style_guide()`'s ready dicts are most of it. **M.**
27. **Preflight is split up.** `docsync.check` is already a good preflight
    (legibility floor, citations, bounds, dead text), but it runs from the
    shell and the editor shows parts of it in separate chips. One panel
    listing it all, with (13) and (16) added and a low-resolution image
    warning for print. **M.**

### Already on this list, still open

The baseline grid (9), now unblocked by threading (4), named
layers with lock and hide across the document (8), bleed outside report2027
(11), tables as master items and anchors, per-item master overrides, and
`shape-outside` contour wrap.

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
