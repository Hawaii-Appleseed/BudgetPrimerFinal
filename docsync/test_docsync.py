#!/usr/bin/env python3
"""Regression tests for the doc <-> repo round-trip.

Google Docs reshapes a content file when it imports and re-exports it. Every
normalise() case below is a real mangling observed from the live doc — if
Google changes its exporter, these fail here rather than silently corrupting a
committed file. The rest cover fragment mode and the conflict rules that decide
whether a push is safe.

    python3 docsync/test_docsync.py
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docsync.content import ContentError                  # noqa: E402
from docsync.fetch import (FetchError, access_token,      # noqa: E402
                           service_account_email)
from docsync.content import paragraph                     # noqa: E402
from docsync.fragment import extract, inject, to_html     # noqa: E402
from docsync.normalise import leading_comment, normalise  # noqa: E402
from docsync.state import State, content_hash            # noqa: E402
from docsync import text as dtext                       # noqa: E402
from docsync.check import (check_citations,             # noqa: E402
                           check_markdown, check_svg_bounds)

FAILS = []


def check(name, got, want):
    if want not in got:
        FAILS.append(f"{name}\n  want (substring): {want!r}\n  got: {got!r}")


def check_eq(name, got, want):
    if got != want:
        FAILS.append(f"{name}\n  want: {want!r}\n  got:  {got!r}")


def check_raises(name, fn, expect):
    try:
        fn()
    except ContentError as e:
        if expect not in str(e):
            FAILS.append(f"{name}\n  want error containing: {expect!r}\n  got: {e}")
        return
    FAILS.append(f"{name}\n  expected ContentError containing {expect!r}, none raised")


# ---------------------------------------------------------------- normalise()

# Docs merges a marker into the paragraph that follows it.
check("marker merged with paragraph",
      normalise("[[basics.p1]] The state budget funds three branches."),
      "[[basics.p1]]\nThe state budget funds three branches.")

# Markers before a heading/list get padded with a blank line instead.
check("marker padded before heading",
      normalise("[[basics.h1]]\n\n# BUDGET BASICS"),
      "[[basics.h1]]\n# BUDGET BASICS")

# Underscores are \-escaped; '_' is a word char, so the unescape class must
# name it explicitly (this shipped broken once — {cip\_total} reached .format()).
check("escaped underscore in a format placeholder",
      normalise("[[cip.body]]\nCIP in FY{fy} is {cip\\_total} total."),
      "{cip_total}")

# Bare URLs come back as [url](url) — with the label escaped and the href not.
check("autolinked url with escaped label",
      normalise("[[sources]]\n[a]: Report. — "
                "[https://x.gov/a\\_b\\_c.pdf](https://x.gov/a_b_c.pdf)"),
      "[a]: Report. — https://x.gov/a_b_c.pdf")

# A genuinely labelled link must survive — only [url](url) collapses.
check("labelled link preserved",
      normalise("[[a.b]]\nSee [the report](https://x.gov/r.pdf) for detail."),
      "[the report](https://x.gov/r.pdf)")

# The whole sources block returns as one line; it must split at each "[id]: ".
check("sources block collapsed to one line",
      normalise("[[sources]]\n[a]: A. — https://a.gov [b]: B. — https://b.gov"),
      "[a]: A. — https://a.gov\n[b]: B. — https://b.gov")

# A "[id]:" outside the sources block must NOT be split onto its own line.
check("prose link refs are left alone",
      normalise("[[spent.p1]]\nSee the report [a]: not a source line."),
      "See the report [a]: not a source line.")

# The doc title above the first marker is chrome, not content.
check("doc title stripped",
      normalise("# Budget Primer — Content\n\n[[toc.author]]\nAuthor: X"),
      "[[toc.author]]\nAuthor: X")

# Curly quotes/apostrophes are the report's own typography — never rewritten.
check("smart punctuation preserved",
      normalise("[[a.b]]\nEach biennium’s “Fixed Costs” table."),
      "Each biennium’s “Fixed Costs” table.")

# Docs drops HTML comments, so a file's header is re-attached by the caller.
check("header re-attached",
      normalise("[[a.b]]\nText.", header="<!-- keep me -->\n\n"),
      "<!-- keep me -->\n\n[[a.b]]")

check_eq("leading_comment extracts the instructions block",
         leading_comment("<!-- hi -->\n\n[[a.b]]\nText."), "<!-- hi -->\n\n")
check_eq("leading_comment on a file without one",
         leading_comment("[[a.b]]\nX."), "")

# ----------------------------------------------------------------- fragment

check("fragment: heading level preserved",
      to_html("## Why this matters"), "<h2>Why this matters</h2>")
check("fragment: paragraph with inline markdown",
      to_html("A **bold** claim with a [link](https://x.gov)."),
      '<p>A <b>bold</b> claim with a <a href="https://x.gov">link</a>.</p>')
check("fragment: bullet list",
      to_html("- one\n- two"), "<ul><li>one</li><li>two</li></ul>")
check("fragment: numbered list",
      to_html("1. one\n2. two"), "<ol><li>one</li><li>two</li></ol>")
# Soft-wrapped lines are one paragraph; a blank line starts a new one.
check_eq("fragment: blank line splits paragraphs",
         to_html("one\ntwo\n\nthree"), "<p>one two</p>\n<p>three</p>")

PAGE = "<main>\n<!-- docsync:start -->\nold\n<!-- docsync:end -->\n</main>"
out = inject(PAGE, "<p>new</p>")
check("fragment: injected between anchors", out, "<p>new</p>")
check_eq("fragment: content outside anchors untouched",
         out.startswith("<main>\n<!-- docsync:start -->")
         and out.endswith("<!-- docsync:end -->\n</main>"), True)
check_eq("fragment: old block replaced", "old" in out, False)
check("fragment: re-injecting is stable",
      inject(out, "<p>new</p>"), "<p>new</p>")
check_eq("fragment: extract round-trips", "<p>new</p>" in extract(out), True)

check_raises("fragment: missing anchors is a hard error",
             lambda: inject("<main>no anchors</main>", "<p>x</p>"),
             "has no '<!-- docsync:start -->'")
check_raises("fragment: reversed anchors rejected",
             lambda: inject("<!-- docsync:end --><!-- docsync:start -->", "<p>x</p>"),
             "appears before")

# ------------------------------------------------------------------- state

check_eq("hash is stable", content_hash("abc"), content_hash("abc"))
check_eq("hash separates different content",
         content_hash("abc") == content_hash("abd"), False)
check_eq("fresh state is uninitialised", State().initialised, False)
check_eq("state with both fields is initialised",
         State(content_hash="a", doc_modified="t").initialised, True)
# A half-written state must not read as initialised — that would let a push
# skip the conflict check and overwrite doc edits.
check_eq("state with only a hash is not initialised",
         State(content_hash="a").initialised, False)


# The state hash is the last thing BOTH sides agreed on, and every "did this
# side move?" is measured from it. A pull that writes the doc's content without
# recording a new sync point leaves the hash pointing at an older common
# ancestor — so the next repo edit reads as "both sides moved" and the engine
# invents a conflict nobody caused. Live-tested: pull, then edit the repo, must
# be repo-ahead. This models that arithmetic.
def _status(state_h, doc_h, local_h):
    doc_moved, repo_moved = doc_h != state_h, local_h != state_h
    if doc_h == local_h:
        return "in-sync"
    if doc_moved and repo_moved:
        return "conflict"
    return "doc-ahead" if doc_moved else "repo-ahead"


check_eq("after a recorded pull, a repo edit is repo-ahead (not a conflict)",
         _status(state_h="H1", doc_h="H1", local_h="H2"), "repo-ahead")
check_eq("an UNrecorded pull turns the next repo edit into a false conflict",
         _status(state_h="H0", doc_h="H1", local_h="H2"), "conflict")
check_eq("a doc edit against a recorded state is doc-ahead",
         _status(state_h="H1", doc_h="H2", local_h="H1"), "doc-ahead")
check_eq("genuinely divergent sides are still a conflict",
         _status(state_h="H1", doc_h="H2", local_h="H3"), "conflict")

# ------------------------------------------------------------------- setup

# Setup fails in a handful of ways and every one of them used to arrive as a
# stack trace from several libraries down. A bad key must always produce a
# sentence someone can act on.
check_eq("a key that is not JSON yields no identity, rather than raising",
         service_account_email("oops-i-pasted-the-wrong-thing"), "")
check_eq("a key without client_email yields no identity",
         service_account_email('{"private_key": "x"}'), "")
check_eq("a real-shaped key yields its identity",
         service_account_email('{"client_email": "docsync@p.iam.gserviceaccount.com"}'),
         "docsync@p.iam.gserviceaccount.com")


def _fetch_error(fn):
    try:
        fn()
    except FetchError as e:
        return str(e)
    except Exception as e:                                    # noqa: BLE001
        return f"WRONG TYPE {type(e).__name__}: {e}"
    return "no error raised"


try:
    from google.auth.transport.requests import Request        # noqa: F401
    from google.oauth2 import service_account                  # noqa: F401
    HAVE_AUTH = True
except ImportError:
    HAVE_AUTH = False

if HAVE_AUTH:
    check("a non-JSON key is reported as such, not as a parser crash",
          _fetch_error(lambda: access_token("not json at all")),
          "not valid JSON")
    check("a malformed private key is a FetchError, not a raw ValueError",
          _fetch_error(lambda: access_token(
              '{"client_email": "d@p.iam.gserviceaccount.com", "type": "service_account",'
              ' "token_uri": "https://oauth2.googleapis.com/token",'
              ' "private_key": "-----BEGIN PRIVATE KEY-----\\nbogus\\n'
              '-----END PRIVATE KEY-----\\n"}')),
          "ValueError")
else:
    # pull-only use needs no credentials, so google-auth is genuinely optional.
    # Say the checks were skipped rather than failing or pretending they ran.
    print("note: google-auth/requests absent — skipped 2 key-handling checks "
          "(pip install google-auth requests to run them)")
    check("a missing dependency names itself",
          _fetch_error(lambda: access_token("{}")),
          "pip install google-auth requests")

# ------------------------------------------------------------------ layout

# Layout overrides exist so a box can be dragged without layout becoming a
# blank canvas: the renderer's design is the default, data only overrides it.
# With nothing overridden the published HTML must be untouched.
import json, tempfile                                          # noqa: E402
from docsync.layout import (Layout, LayoutError, shadow_css,   # noqa: E402
                            fill_css, fill_repr, fill_svg_paint)


def _layout(d):
    t = Path(tempfile.mktemp(suffix=".json"))
    t.write_text(json.dumps(d))
    try:
        return Layout(t)
    finally:
        t.unlink(missing_ok=True)


def _layout_error(d):
    try:
        _layout(d)
    except LayoutError as e:
        return str(e)
    return "no error raised"


def _layout_error_at(fn):
    """For failures that fire at render, not load — page_order needs the page
    count, which only the renderer knows."""
    try:
        fn()
    except LayoutError as e:
        return str(e)
    return "no error raised"


empty = _layout({"positions": {}, "shapes": []})
check_eq("an empty layout adds no attributes", empty.attr("x.y"), "")
check_eq("an empty layout adds no shape layer", empty.layer(3), "")
check_eq("an unmoved element keeps the renderer's own placement",
         empty.style("lc.dec", "left:1in;top:2in"), "left:1in;top:2in")

moved = _layout({"positions": {"c.o": {"x": 1.2, "y": 3.4, "w": 5.0}}, "shapes": []})
check("a moved element is absolutely placed", moved.attr("c.o"),
      'style="margin:0;position:absolute;left:1.2in;top:3.4in;width:5.0in;z-index:1"')
check_eq("an override beats the renderer's placement",
         moved.style("c.o", "left:9in;top:9in"),
         "margin:0;position:absolute;left:1.2in;top:3.4in;width:5.0in;z-index:1")

# Hidden elements: Delete on a designed element records it here rather than
# editing output that is regenerated every build. Published: display:none,
# appended LAST so it beats a display the caller's own css set (graphic()
# passes display:inline-block), and the moved-away spacer gives its flow slot
# back. Edit mode: a selectable ghost instead — still laid out, marked
# data-hidden so the editor can offer restore.
hid = _layout({"positions": {"c.o": {"x": 1, "y": 2, "reserve": 0.5}},
               "hidden": ["c.o", "plain.el"], "shapes": []})
check("a hidden element publishes display:none",
      hid.attr("plain.el"), 'style="display:none"')
check("hide beats the caller's own display, by coming last",
      hid.attr("plain.el", "display:inline-block;width:2in"),
      'style="display:inline-block;width:2in;display:none"')
check_eq("a hidden moved element gives its flow slot back", hid.spacer("c.o"), "")
check("style() hides for the tag()/style() call sites",
      hid.style("plain.el", "left:1in"), "left:1in;display:none")
check_eq("tag() publishes nothing extra for a hidden element",
         hid.tag("plain.el"), "")
# Deleting is deleting: the element is gone on the canvas exactly as it is on
# the published page. It was drawn as a translucent dashed ghost at first, so the
# deletion would look reversible — but a half-faded element reads as a delete
# that did not work, and keeping its box meant the page never closed the gap the
# way publishing would. Undo and File > Restore deleted carry reversibility.
os.environ["DOCSYNC_EDIT"] = "1"
try:
    ed = hid.attr("plain.el")
    check("a deleted element is gone on the canvas too", ed, "display:none")
    check_eq("no half-faded ghost is left behind", "opacity" in ed, False)
    check_eq("the canvas closes the gap, like publishing", hid.spacer("c.o"), "")
    check("the id is still marked, so the editor can list it for restore",
          ed, 'data-hidden="1"')
    check("tag() marks it too, for the call sites that own their style",
          hid.tag("plain.el"), 'data-hidden="1"')
finally:
    del os.environ["DOCSYNC_EDIT"]
check("hidden must be a list of element ids",
      _layout_error({"positions": {}, "shapes": [], "hidden": "c.o"}),
      "hidden: expected a list of element ids")

# .page is overflow:hidden, so content dragged off it does not look broken —
# it is simply gone. Nothing else would catch that, so it must be loud.
off = _layout({"positions": {"c.o": {"x": 7.9, "y": 2, "w": 5.0}}, "shapes": []})
check("a box dragged past the right edge is caught",
      " ".join(off.check_bounds()), "past the right edge")
off2 = _layout({"positions": {"c.o": {"x": 12, "y": 2}}, "shapes": []})
check("a box dragged clean off the page is caught",
      " ".join(off2.check_bounds()), "off the 8.5x11.0in page")
check_eq("content inside the page passes",
         _layout({"positions": {"c.o": {"x": 1, "y": 1, "w": 3}}, "shapes": []}).check_bounds(), [])

check("a shape needs a known kind",
      _layout_error({"shapes": [{"id": "a", "page": 1, "kind": "blob",
                                 "x": 0, "y": 0, "w": 1, "h": 1}]}),
      "must be one of rect, ellipse, line")
check("a shape needs an id",
      _layout_error({"shapes": [{"page": 1, "kind": "rect", "x": 0, "y": 0, "w": 1, "h": 1}]}),
      "needs an 'id'")
check("duplicate shape ids are caught",
      _layout_error({"shapes": [{"id": "a", "page": 1, "kind": "rect", "x": 0, "y": 0, "w": 1, "h": 1},
                                {"id": "a", "page": 1, "kind": "rect", "x": 0, "y": 0, "w": 1, "h": 1}]}),
      "duplicate id 'a'")
check("a non-numeric coordinate is caught",
      _layout_error({"positions": {"c.o": {"x": "left", "y": 1}}}), "not a number")

shaped = _layout({"shapes": [
    {"id": "b", "page": 7, "kind": "rect", "x": 1, "y": 2, "w": 3, "h": 1, "fill": "#6B9E78"},
    {"id": "f", "page": 7, "kind": "ellipse", "x": 1, "y": 2, "w": 1, "h": 1, "z": "front"}]})
check_eq("shapes split into back and front layers", shaped.layer(7).count("shape-layer"), 2)
check("back shapes sit behind the text", shaped.layer(7), "z-index:-1")
check("front shapes sit above it", shaped.layer(7), "z-index:2")
check_eq("a page with no shapes gets no layer", shaped.layer(2), "")
check("shapes never eat clicks", shaped.layer(7), "pointer-events:none")

# Positioning something absolutely takes it out of the flow and its neighbours
# rush into the gap — move the logo, and the title beneath it jumps. A moved
# element must keep holding the height it occupied.
held = _layout({"positions": {"cover.logo": {"x": 1, "y": 2, "reserve": 1.09}}})
check("a moved flow element reserves the height it vacated",
      held.spacer("cover.logo"), 'height:1.09in;flex:0 0 auto')
# A moved element in a FLEX row (a branch photo beside its card) must reserve
# its WIDTH too, or the sibling stretches across the gap.
held_w = _layout({"positions": {"branch.photo.x": {"x": 1, "y": 2, "w": 2.15,
                                                   "reserve": 1.6}}})
check("a moved flex element reserves its width and height",
      held_w.spacer("branch.photo.x"), 'width:2.15in;height:1.6in;flex:0 0 auto')
# 'reserve' (space held in the flow) and 'h' (how tall to draw it) are
# different questions; one file used to answer both with 'h'.
sized = _layout({"positions": {"photo": {"x": 1, "y": 2, "w": 3, "h": 2}}})
check("a resized element is drawn at that size", sized.attr("photo"), "height:2in")
check_eq("a size does not imply reserved flow space", sized.spacer("photo"), "")
check("a box resized past the bottom is caught",
      " ".join(_layout({"positions": {"p": {"x": 1, "y": 10, "h": 3}}}).check_bounds()),
      "past the bottom edge")
# An element that was already absolute never held flow space, so reserving any
# would push its neighbours DOWN — the same bug, mirrored.
absolute = _layout({"positions": {"lc.dec": {"x": 3, "y": 4}}})
check_eq("an already-absolute element reserves nothing",
         absolute.spacer("lc.dec"), "")
check_eq("an unmoved element reserves nothing", held.spacer("callout.whopays"), "")

# z is an integer layer, and the old back/front words still parse.
layered = _layout({"shapes": [
    {"id": "old", "page": 2, "kind": "rect", "x": 0, "y": 0, "w": 1, "h": 1, "z": "back"},
    {"id": "new", "page": 2, "kind": "rect", "x": 0, "y": 0, "w": 1, "h": 1, "z": 4}]})
check("the legacy 'back' word still means behind", layered.layer(2), "z-index:-1")
check("an integer layer is honoured", layered.layer(2), "z-index:4")
check("a nonsense layer is caught",
      _layout_error({"shapes": [{"id": "x", "page": 1, "kind": "rect",
                                 "x": 0, "y": 0, "w": 1, "h": 1, "z": "middle"}]}),
      "not a layer number")

# -------------------------------------------------------------------- text
# Styling must be invisible until it is used. An unstyled report has to build to
# the same bytes it always did — head included — or the whole premise ("the
# design is the default, JSON only speaks where someone changed something")
# quietly stops being true.
from docsync.layout import text_css, FONTS                      # noqa: E402
from docsync.content import Content                             # noqa: E402

SHIPPED_LINK = ('<link href="https://fonts.googleapis.com/css2?family=Barlow:wght@800;900'
                '&family=Source+Sans+3:ital,wght@0,300;0,400;0,600;0,700;1,400'
                '&display=swap" rel="stylesheet">')

nostyle = _layout({})
check_eq("an empty layout styles nothing", nostyle.text_attr("basics.h1"), "")
check_eq("an unstyled report asks for exactly the fonts it always did",
         nostyle.font_link(), SHIPPED_LINK)
check_eq("text_css of nothing is nothing", text_css({}), "")

styled = _layout({"text": {"a.b": {"font": "Playfair Display", "weight": 700}}})
check("a picked font joins the link", styled.font_link(), "family=Playfair+Display:wght@700")
check("the brand's own fonts survive a pick", styled.font_link(), "family=Barlow:wght@800;900")
check("a weight the report asks for is actually requested",
      _layout({"text": {"a": {"font": "Barlow", "weight": 400}}}).font_link(),
      "Barlow:wght@400;800;900")
check("italic moves the family onto the ital axis",
      _layout({"text": {"a": {"font": "Barlow", "weight": 400, "italic": True}}}).font_link(),
      "Barlow:ital,wght@0,800;0,900;1,400")

# A style attribute is quoted with ", so a family name quoted the same way ends
# the attribute early and the rest becomes stray markup. Every family with a
# space in it would have done this.
check("a font with a space cannot break out of the style attribute",
      text_css({"font": "Playfair Display"}), "font-family:'Playfair Display'")
check_eq("no double quote ever reaches the style attribute",
         '"' in text_css({"font": "Playfair Display", "color": "#fff"}), False)

# text-align does nothing to an inline box, and inline slots are spans.
check("centring an inline slot gives it a box to centre in",
      text_css({"align": "center"}), "display:inline-block;width:100%")
check_eq("a slot that was not aligned grows no width it never had",
         "width:100%" in text_css({"size": 20}), False)

check("an unknown family is caught at load",
      _layout_error({"text": {"a": {"font": "Comic Papyrus"}}}),
      "not a font this report can load")
check("a weight the family lacks is caught, because the browser would fake it",
      _layout_error({"text": {"a": {"font": "Barlow", "weight": 333}}}),
      "has no weight 333")
check("a colour that is not a colour is caught",
      _layout_error({"text": {"a": {"color": "red"}}}), "not a hex colour")
check("an alignment that is not one is caught",
      _layout_error({"text": {"a": {"align": "middle"}}}), "must be one of")
check_eq("the font list is a list, not free text", len(FONTS) > 10, True)


def _content(styles=None, body="[[a.b]]\nText.\n\n[[sources]]\n[x]: A. — https://a.gov\n"):
    t = Path(tempfile.mktemp(suffix=".md"))
    t.write_text(body)
    try:
        return Content(t, styles=styles)
    finally:
        t.unlink(missing_ok=True)


plain = _content(nostyle)
check_eq("an unstyled paragraph is the paragraph it always was",
         plain.html("a.b"), "<p>Text.</p>")
# t() emits a bare string in the published build. Styling it means a span — but
# ONLY for a slot someone actually styled, or every heading in the report grows
# a wrapper it never had.
check_eq("an unstyled t() is still a bare string, not a span", plain.t("a.b"), "Text.")

one = _content(_layout({"text": {"a.b": {"size": 20}}}))
check("a styled paragraph carries its style", one.html("a.b"), '<p style="font-size:20px">')
check("a styled t() becomes a span, and only then", one.t("a.b"), '<span style="font-size:20px">')
check_eq("text() is never wrapped — it lands in alt= and SVG, where a span is invalid",
         one.text("a.b"), "Text.")

# A prose block is a movable unit: moved, the whole slot travels in one
# wrapper. Unmoved and published, the bytes are the bare paragraphs above —
# already proven by "an unstyled paragraph is the paragraph it always was".
moved_para = _content(_layout({"positions": {"para.a.b": {"x": 1, "y": 2, "w": 4,
                                                          "reserve": 0.5}}}))
check("a moved prose block travels in one positioned wrapper",
      moved_para.html("a.b"),
      '<div data-placed style="margin:0;position:absolute;left:1in;top:2in;width:4in;z-index:1">'
      '<p>Text.</p></div>')
check("its vacated flow space stays held", moved_para.html("a.b"),
      '<div class="ds-spacer" style="width:4in;height:0.5in;flex:0 0 auto"')
os.environ["DOCSYNC_EDIT"] = "1"
try:
    check("in edit mode the wrapper is the editor's drag handle",
          _content(nostyle).html("a.b"), '<div data-el="para.a.b"><p')
finally:
    del os.environ["DOCSYNC_EDIT"]

# data-slot <=> styleable: the editor never offers a control that does nothing,
# and a style aimed at a slot the renderer builds into a string fails loudly.
c = _content(nostyle)
c.html("a.b")
check_eq("a slot that rendered an element is styleable", "a.b" in c.styleable(), True)
check_eq("a style aimed at a slot that never rendered one is reported",
         nostyle.unknown_text_keys({"a.b"}), [])
check_eq("a style aimed at an unstyleable slot is reported",
         _layout({"text": {"cip.body": {"size": 12}}}).unknown_text_keys({"a.b"}),
         ["cip.body"])

# ------------------------------------------------------- legibility floors
from docsync.layout import (MIN_TEXT_PX, MIN_TEXT_PT, MIN_SUBLABEL_IN,   # noqa: E402
                            chart_svg, text_css, _check_text)

# The floor is REFUSED at load, not clamped: a size a person authored is worth
# an error naming the unit it went wrong in. Clamping it silently would ship a
# document that disagrees with the layout.json beside it.
try:
    _check_text({"size": MIN_TEXT_PX - 0.5}, "text 'x'")
    check_eq("a size under the floor is refused", "no error", "LayoutError")
except LayoutError as e:
    check_eq("a size under the floor names the print size",
             f"{MIN_TEXT_PT:g}pt" in str(e) and "px" in str(e), True)
check_eq("a size exactly on the floor is allowed",
         _check_text({"size": MIN_TEXT_PX}, "text 'x'"), None)
# text_css clamps rather than raising: it is also the live-preview path, and a
# slider mid-drag must not throw.
check_eq("text_css clamps a size that reached CSS unvalidated",
         text_css({"size": 4}), f"font-size:{MIN_TEXT_PX:g}px")

# Charts size their labels in INCHES, so they shrink with the box. The floor is
# what stops a short chart from writing four-point type — the bug that shipped.
_short = chart_svg({"type": "bar", "labels": ["a"], "values": True, "legend": True,
                    "title": "T", "series": [{"name": "s", "data": [1]}]},
                   0.5, 0.5, 4.0, 0.8)
_sizes = [float(v) for v in re.findall(r'font-size="([\d.]+)"', _short)]
check_eq("no chart label is drawn below the sub-label floor",
         [v for v in _sizes if v < MIN_SUBLABEL_IN - 1e-9], [])
check_eq("a chart squeezed flat still labels at 9pt",
         round(min(_sizes) * 72, 1), 9.0)


# ----------------------------------------------------------------- effects
from docsync.layout import EFFECTS, EFFECT_PARAMS                # noqa: E402

# The direction convention is 0 = 12 o'clock, clockwise — the same one
# arc_path() uses in the renderer. Pin it: a second convention for the same idea
# in one repo is a bug waiting to be written.
check("direction 0 casts the shadow straight up",
      text_css({"effect": {"kind": "shadow", "offset": 0.1, "direction": 0, "blur": 0}}),
      "0.0em -0.1em")
check("direction 90 casts it to the right",
      text_css({"effect": {"kind": "shadow", "offset": 0.1, "direction": 90, "blur": 0}}),
      "0.1em -0.0em")
# em, not px: a shadow measured in px detaches from its glyphs the moment the
# type is resized.
check("offsets scale with the type", text_css({"effect": {"kind": "shadow"}}), "em")

check("hollow empties the glyph",
      text_css({"effect": {"kind": "hollow"}}), "color:transparent")
check("hollow strokes the glyph",
      text_css({"effect": {"kind": "hollow"}}), "-webkit-text-stroke:")
check("neon glows in its own colour",
      text_css({"effect": {"kind": "neon", "color": "#6B9E78"}}), "0 0 ")
check("echo repeats at increasing distance and decreasing weight",
      text_css({"effect": {"kind": "echo", "offset": 0.05, "direction": 90}}), "0.1em")
check_eq("every effect produces CSS", all(text_css({"effect": {"kind": k}}) for k in EFFECTS), True)
check_eq("every effect declares its own knobs", sorted(EFFECT_PARAMS) == sorted(EFFECTS), True)

# hollow/splice hollow the glyph out, so they must land after any colour the
# same style set — otherwise the colour wins and the effect silently does
# nothing.
check("an effect that empties the glyph beats a colour set beside it",
      text_css({"color": "#ff0000", "effect": {"kind": "hollow"}}),
      "color:#ff0000;color:transparent")

check("an unknown effect is caught at load",
      _layout_error({"text": {"a": {"effect": {"kind": "sparkle"}}}}), "must be one of")
# An empty effect object is falsy, so a truthiness check would skip every
# validation below it and let a malformed file through as a no-op.
check("an effect with no kind is caught",
      _layout_error({"text": {"a": {"effect": {}}}}), "needs a 'kind'")
check("alpha outside 0..1 is caught",
      _layout_error({"text": {"a": {"effect": {"kind": "shadow", "alpha": 40}}}}),
      "not a fraction")
check("an effect colour that is not a colour is caught",
      _layout_error({"text": {"a": {"effect": {"kind": "neon", "color": "hotpink"}}}}),
      "not a hex colour")
check_eq("no effect means no effect CSS", "text-shadow" in text_css({"size": 12}), False)

# The caption used to be sliced at "[^" by the renderer to bold its label.
# Markdown already says "this is bold", so the prose says it and the surgery is
# gone — but only if paragraph() produces exactly what the split did.
check_eq("a bold label in the prose renders as the surgery used to",
         paragraph("**General-fund obligated costs, FY2018–FY2027 ($Billions).**[^exec-biennium]"),
         "<b>General-fund obligated costs, FY2018–FY2027 ($Billions).</b>[^exec-biennium]")

# ------------------------------------------------------------- text boxes
boxed = _layout({"boxes": [{"id": "t1", "page": 3, "x": 1.2, "y": 4, "w": 3, "z": 2,
                            "md": "**Note:** a pull quote",
                            "style": {"size": 13}}]})
check("a box is absolutely placed on its page", boxed.text_boxes(3), "left:1.2in;top:4in")
check_eq("a box only appears on its own page", boxed.text_boxes(4), "")
check("a box renders its markdown", boxed.text_boxes(3), "<b>Note:</b>")
check("a box takes the same styles as a slot", boxed.text_boxes(3), "font-size:13px")
# Same reason a text slot has no height: pin one and it either clips its words
# or leaves a hole the moment they change. Its bottom is the fit check's job.
# (Numeric heights only — the engine's box-image rule says height:auto, which
# is the opposite of pinning.)
check_eq("a box pins no height",
         bool(re.search(r"(?<!min-)height:[\d.]", boxed.text_boxes(3))), False)
check_eq("no boxes means no markup at all", _layout({}).text_boxes(3), "")

check("a box needs an id",
      _layout_error({"boxes": [{"page": 1, "x": 1, "y": 1, "w": 2, "md": "x"}]}),
      "needs an 'id'")
check("an empty box is caught — it would render as nothing",
      _layout_error({"boxes": [{"id": "a", "page": 1, "x": 1, "y": 1, "w": 2, "md": " "}]}),
      "has no text")
# The editor resolves an id to a thing by searching shapes then boxes, so a
# collision makes the right-click menu act on whichever it finds first.
check("a box id may not collide with a shape id",
      _layout_error({"shapes": [{"id": "d", "page": 1, "kind": "rect", "x": 0, "y": 0, "w": 1, "h": 1}],
                     "boxes": [{"id": "d", "page": 1, "x": 1, "y": 1, "w": 2, "md": "x"}]}),
      "duplicate id 'd' — already a shape")
check("a box dragged off the side is caught",
      " ".join(_layout({"boxes": [{"id": "w", "page": 1, "x": 7, "y": 1, "w": 4,
                                   "md": "x"}]}).check_bounds()),
      "past the right edge")
check("a box's style is validated like any other",
      _layout_error({"boxes": [{"id": "b", "page": 1, "x": 1, "y": 1, "w": 2, "md": "x",
                                "style": {"font": "Comic Papyrus"}}]}),
      "not a font this report can load")

# ------------------------------------------------------------------ fills
# attr() merges extra declarations into the position style: an element with two
# style attributes silently keeps only the first, so a recoloured-and-moved
# callout must come out as ONE attribute carrying both.
both = _layout({"positions": {"c.o": {"x": 1, "y": 2}}})
check("attr merges a computed background into the move",
      both.attr("c.o", "background:#2F3E46"),
      'style="margin:0;position:absolute;left:1in;top:2in;z-index:1;background:#2F3E46"')
check_eq("extra alone still emits a style", empty.attr("c.o", "background:#2F3E46"),
         ' style="background:#2F3E46"')
check_eq("no move, no extra, no attribute", empty.attr("c.o"), "")

# The generic fill hooks: outside edit mode an unfilled surface must emit
# NOTHING (the published bytes cannot move), and the editor's data-fill hook
# exists only while editing — like data-el.
check_eq("fill_attr is silent when unfilled", empty.fill_attr("page.3"), "")
check_eq("fill_tag is silent outside edit mode", empty.fill_tag("card.a"), "")
pg = _layout({"fill": {"page.3": "#E8EDE6"}})
check_eq("a filled page carries its background",
         pg.fill_attr("page.3"), ' style="background:#E8EDE6"')
os.environ["DOCSYNC_EDIT"] = "1"
try:
    check_eq("edit mode stamps the right-click hook",
             empty.fill_tag("card.a"), ' data-fill="card.a"')
    check_eq("edit mode stamps hook and background together",
             pg.fill_attr("page.3"), ' data-fill="page.3" style="background:#E8EDE6"')
finally:
    del os.environ["DOCSYNC_EDIT"]

check("a shape colour that is not a colour is caught",
      _layout_error({"shapes": [{"id": "a", "page": 1, "kind": "rect",
                                 "x": 0, "y": 0, "w": 1, "h": 1, "fill": "sage"}]}),
      "not a hex colour")
check_eq("'none' stays a legal shape fill",
         _layout({"shapes": [{"id": "a", "page": 1, "kind": "rect", "x": 0, "y": 0,
                              "w": 1, "h": 1, "fill": "none"}]}).layer(1).count('fill="none"'), 1)

# A text box may carry a panel colour; only then does it grow padding, so a
# plain box's words sit exactly where they were put.
bfill = _layout({"boxes": [{"id": "t1", "page": 3, "x": 1, "y": 2, "w": 3,
                            "md": "note", "fill": "#D6E0D2"}]})
check("a filled text box paints and pads", bfill.text_boxes(3),
      "background:#D6E0D2;padding:.08in .12in")
bplain = _layout({"boxes": [{"id": "t1", "page": 3, "x": 1, "y": 2, "w": 3, "md": "note"}]})
check_eq("an unfilled text box grows no padding",
         "padding" in bplain.text_boxes(3), False)
check("a box colour that is not a colour is caught",
      _layout_error({"boxes": [{"id": "t1", "page": 3, "x": 1, "y": 2, "w": 3,
                                "md": "note", "fill": "teal"}]}),
      "not a hex colour")

# ------------------------------------------------- rotation, opacity, shadow
rotp = _layout({"positions": {"c.o": {"x": 1, "y": 2, "rot": 15, "alpha": 0.8}}})
check("a rotated element turns in place", rotp.attr("c.o"), "transform:rotate(15deg)")
check("a faded element carries its opacity", rotp.attr("c.o"), "opacity:0.8")
rots = _layout({"shapes": [{"id": "a", "page": 1, "kind": "rect", "x": 1, "y": 1,
                            "w": 2, "h": 1, "rot": 30, "alpha": 0.5,
                            "shadow": {"offset": 0.05, "blur": 0.08}}]})
check("a shape rotates about its own centre", rots.layer(1), 'transform="rotate(30 2.0 1.5)"')
check("shape opacity is an attribute", rots.layer(1), 'opacity="0.5"')
check("a shape shadow is a drop-shadow filter", rots.layer(1), "drop-shadow(")
rotb = _layout({"boxes": [{"id": "t1", "page": 3, "x": 1, "y": 2, "w": 3, "md": "hi",
                           "rot": -10, "shadow": {"blur": 0.1, "alpha": 0.5}}]})
check("a box shadow is box-shadow", rotb.text_boxes(3), "box-shadow:")
check("a box rotates too", rotb.text_boxes(3), "rotate(-10deg)")
check("an opacity above one is caught",
      _layout_error({"positions": {"c.o": {"x": 1, "y": 2, "alpha": 1.5}}}),
      "not a fraction")
scaled = _layout({"positions": {"logo": {"x": 1, "y": 2, "scale": 1.4}}})
check("a scaled graphic carries its factor", scaled.attr("logo"), "scale(1.4)")
check_eq("a scale of exactly 1 emits nothing",
         "scale" in _layout({"positions": {"c.o": {"x": 1, "y": 2, "scale": 1}}}).attr("c.o"),
         False)
check("rotation and scale share one transform, in order",
      _layout({"positions": {"g": {"x": 1, "y": 2, "rot": 20, "scale": 1.5}}}).attr("g"),
      "transform:rotate(20deg) scale(1.5)")
check("a non-positive scale is caught",
      _layout_error({"positions": {"g": {"x": 1, "y": 2, "scale": 0}}}),
      "scale must be positive")
check("a shadow that is not an object is caught",
      _layout_error({"boxes": [{"id": "t1", "page": 3, "x": 1, "y": 2, "w": 3,
                                "md": "hi", "shadow": "big"}]}),
      "expected a shadow object")

# Rotation swings corners past edges the unrotated frame never reached: a
# 4x1in shape at 45° stands ~1.77in proud of its own top edge.
spin = _layout({"shapes": [{"id": "a", "page": 1, "kind": "rect",
                            "x": 2, "y": 0.2, "w": 4, "h": 1, "rot": 45}]})
check("a rotated shape is judged by its rotated box",
      " ".join(spin.check_bounds()), "swings past")
flat = _layout({"shapes": [{"id": "a", "page": 1, "kind": "rect",
                            "x": 2, "y": 0.2, "w": 4, "h": 1}]})
check_eq("unrotated, the same shape is fine", flat.check_bounds(), [])
check_eq("shadow_css is inches and rgba",
         shadow_css({"offset": 0.1, "direction": 90, "blur": 0.05,
                     "alpha": 0.4, "color": "#2F3E46"}),
         "0.1in -0.0in 0.05in rgba(47,62,70,0.4)")

# ------------------------------------------------ shape styling & new kinds
styled_sh = _layout({"shapes": [
    {"id": "t", "page": 2, "kind": "triangle", "x": 1, "y": 1, "w": 2, "h": 1,
     "fill": "#6B9E78"},
    {"id": "a", "page": 2, "kind": "arrow", "x": 4, "y": 1, "w": 2, "h": 0.8,
     "fill": "#52796F"},
    {"id": "l", "page": 2, "kind": "line", "x": 1, "y": 3, "w": 3, "h": 0,
     "stroke": "#2F3E46", "ends": "end", "dash": [0.08, 0.05]}]})
lay2 = styled_sh.layer(2)
check("a triangle is a polygon with its apex centred", lay2, 'points="2,1 3,2 1,2"')
check("an arrow closes seven points", lay2, "5.24,1.224 5.24,1")
check("a dashed line carries its dash", lay2, 'stroke-dasharray="0.08 0.05"')
check("an ended line points its marker", lay2, 'marker-end="url(#ds-arr-2--1)"')
check("the layer defines the arrowhead once", lay2, 'id="ds-arr-2--1"')
check_eq("markers inherit the line's own colour", lay2.count('fill="context-stroke"'), 1)
check("a dash that is not lengths is caught",
      _layout_error({"shapes": [{"id": "l", "page": 1, "kind": "line", "x": 0,
                                 "y": 0, "w": 1, "h": 0, "dash": [0]}]}),
      "positive")
check("unknown line ends are caught",
      _layout_error({"shapes": [{"id": "l", "page": 1, "kind": "line", "x": 0,
                                 "y": 0, "w": 1, "h": 0, "ends": "sideways"}]}),
      "must be one of none, start, end, both")
check("a negative corner radius is caught",
      _layout_error({"shapes": [{"id": "r", "page": 1, "kind": "rect", "x": 0,
                                 "y": 0, "w": 1, "h": 1, "r": -0.1}]}),
      "cannot be negative")

# ---------------------------------------------------------------- images
imged = _layout({"positions": {"p": {"x": 1, "y": 1, "rot": 10, "flip": "h"}},
                 "img": {"p": {"radius": 0.12, "src": "assets/new.jpg",
                               "filter": {"bright": 1.1, "gray": 0.3},
                               "crop": {"imgW": 6.0, "dx": 1.2, "dy": 0.4}}}})
check("rotate and flip share one transform declaration",
      imged.attr("p"), "transform:rotate(10deg) scale(-1,1)")
check_eq("a replaced image shows its replacement",
         imged.img_src("p", "assets/old.jpg"), "assets/new.jpg")
check_eq("an unreplaced image keeps the designed file",
         imged.img_src("q", "assets/old.jpg"), "assets/old.jpg")
check("radius and filters come out as one style", imged.img_css("p"),
      "border-radius:0.12in;filter:brightness(1.1) grayscale(0.3)")
check_eq("no overrides, no style", imged.img_css("q"), "")
check_eq("the crop window's geometry round-trips",
         imged.cropped("p"), {"imgW": 6.0, "dx": 1.2, "dy": 0.4})
check("an unknown flip is caught",
      _layout_error({"positions": {"p": {"x": 1, "y": 1, "flip": "x"}}}),
      "must be h, v or hv")
check("a crop missing its geometry is caught",
      _layout_error({"img": {"p": {"crop": {"imgW": 5}}}}), "needs imgW, dx and dy")
check("a negative filter is caught",
      _layout_error({"img": {"p": {"filter": {"sat": -1}}}}), "cannot be negative")

# --------------------------------------------------------------- page order
# The load-bearing case: no override means the identity order, so the report
# builds byte for byte as before.
check_eq("no page override is the identity order",
         empty.page_order(12), list(range(1, 13)))
reordered = _layout({"pages": {"blanks": [{"id": "b1"}],
                               "order": [1, 2, "b1", 3, 5, 12]}})
check_eq("order interleaves blanks and hides by omission",
         reordered.page_order(12), [1, 2, "b1", 3, 5, 12])
check_eq("blanks are named", reordered.blank_ids(), ["b1"])
check("a designed page beyond the report is caught at render",
      _layout_error_at(lambda: _layout({"pages": {"order": [1, 2, 99]}}).page_order(12)),
      "pages 1–12, not 99")
check("an order naming an undeclared blank is caught",
      _layout_error({"pages": {"order": [1, "ghost"]}}),
      "not a blank this file declares")
check("a duplicate page in the order is caught",
      _layout_error({"pages": {"order": [1, 2, 2]}}), "appears twice")
check("a shape may live on a blank page",
      _layout({"pages": {"blanks": [{"id": "b1"}]},
               "shapes": [{"id": "s", "page": "b1", "kind": "rect",
                           "x": 1, "y": 1, "w": 1, "h": 1}]}).layer("b1"),
      "shape-layer")

# The lock list is an editor affordance the renderer never reads — but a
# malformed one must still fail at load, not quietly stop locking anything.
check_eq("locked ids load", _layout({"locked": ["cover.logo", "s1-rect"]}).locked,
         ["cover.logo", "s1-rect"])
check("a lock list that is not ids is caught",
      _layout_error({"locked": [{"id": "x"}]}), "list of element ids")
check_eq("no lock list means nothing locked", empty.locked, [])

# Groups are an editor affordance too — select and move as one — and the
# renderer never reads them, but a malformed one still fails at load.
check_eq("groups load as lists of member ids",
         _layout({"groups": [["a", "b"], ["c", "d", "e"]]}).groups,
         [["a", "b"], ["c", "d", "e"]])
check("a group of one is not a group",
      _layout_error({"groups": [["solo"]]}), "two or more element ids")
check("an element cannot be in two groups",
      _layout_error({"groups": [["a", "b"], ["b", "c"]]}), "at most one")
check_eq("a group never reaches the rendered page",
         _layout({"groups": [["a", "b"]]}).layer(3), "")

# Ruler guides are editor-only — the renderer never emits one, so an empty
# guides block cannot move a byte, but a malformed one still fails at load.
check_eq("guides load as inches", _layout({"guides": {"x": [1.2, 4.25], "y": [3.0]}}).guides,
         {"x": [1.2, 4.25], "y": [3.0]})
check("a guide off the page is caught",
      _layout_error({"guides": {"x": [9.9]}}), "off the 8.5in page")
check("a guide that is not a number is caught",
      _layout_error({"guides": {"y": ["top"]}}), "not a number")
check_eq("a guide never reaches the rendered page",
         _layout({"guides": {"x": [1.2]}}).layer(3), "")

filled = _layout({"fill": {"card.a": "#2F3E46"}})
check_eq("a fill overrides the designed colour", filled.fill("card.a", "#6B9E78"), "#2F3E46")
check_eq("an unfilled element keeps the colour the report chose",
         filled.fill("card.b", "#6B9E78"), "#6B9E78")
check_eq("refilled() knows which is which", (filled.refilled("card.a"), filled.refilled("card.b")),
         (True, False))
check("a fill that is not a colour is caught",
      _layout_error({"fill": {"card.a": "burnt sienna"}}), "not a hex colour")

# The reason colour cannot be painted onto the DOM: is_light_bg() picks white or
# charcoal text from a tile's luminance AT BUILD TIME, and the footnote pills
# ride the same class. One card is hand-judged light=True by the renderer — a
# judgment about the colour it chose. Recolour it and that judgment is about a
# colour that is no longer there.
def _is_light(hexc):                                  # mirrors render_report.py
    h = hexc.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) > 130


check_eq("the pale tile the renderer hand-judges really is light", _is_light("#CAD2C5"), True)
check_eq("recoloured to charcoal it is not — so its text must reverse",
         _is_light("#2F3E46"), False)


def _is_light_a(hexc):                                # 8-digit, composited over white
    h = hexc.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    if len(h) == 8:
        a = int(h[6:8], 16) / 255
        r, g, b = (v * a + 255 * (1 - a) for v in (r, g, b))
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) > 130


check_eq("an opaque charcoal fill reads dark", _is_light_a("#2F3E46FF"), False)
check_eq("the same charcoal at 25% shows the page and reads light",
         _is_light_a("#2F3E4640"), True)

# --- gradient fills --------------------------------------------------------
# A fill may be a hex (byte-identity) or a gradient object; shape, box and the
# fill{} surfaces all accept both. The three helpers must keep a hex verbatim so
# a solid fill never moves a byte, and must turn a gradient into CSS / an SVG
# paint+defs / one contrast colour.
_LIN = {"type": "linear", "angle": 90,
        "stops": [{"color": "#FFFFFF", "at": 0}, {"color": "#2F3E46", "at": 1}]}
_RAD = {"type": "radial",
        "stops": [{"color": "#6B9E78", "at": 0}, {"color": "#354F52", "at": 1}]}

check_eq("a gradient fill loads on a shape",
         _layout({"shapes": [{"id": "g", "page": 5, "kind": "rect",
                              "x": 1, "y": 1, "w": 2, "h": 2, "fill": _LIN}]}).shapes[0]["fill"]["type"],
         "linear")
check_eq("a gradient fill loads on a fill surface",
         _layout({"fill": {"card.a": _RAD}}).fill("card.a", "#fff")["type"], "radial")
check("a gradient needs two or more stops",
      _layout_error({"fill": {"card.a": {"type": "linear",
                                         "stops": [{"color": "#fff", "at": 0}]}}}),
      "two or more stops")
check("a gradient needs a known type",
      _layout_error({"fill": {"card.a": {"type": "conic",
                                         "stops": [{"color": "#fff", "at": 0},
                                                   {"color": "#000", "at": 1}]}}}),
      "'linear' or 'radial'")
check("a gradient stop needs a hex colour",
      _layout_error({"fill": {"card.a": {"type": "linear",
                                         "stops": [{"color": "teal", "at": 0},
                                                   {"color": "#000", "at": 1}]}}}),
      "not a hex colour")
check("a gradient stop position stays within 0..1",
      _layout_error({"fill": {"card.a": {"type": "linear",
                                         "stops": [{"color": "#fff", "at": 0},
                                                   {"color": "#000", "at": 2}]}}}),
      "between 0 and 1")

# The byte-identity guarantee: a SOLID fill emits exactly what it did before —
# no gradient <defs>, hex verbatim in CSS and SVG.
_solidshape = _layout({"shapes": [{"id": "s", "page": 5, "kind": "rect",
                                   "x": 1, "y": 1, "w": 2, "h": 2, "fill": "#6B9E78"}]})
check("a solid shape emits its hex verbatim", _solidshape.layer(5), 'fill="#6B9E78"')
check_eq("a solid shape draws no gradient def", "linearGradient" in _solidshape.layer(5), False)
check_eq("fill_css keeps a hex verbatim", fill_css("#E8EDE6"), "#E8EDE6")
check_eq("fill_repr keeps a hex verbatim", fill_repr("#123456"), "#123456")

# A gradient shape references a def; the def is emitted once in the layer.
_gradshape = _layout({"shapes": [{"id": "gg", "page": 5, "kind": "rect",
                                  "x": 1, "y": 1, "w": 2, "h": 2, "fill": _LIN}]})
check("a gradient shape paints from a def", _gradshape.layer(5), 'fill="url(#ds-fill-gg)"')
check("the gradient def is emitted", _gradshape.layer(5), '<linearGradient id="ds-fill-gg"')
check("fill_css writes a CSS linear-gradient", fill_css(_LIN), "linear-gradient(90deg,")
check("fill_css writes a CSS radial-gradient", fill_css(_RAD), "radial-gradient(circle,")
check_eq("fill_repr returns a six-digit hex for a gradient", len(fill_repr(_LIN)), 7)
check_eq("contrast still resolves on a gradient (it returns a bool)",
         isinstance(_is_light(fill_repr(_LIN)), bool), True)
# 90deg = to the right: the last stop sits at x=1.
check("a 90deg gradient runs left to right", fill_svg_paint(_LIN, "d")[1], 'x1="0.0"')
check("its last stop is on the right", fill_svg_paint(_LIN, "d")[1], 'x2="1.0"')
# An 8-digit stop splits into colour + opacity for SVG.
check("an 8-digit stop splits its alpha",
      fill_svg_paint({"type": "linear", "stops": [{"color": "#2F3E4680", "at": 0},
                                                  {"color": "#fff", "at": 1}]}, "d")[1],
      'stop-color="#2F3E46" stop-opacity="0.5')

# ------------------------------------------------------------------- icons
# An icon's geometry is copied into layout.json from an open-source set, so
# markup from the internet reaches the rendered page. layout.py is the only
# gate the RENDERER controls (layout.json can be hand-edited), so it checks
# rather than trusts — and fails loudly instead of stripping, which would
# leave a half-drawn icon nobody could explain.
from docsync.layout import check_icon_svg, icon_color                # noqa: E402


def check_icon_raises(name, body, expect):
    try:
        check_icon_svg(body, "shape #1")
    except LayoutError as e:
        if expect not in str(e):
            FAILS.append(f"{name}\n  want error containing: {expect!r}\n  got: {e}")
        return
    FAILS.append(f"{name}\n  expected LayoutError containing {expect!r}, none raised")


_ICON = '<g fill="none" stroke="currentColor"><path d="M3 10a2 2 0 0 1 .7-1.5"/></g>'
check_eq("a plain drawing passes", check_icon_svg(_ICON, "s"), _ICON)
check_icon_raises("a script tag is refused", "<script>alert(1)</script>", "not allowed")
check_icon_raises("an event handler is refused", '<path onload="x" d="M0 0"/>', "not allowed")
check_icon_raises("a remote image is refused", '<image href="http://e/x.png"/>', "not allowed")
check_icon_raises("a javascript: link is refused", '<a href="javascript:1">x</a>', "not allowed")
check_icon_raises("foreignObject is refused", "<foreignObject><b>x</b></foreignObject>",
                  "not allowed")
check_icon_raises("an unknown tag is refused", "<video/>", "not one of the allowed")
check_icon_raises("empty markup is refused", "  ", "needs its 'svg'")
check_icon_raises("absurd markup is refused", '<path d="' + "M0 0" * 20000 + '"/>',
                  "refusing it")

# currentColor is the whole reason these sets recolour cleanly: one CSS
# property repaints the glyph. A gradient cannot BE a colour, so it lends its
# first stop rather than failing — the icon still lands in the palette.
check_eq("a solid fill is the icon's colour", icon_color("#B23A48"), "#B23A48")
check_eq("a gradient lends its first stop",
         icon_color({"type": "linear", "stops": [{"color": "#123456", "at": 0}]}), "#123456")
check_eq("no fill falls back to the report's ink", icon_color(None), "#2F3E46")

# ------------------------------------------------------- page size override
# A report is BUILT at a size; layout.json can override it, which is what
# File > Resize writes. It lives in the layout rather than the report's
# stylesheet because every coordinate in layout.py is inches measured against
# the page — the geometry and the CSS have to come from ONE value, or a resize
# moves everything that was placed before it.
import json as _json                                        # noqa: E402
import tempfile as _tempfile                                # noqa: E402
from docsync.layout import Layout, LayoutError, PAGELESS_H  # noqa: E402


def _layout(d):
    f = Path(_tempfile.mkstemp(suffix=".json")[1])
    f.write_text(_json.dumps(d))
    return Layout(f)


def check_page_raises(name, raw, expect):
    try:
        _layout(raw)
    except LayoutError as e:
        if expect not in str(e):
            FAILS.append(f"{name}\n  want error containing: {expect!r}\n  got: {e}")
        return
    FAILS.append(f"{name}\n  expected LayoutError containing {expect!r}, none raised")


check_eq("no override leaves the built size alone",
         (lambda L: (L.page_w, L.page_h, L.page_style()))(_layout({})), (8.5, 11.0, ""))
check_eq("an override moves the geometry too",
         (lambda L: (L.page_w, L.page_h))(_layout({"page": {"w": 11.69, "h": 16.54}})),
         (11.69, 16.54))
# Both halves, or the preview and the printed sheet disagree — which is worse
# than not being able to resize at all.
check("the box is restyled", _layout({"page": {"w": 11.69, "h": 16.54}}).page_style(),
      ".page{width:11.69in;min-height:16.54in}")
check("and so is the printed sheet", _layout({"page": {"w": 11.69, "h": 16.54}}).page_style(),
      "@page{size:11.69in 16.54in")
# Pageless is a height of NULL, not a large height: the sheet grows with its
# content, and the rules that keep content on the page get a number no report
# will reach rather than a special case in each of them.
check("pageless drops the fixed height",
      _layout({"page": {"w": 8.5, "h": None}}).page_style(), ".page{width:8.5in;min-height:0}")
check_eq("pageless clamps against a sentinel",
         _layout({"page": {"w": 8.5, "h": None}}).page_h, PAGELESS_H)
# It rides out with the first layer() call, so a report gets page sizing
# without its own renderer being changed — a consumer repo vendors this
# package but owns its renderer, so a line added THERE would reach nobody.
_L = _layout({"page": {"w": 8.5, "h": None}})
check("the style rides out with the first layer", _L.layer(1), "<style>")
check_eq("and only once", _L.layer(2), "")
check_eq("a report with no override renders exactly as before", _layout({}).layer(1), "")

check_page_raises("a non-object page is refused", {"page": [8.5, 11]}, "must be an object")
check_page_raises("a non-numeric width is refused", {"page": {"w": "wide", "h": 11}},
                  "is not a number")
check_page_raises("an absurd width is refused", {"page": {"w": 0.2, "h": 11}}, "outside")
check_page_raises("an absurd height is refused", {"page": {"w": 8.5, "h": 900}}, "outside")

# ---- underline ---------------------------------------------------------
# Markdown has no underline and md_inline escapes '<', so raw <u> cannot get
# through — a reader who selects a word and presses U needs a token that
# round-trips. '__' was unused by this grammar (bold '**', italic '*').
from docsync.content import md_inline                      # noqa: E402

check("underline renders", md_inline("pausing __all__ tax cuts"),
      "pausing <u>all</u> tax cuts")
check("underline coexists with bold and italic",
      md_inline("a **b** and __u__ and *i* run"),
      "a <b>b</b> and <u>u</u> and <i>i</i> run")
# A single underscore is not a mark: identifiers and file names carry them.
check_eq("a lone underscore is left alone",
         md_inline("snake_case_name stays"), "snake_case_name stays")
check_eq("raw <u> is still escaped, so markup cannot be smuggled in",
         md_inline("<u>x</u>"), "&lt;u>x&lt;/u>")

# ---- blocks: the PDF download capability -------------------------------
# A "Download PDF" button and the print CSS that makes its output correct are
# ONE capability: without @page/margin:0, forced breaks, shadow removal and
# print-color-adjust, the button hands the reader a PDF with the browser's
# margins stacked on the page's, grey shadow bands between sheets, and every
# background dropped to white. pdf_button() therefore carries print_css().
from docsync.blocks import pdf_button, print_css     # noqa: E402

letter = _layout({"positions": {}, "shapes": [], "page": {"w": 8.5, "h": 11}})
css = print_css(letter)
check("print css sizes the sheet and kills its margin", css,
      "@page{size:8.5in 11in;margin:0}")
check("print css forces exact sheets", css, "height:11in;page-break-after:always")
check("the last sheet does not force a trailing blank",
      css, ".page:last-child{page-break-after:auto}")
check("print css drops the screen shadow and gutter", css, "margin:0;box-shadow:none")
check("print css hides on-screen chrome", css, ".noprint{display:none !important}")
# Chrome prints every background white unless this is set, and it is invalid
# as a bare declaration — it has to hang off a selector.
check("print css forces background graphics", css,
      "*,*::before,*::after{-webkit-print-color-adjust:exact !important")
check_eq("links are left alone unless asked for", "text-decoration:underline" in css, False)
check("link_ink underlines and recolours for print",
      print_css(letter, link_ink="#354F52"), "a{color:#354F52;text-decoration:underline}")
check("pad overrides the page padding for print only",
      print_css(letter, pad="0.75in 0.62in"), "padding:0.75in 0.62in")

# A pageless report is one continuous surface: it takes a width and lets the
# sheet run, and must NOT be cut into fixed-height pages.
pageless = _layout({"positions": {}, "shapes": [], "page": {"w": 8.5, "h": None}})
pl = print_css(pageless)
check("a pageless report prints as one running sheet", pl, "@page{size:8.5in auto;margin:0}")
check_eq("a pageless report is not cut into fixed sheets",
         "page-break-after:always" in pl, False)
check_eq("a pageless report gets no forced height", "height:" in pl, False)

btn = pdf_button(letter)
check("the button prints through the browser, so it can never go stale",
      btn, 'onclick="window.print()"')
check("the button is screen-only chrome", btn, 'class="noprint"')
check("the button is pinned to the upper-right corner", btn,
      "position:fixed;top:18px;right:18px")
check("the button carries the print css with it", btn, "@media print{")
check_eq("css=False leaves the sheet rules to the caller",
         "@media print{" in pdf_button(letter, css=False), False)
check("a second button can skip the duplicate css",
      pdf_button(letter, css=False), 'onclick="window.print()"')

# On the editor canvas the button is drawn AND live — an inert button that
# looks clickable reads as broken (reported as exactly that). It must not
# window.print() there, which would print the artboard iframe with its edit
# affordances; instead it posts up to the chrome, which runs the same
# server-side export as File > Download > PDF.
os.environ["DOCSYNC_EDIT"] = "1"
try:
    ed = pdf_button(letter)
    check("the button is drawn on the editor canvas", ed, "Download PDF")
    check("in the editor it defers to the chrome's exporter",
          ed, "parent.postMessage({ds:'export-pdf'}")
    # The ds- prefix is what keeps it CLICKABLE there: deafenStickyChrome()
    # pointer-deafens every other fixed/sticky child of <body>. Without this
    # class, real clicks pass straight through the button (synthetic dispatch
    # still fires, which is how that breakage got past a hand test).
    check("the ds- class exempts it from sticky-chrome deafening",
          ed, 'class="ds-pdfbtn noprint"')
    check_eq("never window.print() over the artboard",
             "window.print()" in ed, False)
    check("the print rules still ship in edit mode", ed, "@media print{")
finally:
    del os.environ["DOCSYNC_EDIT"]

# ---- blocks: a card is its detached pieces' coordinate frame ----------------
# The tile must be a containing block FROM BIRTH (position:relative). Its
# title/bullets/icon pin against the nearest positioned ancestor; when the
# static tile only became positioned on its first move, every coordinate saved
# before that re-based against it — the words landed displaced by exactly the
# tile's offset, clipped or white-on-white ("the text disappeared").
from docsync.blocks import card                       # noqa: E402

class _CardC:
    def t(self, k): return "Key points"
    def list(self, k): return ["one", "two"]
    def ul_attr(self, k): return ""

_card = card(_CardC(), letter, "kp.title", "kp.bullets", "#52796F",
             detachable=True)
check("card: the tile is a containing block from birth, so a piece's saved "
      "frame can never change under it", _card, "position:relative")
# A MOVED tile appends its override after the base style, so its
# position:absolute is the later declaration and wins — while the tile
# stays a containing block either way.
_moved = card(_CardC(),
              _layout({"positions": {"card.kp.bullets": {"x": 1, "y": 2}},
                       "shapes": []}),
              "kp.title", "kp.bullets", "#52796F", detachable=True)
check("card: a moved tile's position:absolute lands after the frame rule",
      _moved, "padding:16px 18px;margin:0;position:absolute")

# ---- chart entrances: bars that grow out of the axis ------------------------
# A part animation, unlike the seven whole-element kinds: the chart, its axes
# and its labels are there from the start and only the bars arrive. So it must
# NOT take the shared opacity:0 wait state, and print and reduced-motion must
# hand back full-height bars — a chart of empty axes reads as missing data,
# not as missing motion.
def _chart_layout(anim=None, ctype="bar", kind="chart"):
    sh = {"id": "c1", "page": 1, "kind": kind, "x": 1, "y": 1, "w": 4, "h": 3,
          "chart": {"type": ctype, "labels": ["A", "B", "C"],
                    "series": [{"name": "S", "data": [12, 19, 8]}]}}
    if anim:
        sh["anim"] = anim
    return _layout({"positions": {}, "shapes": [sh]})

_BARS = {"kind": "bars", "duration": 0.8, "delay": 0.1}
_cb = _chart_layout(_BARS).layer(1)
check("chart bars: every bar is marked so the entrance can find it",
      _cb, 'class="ds-cbar"')
check("chart bars: the chart carries the kind", _cb, 'data-ds-anim="bars"')
# Staggered, so the row reads across rather than arriving at once — and each
# bar carries its own timing, since the script only talks to the chart.
check("chart bars: the first bar starts at the chart's own delay",
      _cb, "animation-delay:0.100s")
check("chart bars: the next one starts a beat later", _cb, "animation-delay:0.190s")
check("chart bars: and the third a beat after that", _cb, "animation-delay:0.280s")
check("chart bars: each takes the chosen duration", _cb, "animation-duration:0.8s")
# The chart stays visible; only the bars are held back.
check("chart bars: the chart is NOT hidden while it waits",
      _cb, '[data-ds-anim="bars"].ds-anim-wait{opacity:1}')
check("chart bars: the bars are what waits",
      _cb, '[data-ds-anim="bars"].ds-anim-wait .ds-cbar{scale:1 0}')
check("chart bars: they grow from the bar's own baseline",
      _cb, ".ds-cbar{transform-box:fill-box;transform-origin:bottom}")
check("chart bars: print draws them at full height",
      _cb, ".ds-cbar{animation:none !important;scale:1 1 !important}")
check_eq("chart bars: the chart itself gets no whole-element animation",
         '.ds-anim-in[data-ds-anim="bars"]{animation-name' in _cb, False)
# A row chart grows sideways, from the axis it starts at.
_rows = _chart_layout(_BARS, ctype="row").layer(1)
check("chart bars: a row chart grows from the left", _rows, 'class="ds-cbar ds-cbar-x"')
check("chart bars: sideways has its own keyframes",
      _rows, "@keyframes ds-a-bar-x{from{scale:0 1}to{scale:1 1}}")
# No animation asked for: no timing baked into the bars at all.
check_eq("chart bars: an unanimated chart bakes no timing",
         "animation-delay" in _chart_layout().layer(1), False)
# A whole-element kind still works on a chart, and touches no bar.
check("chart: a plain entrance still applies to the whole drawing",
      _chart_layout({"kind": "rise"}).layer(1), 'data-ds-anim="rise"')
check_eq("chart: a whole-element entrance bakes no per-bar timing",
         "animation-delay" in _chart_layout({"kind": "rise"}).layer(1), False)

# 'bars' is refused anywhere it would have nothing to animate — it would
# validate, render, and then never happen.
for bad, why in (
    (lambda: _chart_layout(_BARS, kind="rect"), "a rect shape"),
    (lambda: _layout({"positions": {}, "shapes": [],
                      "boxes": [{"id": "b", "page": 1, "x": 1, "y": 1, "w": 2,
                                 "md": "hi", "anim": _BARS}]}), "a text box"),
    (lambda: _layout({"positions": {"basics.h1": {"x": 1, "y": 1,
                                                  "anim": _BARS}},
                      "shapes": []}), "a designed element"),
):
    try:
        bad()
        FAILS.append(f"anim 'bars' on {why} should be refused")
    except LayoutError as e:
        if "only works on a chart" not in str(e):
            FAILS.append(f"anim 'bars' on {why}: unhelpful message {e}")

# ---- the expand button's chevron -------------------------------------------
# Drawn art, not a glyph: it turns to point up when the section opens, it is
# present on the EDITOR canvas too (where the button used to have none), and
# it takes a colour of its own from the same `fill` map every other
# recolourable piece of artwork uses — which is what "click it and restyle it"
# rests on.
def _tgl_layout(fill=None):
    d = {"positions": {}, "shapes": [],
         "boxes": [{"id": "b", "page": 1, "x": 1, "y": 1, "w": 2,
                    "md": "Show details", "act": "toggle", "target": "c"},
                   {"id": "c", "page": 1, "x": 1, "y": 2, "w": 2,
                    "md": "Hidden content"}]}
    if fill:
        d["fill"] = fill
    return _layout(d)

_pub = _tgl_layout().text_boxes(1)
check("chevron: published as a real svg, not a glyph", _pub, 'class="ds-tgl-i ds-tgl-svg"')
check_eq("chevron: the old text glyph is gone", "▾" in _pub, False)
check("chevron: it turns to point up when the section is open",
      _pub, ".ds-tgl-on .ds-tgl-i{transform:rotate(180deg)}")
check("chevron: it inherits the button's ink until recoloured",
      _pub, 'stroke="currentColor"')
check_eq("chevron: no editing hook in the published page",
         "tglarrow." in _pub, False)
# Recoloured: keyed per button, so two buttons colour independently.
check("chevron: a recolour reaches the drawn stroke",
      _tgl_layout({"tglarrow.b": "#C0603F"}).text_boxes(1), 'stroke="#C0603F"')

os.environ["DOCSYNC_EDIT"] = "1"
try:
    _ed = _tgl_layout().text_boxes(1)
    check("chevron: drawn on the editor canvas too", _ed, 'class="ds-tgl-i ds-tgl-svg"')
    check("chevron: selectable there, under its own id", _ed, 'data-el="tglarrow.b"')
    # The button is a plain div while editing (a live <button> would escape the
    # drag guard) — the arrow has to ride that div, not the published <button>.
    check_eq("chevron: no live button on the canvas", "<button" in _ed, False)
    check("chevron: a recolour shows while editing, not just when published",
          _tgl_layout({"tglarrow.b": "#C0603F"}).text_boxes(1), 'stroke="#C0603F"')
    # Only the toggle gets one — a plain box has nothing to expand.
    check_eq("chevron: a plain text box grows no arrow",
             "ds-tgl-svg" in _layout({"positions": {}, "shapes": [],
                 "boxes": [{"id": "p", "page": 1, "x": 1, "y": 1, "w": 2,
                            "md": "plain"}]}).text_boxes(1), False)
finally:
    del os.environ["DOCSYNC_EDIT"]

# ---- the mount marker: where an insert may land, and as which page ---------
# The editor stamps a page onto every new element and the validator refuses
# one without a real page number, so a page it cannot work out is not a
# degraded guess — it bricks the draft on the FIRST thing added. A renderer that
# stamps no data-page on its sections relies on this marker, which carries the
# number the renderer itself looks boxes up by. Published output must not
# change at all.
_mt = _layout({"positions": {}, "shapes": [], "boxes": []})
check_eq("mount: the published page carries no editing scaffolding",
         _mt.text_boxes(1), "")
os.environ["DOCSYNC_EDIT"] = "1"
try:
    _m1 = _layout({"positions": {}, "shapes": [], "boxes": []}).text_boxes(1)
    check("mount: an empty page still says it can host an insert",
          _m1, 'data-ds-mount="1"')
    check("mount: it is invisible and out of the a11y tree",
          _m1, 'style="display:none" aria-hidden="true"')
    # The number is the RENDERER'S, not the section's position: a report may
    # mount only some of its sheets, or number them its own way.
    check("mount: it carries the page number the renderer asked for",
          _layout({"positions": {}, "shapes": [], "boxes": []}).text_boxes(7),
          'data-ds-mount="7"')
    # And it must survive the page actually having boxes on it.
    _m2 = _layout({"positions": {}, "shapes": [],
                   "boxes": [{"id": "b1", "page": 1, "x": 1, "y": 1, "w": 2,
                              "md": "hi"}]}).text_boxes(1)
    check("mount: a page WITH boxes is marked too", _m2, 'data-ds-mount="1"')
    check("mount: the boxes still render alongside it", _m2, "hi")
finally:
    del os.environ["DOCSYNC_EDIT"]

# ---- pagemeta: the renderer declaring which pages it has --------------------
# The editor's page strip needs the list BEFORE any of it is reordered, and a
# hidden page is absent from the DOM entirely, so it is declared rather than
# inferred. It is also the capability flag: the strip offers reordering and
# blank pages only to a renderer that made this call, because layout.pages
# means nothing to one that never reads page_order().
_pm = _layout({"positions": {}, "shapes": [], "boxes": []})
check_eq("pagemeta: the published page carries none of it", _pm.pagemeta([1, 2]), "")
os.environ["DOCSYNC_EDIT"] = "1"
try:
    _p1 = _layout({"positions": {}, "shapes": [], "boxes": []}).pagemeta([1, 2, 3])
    check("pagemeta: it is JSON the editor can parse", _p1,
          '<script type="application/json" id="ds-pagemeta">')
    check_eq("pagemeta: bare ids need no label",
             json.loads(_p1.split(">", 1)[1].rsplit("<", 1)[0]),
             [{"id": 1}, {"id": 2}, {"id": 3}])
    # (id, label) pairs are what puts a page's NAME in the strip — the fallback
    # can count sheets but can never know they are called Cover and Contents.
    _p2 = _layout({"positions": {}, "shapes": [], "boxes": []}).pagemeta(
        [(1, "Cover"), (2, "Contents")])
    check_eq("pagemeta: pairs carry the label through",
             json.loads(_p2.split(">", 1)[1].rsplit("<", 1)[0]),
             [{"id": 1, "label": "Cover"}, {"id": 2, "label": "Contents"}])
    # A generator is the natural argument (range(1, n + 1)), so it must not be
    # consumed before it is read.
    check_eq("pagemeta: a range works like a list",
             json.loads(_layout({"positions": {}, "shapes": [], "boxes": []})
                        .pagemeta(range(1, 3)).split(">", 1)[1].rsplit("<", 1)[0]),
             [{"id": 1}, {"id": 2}])
finally:
    del os.environ["DOCSYNC_EDIT"]

# ---- notices: what a conversion had to decide, said in the editor -----------
_nl = _layout({"positions": {}, "shapes": [], "boxes": []})
check_eq("notices: the published page carries none of them",
         _nl.notices(["a page size was guessed"]), "")
os.environ["DOCSYNC_EDIT"] = "1"
try:
    _n = _layout({"positions": {}, "shapes": [], "boxes": []}).notices(
        ["page size came from page 1", "fonts were not carried"])
    check("notices: JSON the editor can parse", _n,
          '<script type="application/json" id="ds-notices">')
    check_eq("notices: both messages, in order",
             json.loads(_n.split(">", 1)[1].rsplit("<", 1)[0]),
             ["page size came from page 1", "fonts were not carried"])
    # A report with nothing to say must emit nothing at all, or the editor
    # shows an empty strip on every project that was authored not converted.
    for empty in ([], None, ["", "   "]):
        check_eq(f"notices: {empty!r} emits nothing",
                 _layout({"positions": {}, "shapes": [], "boxes": []}).notices(empty), "")
finally:
    del os.environ["DOCSYNC_EDIT"]

# ---- ingest: the coordinate translation, without writing a project ---------
# A PDF is already a placed document; conversion is points -> inches. Tested
# through the pure functions so it needs no PDF and no project on disk.
from docsync import ingest as _ing                                # noqa: E402

check_eq("ingest: 72pt is one inch", _ing._in(72), 1.0)
check_eq("ingest: a letter sheet in inches", (_ing._in(612), _ing._in(792)), (8.5, 11.0))
# One size per document: the first page wins and the rest are NAMED, which is
# what the editor's dismissable notice is built from.
check_eq("ingest: no outliers when every page matches",
         _ing.size_outliers([(8.5, 11.0), (8.5, 11.0)]), [])
check_eq("ingest: outliers are 1-based page numbers",
         _ing.size_outliers([(8.5, 11.0), (8.5, 14.0), (8.5, 11.0), (11.0, 8.5)]), [2, 4])
# Generators emit 611.976 for letter often enough that an exact comparison
# would call every page an outlier.
check_eq("ingest: a rounding-error difference is the same size",
         _ing.size_outliers([(8.5, 11.0), (8.499, 11.001)]), [])
# Markdown's own leading markers would silently turn a line into a list.
check_eq("ingest: a numbered line stays that line", _ing._md("1. Introduction"),
         "\\1. Introduction")
check_eq("ingest: a dashed line stays that line", _ing._md("- not a bullet"),
         "\\- not a bullet")
check_eq("ingest: PDF line breaks collapse", _ing._md("one\ntwo   three\n"),
         "one two three")
check_eq("ingest: colour black is left unsaid", _ing._colour(0), None)
check_eq("ingest: a packed sRGB int becomes hex", _ing._colour(0x52796F), "#52796F")
# The style must never name a FONT: layout.py refuses a family it cannot load,
# so carrying the PDF's own would refuse the whole draft rather than degrade.
_st = _ing._style_for({"lines": [{"spans": [
    {"text": "a heading", "size": 18.0, "color": 0x222E33, "font": "ABCDEF+Helvetica"}]}]})
check_eq("ingest: point size becomes CSS px", _st.get("size"), 24.0)
check_eq("ingest: colour carries", _st.get("color"), "#222E33")
check_eq("ingest: font is never carried", "font" in _st, False)
from docsync.layout import _check_text as _ct                     # noqa: E402
_ct(_st, "ingest style")                  # and it must pass layout's own gate
# The dominant span decides, so a heading is not described by its footnote mark.
_st2 = _ing._style_for({"lines": [{"spans": [
    {"text": "x", "size": 6.0, "color": 0},
    {"text": "a much longer run of body text", "size": 10.0, "color": 0}]}]})
check_eq("ingest: the longest run sets the size", _st2.get("size"), round(10 * 96 / 72, 1))

# ---- act boxes: the editor-native Download-PDF button -----------------------
# A text box carrying act:'pdf' is an ordinary movable in the editor and a
# REAL button in the published page — layout.text_boxes owns both faces.
import tempfile as _tf
from docsync.layout import Layout as _L, LayoutError as _LE

def _act_layout(extra=None):
    box = {"id": "t1", "page": 1, "x": 1, "y": 2, "w": 1.9,
           "md": "**Download** PDF", "fill": "#2F3E46",
           "style": {"size": 14, "color": "#FFFFFF"}, "act": "pdf"}
    box.update(extra or {})
    f = Path(_tf.mkstemp(suffix=".json")[1])
    f.write_text(json.dumps({"boxes": [box]}))
    return _L(f)

os.environ.pop("DOCSYNC_EDIT", None)
_pub = _act_layout().text_boxes(1)
check("published: a real button element", _pub, '<button type="button"')
check("published: prints, the never-stale download", _pub, 'onclick="window.print()"')
check("published: absent from the PDF it downloads", _pub,
      "@media print{.ds-actbtn{display:none}}")
check("published: the box's own fill styles it", _pub, "background:#2F3E46")
check("published: the box's own type styles it", _pub, "font-size:14px")
check("published: markdown works in the label", _pub, "<b>Download</b>")
check_eq("published: label is one line, not paragraphs",
         "<p>" in _pub.split("<button")[1], False)
check_eq("published: no editor hooks leak", "data-el" in _pub, False)

os.environ["DOCSYNC_EDIT"] = "1"
try:
    _ed = _act_layout().text_boxes(1)
    check("editor: an ordinary movable text box", _ed, 'data-el="text.t1"')
    check_eq("editor: never a live button (it would be unmovable — the drag "
             "guard skips real controls)", "<button" in _ed, False)
    check_eq("editor: no print onclick under the artboard",
             "onclick" in _ed, False)
    check("editor: same padding as published, so WYSIWYG holds",
          _ed, "padding:.08in .12in")
finally:
    del os.environ["DOCSYNC_EDIT"]

# align's inline-slot compensation appends width:100%; on a BOX the box's own
# width must land after it and win, or every aligned text box spans the page
# (and the drag math, anchored to the box it meant to draw, flings it left).
_aligned = _act_layout({"style": {"align": "center"}}).text_boxes(1)
check_eq("an aligned box keeps its own width (geometry wins the collision)",
         _aligned.rindex("width:1.9in") > _aligned.rindex("width:100%"), True)

_unfilled = _act_layout({"fill": None}).text_boxes(1)
check("an unfilled button still reads as a button", _unfilled,
      "padding:.08in .12in;border-radius:8px")

# ---- act 'toggle': the expandable section -----------------------------------
def _pair_layout(btn_extra=None, tgt_extra=None):
    btn = {"id": "t1", "page": 1, "x": 1, "y": 1, "w": 2.6, "md": "Show details",
           "fill": "#52796F", "act": "toggle", "target": "t2"}
    tgt = {"id": "t2", "page": 1, "x": 1, "y": 1.6, "w": 5, "md": "Hidden words."}
    btn.update(btn_extra or {}); tgt.update(tgt_extra or {})
    f = Path(_tf.mkstemp(suffix=".json")[1])
    f.write_text(json.dumps({"boxes": [btn, tgt]}))
    return _L(f)

os.environ.pop("DOCSYNC_EDIT", None)
_tp = _pair_layout().text_boxes(1)
check("toggle published: a real button", _tp, 'class="ds-actbtn ds-tglbtn"')
check("toggle published: flips the target by id, through the shared driver",
      _tp, "onclick=\"__dsTgl(this,['ds-x-t2'],0.3)\"")
check("toggle published: the shared driver flips the button's own state",
      _tp, "var open=!btn.classList.contains('ds-tgl-on')")
check("toggle published: the target carries that id", _tp, 'id="ds-x-t2"')
check("toggle published: the target starts collapsed", _tp,
      "ds-textbox ds-tglable")
check("toggle published: a REAL height transition, not an instant show/hide",
      _tp, "transition:max-height var(--ds-tgl-d,.3s) cubic-bezier(.2,.7,.3,1),"
           "opacity var(--ds-tgl-d,.3s) ease")
check("toggle published: opening measures the target's true height before "
      "animating to it — a fixed max-height would make short content snap "
      "open almost instantly instead of easing over the full duration",
      _tp, "el.style.maxHeight=el.scrollHeight+'px'")
check("toggle published: opening settles at max-height:none once done, so a "
      "reflow (a narrower viewport) never clips content that grew",
      _tp, "el.style.maxHeight='none'")
check("toggle published: closing commits a real starting height first — "
      "max-height cannot transition FROM 'none'", _tp,
      "void el.offsetHeight;el.style.maxHeight='0px'")
check("toggle published: each button's own configured speed rides its style, "
      "as the CSS var the transition reads", _tp, "--ds-tgl-d:0.3s")
check("toggle published: collapsed content leaves the tab order too, not "
      "just the screen", _tp, "el.toggleAttribute('inert',!open)")
check("toggle published: PRINT shows everything — collapsing is a screen "
      "affordance", _tp, "@media print{.ds-tglable{max-height:none!important;"
      "opacity:1!important;overflow:visible!important}}")
check("toggle published: reduced motion gets the open/closed STATE with no "
      "animation, not a stuck-mid-transition element", _tp,
      "@media (prefers-reduced-motion:reduce){.ds-tglable{transition:none!important}")
check("toggle published: aria state tracks the flip", _tp,
      "setAttribute('aria-expanded',String(open))")
check("toggle published: the arrow that turns", _tp, "ds-tgl-i")
check("toggle published: the arrow's own rotation rides the same speed",
      _tp, "transition:transform var(--ds-tgl-d,.3s)")

_tp2 = _pair_layout(btn_extra={"tglSpeed": 0.9}).text_boxes(1)
check("toggle published: a configured speed rides both the onclick call and "
      "the CSS var", _tp2, "__dsTgl(this,['ds-x-t2'],0.9)")
check("toggle published: …and the target's own style, not just the button's",
      _tp2, "--ds-tgl-d:0.9s")

try:
    _pair_layout(btn_extra={"tglSpeed": 3}); FAILS.append("tglSpeed 3 (>2) was accepted")
except _LE as e:
    check("tglSpeed above 2s is refused, named", str(e), "tglSpeed")
try:
    _pair_layout(btn_extra={"tglSpeed": 0.02}); FAILS.append("tglSpeed 0.02 (<0.1) was accepted")
except _LE as e:
    check("tglSpeed below 0.1s is refused", str(e), "tglSpeed")

os.environ["DOCSYNC_EDIT"] = "1"
try:
    _te = _pair_layout().text_boxes(1)
    check("toggle editor: button is an ordinary movable", _te, 'data-el="text.t1"')
    check("toggle editor: target is an ordinary movable", _te, 'data-el="text.t2"')
    check_eq("toggle editor: no live button on the artboard", "<button" in _te, False)
    check_eq("toggle editor: the target stays VISIBLE — collapsed content you "
             "cannot see is content you cannot edit",
             "ds-textbox ds-tglable" in _te, False)
finally:
    del os.environ["DOCSYNC_EDIT"]

# A toggle can reveal shapes, tables and several things at once — each kind
# stamps its hook on the node it already owns.
def _trio_layout():
    f = Path(_tf.mkstemp(suffix=".json")[1])
    f.write_text(json.dumps({
        "boxes": [
            {"id": "t1", "page": 1, "x": 1, "y": 1, "w": 2.6, "md": "Show all",
             "act": "toggle", "target": ["t2", "s1", "t3"]},
            {"id": "t2", "page": 1, "x": 1, "y": 1.6, "w": 5, "md": "Box."}],
        "shapes": [{"id": "s1", "page": 1, "kind": "rect",
                    "x": 1, "y": 3, "w": 2, "h": 1, "fill": "#6B9E78"}],
        "tables": [{"id": "t3", "page": 1, "x": 1, "y": 4.5, "w": 3,
                    "rows": [["A", "B"]]}]}))
    return _L(f)

os.environ.pop("DOCSYNC_EDIT", None)
_L3 = _trio_layout()
_b3, _l3, _h3 = _L3.text_boxes(1), _L3.layer(1), _L3.tables_html(1)
check("trio: one click drives all three", _b3, "['ds-x-t2','ds-x-s1','ds-x-t3']")
check("trio: aria-controls names them all", _b3,
      'aria-controls="ds-x-t2 ds-x-s1 ds-x-t3"')
check("trio: the SHAPE carries the hook on its own node", _l3,
      'id="ds-x-s1" class="ds-tglable"')
check("trio: the TABLE carries the hook", _h3, "ds-table ds-tglable")

os.environ["DOCSYNC_EDIT"] = "1"
try:
    _E3 = _trio_layout()
    check_eq("trio editor: no shape hook stamped",
             'ds-tglable' in _E3.layer(1), False)
    check_eq("trio editor: no table hook stamped",
             'ds-table ds-tglable' in _E3.tables_html(1), False)
finally:
    del os.environ["DOCSYNC_EDIT"]

try:
    f = Path(_tf.mkstemp(suffix=".json")[1])
    f.write_text(json.dumps({"boxes": [
        {"id": "t1", "page": 1, "x": 1, "y": 1, "w": 2, "md": "x",
         "act": "toggle", "target": ["nope"]}]}))
    _L(f)
    FAILS.append("a list target with an unknown id was accepted")
except _LE as e:
    if "not a box, shape or table" not in str(e):
        FAILS.append(f"unknown list target refused with the wrong words: {e}")

for name, kw, want in [
    ("a toggle with no target is refused", {"btn_extra": {"target": None}}, "needs a 'target'"),
    ("a target that exists nowhere is refused", {"btn_extra": {"target": "ghost"}}, "not a box, shape or table"),
    ("a self-toggling button is refused", {"btn_extra": {"target": "t1"}}, "cannot reveal itself"),
    ("a target id that could break the script is refused",
     {"btn_extra": {"target": "a'b"}}, "letters, digits"),
]:
    try:
        _pair_layout(**kw)
        FAILS.append(name + " — accepted")
    except _LE as e:
        if want not in str(e):
            FAILS.append(f"{name} — wrong words: {e}")

# ---- entrance animations ----------------------------------------------------
# anim: {kind, duration, delay} on any element. AOS-vocabulary kinds; the
# initial hidden state is applied BY THE SCRIPT (a page whose JS never runs
# shows everything), print and reduced-motion always show content, and the
# keyframes animate translate/scale so an element's own rotation survives.
def _anim_layout():
    f = Path(_tf.mkstemp(suffix=".json")[1])
    f.write_text(json.dumps({
        "boxes": [{"id": "t1", "page": 1, "x": 1, "y": 1, "w": 3,
                   "md": "Animated", "anim": {"kind": "rise",
                                              "duration": 0.8, "delay": 0.2}}],
        "shapes": [{"id": "s1", "page": 1, "kind": "rect", "x": 1, "y": 3,
                    "w": 2, "h": 1, "fill": "#6B9E78", "rot": 15,
                    "anim": {"kind": "pop"}}],
        "tables": [{"id": "t3", "page": 1, "x": 1, "y": 5, "w": 3,
                    "rows": [["A"]], "anim": {"kind": "fade"}}],
        "positions": {"basics.h1": {"x": 1, "y": 0.5,
                                    "anim": {"kind": "slide-left"}}}}))
    return _L(f)

os.environ.pop("DOCSYNC_EDIT", None)
_A = _anim_layout()
_ab, _al, _ah, _aa = (_A.text_boxes(1), _A.layer(1), _A.tables_html(1),
                      _A.attr("basics.h1"))
check("anim: a box carries kind, duration and delay", _ab,
      'data-ds-anim="rise" data-ds-ad="0.8" data-ds-aw="0.2"')
check("anim: a shape carries it on its data-shape node", _al, 'data-ds-anim="pop"')
check("anim: the shape's own rotation survives (translate/scale keyframes)",
      _al, 'transform="rotate(15')
check("anim: a table carries it", _ah, 'data-ds-anim="fade"')
check("anim: a designed element carries it via attr()", _aa,
      'data-ds-anim="slide-left"')
_blob = _ab + _al + _ah
check_eq("anim: keyframes emitted exactly once", _blob.count("@keyframes ds-a-fade"), 1)
check("anim: hidden state is applied by the script, so no-JS shows everything",
      _blob, "e.classList.add('ds-anim-wait')")
check("anim: the script waits for the DOM — it is emitted before most of the "
      "elements it must observe", _blob, "DOMContentLoaded")
check("anim: reduced motion opts out", _blob, "prefers-reduced-motion")
check("anim: print always shows content", _blob,
      "@media print{[data-ds-anim]{opacity:1 !important")

os.environ["DOCSYNC_EDIT"] = "1"
try:
    _E = _anim_layout()
    _eb = _E.text_boxes(1) + _E.layer(1) + _E.tables_html(1)
    check("anim editor: data attributes present (presentation replays from them)",
          _eb, 'data-ds-anim="rise"')
    check("anim editor: keyframes ship for the replay", _eb, "@keyframes ds-a-rise")
    check_eq("anim editor: no observer, no hiding — elements stay static",
             "IntersectionObserver" in _eb, False)
finally:
    del os.environ["DOCSYNC_EDIT"]

for raw, want in [
    ({"boxes": [{"id": "t1", "page": 1, "x": 1, "y": 1, "w": 2, "md": "x",
                 "anim": {"kind": "spin"}}]}, "anim kind"),
    ({"boxes": [{"id": "t1", "page": 1, "x": 1, "y": 1, "w": 2, "md": "x",
                 "anim": {"kind": "fade", "duration": 99}}]}, "duration"),
    ({"shapes": [{"id": "s1", "page": 1, "kind": "rect", "x": 1, "y": 1,
                  "w": 1, "h": 1, "anim": "fade"}]}, "must be an object"),
    ({"positions": {"e": {"x": 1, "y": 1,
                          "anim": {"kind": "fade", "delay": -1}}}}, "delay"),
]:
    try:
        f = Path(_tf.mkstemp(suffix=".json")[1])
        f.write_text(json.dumps(raw))
        _L(f)
        FAILS.append(f"anim refusal missed: {want}")
    except _LE as e:
        if want not in str(e):
            FAILS.append(f"anim refusal wrong words for {want}: {e}")

# check_raises catches ContentError; this is the LAYOUT validator's error.
try:
    _act_layout({"act": "launch"})
    FAILS.append("an unknown act was accepted — it would ship as a dead button")
except _LE as e:
    if "unknown act 'launch'" not in str(e):
        FAILS.append(f"unknown act refused with the wrong words: {e}")

# --- the style guide IS the template -----------------------------------------
# style_guide()'s patterns are served to pilots as "the exact styles the
# reference reports use" — and the report template's placed elements are the
# ground truth, digested from the published 2025–26 PDFs. If the two drift, a
# pilot following the guide lands headers that only LOOK close, so every
# pattern is held equal to the template element it describes, field by field.
from docsync.templates import SCHEMES, TEMPLATES, style_guide  # noqa: E402

_sg = style_guide()["patterns"]
_rep = TEMPLATES["appleseed-report"]["layout"]()
_box = {b["id"]: b for b in _rep["boxes"]}
_shape = {s["id"]: s for s in _rep["shapes"]}
_topic = SCHEMES["blue"]["color"]           # the template's default scheme

check_eq("style guide: cover title == the template's",
         _box["tpl-title"]["style"], _sg["cover_title"]["style"])
check_eq("style guide: cover title placement == the template's",
         {k: _box["tpl-title"][k] for k in ("x", "y", "w")},
         _sg["cover_title"]["at"])
check_eq("style guide: section heading == the template's page-4 headline",
         _box["tpl-h4"]["style"], _sg["section_heading"]["style"])
check_eq("style guide: section heading placement == the template's",
         {k: _box["tpl-h4"][k] for k in ("x", "y", "w")},
         _sg["section_heading"]["at"])
check_eq("style guide: body == the template's page-4 body",
         _box["tpl-body4a"]["style"], _sg["body"]["style"])
check_eq("style guide: subheading + topic colour == the template's",
         _box["tpl-sub4"]["style"],
         dict(_sg["subheading"]["style"], color=_topic))
check_eq("style guide: footer == the template's page-4 footer",
         _box["tpl-foot4"]["style"], _sg["footer"]["style"])
check_eq("style guide: footer placement == the template's",
         {k: _box["tpl-foot4"][k] for k in ("x", "y", "w")},
         _sg["footer"]["at"])
check_eq("style guide: hairline geometry == the template's page-4 rule",
         {k: _shape["tpl-rule4"][k] for k in ("kind", "x", "y", "w", "h")},
         _sg["hairline"]["shape"])
check_eq("style guide: hairline fill is the topic colour",
         _shape["tpl-rule4"]["fill"], _topic)
check_eq("style guide: callout == the template's page-3 panel",
         _box["tpl-callout3"]["style"], _sg["callout"]["style"])
check_eq("style guide: callout fill == the template's",
         _box["tpl-callout3"]["fill"], _sg["callout"]["fill"])
check_eq("style guide: pull quote + topic colour == the template's",
         _box["tpl-pull3"]["style"],
         dict(_sg["pull_quote"]["style"], color=_topic))
check_eq("style guide: figure caption == the template's",
         _box["tpl-fig3"]["style"], _sg["figure_caption"]["style"])
# The two section headlines the template places (executive summary, page 4)
# must themselves agree — one reference style, not two near-misses.
check_eq("template: page-3 and page-4 headlines share one style",
         _box["tpl-h3"]["style"], _box["tpl-h4"]["style"])

# ---- placed elements are stamped ---------------------------------------
# The mobile release keys off data-placed, and the stamp is an explicit opt-in
# (see PLACED) rather than a match on the inline style, because the shape layer
# is absolutely positioned and must NEVER be released — it is the page's
# background, and in the flow it detaches from the page it belongs to. That
# makes a forgotten stamp on some future emitter the one failure mode worth a
# test: this renders one of everything this module pins and asserts that every
# `position:absolute` it produced is either stamped or the shape layer.
import re as _re                                             # noqa: E402

_placed_all = _layout({
    "page": {"w": 8.5, "h": 11},
    "positions": {"el.a": {"x": 1, "y": 1, "w": 2, "reserve": 0.4}},
    "boxes": [{"id": "b1", "page": 1, "x": 1, "y": 5, "w": 3, "md": "Note"},
              {"id": "b2", "page": 1, "x": 1, "y": 7, "w": 3, "md": "PDF",
               "act": "pdf"}],
    "tables": [{"id": "t1", "page": 1, "x": 1, "y": 9, "w": 3,
                "rows": [["a", "b"], ["c", "d"]], "header": True}],
    "shapes": [{"id": "s1", "page": 1, "kind": "rect", "x": 1, "y": 1,
                "w": 2, "h": 1, "fill": "#6B9E78", "z": "back"}],
})
_rendered = (_placed_all.layer(1) + _placed_all.attr("el.a")
             + _placed_all.text_boxes(1) + _placed_all.tables_html(1))
# Every tag carrying an inline position:absolute, paired with whether that same
# tag also carries the stamp. A <style> block's rules are not tags and are not
# matched — only real elements.
_pinned = [(t, "data-placed" in t) for t in _re.findall(r"<[a-zA-Z][^>]*>", _rendered)
           if "position:absolute" in t]
check_eq("everything pinned by inch is rendered", len(_pinned) > 3, True)
_unstamped = [t[:70] for t, ok in _pinned if not ok and "shape-layer" not in t]
check_eq("every pinned element carries the mobile-release stamp", _unstamped, [])
# The other half of the same contract, and the reason the stamp exists.
_layers = [t[:70] for t, ok in _pinned if ok and "shape-layer" in t]
check_eq("the shape layer is never stamped — it is the page's background",
         _layers, [])

# The release itself: emitted once, keyed to the sheet width, and absent from a
# report that pinned nothing (this module's byte-for-byte promise).
# A FRESH layout each time: the emitters are once-per-render, so reusing
# _placed_all (already rendered above) would test a spent flag and pass for
# the wrong reason.
def _fresh():
    return _layout({"page": {"w": 8.5, "h": 11},
                    "positions": {"el.a": {"x": 1, "y": 1, "w": 2}}})


check("the release rides out with the first layer()",
      _fresh().layer(1), "@media screen and (max-width:8.5in)")
check_eq("and once per render, not once per page",
         (lambda L: L.layer(1).count("@media screen") + L.layer(2).count("@media screen"))(_fresh()),
         1)
# The other half of the release: style() positions an element for a renderer
# and has nowhere to put a stamp, so an inline pin with no stamp must be caught
# too — and the shape layer, which is exactly that shape, must not be.
check("an element a renderer pinned itself is released as well",
      _fresh().mobile_css(), '.page [style*="position:absolute"]:not(.shape-layer)')
check("the breakpoint is the sheet, not a device width",
      _layout({"page": {"w": 12.5, "h": None},
               "positions": {"el.a": {"x": 1, "y": 1}}}).mobile_css(),
      "max-width:12.5in")
# The width a report was BUILT at counts as much as one File > Resize wrote.
# Reading only the override gave a 12.5in web page (rxkids) the 8.5in default,
# so its sheet stopped fitting at 1200px while the release waited for 816.
_wide = Layout(Path(_tempfile.mkstemp(suffix=".json")[1]), page=(12.5, 84))
_wide.positions = {"el.a": {"x": 1, "y": 1}}
check("a report BUILT wide gets its own breakpoint, with no override written",
      _wide.mobile_css(), "max-width:12.5in")
check_eq("a report that pinned nothing emits no release",
         _layout({"page": {"w": 8.5, "h": 11}}).layer(1).count("@media screen"), 0)


# ------------------------------------------------------------ docsync.check

# These guard the invariants two finished one-pagers each broke: an `endnotes`
# string built and never interpolated, so numbered markers rendered pointing at
# nothing. Each case below is the real defect, reduced. Every one was confirmed
# to FAIL against the pre-fix output before being trusted here.

def _errs(fn, html):
    return [str(p) for p in fn(html) if p.is_error]


def _warns(fn, html):
    return [str(p) for p in fn(html) if not p.is_error]


# -- citations
check("a marker with no anchor anywhere is an error",
      "\n".join(_errs(check_citations, '<p>x<sup><a href="#en1">1</a></sup></p>')),
      'no id="en1"')
check_eq("a marker whose anchor exists is clean",
         _errs(check_citations,
               '<p>x<sup><a href="#en1">1</a></sup></p><li id="en1">S</li>'),
         [])
check("a link to a missing anchor is an error",
      "\n".join(_errs(check_citations,
                      '<li id="en1">S</li><a href="#en9">9</a>'
                      '<sup><a href="#en1">1</a></sup>')),
      "#en9, which does not exist")
# The weaker half: it resolves in print, so it must not fail a build.
check("a bare, unlinked marker warns rather than failing",
      "\n".join(_warns(check_citations, '<sup>1</sup><li id="en1">S</li>')),
      "bare numeral")
check_eq("...and is not an error",
         _errs(check_citations, '<sup>1</sup><li id="en1">S</li>'), [])
# A superscript that is not a footnote marker belongs to the prose.
check_eq("an ordinal superscript is left alone",
         check_citations("<p>1<sup>st</sup> reading</p>"), [])

# -- unrendered markdown (C.t where C.html was needed)
check("bold that never became <b> is caught",
      "\n".join(_errs(check_markdown, "<p>**Method.** Census PUMS</p>")),
      "unrendered bold")
check_eq("rendered bold is clean",
         _errs(check_markdown, "<p><b>Method.</b> Census PUMS</p>"), [])
# The NUL-for-tag substitution exists for exactly this: two emphasis runs in a
# row must not be read as one bold span across the element boundary.
check_eq("asterisks in adjacent elements do not join",
         _errs(check_markdown, "<p><i>*</i><i>* not bold *</i><i>*</i></p>"), [])
check_eq("a url in an attribute is not a markdown link",
         _errs(check_markdown, '<a href="https://x.test/[a](b)">text</a>'), [])
check("an unresolved footnote ref is caught",
      "\n".join(_errs(check_markdown, "<p>see[^model]</p>")), "footnote ref")
check_eq("markdown inside <code> is a sample, not a defect",
         _errs(check_markdown, "<code>**bold**</code>"), [])

# -- svg bounds
_CLIP = ('<svg viewBox="0 0 100 54"><text y="54" font-size="11.5">'
         'Only the left-hand pool is reachable with federal dollars.'
         '</text></svg>')
check("a baseline whose descenders fall off the canvas is caught",
      "\n".join(_errs(check_svg_bounds, _CLIP)), "descenders are clipped")
# Without a descending glyph the same baseline is genuinely fine — faulting it
# would make the check cry wolf on every caption sitting on the last line.
check_eq("the same baseline with no descender is clean",
         _errs(check_svg_bounds,
               '<svg viewBox="0 0 100 54"><text y="54" font-size="11.5">'
               'months after birth</text></svg>'), [])
check("a rect past the canvas is caught",
      "\n".join(_errs(check_svg_bounds,
                      '<svg viewBox="0 0 100 54"><rect y="50" height="10"/></svg>')),
      "past the")
# The regression that made this check unusable at first: a naive non-greedy
# match paired the OUTER svg's viewBox with an inner icon's coordinates, so the
# editor's page-sized shape layer reported every 24-unit icon as overflowing.
check_eq("a nested svg does not report its coordinates against the outer box",
         check_svg_bounds(
             '<svg viewBox="0 0 8.5 11"><rect y="1" height="1"/>'
             '<svg x="1" y="2" viewBox="0 0 24 24" overflow="visible">'
             '<rect y="4" height="16"/></svg></svg>'),
         [])
check_eq("content the editor positions by hand is not policed",
         check_svg_bounds('<svg class="shape-layer" viewBox="0 0 8.5 11">'
                          '<rect y="10" height="4"/></svg>'), [])
check_eq("a transformed svg is skipped rather than guessed at",
         check_svg_bounds('<svg viewBox="0 0 100 54"><g transform="translate(0,-20)">'
                          '<rect y="50" height="20"/></g></svg>'), [])


# ------------------------------------------------------------- docsync.text
#
# Every rule in text.py keys off document STRUCTURE, never a project's class
# names — nine reports share this engine and each styles itself with its own
# prefix. So each case below is written as the shape it recognises, with class
# names that deliberately mean nothing, to keep it that way.

def _md(html, **kw):
    return dtext.extract(f"<section class=\"page\">{html}</section>", **kw)


def _blocks(html, variant=None):
    return dtext.pages_of(f'<section class="page">{html}</section>', variant)[0]


# -- the page is the unit
check_eq("each <section class='page'> is its own page",
         len(dtext.pages_of('<section class="page">a</section>'
                            '<section class="page">b</section>')), 2)
# A fragment or a web-only one-pager has no page sections. That is a shape to
# handle, not an error: the body is the page.
check_eq("a report with no page sections still yields its text",
         len(dtext.pages_of("<body><p>loose</p></body>")), 1)
check("...and keeps the text",
      dtext.extract("<body><p>loose prose</p></body>"), "loose prose")

# -- prose
check("headings keep their level", _md("<h3>Taxes</h3>"), "### Taxes")
check("bold survives as markdown", _md("<p><b>GET</b> is a tax</p>"), "**GET** is a tax")
check("list items become bullets", _md("<ul><li>one</li><li>two</li></ul>"), "- one\n- two")
check("ordered lists keep their numbers",
      _md("<ol><li>first</li><li>second</li></ol>"), "1. first\n2. second")
check("table rows are pipe-separated",
      _md("<table><tr><td>Total</td><td>$25.85 billion</td></tr></table>"),
      "Total | $25.85 billion")
# A footnote marker is a reference the reader may want to chase; an ordinal is
# part of the sentence. Same tag, so the digits have to decide.
check("a footnote marker is kept as a reference", _md("<p>x<sup>9</sup></p>"), "x[^9]")
check_eq("an ordinal superscript stays in the prose",
         _blocks("<p>1<sup>st</sup> reading</p>")[0]["text"], "1st reading")
# Regression: <br> became "\n" and the following text kept its indent, so a
# caption came out with a stray leading space on its second line.
check_eq("a line break does not leave a leading space",
         _blocks("<p>Figure 2.<br>\n   click a department</p>")[0]["text"],
         "Figure 2.\nclick a department")

# -- charts
# The numbers live in data-tip: the visible <text> of a bar chart is axis ticks
# and bare figures with nothing to attach them to.
check_eq("a chart is read from its tips, not its axis ticks",
         _blocks('<svg><text>$0B</text><rect data-tip="Transportation: $2.7B"/>'
                 '<text>$2,691</text></svg>')[0]["data"],
         [("Transportation", "$2.7B")])
# The tip doubles as the hover string, so it carries an instruction no reader
# of a text file can act on.
check_eq("an on-screen instruction is stripped from the value",
         _blocks('<svg><rect data-tip="Health: $203M · click for tracker link"/></svg>'
                 )[0]["data"], [("Health", "$203M")])
check_eq("a chart drawn without tips falls back to its labels",
         _blocks("<svg><text>JAN</text><text>FEB</text></svg>")[0]["labels"],
         ["JAN", "FEB"])
# <svg><title> is the accessible name of a picture, not a chart to mine.
check_eq("a named graphic is a picture, not a chart",
         _blocks("<svg><title>Appleseed logo</title><path/></svg>")[0],
         {"t": "img", "alt": "Appleseed logo"})

# -- variants
# A report that offers the reader two years renders both. Text has no toggle,
# so the picker's first option — the current year — wins.
_FY = ('<p><select><option value="2027">FY2027</option>'
       '<option value="2026">FY2026</option></select></p>'
       '<p data-fy="2027">new money</p><p data-fy="2026">old money</p>'
       '<p>always</p>')
check("the current year is kept", _md(_FY), "new money")
check_eq("...and the other year is dropped",
         "old money" in _md(_FY), False)
check("--fy picks the other year", _md(_FY, variant="2026"), "old money")
check("prose outside the toggle is never dropped", _md(_FY, variant="2026"), "always")
check("the picker itself collapses to the chosen year", _md(_FY), "FY2027")

# -- furniture
# A folio restates the page number in a few words at the page's edge. All three
# have to hold, or an ordinary short line gets eaten.
_FOLIO = ('<section class="page" data-page="6"><p>Real prose here.</p>'
          '<div>BUDGET PRIMER • 6</div></section>'
          '<section class="page" data-page="7"><p>More prose.</p>'
          '<div>BUDGET PRIMER • 7</div></section>')
check_eq("a running head that repeats across pages is dropped",
         "BUDGET PRIMER" in dtext.extract(_FOLIO), False)
check("...and the prose it sat under is kept",
      dtext.extract(_FOLIO), "Real prose here.")
# Recurrence IS the rule, so a lone page has nothing that has proven itself
# furniture — and guessing there would eat a real line.
check("a one-page report keeps its short edge line",
      dtext.extract('<section class="page" data-page="7"><p>Prose.</p>'
                    '<div>BUDGET PRIMER • 7</div></section>'), "BUDGET PRIMER")
check("a short line mid-page survives even with the page number in it",
      dtext.extract('<section class="page" data-page="7"><p>lead</p>'
                    '<p>Act 7 passed</p><div>BUDGET PRIMER • 7</div></section>'),
      "Act 7 passed")
check("a long line at the edge is prose, not a folio",
      dtext.extract('<section class="page" data-page="3">'
                    '<p>Consists of the State Senate and House of Representatives, '
                    'the Office of the Auditor, and 3 more bodies besides.</p>'
                    '</section>'), "State Senate")

# -- shapes that would otherwise come out as orphaned lines
# A badge beside a heading is often the figure a reader most wants; stacked on
# its own line it reads as a stray fragment.
check("a numbered heading keeps its number and its badge",
      _md('<div><span>1</span><h4>Tax investment profits</h4>'
          '<span>Up to $132M a year</span></div>'),
      "#### 1. Tax investment profits  [Up to $132M a year]")
# A legend: each child led by an empty inline element, the colour chip.
check("a chart key is joined onto one line",
      _md('<div><div><span></span>General Funds</div>'
          '<div><span></span>Special Funds</div></div>'),
      "KEY: General Funds | Special Funds")
# A label and the sentence it labels are one line on the page.
check("a label and its sentence stay together",
      _md("<div><span>DEC</span>The governor submits the budget.</div>"),
      "DEC — The governor submits the budget.")
check("a row of cells is joined, not stacked",
      _md("<div><span>Budget Basics</span><span>3</span></div>"),
      "Budget Basics  —  3")
# Regression: the logo sat in a wrapper div, so the img rule never saw it as a
# direct child and the picture vanished from the text entirely.
check("a picture inside a wrapper is still named",
      _md('<div class="lockup"><img src="logo.svg" alt="Appleseed logo"></div>'),
      "[image: Appleseed logo]")
check_eq("a decorative picture with no alt stays silent",
         _blocks('<div><img src="rule.svg" alt=""></div>'), [])

# -- what the reader only sees on screen
check("an expandable section is labelled and kept",
      _md("<details><summary>View all appropriations</summary>"
          "<p>$700M transit</p></details>"),
      'shown on the web version: "View all appropriations"')
check("...along with everything inside it",
      _md("<details><summary>More</summary><p>$700M transit</p></details>"),
      "$700M transit")
check_eq("script and style never reach the text",
         "hidden" in _md("<style>.x{color:hidden}</style>"
                         "<script>var hidden=1</script><p>shown</p>"), False)

# Real renderers are well-formed, but a text dump is not worth crashing over.
check("a stray close tag does not derail the walk",
      _md("<p>first</p></div><p>second</p>"), "second")


# ------------------------------------------------- editability coverage

# The classifier behind docsync.check's edit-mode pass. Three findings, each
# with a way OUT that must not be flagged: dead text (out: any hook), frozen
# SVG prose (out: data-slot on the <text> — the report2027 placeholder
# pattern), and the C() trap — prose in a movable wrapper with no slot (out:
# a data-slot anywhere above, or a panel-edited data-el namespace).
from docsync.check import _Coverage, check_editability     # noqa: E402
from docsync.registry import (Binding, Editor,             # noqa: E402
                              RegistryError, load_registry)
import tempfile                                            # noqa: E402


def _cov(html):
    c = _Coverage()
    c.feed(html)
    return c


check_eq("a bare digit is not exempt by length — it is exactly as dead as a longer number",
         _cov('<section class="page"><p>7</p></section>').dead_paged, ["7"])
check_eq("...but a single non-digit glyph (icon font, bullet) stays exempt",
         _cov('<section class="page"><i>&#9660;</i></section>').dead_paged, [])

_c = _cov('<section class="page"><p>Dead orphan sentence here.</p></section>')
check_eq("unhooked text in the sheet is dead",
         _c.dead_paged, ["Dead orphan sentence here."])
check_eq("slotted text is not dead",
         _cov('<section class="page"><p data-slot="k">Fine slotted sentence '
              'here.</p></section>').dead_paged, [])
# The one that bit rxkids-fiscal for real: page chrome must not drown the
# signal — with a .page present, only its inside is content.
_c = _cov('<div class="bar">Download the PDF right now please</div>'
          '<section class="page"><p data-slot="k">x y</p></section>')
check_eq("chrome outside the sheet is not dead", _c.dead_paged, [])
check_eq("...but a page-less document is scanned whole",
         _cov("<div><p>Loose sentence outside any sheet.</p></div>").dead_all,
         ["Loose sentence outside any sheet."])
check_eq("style and script text never count",
         _cov('<section class="page"><style>.x{color:red}</style>'
              '<script>var a=1</script><p data-slot="k">ok</p>'
              "</section>").dead_paged, [])
# Void-tag regression: an <img data-el> must not bless the text after it —
# HTMLParser never sends an endtag for a void element, so pushing it would
# leave its hook on the stack for the rest of the document.
_c = _cov('<section class="page"><img data-el="cover.logo">'
          "<p>Orphan sentence after the image.</p></section>")
check_eq("a void tag's hook does not leak onto later text",
         _c.dead_paged, ["Orphan sentence after the image."])

# SVG: data marks stay silent, sentences are frozen prose, a slotted <text>
# is editable and exempt.
check_eq("an svg data label is not frozen prose",
         _cov('<svg viewBox="0 0 8 2"><text font-size="12">$20.7M</text>'
              "</svg>").frozen_prose, [])
check_eq("an svg sentence is frozen prose",
         _cov('<svg viewBox="0 0 8 2"><text>Only the left pool is reachable '
              "today.</text></svg>").frozen_prose,
         ["Only the left pool is reachable today."])
check_eq("a slotted svg text is editable, not frozen",
         _cov('<svg viewBox="0 0 8 2"><text data-slot="why">Only the left '
              "pool is reachable today.</text></svg>").frozen_prose, [])
check_eq("tspans are judged as one sentence",
         _cov('<svg viewBox="0 0 8 2"><text><tspan>Only the left pool</tspan>'
              "<tspan>is reachable today.</tspan></text></svg>").frozen_prose,
         ["Only the left pool is reachable today."])

# The C() trap: movable wrapper, frozen words.
check_eq("prose in a movable wrapper with no slot is trapped",
         _cov('<section class="page"><div data-el="who.p1"><p>The governor '
              "submits the budget in December.</p></div></section>").trapped,
         ["The governor submits the budget in December."])
check_eq("a long unpunctuated heading is trapped too",
         len(_cov('<section class="page"><h1 data-el="hero.h1">Seven word '
                  "heading without any period here yes</h1></section>")
             .trapped), 1)
check_eq("...but a short movable label is not",
         _cov('<section class="page"><div data-el="badge">Fiscal estimate '
              "2028</div></section>").trapped, [])
check_eq("a slot inside the wrapper clears the trap (C.html pattern)",
         _cov('<section class="page"><div data-el="para.k"><p data-slot="k">'
              "The governor submits the budget in December.</p></div>"
              "</section>").trapped, [])
for ns, what in (("text.b12", "a layout text box"),
                 ("table.t3", "a table"),
                 ("endnote.model", "an endnote entry")):
    check_eq(f"{what} is panel-edited, never trapped",
             _cov(f'<section class="page"><div data-el="{ns}">The words here '
                  "are edited through their own panel.</div></section>")
             .trapped, [])
# data-fixed (C.derived): a declared tally is covered, not dead — and the
# declaration survives a re-tally, which an editability_ok string cannot.
check_eq("a declared derived value is not dead text",
         _cov('<section class="page"><span data-fixed="python -m x stats">'
              "71</span></section>").dead_paged, [])
check_eq("...nor trapped inside a movable wrapper",
         _cov('<section class="page"><div data-el="row.1">'
              '<span data-fixed="python -m x stats">Eleven submissions on '
              "four bills.</span></div></section>").trapped, [])
check_eq("...but an undeclared sibling in the same row still counts",
         _cov('<section class="page"><span data-fixed="m">71</span>'
              "<span>Undeclared label sitting here.</span></section>")
         .dead_paged, ["Undeclared label sitting here."])
# The one thing it must NOT excuse: a sentence drawn inside a graphic is a
# caption wherever its numbers came from, so frozen-prose still fires.
check_eq("a declared sentence inside an svg is still frozen prose",
         _cov('<svg viewBox="0 0 8 2"><text data-fixed="m">Only the left '
              "pool is reachable today.</text></svg>").frozen_prose,
         ["Only the left pool is reachable today."])

# Content.derived() is edit-mode scaffolding: the published build must carry
# none of it, exactly like slot_attr.
def _derived_attr(edit: bool) -> str:
    import tempfile as _tf
    from docsync.content import Content as _C
    with _tf.TemporaryDirectory() as td:
        p = Path(td) / "content.md"
        p.write_text("[[k]]\nx\n\n[[sources]]\n[s]: t — https://e.com\n")
        was = os.environ.get("DOCSYNC_EDIT")
        if edit:
            os.environ["DOCSYNC_EDIT"] = "1"
        else:
            os.environ.pop("DOCSYNC_EDIT", None)
        try:
            return _C(p).derived("python -m testimony stats")
        finally:
            os.environ.pop("DOCSYNC_EDIT", None)
            if was is not None:
                os.environ["DOCSYNC_EDIT"] = was

check("derived() names its source in edit mode",
      _derived_attr(True), 'data-fixed="python -m testimony stats"')
check_eq("derived() is silent in a published build", _derived_attr(False), "")

check_eq("the ds-textbox class is exempt whatever its id",
         _cov('<section class="page"><div class="ds-textbox" data-el="x.y">'
              "The words here are edited through their own panel.</div>"
              "</section>").trapped, [])

# ------------------------------------------------- editability registry

with tempfile.TemporaryDirectory() as _td:
    _reg = Path(_td) / "docsync.yml"
    _reg.write_text(
        "bindings:\n"
        "  - id: t-strict\n"
        "    content: content.md\n"
        "    editability: strict\n"
        '    editability_ok: ["chrome bit"]\n'
        "  - id: t-plain\n"
        "    content: content.md\n")
    _bs = {b.id: b for b in load_registry(_reg)}
    check_eq("editability parses", _bs["t-strict"].editability, "strict")
    check_eq("editability_ok parses", _bs["t-strict"].editability_ok,
             ["chrome bit"])
    check_eq("editability defaults to warn", _bs["t-plain"].editability,
             "warn")
    _reg.write_text("bindings:\n  - id: t-bad\n    content: c.md\n"
                    "    editability: loose\n")
    try:
        load_registry(_reg)
        FAILS.append("a bad editability value must not load")
    except RegistryError as e:
        check("a bad editability value names its options", str(e),
              "must be warn, strict or wip")

# ------------------------------------------------- check_editability, live

# End to end: a real subprocess build of a tiny renderer, classified and
# levelled exactly as docsync.check would for a registered binding.
_DIRTY = ('<section class="page"><p>Sad unwired sentence sits here.</p>'
          '<p data-slot="k">fine</p></section>')


def _binding(html, editability="warn", ok=(), broken=False):
    td = Path(tempfile.mkdtemp(prefix="ds-editability-"))
    r = td / "render.py"
    r.write_text("import os, pathlib, sys\n"
                 + ("sys.exit(1)\n" if broken else "")
                 + f"pathlib.Path(os.environ['DOCSYNC_OUT'])"
                 f".write_text({html!r})\n")
    return Binding(id="t", content=td / "c.md", editability=editability,
                   editability_ok=list(ok),
                   editor=Editor(render=r, engine=[], out=td / "out.html",
                                 dir=td))


_p = check_editability(_binding(_DIRTY))
check_eq("a warn binding warns", [(pr.level, pr.check) for pr in _p],
         [("warn", "editability")])
check("the finding names the string", str(_p[0]), "Sad unwired sentence")
_p = check_editability(_binding(_DIRTY, "strict"))
check_eq("a strict binding errors", [pr.is_error for pr in _p], [True])
check("...and points at editability_ok", str(_p[0]), "editability_ok")
check_eq("an accepted string is not a finding",
         check_editability(_binding(_DIRTY, "strict",
                                    ok=["Sad unwired sentence sits here."])),
         [])
_p = check_editability(_binding(_DIRTY, "strict", broken=True))
check_eq("a broken edit build fails a strict binding",
         [(pr.is_error, "build failed" in str(pr)) for pr in _p],
         [(True, True)])
check_eq("a binding with no editor has nothing to check",
         check_editability(Binding(id="t", content=Path("c.md"))), [])


# ------------------------------------------------------ docsync.docimport

from docsync import docimport as di                        # noqa: E402

# ---- the link itself -------------------------------------------------------

check_eq("a doc URL yields its id",
         di.parse_doc_id("https://docs.google.com/document/d/1AbC_dEfG-hIjKlMnOp/edit#heading=h.x"),
         "1AbC_dEfG-hIjKlMnOp")
check_eq("a copy link yields the same id",
         di.parse_doc_id("https://docs.google.com/document/d/1AbC_dEfG-hIjKlMnOp/edit?usp=sharing"),
         "1AbC_dEfG-hIjKlMnOp")
check_eq("a bare id is taken as one",
         di.parse_doc_id("1AbC_dEfG-hIjKlMnOp"), "1AbC_dEfG-hIjKlMnOp")


def _link_error(name, fn, expect):
    try:
        fn()
    except di.DocLinkError as e:
        if expect not in str(e):
            FAILS.append(f"{name}\n  want error containing: {expect!r}\n  got: {e}")
        return
    FAILS.append(f"{name}\n  expected DocLinkError containing {expect!r}, none raised")


_link_error("a spreadsheet URL with no /d/ is refused",
            lambda: di.parse_doc_id("https://docs.google.com/spreadsheets"),
            "no /d/<id>")
_link_error("random text is refused rather than fetched",
            lambda: di.parse_doc_id("my report"), "does not look like")
_link_error("an empty link is refused", lambda: di.parse_doc_id(""), "no link given")

_YML = """# a comment that must survive
bindings:
  - id: alpha
    content: projects/alpha/content.md
    # alpha's own note
    build: make alpha
  - id: beta
    doc: OLDDOCID_0123456789
    content: projects/beta/content.md
"""


def _yml(text=_YML):
    td = Path(tempfile.mkdtemp(prefix="ds-doclink-"))
    f = td / "docsync.yml"
    f.write_text(text)
    return f


_f = _yml()
di.set_doc("alpha", "NEWDOCID_0123456789", _f)
_out = _f.read_text()
check("linking inserts doc: under the id", _out,
      "  - id: alpha\n    doc: NEWDOCID_0123456789\n    content:")
check("...and leaves the comments alone", _out, "# a comment that must survive")
check("...and alpha's own note", _out, "# alpha's own note")
check("...and does not touch the other binding", _out, "doc: OLDDOCID_0123456789")

di.set_doc("beta", "REPLACED_0123456789", _f)
check("linking an already-linked binding replaces its line", _f.read_text(),
      "  - id: beta\n    doc: REPLACED_0123456789\n")
check_eq("...and does not add a second doc: line",
         _f.read_text().count("doc:"), 2)

di.set_doc("beta", "", _f)
check_eq("unlinking removes the line", _f.read_text().count("doc: REPLACED"), 0)
check("...and beta is still a binding", _f.read_text(), "- id: beta")
di.set_doc("beta", "", _f)          # idempotent: unlinking twice is not an error
check_eq("unlinking an unlinked binding is a no-op",
         _f.read_text().count("doc:"), 1)

_link_error("linking an id that is not in the registry is refused",
            lambda: di.set_doc("gamma", "X" * 20, _f), "no binding with id 'gamma'")

# ---- reading a doc ---------------------------------------------------------

check_eq("Docs' punctuation escapes are undone",
         di.clean("The 60\\-40 split \\(roughly\\)"), "The 60-40 split (roughly)")
check_eq("a doc with markers reports them",
         di.markers("[[a.p1]]\nfirst\n\n[[a.p2]]\nsecond"),
         {"a.p1": "first", "a.p2": "second"})
check_eq("prose reports no markers", di.markers("# Heading\n\nWords."), {})
check_eq("blocks split at headings",
         [(b["heading"], b["level"]) for b in di.blocks("Lead in.\n\n# One\n\na\n\n## Two\n\nb")],
         [("", 0), ("One", 1), ("Two", 2)])
check_eq("a bulleted list stays one paragraph",
         di.paragraphs("- one\n- two\n\nAfter."), ["- one\n- two", "After."])

# ---- what would land where -------------------------------------------------

_SLOTS = {
    "cover.title": "A Fairer Tax Code",
    "whopays.title": "Who pays",
    "whopays.p1": "old first",
    "whopays.p2": "old second",
    "fix.title": "What would fix it",
    "fix.p1": "old fix",
}

_p = di.proposals(
    "# A Fairer Tax Code\n\n"
    "## Who pays?\n\nThe bottom fifth pay most.\n\nThe top one percent pay least.\n\n"
    "## What would fix it\n\nA refundable credit.\n\n"
    "## An unrelated aside\n\nOrphan words.\n", _SLOTS)
check_eq("prose is matched by heading", _p["mode"], "prose")
_by = {r["key"]: r["md"] for r in _p["rows"] if r["key"]}
check_eq("a heading lands in its section's title slot",
         _by["whopays.title"], "Who pays?")
check_eq("the first paragraph lands in the first prose slot",
         _by["whopays.p1"], "The bottom fifth pay most.")
check_eq("the second lands in the second",
         _by["whopays.p2"], "The top one percent pay least.")
check_eq("the doc's own H1 finds the cover title",
         _by["cover.title"], "A Fairer Tax Code")
check_eq("a section matching nothing is reported, not dropped",
         [(r["how"], "Orphan words." in r["md"]) for r in _p["rows"] if not r["key"]],
         [("none", True)])

# Two headings sharing a word must not let the first one claim the other's
# slots — the pairing is taken best-first, not in document order.
_p = di.proposals("## What would fix it\n\nA credit.\n\n## Who pays\n\nThe bottom fifth.\n",
                  _SLOTS)
_by = {r["key"]: r["md"] for r in _p["rows"] if r["key"]}
check_eq("each heading claims its own group", _by["fix.p1"], "A credit.")
check_eq("...and the other claims its own", _by["whopays.p1"], "The bottom fifth.")

# A group's last slot soaks up the paragraphs that are left, so a section that
# grew in the doc arrives whole rather than truncated at the slot count.
_p = di.proposals("## Who pays\n\none\n\ntwo\n\nthree\n\nfour\n", _SLOTS)
_by = {r["key"]: r["md"] for r in _p["rows"] if r["key"]}
check_eq("extra paragraphs join the last slot of the group",
         _by["whopays.p2"], "two\n\nthree\n\nfour")

_p = di.proposals("[[whopays.p1]]\nfrom a marker\n\n[[nope.p9]]\nnowhere\n", _SLOTS)
check_eq("a doc with markers uses them", _p["mode"], "markers")
check_eq("...writing the slot each names",
         [(r["key"], r["md"], r["how"]) for r in _p["rows"]],
         [("whopays.p1", "from a marker", "marker")])
check_eq("...and naming the keys this report does not have",
         _p["unknown"], ["nope.p9"])

_p = di.proposals("Just prose.\n\nNo headings at all.\n", _SLOTS)
check_eq("a doc with no headings falls back to position",
         [r["how"] for r in _p["rows"]][:2], ["position", "position"])
check_eq("...skipping the title slots, which position cannot know about",
         [r["key"] for r in _p["rows"]][:2], ["whopays.p1", "whopays.p2"])
check_eq("an empty doc proposes nothing", di.proposals("   \n", _SLOTS)["rows"], [])


if FAILS:
    print("\n\n".join("FAIL: " + f for f in FAILS))
    print(f"\n{len(FAILS)} failed")
    raise SystemExit(1)
print("docsync: all checks passed")
