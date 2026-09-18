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

2. **Object styles.** The same argument for boxes, shapes and tables. A
   pull-quote box is a fill, a padding, a border and a text style; the fourth
   one is built by hand like the first. Cheaper than (1) once (1) exists,
   because it is the same machinery pointed at a different map.

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
   frame, one column, and text that overruns it simply overruns. A two-column
   page is two boxes and a manual split of the prose — re-split by hand every
   time a sentence is added. This is the most *InDesign* thing on the list and
   the most work: it needs a measured text layout, which the engine
   deliberately does not have (it hands text to the browser and lets CSS set
   it). A cheap first cut that is not a lie: **columns inside one box**, which
   CSS does natively (`column-count`, `column-gap`), gets the common
   two-column page without any flow machinery at all. Threading between
   frames should be scoped separately, and honestly, as a large piece.

5. **Text wrap around an object.** An image or pull-quote dropped into a
   column does not push prose aside; it sits on top of it or below it. CSS
   `shape-outside` / a float gets the rectangular and simple-shape cases,
   which is most of them; InDesign's contour-from-alpha-channel wrap is not
   the part anyone here needs.

6. **Anchored objects.** A figure that belongs to a paragraph does not move
   when the paragraph does. Every placed object is pinned to a page by inch.
   Once text reflows for any reason — a style redefined, a sentence added —
   every figure on the page has to be dragged back. This gets sharply worse
   the moment (1) ships, because redefining a style is exactly what makes text
   reflow.

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
10. **Table headers that repeat across a break, and cell styles.** The engine
    emits no `<thead>` at all, so a table split by a page break loses its
    header on the second half.
11. **Print production — bleed, slug, spot colour, preflight, package.** The
    deliverable here is HTML plus a Chrome-printed PDF, so most of this is
    genuinely out of scope. Bleed is the exception and only for a report that
    is actually going to a printer.
12. **Blend modes.** Opacity yes, `mix-blend-mode` no. One CSS property and a
    picker; almost never asked for in this kind of document.

## Not coming, and why

- **A measured, InDesign-faithful text engine.** The whole design is that the
  browser sets the type and the same markup serves the preview, the published
  page, the phone and the PDF. A second text engine would have to agree with
  CSS exactly or the preview would stop being the thing.
- **Anything that only makes sense on paper** — printer's marks, colour
  separation, ink limits. These documents are read on a screen first.

## The order to do them in

(1) and (3) are done. (3) went first of the two remaining because writing
this file made the estimate for (2) look wrong: "the same machinery pointed at
a different map" is true of text styles and charts, which are one key set
each. An object style is THREE — a shape is fill/stroke/radius/shadow/opacity,
a box is fill plus a text style plus padding, a table is borders, fills,
column widths and per-cell overrides — so it is three maps and three pickers,
not one, and it should be scoped again before it is started.

So: (2) next, re-scoped. Then (6) before (4) — anchored objects are cheap and
they are what keeps (1) from making a mess, since redefining a style reflows
the text that every pinned figure was positioned against.
