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


def _tmp_json():
    """A fresh temp .json path. mkstemp() also OPENS the file; its descriptor
    is closed here, or a few hundred layouts exhaust macOS's default limit of
    256 open files (OSError: Too many open files)."""
    import atexit
    import tempfile
    fd, name = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    atexit.register(lambda: os.path.exists(name) and os.unlink(name))
    return Path(name)

from docsync.content import ContentError                  # noqa: E402
from docsync.fetch import (FetchError, access_token,      # noqa: E402
                           service_account_email)
from docsync.content import paragraph, paragraphs         # noqa: E402
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
from docsync.layout import (Layout, LayoutError, shadow_css, feather_css,   # noqa: E402
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
      'style="margin:0;position:absolute;left:1.2in;top:3.4in;bottom:auto;box-sizing:border-box;width:5.0in;z-index:1"')
check_eq("an override beats the renderer's placement",
         moved.style("c.o", "left:9in;top:9in"),
         "margin:0;position:absolute;left:1.2in;top:3.4in;bottom:auto;box-sizing:border-box;width:5.0in;z-index:1")

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
# The inch came from a measured rect — padding and border included. Read as
# content width, a padded piece paints wider than it was dropped and the next
# drag writes the wider number back, exactly like the margin bug above it.
check("a sized element measures border-box", sized.attr("photo"),
      "box-sizing:border-box")
check_eq("no size, no box model to declare",
         "box-sizing" in _layout({"positions": {"p": {"x": 1, "y": 2}}}).attr("p"),
         False)
check_eq("a size does not imply reserved flow space", sized.spacer("photo"), "")
# The phone release frees what the engine pinned, and a rotation is part of
# that pin: `transform:none` alone no longer reaches it.
check("the phone release clears a rotation too",
      _layout({"positions": {"c.o": {"x": 1, "y": 2, "rot": 15}}}).mobile_css(),
      "rotate:none !important")
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
      '<div data-placed style="margin:0;position:absolute;left:1in;top:2in;bottom:auto;box-sizing:border-box;width:4in;z-index:1" '
      'data-reserve-for="para.a.b" data-reserve="0.5" data-reserve-w="4">'
      '<p>Text.</p></div>')
# A renderer that never calls spacer() still gets a strut: the element names
# what it reserves and the runtime puts one before it. Hidden gives it back.
check_eq("an unmoved element reserves nothing",
         "data-reserve" in _layout({}).attr("x"), False)
check_eq("a hidden moved element reserves nothing",
         "data-reserve" in _layout({"positions": {"x": {"x": 1, "y": 2, "reserve": 0.5}},
                                    "hidden": ["x"]}).attr("x"), False)
check("a reserving layout ships the runtime that makes the strut",
      _layout({"positions": {"x": {"x": 1, "y": 2, "reserve": 0.5}}})._anchor_once(),
      "data-reserve-for")
check("its vacated flow space stays held", moved_para.html("a.b"),
      '<div class="ds-spacer" data-spacer-for="para.a.b" data-anc-host="spacer:para.a.b" '
      'style="width:4in;height:0.5in;flex:0 0 auto"')
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

# ---- an emptied slot ------------------------------------------------------
# Deleting the last words of a paragraph left the block in content.md and
# NOTHING in the page: no element, so no way to click back into the slot and
# no way to get the text back but editing content.md by hand.
_empty_body = "[[a.b]]\n\n[[sources]]\n[x]: A. — https://a.gov\n"
os.environ["DOCSYNC_EDIT"] = "1"
try:
    _ec = _content(None, _empty_body).html("a.b")
    check("an emptied slot still has something to click", _ec, 'data-slot="a.b"')
    check("and says what it is", _ec, "empty — click and type")
finally:
    del os.environ["DOCSYNC_EDIT"]
check_eq("published, an emptied slot prints nothing",
         _content(None, _empty_body).html("a.b"), "")
check_eq("a style aimed at a slot that never rendered one is reported",
         nostyle.unknown_text_keys({"a.b"}), [])
# ---- graphics on a phone -----------------------------------------------------
# graphic() pins a figure at its inch width with a plain inline style: no
# position, no data-placed, so neither of mobile_css()'s selectors reaches it —
# and mobile_css() is not emitted at all for a report that pinned nothing. A
# 7.26in figure stayed ~697px wide inside a 375px page whose overflow is
# hidden, and the right-hand third was cut off with nothing to scroll to it.
from docsync import blocks as _blocks                           # noqa: E402

_SVG = '<svg viewBox="0 0 10 5"><rect width="10" height="5"/></svg>'
_gl = _layout({})
_g = _blocks.graphic(_gl, "fig.1", _SVG, w=7.26)
check("a graphic is drawn at its inch width", _g, "width:7.26in")
check("and a phone is told to let it go", _g,
      ".ds-graphic{width:auto !important;max-width:100% !important}")
check("released at the sheet's own width, as mobile_css measures it", _g,
      "@media screen and (max-width:8.5in)")
check_eq("once per document, however many figures",
         _blocks.graphic(_gl, "fig.2", _SVG, w=3.0).count("<style>"), 0)
# One that has been resized carries a position instead, and mobile_css
# releases that — so it emits nothing new and its bytes do not move.
_sized = _layout({"positions": {"fig.1": {"x": 1, "y": 1, "w": 4}}})
check_eq("a resized graphic needs no rule of its own",
         "<style>" in _blocks.graphic(_sized, "fig.1", _SVG, w=7.26), False)
check_eq("nor does one with no width at all",
         "<style>" in _blocks.graphic(_layout({}), "fig.1", _SVG, w=0), False)

# ---- slot markers ------------------------------------------------------------
# What counts as a marker, which the EDITOR's slotRe has to read the same way.
# It did not: `]]` followed by a space matched here and not there, so the editor
# appended a second block and the duplicate key refused the whole document.
from docsync.content import parse_content                       # noqa: E402


def _keys(text):
    f = Path(tempfile.mktemp(suffix=".md"))
    f.write_text(text)
    try:
        return list(parse_content(f))
    finally:
        f.unlink(missing_ok=True)


check_eq("a marker may end in a space", _keys("[[a.b]] \nx\n"), ["a.b"])
check_eq("a marker may end in a tab", _keys("[[a.b]]\t\nx\n"), ["a.b"])
check_eq("a marker may end CRLF", _keys("[[a.b]]\r\nx\r\n"), ["a.b"])
check_eq("a marker indented is NOT one", _keys("[[a.b]]\n  [[c.d]]\n"), ["a.b"])
check_eq("brackets inside a line are ordinary words",
         _keys("[[a.b]]\nsee [[c.d]] there\n"), ["a.b"])

# ---- sources ---------------------------------------------------------------
from docsync.content import Footnotes, md_inline, parse_sources  # noqa: E402


def _content_error_src(line):
    try:
        parse_sources(line)
    except ContentError as e:
        return str(e)
    return "no error"


# A link is optional: a book, an interview or a document somebody handed over
# has none, and the only way to cite one used to be to invent a URL.
check_eq("a source can have no link",
         parse_sources("[x]: A book, no link."), {"x": ("A book, no link.", "")})
check_eq("a link is still read when it is there",
         parse_sources("[x]: A. — https://a.gov"), {"x": ("A.", "https://a.gov")})
# The scheme is what tells a link from a citation's own em dash.
check_eq("an em dash inside the citation is not a link",
         parse_sources("[x]: Smith — Jones, an interview."),
         {"x": ("Smith — Jones, an interview.", "")})
check_eq("the LAST dash wins when there is a real link",
         parse_sources("[x]: A — B — https://c.gov"), {"x": ("A — B", "https://c.gov")})
check("a line with no id is still refused — one stray URL fragment used to "
      "fail the build hours later",
      _content_error_src("25-2027/"), "source line is not")
# An endnote with no link draws no <a>: an empty href is a link back to the page.
_fn_nolink = Footnotes({"x": ("A book.", "")})
_fn_nolink.resolve("<p>Words.[^x]</p>")
check_eq("an endnote with no link draws no anchor",
         "<a href" in _fn_nolink.endnotes_html(), False)
check("and still prints its citation", _fn_nolink.endnotes_html(), "A book.")
# A citation naming no source draws a red "?" in edit mode, and linkify must
# leave it alone: it read the digits of the marker's colour (#C0392B) as note
# numbers and replaced the "?" with inert "0392" (2026-10-22 audit).
_fn_missing = Footnotes({"x": ("A book.", "")})
os.environ["DOCSYNC_EDIT"] = "1"
try:
    _missing = _fn_missing.linkify(_fn_missing.resolve("<p>Words.[^nope] More.[^x]</p>"))
finally:
    os.environ.pop("DOCSYNC_EDIT", None)
check("a citation with no source keeps its ? marker through linkify", _missing, '>?</a>')
check_eq("and the marker's colour is never read as a note number",
         "<sup>0392</sup>" in _missing or ">0392<" in _missing, False)
check("while the real citation beside it still links", _missing, 'href="#en1"')
# A URL with brackets in it — the shape Wikipedia and many statute sites use.
# The link destination stopped at the first ')', so the href was cut short and
# the stray bracket was left sitting in the prose.
check("a link destination balances its brackets",
      md_inline("see [the law](https://en.wikipedia.org/wiki/Act_(2026)) now"),
      '<a href="https://en.wikipedia.org/wiki/Act_(2026)">the law</a> now')
check_eq("an unbalanced one still ends at the bracket, as CommonMark does",
         md_inline("[a](https://x.com/a)b)"),
         '<a href="https://x.com/a">a</a>b)')
# Emptying the sources block is a state to pass THROUGH, not a wall: it used
# to stop the whole draft building, and a draft that will not build is one you
# cannot get back into to fix.
os.environ["DOCSYNC_EDIT"] = "1"
try:
    check_eq("an empty sources block is allowed while editing",
             parse_sources(""), {})
finally:
    del os.environ["DOCSYNC_EDIT"]
check("but publishing still refuses it", _content_error_src(""), "is empty")

check_eq("a style aimed at an unstyleable slot is reported",
         _layout({"text": {"cip.body": {"size": 12}}}).unknown_text_keys({"a.b"}),
         ["cip.body"])

# ---- added sections -------------------------------------------------------
# extras() rendered its slots through block_html alone, so an added section was
# the one slot that dropped both of these. The type panel offered a size and
# the BUILD then refused the whole document ("styles text that cannot carry a
# style"), and a figure told to follow a section found no data-anc-host to
# measure against, so it never moved with the words.
_xbody = ("[[extra.p1.foo]]\n## Hello\n\nWords.\n\n[[extra.p1.bare]]\nPlain.\n\n"
          "[[sources]]\n[x]: A. — https://a.gov\n")
_xlay = _layout({"text": {"extra.p1.foo": {"size": 22}},
                 "positions": {"fig": {"x": 1, "y": 2,
                                       "anchor": {"to": "extra.p1.foo"}}}})
_xc = _content(_xlay, _xbody)
_x = _xc.extras("p1")
check("a styled section is rendered with its style", _x, '<div style="font-size:22px"')
check("a followed section carries the host the anchor runtime measures",
      _x, 'data-anc-host="extra.p1.foo"')
check_eq("a section nobody styled or followed is the bare bytes it always was",
         _x.endswith("<p>Plain.</p>"), True)
check_eq("styling an added section no longer refuses the build",
         _xlay.unknown_text_keys(_xc.styleable()), [])

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
      'style="margin:0;position:absolute;left:1in;top:2in;bottom:auto;z-index:1;background:#2F3E46"')
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
# Its own property, never inside `transform`: an inline transform replaces
# the class one a designed piece may wear (.lc-right's translateY(-50%)),
# and the piece then jumps by exactly that translate.
check("a rotated element turns in place", rotp.attr("c.o"), ";rotate:15deg")
check_eq("a rotation never lands in transform",
         "transform:rotate" in rotp.attr("c.o"), False)
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
check("a box rotates too", rotb.text_boxes(3), ";rotate:-10deg")
check("an opacity above one is caught",
      _layout_error({"positions": {"c.o": {"x": 1, "y": 2, "alpha": 1.5}}}),
      "not a fraction")
scaled = _layout({"positions": {"logo": {"x": 1, "y": 2, "scale": 1.4}}})
check("a scaled graphic carries its factor", scaled.attr("logo"), "scale(1.4)")
check_eq("a scale of exactly 1 emits nothing",
         "scale" in _layout({"positions": {"c.o": {"x": 1, "y": 2, "scale": 1}}}).attr("c.o"),
         False)
# Scale and flip stay in `transform` — the entrance keyframes animate the
# `scale` property, and a grown entrance would otherwise erase a graphic's
# own factor mid-flight.
check("scale rides transform, rotation rides its own property",
      _layout({"positions": {"g": {"x": 1, "y": 2, "rot": 20, "scale": 1.5}}}).attr("g"),
      "rotate:20deg;transform:scale(1.5)")
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
check("flip shares the one transform declaration with scale",
      imged.attr("p"), "rotate:10deg;transform:scale(-1,1)")
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
    f = _tmp_json()
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

# A report BUILT pageless (the web-page reports: our-mission, wfc-credits,
# rxkids, tfc-2027-priorities pass page=(12.5, None)) is the same as one whose
# layout.json says h:null — and its stylesheet must not carry the sentinel as a
# min-height (that was the 160in sheet: 11 letter pages, 8 empty).
def _built_pageless():
    t = _tmp_json(); t.write_text("{}")
    return Layout(t, page=(12.5, None))
_BP = _built_pageless()
check_eq("built pageless: page_h is the sentinel", _BP.page_h, PAGELESS_H)
check_eq("built pageless: the .page min-height is 0, not the sentinel", _BP.page_min_h, 0)
check("built pageless: the sheet is `Win auto`", _BP.page_style(), "@page{size:12.5in auto;margin:0}")
check_eq("a fixed page keeps its own min-height",
         _layout({"page": {"w": 8.5, "h": 11}}).page_min_h, 11.0)
check_eq("a layout override of a built-pageless report wins",
         _layout({"page": {"w": 8.5, "h": 11}}).page_h, 11.0)
# The shipped renderers, rebuilt: no min-height of 160in/84in/38.5in left.
import subprocess as _sp
for _slug in ("our-mission", "wfc-credits", "rxkids", "tfc-2027-priorities"):
    _out = Path(tempfile.mkdtemp()) / "out.html"
    _r = _sp.run([sys.executable, str(Path(__file__).resolve().parent.parent / "projects" / _slug / "render_report.py")],
                 env={**os.environ, "DOCSYNC_OUT": str(_out)}, capture_output=True, text=True)
    _h = _out.read_text() if _out.exists() else ""
    check(f"{_slug} builds", _r.stdout, "wrote")
    check(f"{_slug}: .page has no fixed height", _h, ".page{width:12.5in;min-height:0}")
    check(f"{_slug}: the print sheet is 12.5in wide with auto height", _h, "@page{size:12.5in auto")
    if "min-height:160in" in _h or "min-height:84.0in" in _h or "min-height:38.5in" in _h:
        FAILS.append(f"{_slug}: a fixed sheet height is still in the stylesheet")
    if _slug == "tfc-2027-priorities" and "size: letter" in _h:
        FAILS.append("tfc prints on its own letter @page again; it is pageless")
    if _slug == "rxkids" and "break-before: page" in _h:
        FAILS.append("rxkids forces a page break between tabs; a pageless sheet runs on")

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

# ---- charts: a signed axis, a stated scale, formatted numbers, two-line
# ---- labels, and tooltips ---------------------------------------------------
# The bug this set exists for: a chart whose data went below zero drew that
# bar at height ZERO while its own data label read the negative number. The
# chart contradicted itself, silently, and every budget figure that shows a
# cut or a year-over-year change is one of these.
import re as _re
from docsync.layout import chart_svg as _csvg


def _bars(svg):
    return [(float(m.group(1)), float(m.group(2)))
            for m in _re.finditer(r'<rect class="ds-cbar"[^>]*?y="([\d.-]+)"'
                                  r'[^>]*?height="([\d.-]+)"', svg)]


def _texts(svg):
    return _re.findall(r'>([^<>]+)</text>', svg)


_neg = {"type": "bar", "labels": ["A", "B", "C"],
        "series": [{"name": "chg", "data": [120, -45, 60]}], "values": True}
_negsvg = _csvg(dict(_neg), 0, 0, 4, 2.5)
_nb = _bars(_negsvg)
check_eq("chart negatives: the negative bar is DRAWN, not flattened to nothing",
         _nb[1][1] > 0, True)
# A: 120 up from zero, B: -45 down from zero, C: 60 up. So A and C end where B
# starts — that shared edge IS the zero line.
check_eq("chart negatives: positive bars end on the same line the negative starts",
         round(_nb[0][0] + _nb[0][1], 3) == round(_nb[1][0], 3)
         and round(_nb[2][0] + _nb[2][1], 3) == round(_nb[1][0], 3), True)
check_eq("chart negatives: the bar below zero is proportional to its value",
         abs(_nb[1][1] / _nb[0][1] - 45 / 120) < 0.005, True)
check("chart negatives: the axis reaches below zero", _negsvg, ">-50<")
check_eq("chart negatives: a label that says -45 now sits on a bar that shows it",
         "-45" in _texts(_negsvg), True)
# All-positive data must be untouched by any of it.
_pos = _csvg({"type": "bar", "labels": ["A"], "series": [{"name": "s", "data": [10]}]},
             0, 0, 4, 2.5)
check_eq("chart: an all-positive chart still starts its axis at zero",
         _texts(_pos)[0], "0")

_axis = _csvg(dict(_neg, axisMin=-100, axisMax=200, axisTicks=7), 0, 0, 4, 2.5)
check_eq("chart axis: pinned ends and a tick count are obeyed",
         [t for t in _texts(_axis) if _re.match(r"^-?[\d,]+$", t)][:7],
         ["-100", "-50", "0", "50", "100", "150", "200"])
check_page_raises("chart axis: an inside-out pair is refused",
                  {"positions": {}, "shapes": [
                      {"id": "c", "page": 1, "kind": "chart", "x": 1, "y": 1,
                       "w": 2, "h": 2,
                       "chart": {"type": "bar", "labels": ["A"],
                                 "series": [{"name": "s", "data": [1]}],
                                 "axisMin": 5, "axisMax": 2}}]},
                  "must be above axisMin")

_fmt = _csvg({"type": "bar", "labels": ["A"],
              "series": [{"name": "s", "data": [5_000_000_000]}],
              "values": True, "format": {"prefix": "$", "scale": "B", "decimals": 0},
              "labelFormat": {"decimals": 2}}, 0, 0, 4, 2.5)
check("chart numbers: the axis carries the prefix and the scale", _fmt, ">$0B<")
check("chart numbers: and reaches the top of the scale the same way", _fmt, ">$5B<")
check("chart numbers: a data label may hold more precision than the axis",
      _fmt, ">$5.00B<")
# Ticks that round to the same string say nothing. 0 - 2.5B in five steps at
# zero decimals is "$0B $1B $1B $2B $3B" — two ticks reading the same thing
# and a scale that looks mis-drawn — so the precision is raised until they
# are distinct, which is what asking for billions meant.
_dedup = [t for t in _texts(_csvg(
    {"type": "bar", "labels": ["A"],
     "series": [{"name": "s", "data": [2_440_000_000]}],
     "format": {"prefix": "$", "scale": "B", "decimals": 0}}, 0, 0, 4, 2.5))
    if t.startswith("$")]
check_eq("chart numbers: ticks never collapse into the same label",
         _dedup, ["$0.0B", "$0.6B", "$1.2B", "$1.9B", "$2.5B"])

_stk = _csvg({"type": "stacked-bar", "labels": ["A"],
              "series": [{"name": "p", "data": [30]}, {"name": "q", "data": [20]}],
              "values": True}, 0, 0, 4, 2.5)
check_eq("chart stacked: each segment now carries its own value label",
         sorted(t for t in _texts(_stk) if t in ("30", "20")), ["20", "30"])

_two = _csvg({"type": "bar", "labels": ["Second 20%\n$21,900-$44,200", "Top 1%"],
              "series": [{"name": "s", "data": [13.7, 10.1]}]}, 0, 0, 4, 2.5)
check_eq("chart labels: a break in a label becomes two drawn lines",
         _re.findall(r"<tspan[^>]*>([^<]+)</tspan>", _two),
         ["Second 20%", "$21,900-$44,200"])
check_eq("chart labels: a single-line label draws no tspan at all",
         "<tspan" in _pos, False)
_wrap = _csvg({"type": "bar", "labels": ["Department of Human Services", "Health"],
               "series": [{"name": "s", "data": [1, 2]}], "wrapLabels": True},
              0, 0, 4, 2.5)
check_eq("chart labels: a long name wraps rather than colliding with its neighbour",
         len(_re.findall(r"<tspan", _wrap)) >= 2, True)

check_eq("chart tooltips: off unless asked for", "data-tip" in _pos, False)
check("chart tooltips: on, a bar says what it is and what it is worth",
      _csvg(dict(_neg, tips=True), 0, 0, 4, 2.5), 'data-tip="B: -45"')
check_page_raises("chart: a made-up number-format field is refused",
                  {"positions": {}, "shapes": [
                      {"id": "c", "page": 1, "kind": "chart", "x": 1, "y": 1,
                       "w": 2, "h": 2,
                       "chart": {"type": "bar", "labels": ["A"],
                                 "series": [{"name": "s", "data": [1]}],
                                 "format": {"currency": "USD"}}}]},
                  "not a number-format field")

_pie = _csvg({"type": "pie", "labels": ["GET", "Income"],
              "series": [{"name": "s", "data": [4_100_000_000, 2_900_000_000]}],
              "values": True, "sliceLabel": "both",
              "format": {"prefix": "$", "scale": "B", "decimals": 1}}, 0, 0, 4, 2.5)
check("chart pie: a slice can show its value, not only its share", _pie, "$4.1B")
check_eq("chart pie: value and percent land on two lines",
         "(58.6%)" in _re.findall(r"<tspan[^>]*>([^<]+)</tspan>", _pie), True)

# ---- charts: the InDesign gap, closed ---------------------------------------
# Everything below is what CHART_PARITY.md listed as "still sends someone back
# to InDesign". Each is opt-in: a chart that names none of these keys renders
# byte-for-byte as it did, which is checked by comparing 20,174 old-vs-new
# renders, not here.

# 1. Per-point colour. Colour was per SERIES, so "highlight this year" — the
# single commonest ask of a budget chart — was impossible, and the figure got
# drawn by hand instead.
_hl = _csvg({"type": "bar", "labels": ["FY25", "FY26", "FY27"],
             "series": [{"name": "Total", "data": [3, 4, 5]}],
             "colors": ["#CAD2C5", "#CAD2C5", "#6B9E78"]}, 0, 0, 4, 2.5)
check_eq("chart colour: one bar can be picked out of a single-series chart",
         _re.findall(r'<rect class="ds-cbar"[^>]*fill="(#[0-9A-F]{6})"', _hl),
         ["#CAD2C5", "#CAD2C5", "#6B9E78"])
# With two series the colour means the SERIES again — a legend has to be true.
_two_c = _csvg({"type": "bar", "labels": ["A", "B"],
                "series": [{"name": "x", "data": [1, 2], "color": "#111111"},
                           {"name": "y", "data": [2, 1], "color": "#222222"}],
                "colors": ["#FF0000", "#00FF00"]}, 0, 0, 4, 2.5)
check_eq("chart colour: with two series the chart-level colours stay out of it",
         sorted(set(_re.findall(r'<rect class="ds-cbar"[^>]*fill="(#[0-9A-Fa-f]{6})"',
                                _two_c))), ["#111111", "#222222"])
# A series may still colour its own points, which is the general form.
_ptc = _csvg({"type": "bar", "labels": ["A", "B"],
              "series": [{"name": "x", "data": [1, 2], "color": "#111111",
                          "colors": [None, "#FF0000"]},
                         {"name": "y", "data": [2, 1], "color": "#222222"}]},
             0, 0, 4, 2.5)
check("chart colour: a series can override one of its own points",
      _ptc, 'fill="#FF0000"')

# 2. Reference lines.
_rule = _csvg({"type": "bar", "labels": ["A", "B"],
               "series": [{"name": "s", "data": [8, 19]}],
               "rules": [{"value": 15, "label": "Target", "color": "#B4432F"},
                         {"value": 10, "label": True, "color": "#354F52"}]},
              0, 0, 4, 2.5)
check("chart rules: a target line is drawn across the plot", _rule, '#B4432F')
check("chart rules: and says what it is", _rule, ">Target</text>")
# In the RULE's own ink, not the axis's — "10" alone is also a tick label on
# this scale, so a looser check would pass against an engine drawing no rules.
check("chart rules: label true prints the value itself",
      _rule, 'fill="#354F52" font-weight="600">10</text>')
check_eq("chart rules: off the scale is not drawn at the edge of it",
         "#B4432F" in _csvg({"type": "bar", "labels": ["A"],
                             "series": [{"name": "s", "data": [8]}],
                             "rules": [{"value": 9e9, "color": "#B4432F"}]},
                            0, 0, 4, 2.5), False)
check_page_raises("chart rules: a line with no value is refused",
                  {"positions": {}, "shapes": [
                      {"id": "c", "page": 1, "kind": "chart", "x": 1, "y": 1,
                       "w": 2, "h": 2,
                       "chart": {"type": "bar", "labels": ["A"],
                                 "series": [{"name": "s", "data": [1]}],
                                 "rules": [{"label": "Target"}]}}]},
                  "a reference line needs a value")

# 3. Legend position. Bottom, spread across the whole width, was the only
# option — so long names collided with nowhere to go.
_lr = _csvg({"type": "bar", "labels": ["A", "B"],
             "series": [{"name": "General funds", "data": [1, 2]},
                        {"name": "Special funds", "data": [2, 1]}],
             "legend": True, "legendPos": "right"}, 0, 0, 4, 2.5)
_lb = _csvg({"type": "bar", "labels": ["A", "B"],
             "series": [{"name": "General funds", "data": [1, 2]},
                        {"name": "Special funds", "data": [2, 1]}],
             "legend": True}, 0, 0, 4, 2.5)
check_eq("chart legend: down the right, the two keys share an x",
         len(set(_re.findall(r'<rect x="([\d.]+)"[^>]*rx="0\.02', _lr))), 1)
check_eq("chart legend: along the bottom, they do not", _lr == _lb, False)
# The packed row only replaces the even spread where the even spread collided.
_wide = ["Transportation", "Formal Education", "Economic Development"]
_lp = _csvg({"type": "pie", "labels": _wide,
             "series": [{"name": "s", "data": [3, 2, 1]}], "legend": True},
            0, 0, 4, 2.5)
check_eq("chart legend: names too wide for an even share wrap to a second row",
         len(set(_re.findall(r'<text x="[\d.]+" y="([\d.]+)"[^>]*font-size="0\.1250"[^>]*>'
                             r'(?:Transportation|Formal Education|Economic Development)',
                             _lp))) > 1, True)

# 4. Axis titles — said with a floating text box until now, which did not move
# when the chart did.
_at = _csvg({"type": "bar", "labels": ["A"], "series": [{"name": "s", "data": [1]}],
             "axisTitle": "Millions of dollars", "catTitle": "Fiscal year"},
            0, 0, 4, 2.5)
check("chart axis titles: the value axis is named, turned on its side",
      _at, "rotate(-90")
check("chart axis titles: and the category axis along the foot",
      _at, ">Fiscal year</text>")
check_eq("chart axis titles: a treemap has no axis to name",
         "Fiscal year" in _csvg({"type": "treemap", "labels": ["A"],
                                 "series": [{"name": "s", "data": [1]}],
                                 "catTitle": "Fiscal year"}, 0, 0, 4, 2.5),
         False)

# 5. Area, 100%-stacked, and combo with a secondary axis — between them, what
# fig_obligated is made of.
_area = _csvg({"type": "stacked-area", "labels": ["2019", "2020", "2021"],
               "series": [{"name": "a", "data": [1, 2, 3]},
                          {"name": "b", "data": [2, 2, 2]}]}, 0, 0, 4, 2.5)
check("chart area: a band is a filled polygon, not a line", _area, "<polygon")
check_eq("chart area: two series, two bands",
         len(_re.findall(r"<polygon", _area)), 2)
check("chart area: the first label sits at the plot's own left edge",
      _area, 'text-anchor="start"')
_pct = _csvg({"type": "stacked-bar", "labels": ["A", "B"],
              "series": [{"name": "x", "data": [1, 3]},
                         {"name": "y", "data": [3, 1]}],
              "stackPct": True, "values": True,
              "format": {"decimals": 0}}, 0, 0, 4, 2.5)
check_eq("chart 100%: every column is read as a share of its own total",
         sorted(set(_re.findall(r'fill="#fff">(\d+)</text>', _pct))), ["25", "75"])
_combo = _csvg({"type": "bar", "labels": ["A", "B"],
                "series": [{"name": "Nominal", "data": [3, 4]},
                           {"name": "Real", "type": "line", "axis": "right",
                            "data": [100, 92], "color": "#354F52"}],
                "axis2Min": 80, "axis2Max": 110}, 0, 0, 4, 2.5)
check("chart combo: the line series is drawn as a line", _combo, "<polyline")
check_eq("chart combo: and takes no bar slot — one series of bars, two bars",
         len(_re.findall(r'<rect class="ds-cbar"', _combo)), 2)
check("chart combo: the right-hand scale is labelled too", _combo, ">110</text>")
check_page_raises("chart combo: a series cannot be drawn as something invented",
                  {"positions": {}, "shapes": [
                      {"id": "c", "page": 1, "kind": "chart", "x": 1, "y": 1,
                       "w": 2, "h": 2,
                       "chart": {"type": "bar", "labels": ["A"],
                                 "series": [{"name": "s", "data": [1],
                                             "type": "sparkline"}]}}]},
                  "expected one of bar, line, area")

# 6. Category label rotation — the last resort when wrapping and shrinking
# have run out.
_rot = _csvg({"type": "bar", "labels": ["Department of Human Services", "Health"],
              "series": [{"name": "s", "data": [1, 2]}], "labelAngle": -45},
             0, 0, 4, 2.5)
check("chart labels: a turned label carries its own rotation", _rot, "rotate(-45")
check("chart labels: and anchors at the end, so it leans away from its tick",
      _rot, 'text-anchor="end"')

# 7. Gap width. Hard-coded at slot * 0.78 since the engine was written.
_fat = _csvg({"type": "bar", "labels": ["A"], "series": [{"name": "s", "data": [1]}],
              "barGap": 0.05}, 0, 0, 4, 2.5)
_thin = _csvg({"type": "bar", "labels": ["A"], "series": [{"name": "s", "data": [1]}],
               "barGap": 0.6}, 0, 0, 4, 2.5)
_bw = lambda svg: float(_re.search(r'<rect class="ds-cbar"[^>]*width="([\d.]+)"', svg).group(1))
check_eq("chart bars: a narrower gap makes a wider bar", _bw(_fat) > _bw(_thin), True)
check_page_raises("chart bars: a gap wider than the slot is refused",
                  {"positions": {}, "shapes": [
                      {"id": "c", "page": 1, "kind": "chart", "x": 1, "y": 1,
                       "w": 2, "h": 2,
                       "chart": {"type": "bar", "labels": ["A"],
                                 "series": [{"name": "s", "data": [1]}],
                                 "barGap": 2}}]},
                  "expected 0-0.9")

# A row chart's names live in a fixed gutter, anchored at its right edge, and
# nothing wraps them: anything over about six characters used to run off the
# left of the drawing and lose its first letters.
_row = _csvg({"type": "row", "labels": ["Education", "Health"],
              "series": [{"name": "s", "data": [1, 2]}]}, 0, 0, 4, 2.5)
_lx = float(_re.search(r'<text x="([\d.]+)"[^>]*text-anchor="end"[^>]*>Education',
                       _row).group(1))
check_eq("chart rows: the gutter is measured from the names, so they fit in it",
         _lx > 0.58, True)


# 9. Chart styles — a saved LOOK, applied to the next chart. Stored on the
# document, never read by the renderer: applying a style writes its keys onto
# the chart, so a published page cannot depend on a definition still existing.
check_page_raises("chart style: a look cannot carry the figure's own numbers",
                  {"positions": {}, "shapes": [],
                   "chartStyles": {"House": {
                       "series": [{"name": "s", "data": [1]}]}}},
                  "not part of a chart's look")
check_page_raises("chart style: nor its title, which is about one figure",
                  {"positions": {}, "shapes": [],
                   "chartStyles": {"House": {"title": "Figure 2"}}},
                  "not part of a chart's look")
check_page_raises("chart style: a colour in a look is checked like any other",
                  {"positions": {}, "shapes": [],
                   "chartStyles": {"House": {"labelColor": "teal"}}},
                  "labelColor")
check_page_raises("chart style: so is a legend position",
                  {"positions": {}, "shapes": [],
                   "chartStyles": {"House": {"legendPos": "middle"}}},
                  "legendPos: expected one of")
_styled = _layout({"positions": {}, "shapes": [], "chartStyles": {"House": {
    "legend": True, "legendPos": "right", "labelColor": "#2F3E46",
    "format": {"prefix": "$", "scale": "M"}, "barGap": 0.3,
    "seriesColors": ["#6B9E78", "#354F52"]}}})
check_eq("chart style: a whole look validates and survives the load",
         _styled.chart_styles["House"]["seriesColors"], ["#6B9E78", "#354F52"])


# ---- named text styles: the stylesheet -------------------------------------
# layout.text is one style per SLOT, so a report's type was set slot by slot
# and taking body copy from 11px to 10.5 meant finding every slot anyone had
# ever touched. A named style is applied by reference, so there is one place
# to change. The rule everywhere below: the nearer the author, the stronger.
from docsync.layout import resolve_style as _rs                # noqa: E402

_S = {"Body": {"font": "Manrope", "size": 14, "weight": 400},
      "Body small": {"from": "Body", "size": 12},
      "Caption": {"from": "Body small", "weight": 700}}
check_eq("text style: a slot wearing one gets all of it",
         _rs({"use": "Body"}, _S),
         {"font": "Manrope", "size": 14, "weight": 400})
check_eq("text style: a style inherits, and overrides only what it names",
         _rs({"use": "Body small"}, _S),
         {"font": "Manrope", "size": 12, "weight": 400})
check_eq("text style: inheritance is a chain, not one step",
         _rs({"use": "Caption"}, _S),
         {"font": "Manrope", "size": 12, "weight": 700})
check_eq("text style: the slot's own keys beat the style — a style is a "
         "starting point, not a cage",
         _rs({"use": "Body", "size": 30}, _S)["size"], 30)
check_eq("text style: no style named is exactly what it always was",
         _rs({"size": 11}, _S), {"size": 11})
# Both of these run in the render path and under Pyodide for a live preview,
# where raising would blank the page. Load-time validation is where they are
# refused; here they resolve to what is left.
check_eq("text style: a name that does not exist resolves to the rest",
         _rs({"use": "Nope", "size": 11}, _S), {"size": 11})
check_eq("text style: a loop stops instead of recursing",
         _rs({"use": "L"}, {"L": {"from": "L", "size": 9}}), {"size": 9})

_ts = _layout({"positions": {}, "shapes": [], "textStyles": _S,
               "text": {"a.p1": {"use": "Body small"},
                        "a.p2": {"use": "Body", "size": 30}}})
check_eq("text style: the slot's CSS is the resolved style",
         _ts.text_style("a.p1"),
         "font-family:'Manrope';font-size:12px;font-weight:400")
check_eq("text style: an override reaches the CSS too",
         "font-size:30px" in _ts.text_style("a.p2"), True)
# A slot that says only `use` names no font of its own, so a font_link that
# read the slot would leave the family to be FAKED in the published page while
# looking right in the editor, which loads every family.
check("text style: a family named only by a style is still fetched",
      _ts.font_link(), "Manrope")

check_page_raises("text style: a `use` must name a style that exists",
                  {"positions": {}, "shapes": [],
                   "text": {"a": {"use": "Body"}}},
                  "no text style called 'Body'")
check_page_raises("text style: and so must a `from`",
                  {"positions": {}, "shapes": [],
                   "textStyles": {"A": {"from": "B"}}},
                  "inherits from 'B', which is not a text style")
check_page_raises("text style: a chain cannot eat itself",
                  {"positions": {}, "shapes": [],
                   "textStyles": {"A": {"from": "B"}, "B": {"from": "A"}}},
                  "inherits from itself")
check_page_raises("text style: a style inherits with 'from', not 'use'",
                  {"positions": {}, "shapes": [],
                   "textStyles": {"A": {"size": 14}, "B": {"use": "A"}}},
                  "inherits with 'from', not 'use'")
# The RESOLVED style is what reaches the page, so that is what has to clear
# the legibility floor: wearing a style is not a way round it.
check_page_raises("text style: a style under the legibility floor is refused",
                  {"positions": {}, "shapes": [],
                   "textStyles": {"Tiny": {"size": 4}}},
                  "below the 7.875pt legibility floor")
check_page_raises("text style: and so is a slot that overrides one down to it",
                  {"positions": {}, "shapes": [],
                   "textStyles": {"Body": {"size": 14}},
                   "text": {"a": {"use": "Body", "size": 4}}},
                  "below the 7.875pt legibility floor")
check_page_raises("text style: a text box's style is held to the same rule",
                  {"positions": {}, "shapes": [],
                   "boxes": [{"id": "b", "page": 1, "x": 1, "y": 1, "w": 2,
                              "md": "hi", "style": {"use": "Nope"}}]},
                  "no text style called 'Nope'")


# ---- object styles: the stylesheet for things that are not text -------------
# The same argument as text styles, for shapes, text boxes and tables: a pull
# quote is a fill, a padding, a rule and a text style, and the fourth one was
# built by hand like the first. A style names its kind because the three are
# three key sets; an object says `use` and its own keys still win.
from docsync.layout import resolve_object as _ro                # noqa: E402

_OS = {"Callout": {"kind": "box", "fill": "#FFF6D8", "pad": 0.15, "radius": 4,
                   "border": {"color": "#6B9E78", "w": 3, "sides": "left"},
                   "style": {"use": "Body"}},
       "Callout tight": {"kind": "box", "from": "Callout", "pad": 0.06},
       "Rule": {"kind": "shape", "fill": "none", "stroke": "#2F3E46", "sw": 0.02,
                "dash": [0.08, 0.05]},
       "Data": {"kind": "table", "border": {"w": 1, "color": "#6E7A7E"},
                "headerFill": "#6E7A7E", "headerColor": "#FFFFFF",
                "band": "#F1F4F2", "header": True}}
_box = {"id": "b", "page": 1, "x": 1, "y": 1, "w": 3, "md": "hi", "use": "Callout"}
check_eq("object style: a box wearing one gets the whole look, never the meta",
         _ro(_box, _OS),
         {"id": "b", "page": 1, "x": 1, "y": 1, "w": 3, "md": "hi",
          "fill": "#FFF6D8", "pad": 0.15, "radius": 4,
          "border": {"color": "#6B9E78", "w": 3, "sides": "left"},
          "style": {"use": "Body"}})
check_eq("object style: inherits, and overrides only what it names",
         _ro(dict(_box, use="Callout tight"), _OS)["pad"], 0.06)
check_eq("object style: the box's own keys beat the style",
         _ro(dict(_box, fill="#FFFFFF"), _OS)["fill"], "#FFFFFF")
check_eq("object style: the TEXT style merges one level down — a box's own "
         "size on top of the style's Body is a 14px Body, not a lost Body",
         _ro(dict(_box, style={"size": 14}), _OS)["style"],
         {"use": "Body", "size": 14})
_plain = {"id": "b", "page": 1, "x": 1, "y": 1, "w": 3, "md": "hi"}
check_eq("object style: an object wearing nothing is returned AS ITSELF",
         _ro(_plain, _OS) is _plain, True)
check_eq("object style: a name that does not exist resolves to the rest",
         _ro(dict(_plain, use="Nope"), _OS), _plain)

_ts2 = {"Body": {"font": "Manrope", "size": 14}}
_ol = _layout({"positions": {}, "textStyles": _ts2, "objectStyles": _OS,
               "shapes": [{"id": "s", "page": 1, "kind": "line", "x": 1, "y": 1,
                           "w": 3, "h": 0, "use": "Rule"}],
               "boxes": [dict(_box, style={"size": 18}),
                         {"id": "c", "page": 1, "x": 1, "y": 5, "w": 6,
                          "md": "two columns", "cols": 2, "gap": 0.25,
                          "blend": "multiply"}],
               "tables": [{"id": "t", "page": 1, "x": 1, "y": 7, "w": 5,
                           "rows": [["a", "b"], ["1", "2"], ["3", "4"]],
                           "use": "Data"}]})
_lay = _ol.layer(1)
check("object style: a shape draws the stroke its style gave it",
      _lay, 'stroke="#2F3E46" stroke-width="0.02"')
check("object style: and the dash", _lay, 'stroke-dasharray="0.08 0.05"')
_bx = _ol.text_boxes(1)
check("object style: a box gets the style's fill", _bx, "background:#FFF6D8")
check("object style: its padding replaces the fill's default breathing room",
      _bx, "padding:.08in .12in;border-radius:8px;padding:0.15in;border-radius:4px")
check("object style: a left rule is one edge, not four",
      _bx, "border-left:3px solid #6B9E78")
# A style can bring padding and a border of its own; both have to sit INSIDE
# the stored width, which is the rect the editor measured around the box.
check("object style: pad and rule stay inside the box's own width",
      _bx, "box-sizing:border-box")
check("object style: the merged text style reaches the CSS — the style's "
      "font and the box's own size", _bx, "font-family:'Manrope';font-size:18px")
check("columns in one box: CSS sets them", _bx, "column-count:2;column-gap:0.25in")
check("blend: a box can multiply into what is under it", _bx, "mix-blend-mode:multiply")
check("columns in one box: a phone gets one column back",
      _ol.mobile_css(), ".ds-textbox{column-count:auto !important}")
_tb = _ol.tables_html(1) if hasattr(_ol, "tables_html") else _ol.table_boxes(1)
check("object style: a table's header band comes from its style",
      _tb, "background:#6E7A7E")
check("object style: and so does the banding", _tb, "background:#F1F4F2")
check("object style: the style's `header: true` makes the first row a <th>",
      _tb, "<th")
check("a table's border sits inside its width too", _tb, "box-sizing:border-box")
# A cell is ONE string, so a newline in it is a break somebody typed (the cell
# editor's Shift-Enter stores one). Prose is the opposite: content.md soft-wraps
# at about eighty columns, so paragraphs() closes every newline up to a space
# and a break there could never be told from a wrapped line.
_brt = _layout({"tables": [{"id": "t", "page": 1, "x": 1, "y": 1, "w": 4,
                            "rows": [["top\nbottom", "b"]]}]}).tables_html(1)
check("a newline in a cell is the line break it was typed as", _brt, "top<br>bottom")
check_eq("but a wrapped prose line still closes up to a space",
         paragraphs("one line\nand its wrap"), ["one line and its wrap"])
check("object style: a family named only through an object style's text "
      "style is still fetched", _ol.font_link(), "Manrope")

# Byte-for-byte: a layout that defines styles and wears none renders exactly
# as it did without the map.
_a = _layout({"positions": {}, "shapes": [], "boxes": [_plain]})
_b = _layout({"positions": {}, "shapes": [], "boxes": [_plain], "objectStyles": _OS,
              "textStyles": _ts2})
check_eq("object style: defining styles nobody wears changes not one byte",
         _a.text_boxes(1), _b.text_boxes(1))
check_eq("object style: nor does the mobile rule appear without columns",
         ".ds-textbox{column-count" in _a.mobile_css(), False)

check_page_raises("object style: a style must say what kind of thing it dresses",
                  {"positions": {}, "shapes": [],
                   "objectStyles": {"X": {"fill": "#FFFFFF"}}},
                  "kind None must be one of shape, box, table")
check_page_raises("object style: a style is a look, not a place",
                  {"positions": {}, "shapes": [],
                   "objectStyles": {"X": {"kind": "shape", "x": 1}}},
                  "'x' is not something a shape style can set")
check_page_raises("object style: nor a content",
                  {"positions": {}, "shapes": [],
                   "objectStyles": {"X": {"kind": "table", "rows": [["a"]]}}},
                  "'rows' is not something a table style can set")
check_page_raises("object style: a bad value fails where the style is defined",
                  {"positions": {}, "shapes": [],
                   "objectStyles": {"X": {"kind": "box", "blend": "glow"}}},
                  "blend: 'glow' must be one of")
check_page_raises("object style: a table wearing a box style has nothing to wear",
                  {"positions": {}, "shapes": [], "objectStyles": _OS,
                   "tables": [{"id": "t", "page": 1, "x": 1, "y": 1, "w": 3,
                               "rows": [["a"]], "use": "Callout"}]},
                  "'Callout' is a box style, and this is a table")
check_page_raises("object style: a `use` must name a style that exists",
                  {"positions": {}, "shapes": [{"id": "s", "page": 1, "kind": "rect",
                                                "x": 1, "y": 1, "w": 1, "h": 1,
                                                "use": "Nope"}]},
                  "no object style called 'Nope'")
check_page_raises("object style: a style inherits from its own kind only",
                  {"positions": {}, "shapes": [], "objectStyles": {
                      "A": {"kind": "shape"}, "B": {"kind": "box", "from": "A"}}},
                  "is a box style and cannot inherit from 'A', a shape style")
check_page_raises("object style: a chain cannot eat itself",
                  {"positions": {}, "shapes": [], "objectStyles": {
                      "A": {"kind": "box", "from": "B"}, "B": {"kind": "box", "from": "A"}}},
                  "inherits from itself")
check_page_raises("object style: the text style it names has to exist",
                  {"positions": {}, "shapes": [], "objectStyles": {
                      "A": {"kind": "box", "style": {"use": "Nope"}}}},
                  "objectStyle 'A'.style.use: no text style called 'Nope'")
check_page_raises("object style: a worn style is not a way under the legibility floor",
                  {"positions": {}, "shapes": [],
                   "objectStyles": {"A": {"kind": "box", "style": {"size": 4}}}},
                  "below the 7.875pt legibility floor")
check_page_raises("box look: columns are a whole number, 1 to 4",
                  {"positions": {}, "shapes": [],
                   "boxes": [dict(_plain, cols=7)]},
                  "cols: 7 — a whole number of columns, 1 to 4")
check_page_raises("box look: a box border names one edge or all of them",
                  {"positions": {}, "shapes": [],
                   "boxes": [dict(_plain, border={"sides": "inner"})]},
                  "border.sides: expected one of all, left, right, top, bottom")


# ---- anchored objects: a figure that belongs to a paragraph -------------------
# Every placed object was pinned by inch, so once text reflowed — a style
# redefined, a sentence added — every figure on the page was wrong by however
# much it moved. An anchored object names the paragraph it follows and its
# distance from it; a runtime the engine emits puts it there after the browser
# has set the type, in both the published page and the editor.
# A fresh Layout per check that looks for the runtime: it rides out ONCE per
# document, so the first emitter to be asked is the one that carries it.
_mk_an = lambda: _layout({"positions": {"fig.1": {"x": 1, "y": 3, "w": 2,
                                                   "anchor": {"to": "a.b", "dy": 0.25}}},
                          "shapes": [],
                          "boxes": [{"id": "n", "page": 1, "x": 5, "y": 4, "w": 2, "md": "note",
                                     "anchor": {"to": "a.b", "dy": -0.1, "edge": "bottom"}}]})
_an = _mk_an()
check_eq("anchor: the layout knows which slots are followed", _an.anchor_hosts, {"a.b"})
check("anchor: a box carries its paragraph and distance", _an.text_boxes(1),
      'data-anc="a.b" data-anc-dy="-0.1" data-anc-edge="bottom"')
check("anchor: a designed element carries them through attr()", _an.attr("fig.1"),
      'data-anc="a.b" data-anc-dy="0.25"')
check_eq("anchor: the top edge is the default and is not written",
         'data-anc-edge' in _an.attr("fig.1"), False)
check("anchor: the runtime rides out with the boxes", _mk_an().text_boxes(1), "window.__dsAnchor")
check("anchor: or with the shape layer, whichever is asked first", _mk_an().layer(1), "window.__dsAnchor")
_once = _mk_an()
check_eq("anchor: and only once per document",
         (_once.layer(1) + _once.text_boxes(1) + _once.layer(2)).count("window.__dsAnchor"), 1)
check("anchor: the runtime measures in this report's page width",
      _layout({"positions": {"f": {"x": 1, "y": 1, "anchor": {"to": "k"}}}, "shapes": [],
               "page": {"w": 12.5, "h": None}}).layer(1), "var W=12.5;")
# The host: content.py stamps the slot the object follows, in both modes,
# because data-slot is edit-only and the published page needs a hook too.
_host = _content(_an)
check("anchor: the followed paragraph is stamped for the runtime",
      _host.html("a.b"), '<p data-anc-host="a.b">')
check("anchor: a heading that is followed becomes a span it can measure",
      _host.t("a.b"), '<span data-anc-host="a.b">')
check_eq("anchor: a slot nobody follows is exactly what it was",
         _content(_an, body="[[c.d]]\nText.\n\n[[sources]]\n[x]: A. — https://a.gov\n").html("c.d"),
         "<p>Text.</p>")
check_eq("anchor: a layout without anchors emits no runtime and stamps nothing",
         "__dsAnchor" in _a.text_boxes(1) + _a.layer(1), False)
check_page_raises("anchor: names the slot it follows",
                  {"positions": {}, "shapes": [],
                   "boxes": [dict(_plain, anchor={"dy": 1})]},
                  "anchor.to: the slot this follows")
check_page_raises("anchor: an edge is top or bottom",
                  {"positions": {}, "shapes": [],
                   "boxes": [dict(_plain, anchor={"to": "a", "edge": "middle"})]},
                  "anchor.edge: 'middle' must be one of top, bottom")
# Text wrap rides the anchor: the runtime floats the object just before its
# paragraph, in the same flow, and CSS shortens the lines around it.
_wr = _layout({"positions": {"fig.1": {"x": 1, "y": 3, "w": 2, "wrap": "right",
                                        "wrapPad": 0.2, "anchor": {"to": "a.b"}}},
               "shapes": [],
               "boxes": [{"id": "n", "page": 1, "x": 5, "y": 4, "w": 2, "md": "note",
                          "anchor": {"to": "a.b", "dy": 0}, "wrap": "left"}]})
check("wrap: a designed element says which side, and its gutter",
      _wr.attr("fig.1"), 'data-wrap="right" data-wrap-pad="0.2"')
check("wrap: a box says which side; the default gutter is the runtime's",
      _wr.text_boxes(1), 'data-anc="a.b" data-anc-dy="0" data-wrap="left"')
# The runtime string itself — it rode out with text_boxes() above, once.
from docsync.layout import ANCHOR_JS as _AJS                     # noqa: E402
check("wrap: the runtime floats it", _AJS, "el.style.cssFloat=wrap")
# The host is looked for on the object's OWN page and nowhere else. Every sum
# after it is page-local, so a host found on another sheet put the object
# however many inches apart the pages are — off the page entirely. No host
# here means no measurement, and the object keeps the y it was left at.
check("anchor: the host is looked for on this page", _AJS,
      "var hs=pg.querySelectorAll(sel);if(!hs.length)continue;")
check_eq("anchor: and never document-wide",
         "document.querySelectorAll(sel)" in _AJS, False)
check("wrap: as a previous sibling of the paragraph, never a child", _AJS, "par.insertBefore(el,host)")
check("wrap: a phone gets the flow back", _wr.mobile_css(), "[data-wrap]{float:none !important}")
check_eq("wrap: no wrap, no phone rule", "[data-wrap]" in _an.mobile_css(), False)
check_page_raises("wrap: needs an anchor — text flows around what belongs somewhere",
                  {"positions": {}, "shapes": [], "boxes": [dict(_plain, wrap="left")]},
                  "wrap: needs an anchor")
check_page_raises("wrap: a side is left or right",
                  {"positions": {}, "shapes": [],
                   "boxes": [dict(_plain, wrap="around", anchor={"to": "a"})]},
                  "wrap: 'around' must be one of left, right")
check_page_raises("anchor: a designed element is held to the same shape",
                  {"positions": {"f": {"x": 1, "y": 1, "anchor": "a.b"}}, "shapes": []},
                  "position 'f'.anchor: expected an object")


# ---- master pages: page furniture in one place ---------------------------------
# Templates seed a document and page 4 is copied; afterwards nothing propagates,
# so the running footer is changed on every page by hand. A master is a named
# set of boxes and shapes; a page names its master and gets them all, as page
# instances `<item>@<page>`, with `{page}` in a box's words as its number.
_MS = {"masters": {"Section": {
           "boxes": [{"id": "folio", "x": 0.7, "y": 10.5, "w": 3, "z": 2,
                      "md": "{page} · YOUR REPORT", "style": {"font": "Manrope"}}],
           "shapes": [{"id": "rule", "kind": "rect", "x": 0.7, "y": 1.6, "w": 7.1,
                       "h": 0.03, "fill": "#1E6194"}]}},
       "pageMasters": {"3": "Section", "p1": "Section"},
       "pages": {"order": [1, 2, 3, "p1"], "blanks": [{"id": "p1"}]},
       "positions": {}, "shapes": [],
       "boxes": [{"id": "own", "page": 3, "x": 1, "y": 1, "w": 2, "md": "mine"}]}
_ms = _layout(_MS)
_p3 = _ms.text_boxes(3)
check("master: a page using one gets its boxes, as instances", _p3, 'top:10.5in')
check("master: {page} becomes the page's number", _p3, "<p>3 · YOUR REPORT</p>")
check("master: the page's own boxes are still there", _p3, "<p>mine</p>")
check("master: a blank page's number is its place in the order",
      _ms.text_boxes("p1"), "<p>4 · YOUR REPORT</p>")
check("master: the shapes come too", _ms.layer(3), 'data-shape="rule@3"')
check_eq("master: a page that names none gets nothing", _ms.text_boxes(2), "")
check_eq("master: nor shapes", 'data-shape' in _ms.layer(2), False)
check("master: a family a master's box asks for is fetched", _ms.font_link(), "Manrope")
check_eq("master: no data-master outside edit mode", "data-master" in _p3 + _ms.layer(3), False)
os.environ["DOCSYNC_EDIT"] = "1"
try:
    _pe = _layout(_MS)
    check("master: in edit mode an instance says which master it is on",
          _pe.text_boxes(3), 'data-el="text.folio@3" data-master="Section"')
    check("master: and so does a shape", _pe.layer(3), 'data-shape="rule@3" data-master="Section"')
finally:
    del os.environ["DOCSYNC_EDIT"]
check_eq("master: defining masters nobody uses changes not one byte",
         _layout({"positions": {}, "shapes": [], "boxes": [_plain]}).text_boxes(1),
         _layout({"positions": {}, "shapes": [], "boxes": [_plain],
                  "masters": _MS["masters"]}).text_boxes(1))
check_page_raises("master: a page must name a master that exists",
                  {"positions": {}, "shapes": [], "masters": {},
                   "pageMasters": {"1": "Nope"}},
                  "no master called 'Nope'")
check_page_raises("master: an item is checked like a page's own",
                  {"positions": {}, "shapes": [], "masters": {"M": {
                      "boxes": [{"id": "b", "x": 1, "y": 1, "w": 2, "md": ""}]}}},
                  "master 'M' box #1: has no text")
check_page_raises("master: an item cannot be anchored — the page places it",
                  {"positions": {}, "shapes": [], "masters": {"M": {
                      "boxes": [{"id": "b", "x": 1, "y": 1, "w": 2, "md": "x",
                                 "anchor": {"to": "a"}}]}}},
                  "a master item cannot carry 'anchor'")
# Tables joined boxes and shapes on a shared layout (INDESIGN_PARITY.md 7's
# follow-up); anything else is still refused.
check_page_raises("master: only boxes, shapes and tables",
                  {"positions": {}, "shapes": [], "masters": {"M": {"images": []}}},
                  "only 'boxes', 'shapes' and 'tables'")
check_page_raises("master: '@' is the instance marker, not part of an id",
                  {"positions": {}, "shapes": [], "boxes": [dict(_plain, id="a@1")]},
                  "an id cannot contain '@'")


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
    f = _tmp_json()
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
    # ...and the same box model, or that shared padding makes the editor's
    # div .24in wider than the reader's button. The stored w is a MEASURED
    # rect: read as content width, a filled box paints wider than its number,
    # the next resize measures that and writes it back, and the box grows by
    # its own padding on every drag.
    check("editor: border-box, so the padding is inside the stored width",
          _ed, "box-sizing:border-box")
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
    f = _tmp_json()
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
    f = _tmp_json()
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
    f = _tmp_json()
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
    f = _tmp_json()
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
        f = _tmp_json()
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
_wide = Layout(_tmp_json(), page=(12.5, 84))
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
# A short label used to pass ("rides along with its graphic"): true of words
# in a drawing, not of HTML, where it is exactly as frozen — rxkids' timeline
# labels sat in a movable block, draggable and impossible to retype.
check_eq("...and so is a short movable label (it was exempt until 2026-09-24)",
         _cov('<section class="page"><div data-el="badge">Fiscal estimate '
              "2028</div></section>").trapped, ["Fiscal estimate 2028"])
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

    _reg.write_text("bindings:\n  - id: t-web\n    content: c.md\n    editor:\n"
                    "      dir: d\n      render: r.py\n      out: o.html\n"
                    "      page: [12.5, null]\n")
    check_eq("a manifest page of [w, null] is pageless",
             load_registry(_reg)[0].editor.page, (12.5, None))
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
# The slot is a movable field (data-el on it): the one finding here must be
# the dead sentence, not IMMOVABLE TEXT on the well-wired line beside it.
_DIRTY = ('<section class="page"><p>Sad unwired sentence sits here.</p>'
          '<p data-slot="k" data-el="field.k">fine</p></section>')


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


# ---- vendoring: a consumer's repo and name are never guessed -----------
# 2026-09-18: a vendor run from a worktree stamped the Budget Primer's hub
# manifest with primer-editor's repo (stage.py's ROOT fallback, reached
# because the consumer restage passed no --repo) and its name as "Budget
# Primer" (hub.py's humanise() fallback, reached because the generated
# projects.json was absent). A room is owner~repo~project, so either moves
# staff into a different room. Both sources of the guess are pinned here.
from docsync.vendor import restage_cmd                    # noqa: E402
from docsync.hub import project_name                      # noqa: E402

check_eq("a consumer restage names the consumer's own repo",
         restage_cmd("budget-primer", "Hawaii-Appleseed/BudgetPrimerFinal")[-4:],
         ["--id", "budget-primer", "--repo", "Hawaii-Appleseed/BudgetPrimerFinal"])
check_eq("no origin: no --repo, so the manifest's own record (or the editor's ask) stands",
         "--repo" in restage_cmd("budget-primer", None), False)
_prev = {"budget-primer": {"name": "Budget Primer FY2026–27", "repo": "x"}}
check_eq("a binding's own name wins",
         project_name("budget-primer", {"name": "The Primer"}, {"budget-primer": "Local"}, _prev),
         "The Primer")
check_eq("then this machine's projects.json",
         project_name("budget-primer", {}, {"budget-primer": "Local"}, _prev), "Local")
check_eq("then what the hub already shows",
         project_name("budget-primer", {}, {}, _prev), "Budget Primer FY2026–27")
check_eq("and never a guess from the id",
         project_name("budget-primer", {}, {}, {}), None)
check_eq("junk on the hub is not a name either",
         project_name("budget-primer", {}, {}, {"budget-primer": "oops"}), None)

# ---- the hub: the editor's fingerprint, beside it ---------------------------
# 2026-10-20: a tab open on the hub never learned that a deploy had replaced
# its editor, and the first fix asked edit.html's own headers — which Pages
# does not send for HTML (no ETag, Last-Modified or Content-Length), so it
# could never fire. The vendor writes editor-version.txt; the tab polls that.
import hashlib as _hashlib                                 # noqa: E402
import tempfile as _tempfile                               # noqa: E402
from docsync.hub import vendor as _hub_vendor              # noqa: E402
from docsync.stage import EDITOR as _EDITOR, COLLAB_CLIENT as _COLLAB_CLIENT  # noqa: E402

with _tempfile.TemporaryDirectory() as _d:
    _hub = Path(_d)
    (_hub / "primer").mkdir()
    _hub_vendor(_hub, [], dry=False)
    _ver = (_hub / "primer" / "editor-version.txt").read_text().strip()
    _want = _hashlib.sha256(_EDITOR.read_bytes())
    if _COLLAB_CLIENT.is_file():
        _want.update(_COLLAB_CLIENT.read_bytes())
    check_eq("the hub gets editor-version.txt: the editor and its client, fingerprinted",
             _ver, _want.hexdigest()[:16])
    check_eq("in the shape the editor's poll accepts",
             bool(re.fullmatch(r"[0-9a-f]{8,64}", _ver)), True)
    _hub_vendor(_hub, [], dry=False)
    check_eq("and it is the same for the same editor (no change, no notice)",
             (_hub / "primer" / "editor-version.txt").read_text().strip(), _ver)
    # A report from a repo this run did not read (the Budget Primer, from a
    # worktree with no consumers configured) is not "gone": it is kept, and so
    # is its registry entry. Deleting it took a live report off the hub.
    (_hub / "primer" / "bp" / "engine").mkdir(parents=True)
    (_hub / "primer" / "bp" / "engine" / "manifest.json").write_text("{}")
    (_hub / "primer" / "projects.json").write_text('{"bp": {"name": "Budget Primer", "repo": "Hawaii-Appleseed/BudgetPrimerFinal"}}')
    _hub_vendor(_hub, [], dry=False)
    check_eq("a report from a repo this run did not read keeps its folder",
             (_hub / "primer" / "bp" / "engine" / "manifest.json").is_file(), True)
    check_eq("and its place on the hub's list",
             json.loads((_hub / "primer" / "projects.json").read_text()).get("bp", {}).get("name"), "Budget Primer")
# ---- the hub: a ship that would hide words people typed is refused ----------
# A renderer that renames or drops a slot does not delete what was typed into
# it — the room keeps it — it just stops drawing it, and nobody is told.
from docsync.hub import hidden_text as _hidden_text, is_ship as _is_ship  # noqa: E402

with _tempfile.TemporaryDirectory() as _d:
    _root = Path(_d)
    _hubd = _root / "hub"
    _src = _root / "src"
    _old = _hubd / "primer" / "r" / "engine" / "projects" / "r"
    _new = _src / "engine" / "projects" / "r"
    for _p in (_old, _new):
        _p.mkdir(parents=True)
    (_src / "engine" / "manifest.json").write_text('{"content": "projects/r/content.md"}')
    (_old / "content.md").write_text("[[a]]\nkept\n\n[[b]]\nseed words\n\n[[c]]\n\n[[sources]]\n- x: y\n")
    (_new / "content.md").write_text("[[a]]\nkept\n\n[[sources]]\n- x: y\n")
    _proj = [{"id": "r", "dir": _src, "repo": "O/R"}]
    check_eq("a dropped slot that holds live words is named, with the words",
             _hidden_text(_hubd, _proj, live=lambda p: {"a": "x", "b": "typed by staff", "c": ""}),
             [("r", "b", "typed by staff")])
    check_eq("an empty one is not (nothing to lose)",
             _hidden_text(_hubd, _proj, live=lambda p: {"a": "x", "b": "  ", "c": ""}), [])
    check_eq("a report never saved holds its seed, so the seed's words count",
             _hidden_text(_hubd, _proj, live=lambda p: None), [("r", "b", "seed words")])
    (_new / "content.md").write_text((_old / "content.md").read_text())
    check_eq("nothing dropped, nothing asked of the live store",
             _hidden_text(_hubd, _proj, live=lambda p: (_ for _ in ()).throw(AssertionError("asked"))), [])
    check_eq("a test rig's copy of the hub is not a ship", _is_ship(_hubd), False)

# ---- the hub: which slots are on the page --------------------------------
# docsync.hub builds each report once in edit mode and records the keys its
# renderer READ into the hub's manifest (`drawn`), so the connector can tell a
# colleague's Claude that a leftover slot is not on the page.
from docsync.hub import drawn_slots as _drawn_slots                  # noqa: E402
with _tempfile.TemporaryDirectory() as _d:
    _dd = Path(_d)
    (_dd / "content.md").write_text("[[on.it]]\nDrawn.\n\n[[left.over]]\nNot drawn.\n\n[[sources]]\n[x]: A source.\n")
    (_dd / "render.py").write_text(
        "from pathlib import Path\nfrom docsync.content import Content\n"
        f"C = Content(Path({str(_dd / 'content.md')!r}))\nC.raw('on.it')\n")
    check_eq("the slots a renderer reads are the ones on the page", _drawn_slots(_dd / "render.py"), ["on.it"])
    (_dd / "broken.py").write_text("raise SystemExit(1)\n")
    check_eq("a report that does not build marks nothing (None), rather than everything",
             _drawn_slots(_dd / "broken.py"), None)
check_eq("nor one with no renderer here (a consumer's)", _drawn_slots(None), None)

_edit_src = _EDITOR.read_text()
check("the editor polls that file on the hub", _edit_src, "new URL('editor-version.txt', location.href)")
check("past the service worker", _edit_src, "fetch(new URL('editor-version.txt', location.href), { cache: 'no-store' })")


# ---- page copies: a duplicated designed page redraws from the document ----
# The editor's Duplicate page stores a designed page's pristine markup on a
# blank page (layout.pages.blanks[].copy) with every hook moved under
# copy.<bid>.; pagecopy.fill() redraws it from the CURRENT words and layout
# on every render, in both modes. See docsync/pagecopy.py.
from docsync import pagecopy                              # noqa: E402

_COPY_HTML = ('<h1 data-el="copy.p1.h"><span data-slot="copy.p1.h" data-inline="1">OLD</span></h1>'
              '<div class="stat" data-el="copy.p1.stat" data-fill="copy.p1.stat" style="color:red">'
              '<p>x</p></div>'
              '<div data-el="copy.p1.gone">bye</div>'
              '<div data-el="para.copy.p1.body"><p class="c" data-slot="copy.p1.body">old</p>'
              '<p class="c" data-slot="copy.p1.body">old2</p></div>'
              '<ul data-slot="copy.p1.items"><li>z</li></ul>'
              '<svg viewBox="0 0 10 10"><path d="M0 0"/>'
              '<text x="1" y="2" text-anchor="middle" data-slot="copy.p1.lab">old</text></svg>'
              '<div class="extra-section" data-slot="copy.p1.sec" data-extra="1">old</div>'
              '&amp; &#169; <img src="a.png">')
_COPY_LAYOUT = {"positions": {"copy.p1.stat": {"x": 1, "y": 2, "w": 3, "reserve": 0.5}},
                "hidden": ["copy.p1.gone"], "fill": {"copy.p1.stat": "#FFEEDD"},
                "text": {"copy.p1.h": {"size": 20}},
                "pages": {"order": [1, "p1"],
                          "blanks": [{"id": "p1", "copy": {"of": 1, "html": _COPY_HTML}}]}}
_COPY_MD = ("[[copy.p1.h]]\nHello\n\n[[copy.p1.body]]\nFirst.[^x]\n\nSecond.\n\n"
            "[[copy.p1.items]]\n- one\n- two\n\n[[copy.p1.lab]]\nSign up & go\n\n"
            "[[copy.p1.sec]]\n## Added\n\nWords.\n\n[[sources]]\n[x]: A. — https://a.gov\n")


def _copy_render(edit):
    was = os.environ.get("DOCSYNC_EDIT")
    if edit:
        os.environ["DOCSYNC_EDIT"] = "1"
    else:
        os.environ.pop("DOCSYNC_EDIT", None)
    try:
        L = _layout(_COPY_LAYOUT)
        _content(L, _COPY_MD)                 # binds itself to L
        return L.copy_html("p1"), L
    finally:
        if was is None:
            os.environ.pop("DOCSYNC_EDIT", None)
        else:
            os.environ["DOCSYNC_EDIT"] = was


_ce, _cL = _copy_render(True)
check("a copied heading is the document's CURRENT words, as a slot",
      _ce, '<span data-slot="copy.p1.h" data-inline="1" style="font-size:20px">Hello</span>')
check("a copied element is placed by its own layout entry, extra css kept",
      _ce, 'data-el="copy.p1.stat" data-placed style="margin:0;position:absolute;left:1in;top:2in;bottom:auto;'
           'box-sizing:border-box;width:3in;z-index:1;color:red;background:#FFEEDD"')
check("…and its strut is emitted before it",
      _ce, '<div class="ds-spacer" data-spacer-for="copy.p1.stat"')
check("a hidden copy element ghosts", _ce, 'data-el="copy.p1.gone" data-hidden="1" style="display:none"')
check_eq("a prose slot renders one <p> per paragraph, never twice",
         _ce.count('data-slot="copy.p1.body"'), 2)
check("…with the citation token left for resolve()", _ce, '<p class="c" data-slot="copy.p1.body">First.[^x]</p>')
check("a list slot re-renders its items", _ce, '<ul data-slot="copy.p1.items"><li>one</li><li>two</li></ul>')
check("an SVG label keeps its case-sensitive attributes and escapes its words",
      _ce, '<svg viewBox="0 0 10 10"><path d="M0 0"/><text x="1" y="2" text-anchor="middle" '
           'data-slot="copy.p1.lab">Sign up &amp; go</text></svg>')
check("an added section renders as block markdown, without the page controls",
      _ce, '<div class="extra-section" data-slot="copy.p1.sec" data-extra="1"><h2 class="sub">Added</h2><p>Words.</p></div>')
check("entities and char refs pass through untouched", _ce, '&amp; &#169; <img src="a.png">')
check_eq("every copied slot counts as used", set(_cL._content.unused_keys()) & {
    "copy.p1.h", "copy.p1.body", "copy.p1.items", "copy.p1.lab", "copy.p1.sec"}, set())
_cp, _ = _copy_render(False)
check_eq("published, a copy carries no editing hooks",
         [a for a in ("data-el", "data-slot", "data-inline", "data-extra", "data-fill", "data-hidden")
          if a in _cp], [])
check("…but the type panel's style still ships", _cp, '<h1><span style="font-size:20px">Hello</span></h1>')
check("…and so does the placement", _cp, 'position:absolute;left:1in;top:2in')
check_eq("a page that is not a copy draws nothing extra", _cL.copy_html(1), "")

# A copy whose slot the document has not got: marked while editing, silent
# when published — never a refused build over a copy.
_miss = dict(_COPY_LAYOUT)
_miss["pages"] = {"order": ["p1"], "blanks": [{"id": "p1", "copy": {
    "of": 1, "html": '<p data-slot="copy.p1.nope" data-inline="1">x</p>'}}]}
os.environ["DOCSYNC_EDIT"] = "1"
_L2 = _layout(_miss); _content(_L2, _COPY_MD)
check("a missing copy slot is marked while editing", _L2.copy_html("p1"), "new slot [[copy.p1.nope]]")
os.environ.pop("DOCSYNC_EDIT", None)
_L3 = _layout(_miss); _content(_L3, _COPY_MD)
check_eq("…and renders empty when published", _L3.copy_html("p1"), '<p></p>')
check_eq("a layout that binds no content skips the copy rather than half-drawing it",
         _layout(_COPY_LAYOUT).copy_html("p1"), "")
check_eq("copy_keys lists what a stored copy reads",
         pagecopy.copy_keys(_COPY_HTML),
         ["copy.p1.h", "copy.p1.body", "copy.p1.body", "copy.p1.items", "copy.p1.lab", "copy.p1.sec"])
check("a malformed copy is refused at the layout",
      _layout_error({"pages": {"order": ["p1"], "blanks": [{"id": "p1", "copy": "nope"}]}}),
      "copy must be an object")

# ---- text_or / svg_text: a label the renderer defaults ----------------------
from docsync.blocks import svg_text                       # noqa: E402
_dc = _content(None, "[[lab.a]]\nRephrased\n\n[[sources]]\n[x]: A. — https://a.gov\n")
check_eq("text_or reads the slot when the document has it", _dc.text_or("lab.a", "Default"), "Rephrased")
check_eq("…and the renderer's default when it has not", _dc.text_or("lab.b", "Default"), "Default")
check_eq("a defaulted key is not reported unused, and not new",
         ("lab.b" in _dc.unused_keys(), "lab.b" in _dc.new_keys()), (False, False))
os.environ["DOCSYNC_EDIT"] = "1"
_de = _content(None, "[[lab.a]]\nRephrased\n\n[[sources]]\n[x]: A. — https://a.gov\n")
check_eq("svg_text draws a slotted <text> from the document's words",
         svg_text(_de, "lab.a", "Default", 17, 9.5, 12.5, "#333", weight=700, anchor="middle"),
         '<text x="17" y="9.5" font-size="12.5" fill="#333" font-weight="700" text-anchor="middle" '
         'data-slot="lab.a">Rephrased</text>')
check_eq("…and from the default, still as a slot, when the document lacks it",
         svg_text(_de, "lab.b", "A & B", 0, 10, 12.5, "#333"),
         '<text x="0" y="10" font-size="12.5" fill="#333" data-slot="lab.b">A &amp; B</text>')
os.environ.pop("DOCSYNC_EDIT", None)
check_eq("published, no hook", svg_text(_dc, "lab.b", "A", 0, 10, 12, "#333"),
         '<text x="0" y="10" font-size="12" fill="#333">A</text>')


# ---- words inside a drawing are refused at the build ---------------------
# graphic() is the one way a movable SVG reaches a page, so it is where a
# literal label is stopped: in edit mode (the editor's draft, the check's
# edit-mode pass) a <text> with words and no hook raises; a slot (svg_text),
# declared data (C.derived) or a declared-frozen graphic pass; publishing
# never refuses. is_data_mark is the rule, shared with docsync.check.
from docsync.blocks import graphic, is_data_mark, svg_literals, SvgLiteralError  # noqa: E402

for _t, _want in [("$20.7M", True), ("60%", True), ("FY26", True), ("TY2023", True),
                  ("1st", True), ("Jan 2026", True), ("$500/mo", True), ("12", True),
                  ("Sign up", False), ("DOTAX", False), ("births served", False),
                  ("Under $10K", False), ("MTok", False), ("$10 / $50 per MTok", False)]:
    check_eq(f"is_data_mark({_t!r})", is_data_mark(_t), _want)

_SVG_BAD = ('<svg viewBox="0 0 10 10"><text x="1" y="2">Sign up</text>'
            '<text x="1" y="4">$1,500</text><text x="1" y="6">'
            '<tspan>Baby</tspan> <tspan>arrives</tspan></text></svg>')
check_eq("svg_literals lists the words and skips the marks",
         svg_literals(_SVG_BAD), ["Sign up", "Baby arrives"])
_SVG_OK = ('<svg viewBox="0 0 10 10"><text data-slot="a.b">Sign up</text>'
           '<text data-fixed="make data">Education</text>'
           '<text data-ch="label:0">Second</text><text>$5M</text></svg>')
check_eq("…and every hooked label passes", svg_literals(_SVG_OK), [])
check_eq("derived data excuses a name, never a sentence",
         svg_literals('<svg><text data-fixed="x">Only the left pool is reachable today.</text>'
                      '<text data-fixed="x">Human Services</text></svg>'),
         ["Only the left pool is reachable today."])

_gL = _layout({})
os.environ["DOCSYNC_EDIT"] = "1"
try:
    graphic(_gL, "fig.x", _SVG_BAD, w=2)
    FAILS.append("graphic() accepted plain words inside an SVG in edit mode")
except SvgLiteralError as e:
    check("the refusal names the graphic and the words", str(e), "graphic 'fig.x': 2 label(s)")
    check("…and says the three ways out", str(e), "svg_text")
    check("…", str(e), "C.derived")
    check("…", str(e), "frozen=")
check("a hooked SVG renders", graphic(_gL, "fig.y", _SVG_OK, w=2), 'data-el="fig.y"')
_fz = graphic(_gL, "fig.z", _SVG_BAD, w=2, frozen="a wordmark: the words are the drawing")
check("a graphic declared frozen renders and says so",
      _fz, 'data-frozen="a wordmark: the words are the drawing"')
os.environ.pop("DOCSYNC_EDIT", None)
_pub = graphic(_gL, "fig.x", _SVG_BAD, w=2)
check_eq("publishing never refuses, and carries no declaration",
         ("Sign up" in _pub, "data-frozen" in _pub, "data-el" in _pub), (True, False, False))


# --- a description is content, and a figure inside a drawing is data ---------
# Two failure shapes that survived every check above, both about words the
# hook-counting could not see: an aria-label/alt (words in an ATTRIBUTE, and
# under role="img" the ONLY words a screen reader is given for that figure),
# and a slotted label that states a number the drawing also draws — editable
# words over geometry that will not follow them.
from docsync.blocks import (describe, is_quantity,       # noqa: E402
                            slot_descriptions)


class _C:
    """The slice of Content these two helpers use: a document with no slots,
    so text_or always falls through to the renderer's own wording."""
    def text_or(self, key, default):
        return default


for _t, _want in [("$1,500", True), ("84.0%", True), ("$86.1M", True),
                  ("12,345", True), ("46 percent", True), ("$10 / $50 per MTok", True),
                  ("Claude Opus 5", False), ("FY26", False), ("Pregnancy", False),
                  ("2031", False), ("1st", False), ("", False)]:
    check_eq(f"is_quantity({_t!r})", is_quantity(_t), _want)

os.environ["DOCSYNC_EDIT"] = "1"
_d = describe(_C(), "chart.claims.desc", 'Claims by "type" & year')
check_eq("describe() is a role, an escaped label and a hook",
         _d, ' role="img" aria-label="Claims by &quot;type&quot; &amp; year"'
             ' data-desc="chart.claims.desc"')
check_eq("role='' leaves a tag that has one alone",
         'role=' in describe(_C(), "k", "x", role=""), False)
check_eq("slot_descriptions keys in document order and keeps the words",
         slot_descriptions(_C(), '<span aria-label="7 agree, 1 pass"></span>'
                                 '<img alt="Cover texture">', "desc"),
         '<span aria-label="7 agree, 1 pass" data-desc="desc.1"></span>'
         '<img alt="Cover texture" data-desc="desc.2">')
os.environ.pop("DOCSYNC_EDIT", None)
check_eq("published, a description is the words and nothing else",
         describe(_C(), "k", "Claims by type"), ' role="img" aria-label="Claims by type"')
check_eq("…and a static body keeps its own bytes",
         slot_descriptions(_C(), '<img alt="Cover texture">', "desc"),
         '<img alt="Cover texture">')

# What _Coverage makes of them.
_PAGE = '<section class="page">%s</section>'
check_eq("an aria-label with words is a frozen description",
         _cov(_PAGE % '<svg role="img" aria-label="Claims by taxpayer type, '
                      'stacked"></svg>').desc_paged,
         ["Claims by taxpayer type, stacked"])
check_eq("an <img alt> counts too, though a void tag never comes back down",
         _cov(_PAGE % '<img alt="Governor at the bill signing">').desc_paged,
         ["Governor at the bill signing"])
check_eq("data-desc clears it, and is listed as declared",
         (_cov(_PAGE % '<svg aria-label="Claims by type" data-desc="c.desc">'
                       "</svg>").desc_paged,
          _cov(_PAGE % '<svg aria-label="Claims by type" data-desc="c.desc">'
                       "</svg>").desc_declared),
         ([], ["Claims by type"]))
check_eq("so does C.derived, and so does not being announced at all",
         (_cov(_PAGE % '<i aria-label="34 of 101" data-fixed="make tally"></i>').desc_paged,
          _cov(_PAGE % '<i aria-label="Decorative rule" aria-hidden="true"></i>').desc_paged),
         ([], []))
check_eq("a data mark is not a description",
         _cov(_PAGE % '<i aria-label="46%"></i><i alt="FY26"></i>').desc_paged, [])
check_eq("chrome outside the sheet is kept apart from the sheet's own",
         (_cov('<button title="Download this draft as a PDF"></button>').desc_paged,
          _cov('<button title="Download this draft as a PDF"></button>').desc_all),
         ([], ["Download this draft as a PDF"]))
check_eq("an SVG <title> is the same words by another route",
         _cov(_PAGE % "<svg><title>Claims by taxpayer type</title></svg>").desc_paged,
         ["Claims by taxpayer type"])
check_eq("…and a wired one passes",
         _cov(_PAGE % '<svg><title data-desc="c.desc">Claims by taxpayer type'
                      "</title></svg>").desc_paged, [])

check_eq("a slotted figure inside a drawing is a restatement",
         _cov('<svg><text data-slot="c.fed">$20.7M</text></svg>').restated,
         ["c.fed: $20.7M"])
check_eq("restates= declares it, and so does C.derived",
         (_cov('<svg><text data-slot="c.fed" data-restates="make split">$20.7M'
               "</text></svg>").restated,
          _cov('<svg><text data-slot="c.fed" data-fixed="make split">$20.7M'
               "</text></svg>").restated), ([], []))
check_eq("a label with no figure in it is nobody's problem",
         _cov('<svg><text data-slot="c.leg">Federal TANF</text></svg>').restated, [])
check_eq("a caption is prose wherever its numbers came from",
         _cov('<svg><text data-slot="c.why">ITEP\u2019s baseline is 2026 policy, '
              "so its figure is that rise, not the $705M itself.</text>"
              "</svg>").restated, [])
check_eq("an UNSLOTTED figure is still just a data mark, not frozen prose",
         _cov('<svg><text>$20.7M</text></svg>').frozen_prose, [])

# svg_text carries the declaration only where the editor can use it.
from docsync.blocks import svg_text                        # noqa: E402
os.environ["DOCSYNC_EDIT"] = "1"


class _CS(_C):
    def slot_attr(self, key):
        return f' data-slot="{key}"'


check("svg_text(restates=) declares where the number comes from",
      svg_text(_CS(), "c.fed", "$20.7M", 1, 2, 11, "#000",
               restates="make split"), 'data-restates="make split"')
os.environ.pop("DOCSYNC_EDIT", None)
check_eq("…and published it carries none of it",
         "data-restates" in svg_text(_CS(), "c.fed", "$20.7M", 1, 2, 11, "#000",
                                     restates="make split"), False)



# ------------------------------------ editability: the four holes, 2026-09-22
# Each was an open way for a renderer — an AI's, usually — to put words on a
# page that nobody could edit while every check stayed green.
from docsync.blocks import graphic as _graphic, svg_literals   # noqa: E402
from docsync.check import (check_publish_only,                  # noqa: E402
                           check_slot_fidelity)

# 1. HTML inside a drawing.
check_eq("graphic() refuses words in a <foreignObject>",
         svg_literals('<svg viewBox="0 0 9 9"><foreignObject><div>Households '
                      'under fifty thousand pay more.</div></foreignObject>'
                      '</svg>'),
         ["Households under fifty thousand pay more."])
check_eq("…but a slot inside one is reachable",
         svg_literals('<svg><foreignObject><p data-slot="k">Words that edit.'
                      '</p></foreignObject></svg>'), [])
check_eq("an upper-case <TEXT> is still text",
         svg_literals("<svg><TEXT x='1'>Label words here</TEXT></svg>"),
         ["Label words here"])
check_eq("'data-slot' in another attribute's value is not a hook",
         svg_literals('<svg><text class="data-slot">Label words here</text>'
                      '</svg>'), ["Label words here"])
check_eq("tspans still read as one label, and data marks still pass",
         svg_literals('<svg><text><tspan>Low</tspan></text><text>$5M</text>'
                      '<text>Two <tspan>words</tspan></text></svg>'),
         ["Two words"])
check_eq("docsync.check sees words in a <foreignObject> too",
         _cov('<section class="page"><svg><foreignObject><div>Households '
              'under fifty thousand pay more.</div></foreignObject></svg>'
              '</section>').frozen_prose,
         ["Households under fifty thousand pay more."])

# 2. A declaration has to say something.
check_eq("an empty data-fixed covers nothing",
         _cov('<section class="page"><p data-fixed="">A sentence nobody can '
              'edit sits here.</p></section>').dead_paged,
         ["A sentence nobody can edit sits here."])
check_eq("a data-fixed of only spaces covers nothing either",
         _cov('<section class="page"><p data-fixed="  ">Tally</p></section>'
              ).dead_paged, ["Tally"])
check_eq("an empty data-restates declares nothing",
         _cov('<svg><text data-slot="c.v" data-restates="">$5M</text></svg>'
              ).restated, ["c.v: $5M"])
check_eq("a SENTENCE under data-fixed is flagged outside an SVG as well",
         _cov('<section class="page"><p data-fixed="make data">Spending rose '
              'by a third over the decade.</p></section>').fixed_prose,
         ["Spending rose by a third over the decade."])
check_eq("…but a derived tally is what data-fixed is for",
         _cov('<section class="page"><p data-fixed="make data">135 bills'
              '</p></section>').fixed_prose, [])
_c = _cov('<p data-fixed="make data">1</p><p data-fixed="make data">2</p>'
          '<svg><text data-slot="a" data-restates="split.py">$1M</text></svg>')
check_eq("every declaration is tallied by what it names",
         (_c.fixed_decl, _c.restates_decl),
         ({"make data": 2}, {"split.py": 1}))
os.environ["DOCSYNC_EDIT"] = "1"
try:
    check_eq("svg_text(restates='  ') emits no declaration",
             "data-restates" in svg_text(_CS(), "c.v", "$5M", 1, 2, 11, "#000",
                                         restates="  "), False)
    try:
        _graphic(type("L", (), {"positions": {}, "spacer": lambda s, i: "",
                                "attr": lambda s, i, b="": ""})(), "g",
                 '<svg viewBox="0 0 9 9"><text>Frozen words here</text></svg>',
                 frozen="   ")
        FAILS.append("graphic(frozen='   ') must not excuse its words")
    except ValueError as e:
        check("…a blank frozen= is no reason", str(e), "Frozen words here")
finally:
    os.environ.pop("DOCSYNC_EDIT", None)


def _derived_blank():
    import tempfile as _tf
    from docsync.content import Content as _C
    with _tf.TemporaryDirectory() as td:
        p = Path(td) / "content.md"
        p.write_text("[[k]]\nx\n\n[[sources]]\n[s]: t — https://e.com\n")
        os.environ["DOCSYNC_EDIT"] = "1"
        try:
            _C(p).derived("  ")
        finally:
            os.environ.pop("DOCSYNC_EDIT", None)


check_raises("C.derived('') is refused in edit mode", _derived_blank,
             "empty source declares nothing")

# 3. A slot hook is held to the words the slot actually served.
_c = _cov('<section class="page">'
          '<h2 data-slot="s.title">Real title <span>— FY27 edition</span></h2>'
          '<p data-slot="made.up">Invented key covering prose.</p>'
          '<p data-slot="s.body">Body with a <b>note</b><sup>3</sup>.</p>'
          '<div role="img" data-desc="s.desc" aria-label="Spoken figure"></div>'
          '<div role="img" data-desc="s.gone" aria-label="Old words"></div>'
          '</section>')
_served_t = {"s.title": ["Real title"], "s.body": ["Body with a **note**[^x]."],
             "s.desc": ["Spoken figure"]}
check_eq("a key never read, and words beside a slot's own, are both found",
         check_slot_fidelity(_c.slot_runs, _served_t),
         (["made.up", "s.gone"], ["s.title: — FY27 edition"]))
_served_f = {"cip": ["The total in FY{fy} is {total}. Roads take half."]}
check_eq("a filled format field is data, not a foreign word",
         check_slot_fidelity([("cip", ["The total in FY2027 is $4.53 billion. "
                                       "Roads take half.",
                                       "in FY2027 is $4.53 billion. Ro"])],
                             _served_f), ([], []))
check_eq("…but words standing in for a field are foreign",
         check_slot_fidelity([("cip", ["The total in FY2027 is a secret "
                                       "number. Roads take half."])],
                             _served_f)[1],
         ["cip: The total in FY2027 is a secret number. Roads take half."])

# 4. Words only the published build has.
check_eq("publish-only strings are found, text and attributes both",
         check_publish_only('<p><span data-slot="a">Hello</span> world</p>',
                            '<p>Hello world</p><p>Only when publishing.</p>'
                            '<img alt="A picture of words">'),
         ["Only when publishing.", "A picture of words"])
from docsync.blocks import PDF_PRINT_TITLE                  # noqa: E402
check_eq("…except the one string the engine itself publishes only",
         check_publish_only("<p>x</p>",
                            f'<p>x</p><button title="{PDF_PRINT_TITLE}">'
                            "</button>"), [])


def _binding_modes(edit_html, pub_html, served=None, editability="strict"):
    """A renderer that writes one thing per mode, and optionally a slot log."""
    td = Path(tempfile.mkdtemp(prefix="ds-editability-"))
    r = td / "render.py"
    r.write_text(
        "import json, os, pathlib\n"
        f"edit = bool(os.environ.get('DOCSYNC_EDIT'))\n"
        f"pathlib.Path(os.environ['DOCSYNC_OUT']).write_text("
        f"{edit_html!r} if edit else {pub_html!r})\n"
        + (f"if os.environ.get('DOCSYNC_SLOTLOG'):\n"
           f"    pathlib.Path(os.environ['DOCSYNC_SLOTLOG']).write_text("
           f"json.dumps({served!r}))\n" if served is not None else ""))
    return Binding(id="t", content=td / "c.md", editability=editability,
                   editability_ok=[],
                   editor=Editor(render=r, engine=[], out=td / "out.html",
                                 dir=td))


_page = ('<section class="page"><div data-el="para.k">'
         '<p data-slot="k">Fine words.</p></div></section>')
_p = check_editability(_binding_modes(
    _page, _page.replace("</section>",
                         "<p>Draft banner only when published.</p></section>")))
check_eq("a strict binding fails on publish-only text, end to end",
         [(pr.is_error, "PUBLISHED" in str(pr)) for pr in _p], [(True, True)])
_p = check_editability(_binding_modes(_page, _page, served={"other": ["x"]}))
check_eq("a strict binding fails on a hook the renderer never read",
         [(pr.is_error, "never read" in str(pr)) for pr in _p], [(True, True)])
check_eq("…and passes when the slot log backs every hook",
         check_editability(_binding_modes(_page, _page,
                                          served={"k": ["Fine words."]})), [])
check_eq("a note alone is not a finding level",
         [pr.level for pr in check_editability(_binding_modes(
             '<section class="page"><p data-fixed="make">12</p></section>',
             '<section class="page"><p>12</p></section>'))], ["note"])


# ------------------------------------------ every text box moves and resizes
# tfc-2027-priorities: 141 fields, every one of them typed into on a click and
# not one of them movable — docsync.propose wired each as a slot (data-slot)
# and never as an object (data-el), and every imported page's renderer filled
# the markers with five re.subs of its own. The engine fills them now, and the
# check refuses a slot nothing can move.
from docsync.blocks import fill_markers, field_plan         # noqa: E402
from docsync.content import merge_attrs, split_style        # noqa: E402
from docsync.layout import strut_extra                      # noqa: E402

check_eq("merge_attrs leaves one style where it was",
         merge_attrs(' data-slot="a"', ' style="color:red"'),
         ' data-slot="a" style="color:red"')
check_eq("…and makes ONE style of two, the later part last",
         merge_attrs(' style="width:30%"', ' data-el="x" style="margin:0;left:1in"'),
         ' data-el="x" style="width:30%;margin:0;left:1in"')
check_eq("split_style reads single quotes too",
         split_style(""" class='a' style='font-family:"X"'"""),
         (" class='a'", 'font-family:"X"'))

_MARKED = (
    '<header><p class="eyebrow"⟦A:eb⟧>⟦T:eb⟧</p>'
    '<div class="stat"><span class="num"⟦A:num⟧>⟦T:num⟧'
    '<span class="of"⟦A:of⟧>⟦T:of⟧</span></span>'
    '<span class="lbl"⟦A:lbl⟧>⟦T:lbl⟧</span></div></header>'
    '<div class="legend"><span><i class="swatch" style="background:red"></i> '
    '<span⟦A:lg⟧>⟦T:lg⟧</span></span></div>'
    '<h1 data-split><span⟦A:h.a⟧>⟦T:h.a⟧</span> '
    '<span class="mark"⟦A:h.b⟧>⟦T:h.b⟧</span></h1>'
    '<p class="note"><span⟦A:run⟧>⟦T:run⟧</span> <a href="#">a link</a></p>'
    '<div class="bar"><i class="f1" style="width:30%"⟦A:n1⟧>⟦T:n1⟧</i></div>'
    '⟦S:pic⟧<img src="a.png" ⟦E:pic⟧>')
_MD = ("[[eb]]\nEyebrow\n\n[[num]]\n13\n\n[[of]]\n/28\n\n[[lbl]]\nVoted\n\n"
       "[[lg]]\nAgree\n\n[[h.a]]\nAdvancing\n\n[[h.b]]\njustice\n\n[[run]]\nSee\n\n"
       "[[n1]]\n39\n\n[[sources]]\n[x]: A. — https://a.gov\n")


def _filled(edit, lay=None):
    if edit:
        os.environ["DOCSYNC_EDIT"] = "1"
    else:
        os.environ.pop("DOCSYNC_EDIT", None)
    try:
        L = _layout(lay or {})
        return fill_markers(_content(L, _MD), L, _MARKED)
    finally:
        os.environ.pop("DOCSYNC_EDIT", None)


check_eq("each field is a unit a drag means: a stat with its label, a legend "
         "entry with its swatch, a split heading whole, a lone run on its own",
         [(f, t) for f, t, _ in field_plan(_MARKED)],
         [("field.eb", "p"), ("field.num", "div"), ("field.lg", "span"),
          ("field.h.a", "h1"), ("field.run", "span"), ("field.n1", "div")])
_pub = _filled(False)
check_eq("published and untouched, the markers fill exactly as five re.subs did",
         _pub, _MARKED.replace("⟦S:pic⟧", "")
         .replace(" ⟦E:pic⟧", " ")
         .replace("⟦T:eb⟧", "Eyebrow").replace("⟦T:num⟧", "13")
         .replace("⟦T:of⟧", "/28").replace("⟦T:lbl⟧", "Voted")
         .replace("⟦T:lg⟧", "Agree").replace("⟦T:h.a⟧", "Advancing")
         .replace("⟦T:h.b⟧", "justice").replace("⟦T:run⟧", "See")
         .replace("⟦T:n1⟧", "39")
         .replace("⟦A:eb⟧", "").replace("⟦A:num⟧", "")
         .replace("⟦A:of⟧", "").replace("⟦A:lbl⟧", "")
         .replace("⟦A:lg⟧", "").replace("⟦A:h.a⟧", "")
         .replace("⟦A:h.b⟧", "").replace("⟦A:run⟧", "")
         .replace("⟦A:n1⟧", ""))
_ed = _filled(True)
check_eq("while editing, no slot is left without a data-el on it or above it",
         (_cov('<section class="page">' + _ed + "</section>").immovable_paged,
          "⟦" in _ed), ([], False))
check("a field that IS its slot carries both hooks on the one tag",
      _ed, '<p class="eyebrow" data-slot="eb" data-el="field.eb">')
_moved = _filled(False, {"positions": {"field.n1": {"x": 1, "y": 2, "w": 3,
                                                    "reserve": 0.5}},
                        "text": {"n1": {"size": 20}}})
check("a moved field with a style of its own and a text style is ONE style "
      "attribute, the page's first and the position last",
      _moved, 'style="margin:0;position:absolute;left:1in;top:2in;bottom:auto')
check_eq("…never two (the parser keeps only the first)",
         re.search(r'<div class="bar"[^>]*>', _moved).group(0).count("style="), 1)
check_eq("a moved field says what room it leaves, for the runtime strut",
         "data-reserve-for=\"field.n1\"" in _moved, True)

check_eq("a strut holds the margin box and sits in a line when its element did",
         strut_extra({"reserveMargin": [0, 0, 0.117, 0], "reserveInline": True}),
         ";margin:0in 0in 0.117in 0in;display:inline-block;vertical-align:top")
check_eq("…and a strut from before carries nothing new",
         (strut_extra({"reserve": 1}), strut_extra({"reserveMargin": [0, 0, 0, 0]})),
         ("", ""))
_sl = _layout({"positions": {"h": {"x": 1, "y": 1, "w": 4, "reserve": 0.5,
                                   "reserveMargin": [0.1, 0, 0.2, 0]}}})
check("spacer() wears the margins", _sl.spacer("h"),
      "height:0.5in;flex:0 0 auto;margin:0.1in 0in 0.2in 0in")
os.environ["DOCSYNC_EDIT"] = "1"
check("…and attr() tells the runtime them", _sl.attr("h"), 'data-reserve-m="0.1 0 0.2 0"')
_mv = _content(_layout({}), "[[k]]\n- a\n\n[[sources]]\n[x]: A. — https://a.gov\n")
check_eq("C.movable holds a caller's list in a movable block while editing",
         _mv.movable("k", "<ul></ul>"), '<div data-el="para.k"><ul></ul></div>')
os.environ.pop("DOCSYNC_EDIT", None)
check_eq("…and published, unmoved, is the bare markup",
         _mv.movable("k", "<ul></ul>"), "<ul></ul>")
# L.wrap: the same block under any id, for a thing with no one element of its
# own — a table drawn once per year with one of the two hidden.
_wl = _layout({"positions": {"table1": {"x": 1, "y": 2, "w": 6, "reserve": 1.5}}})
_twins = '<table data-fy="2027"></table><table data-fy="2026" hidden></table>'
check_eq("published and unmoved, L.wrap adds nothing",
         _layout({}).wrap("table1", _twins), _twins)
_moved_w = _wl.wrap("table1", _twins)
check("…moved, both years ride one positioned block behind a strut",
      _moved_w, 'data-spacer-for="table1"')
check("…positioned", _moved_w, 'style="margin:0;position:absolute;left:1in;top:2in;bottom:auto')
os.environ["DOCSYNC_EDIT"] = "1"
check_eq("…and while editing it is the handle",
         _layout({}).wrap("table1", _twins), f'<div data-el="table1">{_twins}</div>')
os.environ.pop("DOCSYNC_EDIT", None)

check_eq("a slot with nothing movable on it or above it is IMMOVABLE TEXT",
         _cov('<section class="page"><h2><span data-slot="t">Title</span></h2>'
              '<div data-el="para.p"><p data-slot="p">Moves.</p></div>'
              '<svg><text data-slot="lbl">Label</text></svg>'
              '<div data-el="fig"><svg><text data-slot="ok">OK</text></svg></div>'
              "</section>").immovable_paged, ["t", "lbl"])
_p = check_editability(_binding_modes(
    '<section class="page"><h2 data-slot="k">Fine words.</h2></section>',
    '<section class="page"><h2>Fine words.</h2></section>',
    served={"k": ["Fine words."]}))
check_eq("a strict binding fails on it, and names the key",
         [(pr.is_error, "never moved or resized" in str(pr), "'k'" in str(pr))
          for pr in _p], [(True, True, True)])

# ---- the editor's own script -------------------------------------------------
# One classic script of ~20,000 lines, where a function declared twice is not
# an error: the later declaration silently wins, and a fix made to the earlier
# copy does nothing. slotsOf was declared twice (identically, luckily).
_decls = re.findall(r"^(?:async )?function\*? ?([A-Za-z_$][\w$]*)",
                    (Path(__file__).parent / "editor" / "edit.html").read_text(), re.M)
check_eq("no function is declared twice in the editor's script",
         sorted({n for n in _decls if _decls.count(n) > 1}), [])

# The same for an element id in the editor's markup: $('x') finds the FIRST,
# so a second #x is dead and the first one runs the second's handlers too.
# The undo History button (⏱) and the hub's saved-versions History were both
# #history: the hub's never showed, and ⏱ also opened the versions panel.
_markup = re.sub(r"<(script|style)\b.*?</\1>", "",
                 (Path(__file__).parent / "editor" / "edit.html").read_text(), flags=re.S)
_ids = re.findall(r'<[a-zA-Z][^>]*?\sid="([^"]+)"', _markup)
check_eq("no id is given twice in the editor's markup",
         sorted({n for n in _ids if _ids.count(n) > 1}), [])

# ---- every attribute said once -------------------------------------------------
# A start tag naming an attribute twice keeps the first and drops the rest,
# silently. Each case below was one tag two helpers wrote into, each right on
# its own, and none showed until a person moved or styled the thing.
from docsync.check import attributes_twice, check_attributes, stress_layout  # noqa: E402

check_eq("a doubled attribute is found, and named by its hook",
         attributes_twice('<h3 data-el="a.h" style="color:red" style="left:1in">x</h3>'),
         ['<h3 data-el="a.h"> (line 1): style'])
check_eq("one of each is fine", attributes_twice('<p class="a" style="b">x</p>'), [])
from docsync.check import moves_dropped  # noqa: E402
check_eq("a movable whose placement never reaches its tag is a dropped move",
         moves_dropped('<div class="card" data-el="branch.a" style="background:#fff"><h4>t</h4></div>'
                       '<p data-el="para.b" data-placed style="margin:0;position:absolute;left:1in">p</p>'
                       '<div data-el="c.d" style="color:red;margin:0;position:absolute;top:1in">q</div>'),
         ["branch.a"])
check_eq("…endnotes reorder and chevrons never park, so neither is one",
         moves_dropped('<li data-el="endnote.x">n</li>'
                       '<svg class="ds-tgl-svg" data-el="tgl.chev"></svg>'
                       '<p data-el="p" data-placed style="position:absolute">p</p>'), [])
check_eq("…and a build that places nothing has no placements to check",
         moves_dropped('<div data-el="a">x</div><div data-el="b">y</div>'), [])
check_eq("and a doubled one fails the page",
         [pr.is_error for pr in check_attributes('<i style="a" style="b"></i>')], [True])


class _StyledCardC(_CardC):
    def ul_attr(self, k): return ' data-slot="kp.bullets" style="color:#123456"'


_styled_card = card(_StyledCardC(), letter, "kp.title", "kp.bullets", "#52796F")
check_eq("card: styled bullets keep the list's indent, in the one style attribute",
         (attributes_twice(_styled_card),
          "color:#123456;margin:0;padding-left:17px" in _styled_card), ([], True))
_fn_moved = Footnotes({"x": ("A book.", "")})
_fn_moved.resolve("<p>Words.[^x]</p>")
_moved_note = _fn_moved.endnotes_html(_layout({"positions": {"endnote.x": {"x": 1, "y": 1}}}))
check_eq("a moved endnote keeps its place, in one style attribute, margin:0 last",
         (attributes_twice(_moved_note),
          "margin-bottom:.6em;margin:0;position:absolute" in _moved_note), ([], True))
_stress = stress_layout(None, '<div data-el="a"><p data-slot="b">x</p></div><i data-el="a"></i>')
check_eq("the stress layout moves every movable and styles every slot",
         (sorted(_stress["positions"]), sorted(_stress["text"])), (["a"], ["b"]))


# --- meter: one bar of parts, a chart the Chart panel edits -----------------
from docsync.layout import chart_svg as _chart_svg, _check_chart as _cc  # noqa: E402
from docsync.blocks import meter_spec as _mspec                          # noqa: E402
_ms = _mspec([("Agree", 7, "#52796F"), ("Pass", 4, "#C4A23E"), ("Disagree", 0, "#A8603F")], of=16)
_cc(_ms, "meter")
_msvg = _chart_svg(_ms, 0, 0, 1.6, 0.1)
check_eq("meter: a zero part draws nothing, the rest end to end over the track",
         _msvg.count("<path"), 3)
check_eq("meter: the parts run to axisMax, so what is left is track (7 of 16)",
         'H0.7000V' in _msvg, True)
check_eq("meter: keeps its slim height (no 0.2in chart floor)", 'V0.1000' in _msvg, True)
check_eq("meter: one category row, so the Chart panel's grid has cells to type in",
         _ms["labels"], ["Value"])
_mv = _chart_svg(_mspec([("a", 54, "#52796F"), ("b", 24, "#C3CEC5")], values=True), 0, 0, 9, 0.35)
check_eq("meter: values write each part's number inside it, ink by contrast",
         ('>54</text>' in _mv, 'fill="#FFFFFF">54' in _mv, 'fill="#1F2E2A">24' in _mv),
         (True, True, True))


def _hand_bars(html):
    c = _Coverage()
    c.feed(html)
    return c.hand_bars


check_eq("check: a bar sized by typed-in widths inside a figure is found",
         _hand_bars('<span class="bar" role="img" aria-label="7 agree">'
                    '<i style="width:43.8%"></i><i style="width:25%"></i></span>'),
         ["7 agree"])
check_eq("check: the same inside an engine chart is not",
         _hand_bars('<span class="ds-chart" data-chart="x"><svg role="img" aria-label="7 agree">'
                    '<div style="width:43.8%"></div></svg></span>'), [])
check_eq("check: a percentage width outside any figure is layout, not a bar",
         _hand_bars('<div style="width:50%"><p>two columns</p></div>'), [])


check_eq("check: a short label in a movable block with no slot is the C() trap too",
         _cov('<section class="page"><div data-el="tl"><span>Month 4</span>'
              '<span>$500</span></div></section>').trapped, ["Month 4"])
check_eq("check: a label with a slot, or a data mark, is not",
         _cov('<section class="page"><div data-el="tl"><span data-slot="m">Month 4</span>'
              '<span>$500</span></div></section>').trapped, [])
check_eq("check: an image nothing can move is found, named by its file",
         _cov('<section class="page"><img src="assets/hero.png"></section>').graphics_paged,
         ["hero.png"])
check_eq("check: a drawing is named by its container when it has no name of its own",
         _cov('<section class="page"><div class="brand"><svg></svg></div></section>').graphics_paged,
         ["svg in .brand"])
check_eq("check: movable, charted, the shape layer, or a glyph in a control are not",
         _cov('<section class="page"><div data-el="g"><svg></svg></div>'
              '<span data-chart="c" data-el="c"><svg></svg></span>'
              '<svg class="shape-layer"></svg><button><svg></svg></button>'
              '<a href="#"><img src="x.png"></a></section>').graphics_paged, [])


# --- frames: a container that moves whole while its fields stay their own ---
from docsync.blocks import field_plan as _fplan                             # noqa: E402
os.environ["DOCSYNC_EDIT"] = "1"
_fl = _layout({})
check_eq("L.frame: movable, positioned from birth so a field moved in it keeps its place, "
         "and marked a section for the editor",
         _fl.frame("card.a"), ' data-el="card.a" style="position:relative" data-frame="1"')
del os.environ["DOCSYNC_EDIT"]
check_eq("L.frame: the section mark is editing scaffolding, never published",
         _layout({}).frame("card.a"), ' style="position:relative"')
os.environ["DOCSYNC_EDIT"] = "1"
_fm = _layout({"positions": {"card.a": {"x": 1, "y": 2}}})
check_eq("L.frame: once the card itself is moved, its placement's position wins",
         "position:relative" in _fm.frame("card.a"), False)
check_eq("⟦F⟧ is a frame, not a field: the slots inside keep their own handles",
         [fid for fid, _, _ in _fplan('<div class="card"⟦F:card.a⟧><h3⟦A:t⟧>⟦T:t⟧</h3>'
                                      '<p⟦A:b⟧>⟦T:b⟧</p></div>')],
         ["field.t", "field.b"])
del os.environ["DOCSYNC_EDIT"]


# --- stacked totals: each bar's own total past its end ------------------------
_tot = _chart_svg({"type": "stacked-row", "labels": ["a", "b"], "totals": True, "grid": False,
                   "series": [{"name": "x", "data": [2, 5]}, {"name": "y", "data": [1, 0]}]},
                  0, 0, 4, 1.2)
check_eq("stacked totals: one per bar, the sum of its parts",
         re.findall(r">(\d+)</text>", _tot)[-2:] if "totals" else None, ["3", "5"])


# --- a renamed repo keeps the slug its documents are keyed under --------------
# 2026-09: primer-editor became Cider on GitHub. A room is owner~repo~project,
# so a checkout whose origin now names Cider must still stage the old slug, or
# every hub document re-keys on the next vendor and opens empty.
import subprocess as _sp                                                      # noqa: E402
import tempfile as _tf                                                        # noqa: E402
from docsync.stage import _origin_slug                                       # noqa: E402


def _slug_for(url):
    d = _tf.mkdtemp()
    _sp.run(["git", "init", "-q", d], check=True)
    _sp.run(["git", "-C", d, "remote", "add", "origin", url], check=True)
    return _origin_slug(d)


check_eq("an origin naming Cider stages Cider's own slug (its documents were migrated)",
         _slug_for("git@github.com:Hawaii-Appleseed/Cider.git"), "Hawaii-Appleseed/Cider")
check_eq("any other repo's slug passes through untouched",
         _slug_for("https://github.com/Hawaii-Appleseed/BudgetPrimerFinal.git"),
         "Hawaii-Appleseed/BudgetPrimerFinal")

# --- an image described by its file name is flagged, decoration is not ------
from docsync.check import alt_is_filename, check_alt_text                    # noqa: E402

check_eq("an alt ending in an image extension is a file name",
         alt_is_filename("chart-final-v3.png", "assets/chart-final-v3.png"), True)
check_eq("the src's own stem, without an extension, is a file name too",
         alt_is_filename("IMG_2041", "assets/IMG_2041.JPG"), True)
check_eq("a description is not a file name",
         alt_is_filename("Governor Green signs HB 2296", "assets/IMG_2041.jpg"), False)
check_eq("an empty alt is decoration, a decision rather than a miss",
         alt_is_filename("", "assets/rule.svg"), False)
check_eq("check_alt_text warns (never errors) once per file-name alt",
         [(p.check, p.level) for p in check_alt_text(
             '<img src="assets/a.png" alt="a.png"><img src="b.png" alt="">'
             '<img src="c.jpg" alt="A family at the market">')],
         [("alt-text", "warn")])
check_eq("an <img> inside a <script> string is not on the page",
         check_alt_text('<script>x = \'<img src="a.png" alt="a.png">\'</script>'), [])

# --- paragraph keys, text variables, character styles (INDESIGN_PARITY 17-22) -
from docsync.layout import text_css, char_css, TYPE_CSS                      # noqa: E402
from docsync.content import md_inline as _mdi, VAR_RE                        # noqa: E402

check_eq("the paragraph keys are the CSS they mean",
         text_css({"wrap": "pretty", "hyphenate": True, "keep": "together",
                   "figures": "oldstyle", "smallCaps": "small"}),
         "text-wrap:pretty;-webkit-hyphens:auto;hyphens:auto;break-inside:avoid;"
         "font-variant-numeric:oldstyle-nums;font-variant-caps:small-caps;"
         "display:inline-block;width:100%")
check_eq("ligatures are on unless a style says false; true writes nothing",
         (text_css({"ligatures": True}), text_css({"ligatures": False})),
         ("", "font-variant-ligatures:none"))
check_eq("a drop cap and a list look ride as custom properties for TYPE_CSS",
         text_css({"dropCap": 3, "list": {"marker": "decimal", "color": "#123456", "indent": 0.25}}),
         "--ds-cap:3;--ds-lt:decimal;--ds-mk:#123456;--ds-li:0.25in;display:inline-block;width:100%")
for _bad, _says in (({"wrap": "even"}, "wrap"), ({"keep": "all"}, "keep"),
                    ({"figures": "tab"}, "figures"), ({"smallCaps": True}, "smallCaps"),
                    ({"hyphenate": 1}, "hyphenate"), ({"dropCap": 1}, "dropCap"),
                    ({"list": {"marker": "star"}}, "marker"),
                    ({"list": {"bullet": "disc"}}, "unknown key"),
                    ({"list": {"indent": -1}}, "indent")):
    check_eq(f"a style saying {_bad} is refused at load, naming {_says}",
             _says in _layout_error({"text": {"x.y": _bad}}), True)

_plain = _layout({"boxes": [{"id": "b1", "page": 1, "x": 1, "y": 1, "w": 2, "md": "hi"}]})
check_eq("a layout using none of them emits none of their CSS or runtime",
         "--ds-cap" in _plain.layer(1) or "__dsVars" in _plain.layer(1)
         or ".ds-cs[" in _plain.layer(1), False)
_cap = _layout({"text": {"intro.p1": {"dropCap": 2}}})
_h = _cap.layer(1) + _cap.layer(2)
check_eq("TYPE_CSS rides out once, when a style asks for a drop cap",
         _h.count(TYPE_CSS), 1)
_v = _layout({"boxes": [{"id": "f", "page": 1, "x": 1, "y": 1, "w": 2, "md": "p. {page}"}]})
check_eq("the variables runtime rides out once, when a box uses a token",
         (_v.layer(1) + _v.layer(2)).count("window.__dsVars"), 1)
check_eq("only the named tokens are read — a renderer's own {fy} is left alone",
         _mdi("{fy} and {page}"),
         '{fy} and <span class="ds-var" data-var="page"></span>')
check_eq("{pageof:key} carries the key and a '?' for a reader with no script",
         _mdi("see {pageof:whopays.h1}"),
         'see <span class="ds-var" data-var="pageof" data-ref="whopays.h1">?</span>')
import os as _os                                                             # noqa: E402
_os.environ["DOCSYNC_DATE"] = "2026-03-04"
check_eq("{date} is the build date, pinned by DOCSYNC_DATE",
         _mdi("{date}"), '<span class="ds-var" data-var="date">March 4, 2026</span>')
del _os.environ["DOCSYNC_DATE"]
check_eq("VAR_RE knows exactly these names",
         [m.group(1) for m in VAR_RE.finditer("{page}{pages}{section}{title}{date}{contents}{pageof:a.b}{x}")],
         ["page", "pages", "section", "title", "date", "contents", "pageof:a.b"])

check_eq("a character-style run is a span naming its style, words formatted inside",
         _mdi("a [big **red** run]{.Accent} b"),
         'a <span class="ds-cs" data-cs="Accent">big <b>red</b> run</span> b')
check_eq("a footnote ref is never a run",
         _mdi("x [^fn1]{.Accent}"), "x [^fn1]{.Accent}")
check_eq("a link inside a run is the link, not a link from the run's bracket",
         _mdi("[the [GET](https://x.org/a) now]{.P}"),
         '<span class="ds-cs" data-cs="P">the <a href="https://x.org/a">GET</a> now</span>')
check_eq("a character style drops the paragraph's keys",
         char_css({"color": "#C0392B", "align": "center", "leading": 2, "dropCap": 3,
                   "figures": "tabular"}),
         "color:#C0392B;font-variant-numeric:tabular-nums")
_cs = _layout({"textStyles": {"Accent": {"color": "#C0392B", "align": "center"}},
               "boxes": [{"id": "b", "page": 1, "x": 1, "y": 1, "w": 2,
                          "md": "a [run]{.Accent} and [ghost]{.Nobody}"}]})
_csh = _cs.layer(1)
check_eq("one rule per style a run wears, and none for a name with no style",
         ('.ds-cs[data-cs="Accent"]{color:#C0392B}' in _csh, "Nobody" in _csh), (True, False))

# --- paragraph spacing and column alignment (INDESIGN_PARITY 14-15) ---------
from docsync.layout import SPACE_CSS, decimal_tail, decimal_cell             # noqa: E402

check_eq("spacing rides as custom properties; hang folds into the indents",
         text_css({"spaceBefore": 0.2, "spaceAfter": 0.1, "indent": 0.3,
                   "hang": 0.25, "indentLeft": 0.5, "indentRight": 0.1}),
         "--ds-sb:0.2in;--ds-sa:0.1in;--ds-il:0.75in;--ds-ir:0.1in;--ds-ti:0.05in")
for _bad in ({"spaceBefore": -0.1}, {"indent": 4}, {"hang": "x"}):
    check_eq(f"spacing {_bad} is refused at load",
             "no error raised" != _layout_error({"text": {"a.b": _bad}}), True)
_sp = _layout({"text": {"a.b": {"spaceAfter": 0.1}}})
check_eq("SPACE_CSS rides out once when a style spaces paragraphs, without TYPE_CSS",
         ((_sp.layer(1) + _sp.layer(2)).count(SPACE_CSS), TYPE_CSS in _sp.layer(1)), (1, False))
check_eq("a decimal tail is the point, its digits and any suffix",
         [decimal_tail(x) for x in ("$1,234.56", "12", "-3.5%", "n/a", "**4.2**", "—")],
         [".56", "", ".5%", None, None, None])
check_eq("a decimal cell is the whole part and a fixed-width box for the rest",
         decimal_cell("$1,234.5", 3),
         '$1,234<span class="ds-dec" style="display:inline-block;min-width:3ch;'
         'text-align:left">.5</span>')
_tb = {"id": "t", "page": 1, "x": 1, "y": 1, "w": 3, "header": True,
       "rows": [["A", "B"], ["x", "1.5"], ["y", "22"]]}
check_eq("colAlign needs one entry per column",
         "colAlign" in _layout_error({"tables": [dict(_tb, colAlign=["right"])]}), True)
check_eq("colAlign refuses an alignment it does not know",
         "colAlign[1]" in _layout_error({"tables": [dict(_tb, colAlign=[None, "middle"])]}), True)
_dh = _layout({"tables": [dict(_tb, colAlign=[None, "decimal"])]}).tables_html(1)
check_eq("a decimal column splits its figures and leaves its header whole",
         (_dh.count('class="ds-dec"'), "<th" in _dh and ">B</th>" in _dh), (2, True))
_th = re.sub(r"<style>.*?</style>", "", _layout({"tables": [_tb]}).tables_html(1))
check_eq("a table nobody aligned renders as it did",
         "ds-dec" in _th or "text-align" in _th, False)
# A placed table brings its own floor (padding, rules, a line per empty cell)
# for a report whose stylesheet never mentions .ds-table: once per document,
# at zero specificity, so a report's own table rules still win.
_pl = _layout({"tables": [_tb, dict(_tb, id="t2", page=2)]})
_p1, _p2 = _pl.tables_html(1), _pl.tables_html(2)
check_eq("a placed table's floor CSS goes out once, ahead of the first table",
         (_p1.startswith(Layout.PLACED_TABLE_CSS), Layout.PLACED_TABLE_CSS in _p2), (True, False))
check_eq("the floor is :where() all through, so any report rule outranks it",
         re.findall(r"(?:^|})([^{}]+)\{", Layout.PLACED_TABLE_CSS.replace("<style>", "")),
         [":where(table.ds-table[data-placed])",
          ":where(table.ds-table[data-placed]) :where(td,th)",
          ":where(table.ds-table[data-placed]) :where(td,th):empty::before"])
# A flow table is a REAL <table> in the page's own flow: static, full width,
# with the same cell hooks the editor edits, and the header row marked so the
# phone layout can hide it and label each stacked value from it.
_ft = {"tables": [{"id": "ft", "flow": True, "header": True,
                   "rows": [["The change", "Fix"], ["a **b**", "c"]]}]}
_ftp = _layout(_ft)
check_eq("a flow table is not drawn at an absolute page position",
         _ftp.tables_html(1), "")
_fth = _ftp.flow_table("ft")
check("a flow table is a real <table> in flow", _fth, '<table class="ds-table ds-flow"')
check_eq("a flow table never positions itself absolutely",
         "position:absolute" in _fth, False)
check("each stacked value carries its column's header for phones",
      _fth, 'data-label="The change"')
check("the phone layout hides the header row", _fth, "tr.ds-hdr{display:none}")
check_eq("a flow table needs no page, x, y or w", _layout_error(
    {"tables": [{"id": "z", "flow": True, "rows": [["a"], ["b"]]}]}), "no error raised")
check("but a placed table still does", _layout_error(
    {"tables": [{"id": "z", "rows": [["a"]]}]}), "page")
_fte = _layout(_ft)
os.environ["DOCSYNC_EDIT"] = "1"
try:
    _fted = _fte.flow_table("ft")
    _ftu = _layout({}).flow_table("nope", {"header": True, "rows": [["h"], ["v"]]})
finally:
    del os.environ["DOCSYNC_EDIT"]
check("editing a flow table: the editor selects it and edits its cells",
      _fted, 'data-el="table.ft"')
check("editing a flow table: each cell has its hook", _fted, 'data-cell="1,0"')
check("a flow table drawn from a renderer default is flagged unseeded",
      _ftu, 'data-unseeded="nope"')

# docsync.check: a table drawn as sibling grid <div>s is not a real table.
from docsync.check import check_fake_tables
_fake = ("<style>.row{display:grid;grid-template-columns:2in 1fr}</style>"
         "<div class=\"rows\">" + "".join(
             "<div class=\"row\"><div>a</div><div>b</div></div>" for _ in range(4))
         + "</div>")
check_eq("div+grid rows are reported as a fake table",
         [p.check for p in check_fake_tables(_fake)], ["fake-table"])
check_eq("and the exact line editability_ok accepts silences it",
         check_fake_tables(_fake, accepted={".row x4 in .rows"}), [])
check_eq("a real <table> is not a fake table",
         check_fake_tables("<table><tr><td>a</td><td>b</td></tr></table>"), [])
_two = _fake.replace('<div class="row"><div>a</div><div>b</div></div>' * 4,
                     '<div class="row"><div>a</div><div>b</div></div>' * 2)
check_eq("two grid blocks (cards, not rows) are not a table",
         check_fake_tables(_two), [])

# --- threaded frames (INDESIGN_PARITY 4) ------------------------------------
from docsync.layout import THREAD_JS                                         # noqa: E402

_tf = lambda **k: dict({"page": 1, "x": 1, "y": 1, "w": 3, "h": 2}, **k)
_th = _layout({"boxes": [_tf(id="a", md="one\n\ntwo", next="b"), _tf(id="b", page=2, md="")]})
_t1, _t2 = _th.text_boxes(1), _th.text_boxes(2)
check_eq("a frame is exactly its height, and says which thread and place it is",
         ('data-thread="a" data-thread-i="0"' in _t1, "height:2in" in _t1,
          "min-height" in _t1, 'data-thread="a" data-thread-i="1"' in _t2), (True, True, False, True))
check_eq("the runtime rides out once for a document, on the first threaded page",
         (_t1 + _t2).count(THREAD_JS), 1)
check_eq("the story sits in the first frame; the next frame is empty until poured",
         ("two" in _t1, "two" in _t2), (True, False))
check_eq("a layout with no thread emits none of it",
         THREAD_JS in _layout({"boxes": [_tf(id="a", md="x")]}).text_boxes(1), False)
for _bad, _says in (
        ([_tf(id="a", md="x", next="b"), _tf(id="b", page=2, md="words")], "only the first frame"),
        ([_tf(id="a", md="x", next="b"), dict(_tf(id="b", page=2, md=""), h=None)], "needs a height"),
        ([_tf(id="a", md="x", next="zz")], "not a text box"),
        ([_tf(id="a", md="x", next="a")], "itself"),
        ([_tf(id="a", md="x", next="c"), _tf(id="b", md="y", next="c"), _tf(id="c", md="")], "already continues"),
        ([_tf(id="a", md="x", next="b"), _tf(id="b", md="", next="a")], "loops"),
        ([_tf(id="a", md="x", next="b", cols=2), _tf(id="b", md="")], "columns"),
        ([_tf(id="a", md="x", next="b", anchor={"to": "k", "dy": 0}), _tf(id="b", md="")], "anchor")):
    check_eq(f"a thread is refused: {_says}", _says in _layout_error({"boxes": _bad}), True)
check_eq("a master box cannot thread",
         "next" in _layout_error({"masters": {"M": {"boxes": [_tf(id="f", md="x", next="g")]}},
                                  "boxes": [_tf(id="g", md="y")]}), True)

# --- paragraph rules, borders and shading (INDESIGN_PARITY 28) --------------
from docsync.layout import PARA_CSS                                          # noqa: E402

check_eq("rules, shading, padding and a border ride as custom properties",
         text_css({"ruleAbove": {"weight": 2, "color": "#1E6194", "offset": 0.05},
                   "shade": "#FFF6D8", "paraPad": 0.1,
                   "paraBorder": {"weight": 1, "sides": "left", "color": "#333333"}}),
         "--ds-ra:2pt;--ds-ra-c:#1E6194;--ds-ra-o:0.05in;--ds-sh:#FFF6D8;--ds-pp:0.1in;"
         "--ds-pbl:1pt solid #333333")
for _bad in ({"ruleAbove": {"thickness": 1}}, {"ruleBelow": {"weight": 0}},
             {"paraBorder": {"sides": "diagonal"}}, {"shade": "beige"}, {"paraPad": 2}):
    check_eq(f"a paragraph look {_bad} is refused at load",
             "no error raised" != _layout_error({"text": {"a.b": _bad}}), True)
_pr = _layout({"text": {"a.b": {"ruleBelow": {"weight": 1}}}})
check_eq("PARA_CSS rides out once when a style rules a paragraph, alone",
         ((_pr.layer(1) + _pr.layer(2)).count(PARA_CSS), SPACE_CSS in _pr.layer(1)), (1, False))

# --- automatic formatting: GREP and nested styles (INDESIGN_PARITY 29) ------
import base64 as _b64                                                        # noqa: E402
from docsync.layout import GREP_JS                                           # noqa: E402

_gs = text_css({"grep": [{"pattern": r"\d+%", "style": "Fig", "case": False}],
                "nested": [{"style": "Lead", "through": ":"}]})
check_eq("rules ride as one base64 JSON custom property",
         json.loads(_b64.b64decode(_gs.split("--ds-gs:")[1])),
         {"g": [[r"\d+%", "i", "Fig"]], "n": [["Lead", ":"]]})
_ats = {"Fig": {"color": "#C0392B"}}
_ok = _layout({"textStyles": _ats, "boxes": [_tf(id="a", md="12%", style={"grep": [{"pattern": r"\d+%", "style": "Fig"}]})]})
_okh = _ok.layer(1) + _ok.text_boxes(1)
check_eq("GREP_JS rides once, with a character rule for the style it names",
         (_okh.count(GREP_JS), '.ds-cs[data-cs="Fig"]{color:#C0392B}' in _okh), (1, True))
check_eq("no rule, no runtime",
         GREP_JS in _layout({"boxes": [_tf(id="a", md="x")]}).layer(1), False)
check_eq("a rule naming no text style is refused at load",
         "'Nobody'" in _layout_error({"textStyles": _ats, "boxes": [_tf(id="a", md="x",
             style={"grep": [{"pattern": "x", "style": "Nobody"}]})]}), True)
for _bad, _says in (({"grep": [{"pattern": "(", "style": "Fig"}]}, "not a pattern"),
                    ({"grep": [{"pattern": "a?", "style": "Fig"}]}, "matches nothing"),
                    ({"grep": [{"pattern": "(?P<n>a)", "style": "Fig"}]}, "construct"),
                    ({"nested": [{"style": "Fig", "through": "words:two"}]}, "through"),
                    ({"grep": [{"pattern": "a", "style": "Fig", "flags": "i"}]}, "unknown key")):
    check_eq(f"an automatic rule is refused: {_says}",
             _says in _layout_error({"textStyles": _ats, "text": {"a.b": _bad}}), True)

# --- frame fitting (INDESIGN_PARITY 30) -------------------------------------
from docsync.layout import FIT_CSS                                           # noqa: E402

_fb = _layout({"boxes": [_tf(id="i", md="![a photo](assets/p.jpg)", fit="cover", fitAlign="top")]})
_fbh = _fb.text_boxes(1)
check_eq("an image box fits: exact height, the class, the two custom properties, the rule once",
         ('class="ds-textbox ds-fitbox"' in _fbh, "height:2in;--ds-fit:cover;--ds-fitpos:top" in _fbh,
          "min-height" in _fbh, _fbh.count(FIT_CSS)), (True, True, False, 1))
check_eq("a designed image fits in the flow with its frame height",
         _layout({"img": {"p": {"fit": "contain", "fitAlign": "bottom right", "h": 3}}}).img_css("p"),
         "height:3in;object-fit:contain;object-position:bottom right")
check_eq("no fit, no rule and no height",
         (FIT_CSS in _layout({"boxes": [_tf(id="i", md="![a](b.jpg)")]}).text_boxes(1),
          _layout({"img": {"p": {"h": 3}}}).img_css("p")), (False, ""))
for _bad, _says in (({"boxes": [_tf(id="i", md="words", fit="cover")]}, "one image"),
                    ({"boxes": [dict(_tf(id="i", md="![a](b.jpg)", fit="cover"), h=None)]}, "height"),
                    ({"img": {"p": {"fit": "cover", "crop": {"imgW": 3, "dx": 0, "dy": 0}}}}, "crop and fit"),
                    ({"img": {"p": {"fit": "zoom"}}}, "must be one of"),
                    ({"img": {"p": {"fitAlign": "top"}}}, "without a fit")):
    check_eq(f"fitting is refused: {_says}", _says in _layout_error(_bad), True)

# --- special characters and breaks (INDESIGN_PARITY 31) ---------------------
from docsync.layout import BREAK_CSS                                         # noqa: E402

check_eq("breaks: a <br>, a <wbr>, and markers for column and frame",
         _mdi("a{break:line}b{break:optional}c{break:column}d{break:frame}e"),
         'a<br>b<wbr>c<span class="ds-brk" data-brk="column"></span>d'
         '<span class="ds-brk" data-brk="frame"></span>e')
check_eq("there is no page break: {break:page} stays as typed",
         _mdi("{break:page}"), "{break:page}")
check_eq("special characters pass through as themselves",
         _mdi("$1.2\u00a0million re\u00adform"), "$1.2\u00a0million re\u00adform")
_bk = _layout({"boxes": [_tf(id="a", md="x{break:column}y")]})
check_eq("BREAK_CSS rides once when a column or frame break is used; a break needs no variables runtime",
         ((_bk.layer(1) + _bk.layer(2)).count(BREAK_CSS), "__dsVars" in _bk.layer(1)), (1, False))
check_eq("a line break alone needs no CSS",
         BREAK_CSS in _layout({"boxes": [_tf(id="a", md="x{break:line}y")]}).layer(1), False)

# --- the older open items (INDESIGN_PARITY 5, 7, 8, 9, 11, 23) ---------------
from docsync.layout import (BLEED_JS, GRID_JS, path_d, part_d,                # noqa: E402
                            anchor_attrs)

check_eq("contour wrap rides as data-wrap-shape; a box wrap says nothing new",
         (anchor_attrs({"anchor": {"to": "k"}, "wrap": "left", "wrapShape": "contour"}),
          anchor_attrs({"anchor": {"to": "k"}, "wrap": "left", "wrapShape": "box"})),
         (' data-anc="k" data-anc-dy="0" data-wrap="left" data-wrap-shape="contour"',
          ' data-anc="k" data-anc-dy="0" data-wrap="left"'))
check_eq("an unknown wrap shape is refused",
         "wrapShape" in _layout_error({"boxes": [_tf(id="a", md="x", anchor={"to": "k"}, wrap="left",
                                                     wrapShape="star")]}), True)
_os.environ["DOCSYNC_MARKS"] = "1"
_bl = _layout({"boxes": [_tf(id="a", md="x")]})
check_eq("a marks export carries the bleed sheet and its runtime, once",
         ((_bl.layer(1) + _bl.layer(2)).count(BLEED_JS), "@page{size:8.75in 11.25in" in _bl.layer(1)
          or "@page{size:8.75in 11.25in" in _layout({"boxes": []}).layer(1)), (1, True))
del _os.environ["DOCSYNC_MARKS"]
check_eq("a normal build carries no bleed", BLEED_JS in _layout({}).layer(1), False)
check_eq("skew: a CSS skew on a box, a skewX in a shape's one transform",
         ("transform:skewX(15deg)" in _layout({"boxes": [_tf(id="a", md="x", skew=15)]}).text_boxes(1),
          "skewX(-20)" in _layout({"shapes": [{"id": "s", "page": 1, "kind": "rect", "x": 1, "y": 1,
                                               "w": 1, "h": 1, "skew": -20}]}).layer(1)), (True, True))
check_eq("skew past 60 degrees is refused",
         "skew" in _layout_error({"boxes": [_tf(id="a", md="x", skew=70)]}), True)
_ly = _layout({"layers": [{"name": "A"}, {"name": "B", "hidden": True}],
               "boxes": [_tf(id="a", md="on A", layer="A"), _tf(id="b", md="on B", layer="B")]})
_lyh = _ly.text_boxes(1)
check_eq("a layer lifts its objects, and a hidden layer draws none of them",
         ("z-index:102" in _lyh, "on B" in _lyh), (True, False))
check_eq("an object on a layer that does not exist is refused",
         "no layer called" in _layout_error({"boxes": [_tf(id="a", md="x", layer="Nope")]}), True)
_gr = _layout({"grid": {"increment": 12, "start": 0.75}, "text": {"a.b": {"grid": True}}})
check_eq("the grid runtime rides once with its numbers; no style on the grid, no runtime",
         ("var G=12,S=0.75" in _gr.layer(1), GRID_JS[:20] in _layout({"grid": {"increment": 12}}).layer(1)),
         (True, False))
check_eq("a grid increment out of range is refused",
         "grid.increment" in _layout_error({"grid": {"increment": 100}}), True)
_mt = _layout({"masters": {"S": {"tables": [{"id": "k", "x": 1, "y": 9, "w": 3, "rows": [["a", "b"]]}]}},
               "pageMasters": {"2": "S", "3": "S"}})
check_eq("a shared layout's table is drawn on every page using it",
         (_mt.tables_html(2).count("<table"), _mt.tables_html(3).count("<table")), (1, 1))
_mo = _layout({"masters": {"S": {"boxes": [_tf(id="f", md="p. {page}", fill="#EEEEEE")]}},
               "pageMasters": {"2": "S", "3": "S"},
               "masterOverrides": {"f@3": {"x": 5, "fill": None}}})
check_eq("an override changes only its page, only its keys; null removes a key",
         ((_mo.master_items(2, "boxes")[0]["x"], _mo.master_items(3, "boxes")[0]["x"]),
          "fill" in _mo.master_items(3, "boxes")[0], _mo.master_items(3, "boxes")[0]["md"]),
         ((1, 5), False, "p. 3"))
check_eq("an override naming no shared-layout item on that page is refused",
         "no shared-layout item" in _layout_error({"masters": {"S": {"boxes": [_tf(id="f", md="x")]}},
                                                   "pageMasters": {"2": "S"},
                                                   "masterOverrides": {"f@9": {}}}), True)
check_eq("a pen path: lines where there are no handles, a curve where there are",
         path_d([{"x": 0, "y": 1}, {"x": 0.5, "y": 0, "out": [0.25, 0]}, {"x": 1, "y": 1}], True, 1, 1, 2, 1),
         "M1.0 2.0L2.0 1.0C2.5 1.0 3.0 2.0 3.0 2.0L1.0 2.0Z")
check_eq("a Pathfinder part is path data in its own box",
         part_d({"kind": "rect", "x": 0.5, "y": 0, "w": 0.5, "h": 1}, 0, 0, 2, 2), "M1.0 0.0H2.0V2.0H1.0Z")
_cmp = {"id": "c", "page": 1, "kind": "compound", "x": 1, "y": 1, "w": 2, "h": 2,
        "parts": [{"kind": "rect", "x": 0, "y": 0, "w": 1, "h": 1},
                  {"kind": "ellipse", "x": 0.5, "y": 0.5, "w": 0.5, "h": 0.5}]}
check_eq("Pathfinder: union one path, exclude even-odd, subtract a mask, intersect a clip",
         ['fill-rule="nonzero"' in _layout({"shapes": [dict(_cmp, op="union")]}).layer(1),
          'fill-rule="evenodd"' in _layout({"shapes": [dict(_cmp, op="exclude")]}).layer(1),
          "<mask" in _layout({"shapes": [dict(_cmp, op="subtract")]}).layer(1),
          "<clipPath" in _layout({"shapes": [dict(_cmp, op="intersect")]}).layer(1)],
         [True, True, True, True])
check_eq("Pathfinder needs two parts of drawable kinds",
         "two shapes" in _layout_error({"shapes": [dict(_cmp, op="union", parts=_cmp["parts"][:1])]}), True)

# --- INDESIGN_PARITY.md 32–46: the gap list read from InDesign ---------------
from docsync.layout import (tint_hex, polygon_points, star_points, OT_FEATURES,   # noqa: E402
                            tabs_css, TABS_JS, ORDER_JS, table_merges)
from docsync import qr as _qr                                                  # noqa: E402
check_eq("46: strike, baseline shift and OpenType features are CSS",
         [x in text_css({"strike": True, "underline": True, "shift": 3, "features": ["frac", "ss02"]})
          for x in ("text-decoration:underline line-through", "vertical-align:3pt", "'frac' 1", "'ss02' 1")],
         [True] * 4)
check_eq("46: an unknown OpenType tag is refused",
         "features" in _layout_error({"text": {"x": {"features": ["wxyz"]}}}), True)
check_eq("40: a tint mixes toward white", tint_hex("#1E6194", 20), "#D2DFEA")
_sw = _layout({"swatches": {"Blue": "#1E6194"}, "boxes": [_tf(id="s", md="x", fill="@Blue 20%",
                                                            style={"color": "@Blue"})]})
check_eq("40: a swatch reference resolves at load, tint and all",
         (_sw.boxes[0]["fill"], _sw.boxes[0]["style"]["color"]), ("#D2DFEA", "#1E6194"))
check_eq("40: a reference to no swatch is refused",
         "swatch" in _layout_error({"boxes": [_tf(id="s", md="x", fill="@Nope")]}).lower(), True)
_GR = {"type": "linear", "angle": 90, "stops": [{"color": "@Blue", "at": 0}, {"color": "#FFFFFF00", "at": 1}]}
_gs = _layout({"swatches": {"Blue": "#1E6194", "Sky": _GR},
               "boxes": [_tf(id="a", md="x", fill="@Sky"), _tf(id="b", md="x", fill="@Sky 50%")],
               "shapes": [{"id": "s1", "kind": "rect", "page": 1, "x": 1, "y": 1, "w": 1, "h": 1, "fill": "@Sky"}]})
check_eq("64: a gradient swatch resolves to the gradient, its stops' own swatches too",
         (_gs.boxes[0]["fill"]["stops"][0]["color"], _gs.shapes[0]["fill"]["type"]), ("#1E6194", "linear"))
check_eq("64: a tint of a gradient swatch tints every stop and keeps a stop's alpha",
         [st["color"] for st in _gs.boxes[1]["fill"]["stops"]], [tint_hex("#1E6194", 50), "#FFFFFF00"])
check_eq("64: a gradient swatch draws as a gradient",
         "linear-gradient(90deg, #1E6194 0%" in _gs.text_boxes(1), True)
check_eq("64: a gradient swatch named where only a colour goes is refused, and says why",
         "is a gradient" in _layout_error({"swatches": {"Sky": _GR, "Blue": "#1E6194"},
                                           "boxes": [_tf(id="a", md="x", style={"color": "@Sky"})]}), True)
check_eq("64: so is one on a table's fill",
         "is a gradient" in _layout_error({"swatches": {"Sky": _GR, "Blue": "#1E6194"},
                                           "tables": [{"id": "t", "page": 1, "x": 1, "y": 1, "w": 3,
                                                       "rows": [["a"]], "fill": "@Sky"}]}), True)
check_eq("64: a broken gradient swatch is refused at load",
         "two or more stops" in _layout_error({"swatches": {"G": {"type": "linear", "stops": [{"color": "#000", "at": 0}]}}}),
         True)
_fd = _layout({"boxes": [_tf(id="f", md="x", fade={"angle": 180, "from": 0.3, "to": 0.9})]}).text_boxes(1)
check_eq("64: a fade is a mask from opaque to clear along its angle",
         "mask-image:linear-gradient(180deg,#000 30%,transparent 90%)" in _fd, True)
_ff = _layout({"boxes": [_tf(id="f", md="x", feather=0.2, fade={})]}).text_boxes(1)
check_eq("64: a fade with a feather intersects three masks",
         _ff.count("linear-gradient(") >= 3 and "mask-composite:intersect" in _ff, True)
check_eq("64: a feather alone draws exactly what it did",
         feather_css({"feather": 0.2}),
         ";-webkit-mask-image:linear-gradient(to right,transparent,#000 0.2in,#000 calc(100% - 0.2in),transparent),"
         "linear-gradient(to bottom,transparent,#000 0.2in,#000 calc(100% - 0.2in),transparent);"
         "-webkit-mask-composite:source-in;mask-image:linear-gradient(to right,transparent,#000 0.2in,#000 calc(100% - 0.2in),transparent),"
         "linear-gradient(to bottom,transparent,#000 0.2in,#000 calc(100% - 0.2in),transparent);mask-composite:intersect")
for _bad, _says in (({"from": 0.8, "to": 0.5}, "past 'from'"), ({"size": 1}, "unknown size"),
                    ({"to": 2}, "fraction between 0 and 1")):
    check_eq(f"64: a fade is refused: {_says}",
             _says in _layout_error({"boxes": [_tf(id="f", md="x", fade=_bad)]}), True)
check_eq("64: a drawn shape does not fade — its fill does",
         "fades through its fill" in _layout_error({"shapes": [{"id": "s", "kind": "rect", "page": 1, "x": 1, "y": 1,
                                                                 "w": 1, "h": 1, "fill": "#000", "fade": {}}]}), True)
check_eq("39: an outer glow is a box-shadow, a feather a mask",
         [x in _layout({"boxes": [_tf(id="g", md="x", glow={"size": 0.1, "color": "#FFCC00"},
                                      feather=0.2)]}).text_boxes(1)
          for x in ("box-shadow:", "mask-image:")], [True, True])
check_eq("41: a polygon and a star have their points",
         (len(polygon_points(0, 0, 2, 2, 6)), len(star_points(0, 0, 2, 2, 5, 0.5))), (6, 10))
check("41: a bevelled rectangle draws as a path",
      _layout({"shapes": [{"id": "r", "page": 1, "kind": "rect", "x": 1, "y": 1, "w": 2, "h": 1,
                           "corner": "bevel", "r": 0.2}]}).layer(1), "<path")
check_eq("42: a QR code's size follows its version",
         [len(_qr.matrix("x" * n)) for n in (10, 30, 120, 213)], [21, 29, 45, 57])
try:
    _qr.matrix("x" * 214)
    check_eq("42: more than a QR code carries is refused", "accepted", "refused")
except _qr.QRError:
    pass
check("42: a qr shape is a crisp nested svg with its link as its name",
      _layout({"shapes": [{"id": "q", "page": 1, "kind": "qr", "x": 1, "y": 1, "w": 1, "h": 1,
                           "data": "https://hiappleseed.org"}]}).layer(1),
      'aria-label="QR code: https://hiappleseed.org"')
_cap = _layout({"boxes": [_tf(id="im", md="![A chart of rents](assets/r.png)"),
                          _tf(id="c", md="{caption:text.im}")]})
check_eq("43: a caption is the picture's description", _cap.caption_of("text.im"), "A chart of rents")
check("43: and a box saying {caption:…} shows it", _cap.text_boxes(1), "A chart of rents")
check_eq("45: a conditional run is a span; a condition switched off hides it",
         ['data-cond="print"' in md_inline("Only [in print]{?print}."),
          "display:none" in _layout({"conditions": {"print": False},
                                     "boxes": [_tf(id="c", md="a [b]{?print}")]}).layer(1)],
         [True, True])
check_eq("45: a condition is true or false",
         "conditions" in _layout_error({"conditions": {"print": "yes"}}), True)
check_eq("36: margins and columns load, and are checked",
         (_layout({"margins": {"top": 0.75, "columns": 3, "gutter": 0.2}}).margins["columns"],
          "margins.columns" in _layout_error({"margins": {"columns": 0}}),
          "unknown key" in _layout_error({"margins": {"cols": 2}}),
          "margins.gutter" in _layout_error({"masters": {"A": {"margins": {"gutter": 5}}}})),
         (3, True, True, True))
check_eq("32: tab stops are one custom property, sorted",
         tabs_css([{"at": 4, "align": "right", "leader": "."}, {"at": 1}]), "1ln 4rd")
check("32: a style with tab stops carries them", text_css({"tabs": [{"at": 2, "align": "decimal"}]}),
      "--ds-tabs:2dn")
check_eq("32: a stop past 20in, or an unknown alignment, is refused",
         ["tabs" in _layout_error({"text": {"x": {"tabs": [{"at": 30}]}}}),
          "align" in _layout_error({"text": {"x": {"tabs": [{"at": 1, "align": "justify"}]}}})],
         [True, True])
check_eq("32: the tab runtime goes out once, and only when a style has stops",
         [_layout({"boxes": [_tf(id="t", md="a\tb", style={"tabs": [{"at": 2}]})]}).layer(1).count(TABS_JS),
          TABS_JS in _layout({"boxes": [_tf(id="t", md="a\tb")]}).layer(1)],
         [1, False])
_tt = {"id": "t", "page": 1, "x": 1, "y": 1, "w": 4,
       "rows": [["A", "B", "C"], ["1", "2", "3"], ["4", "5", "6"], ["T", "", "9"]]}
_t33 = _layout({"tables": [dict(_tt, headerRows=1, footerRows=1, merges=[[1, 0, 2, 1], [3, 0, 1, 2]],
                                inset=0.05, valign="bottom", rowh=[None, 0.5, None, None],
                                bandStroke={"w": 2, "color": "#333333"})]}).tables_html(1)
check_eq("33: merges span, cover, and header and footer rows get their sections",
         ['rowspan="2"' in _t33, 'colspan="2"' in _t33, _t33.count("<td ") + _t33.count("<th "),
          "<thead>" in _t33 and "<tfoot>" in _t33, "padding:0.05in" in _t33,
          "vertical-align:bottom" in _t33, 'style="height:0.5in"' in _t33, "2px solid #333333" in _t33],
         [True, True, 10, True, True, True, True, True])
check_eq("33: a plain table's markup is as it was",
         ["<thead>" in _layout({"tables": [dict(_tt, header=True)]}).tables_html(1),
          "rowspan" in _layout({"tables": [dict(_tt, header=True)]}).tables_html(1)], [False, False])
check_eq("33: a merge must span two cells, stay inside, and not overlap",
         ["two cells" in _layout_error({"tables": [dict(_tt, merges=[[0, 0, 1, 1]])]}),
          "runs off" in _layout_error({"tables": [dict(_tt, merges=[[3, 2, 2, 1]])]}),
          "overlaps" in _layout_error({"tables": [dict(_tt, merges=[[0, 0, 2, 2], [1, 1, 2, 1]])]})],
         [True, True, True])
check_eq("33: table_merges maps each top-left to its span",
         table_merges({"merges": [[0, 1, 1, 2]]}, 2, 3), {(0, 1): (1, 2)})
_ro = _layout({"readingOrder": {"1": ["text.b", "text.a"]},
               "boxes": [_tf(id="a", md="first in the file"), _tf(id="b", md="read first", y=4)]})
check_eq("34: a reading order stamps its objects and sends its runtime once",
         ['data-ro="0"' in _ro.text_boxes(1), 'data-ro="1"' in _ro.text_boxes(1),
          _ro.layer(1).count(ORDER_JS)], [True, True, 1])
check_eq("34: no reading order, no runtime; an id listed twice is refused",
         [ORDER_JS in _layout({"boxes": [_tf(id="a", md="x")]}).layer(1),
          "twice" in _layout_error({"readingOrder": {"1": ["text.a"], "2": ["text.a"]}})],
         [False, True])

# --- INDESIGN_PARITY.md 49, 54, 56, 60 ------------------------------------------
from docsync.layout import page_label, vars_js, VARS_JS, box_frame_css   # noqa: E402
_secs = [{"from": 1, "style": "lower-roman"}, {"from": 3}, {"from": 10, "style": "upper-alpha", "prefix": "A-"}]
check_eq("54: sections number pages — roman front matter, the body from 1, an appendix",
         [page_label(n, _secs) for n in (1, 2, 3, 9, 10, 11)], ["i", "ii", "1", "7", "A-A", "A-B"])
check_eq("54: without sections the variables runtime is the same string; with them it labels",
         [vars_js(None) is VARS_JS, "t=n?L(n):''" in vars_js(_secs)], [True, True])
check_eq("54: a master's {page} follows the sections",
         _layout({"pages": {"sections": _secs},
                  "masters": {"S": {"boxes": [_tf(id="f", md="p. {page}")]}},
                  "pageMasters": {"2": "S"}}).master_items(2, "boxes")[0]["md"], "p. ii")
check_eq("54: a section must start on a sheet, in a known style",
         ["from" in _layout_error({"pages": {"sections": [{"from": 0}]}}),
          "style" in _layout_error({"pages": {"sections": [{"from": 1, "style": "greek"}]}})], [True, True])
_vr = _layout({"variables": {"bill": "HB 1234"},
               "boxes": [_tf(id="v", md="See {var:bill}; {var:nope} stays.")]})
check("60: a variable is written into a box's words", _vr.text_boxes(1), "See HB 1234; {var:nope} stays.")
check_eq("60: a variable is a name and one line of text",
         ["variables" in _layout_error({"variables": {"a b": "x"}}),
          "one line" in _layout_error({"variables": {"a": "x\ny"}}),
          "cannot hold" in _layout_error({"variables": {"a": "{var:b}"}})], [True, True, True])
check_eq("49: vertical alignment and first baseline are frame CSS",
         [box_frame_css({"vAlign": "bottom"}), box_frame_css({"vAlign": "justify", "firstBaseline": "x"})],
         ["align-content:end",
          "display:flex;flex-direction:column;justify-content:space-between;text-box:trim-start ex alphabetic"])
check("49: and a box carries them", _layout({"boxes": [_tf(id="m", md="x", vAlign="middle")]}).text_boxes(1),
      "align-content:center")
check_eq("49: an unknown alignment is refused",
         "vAlign" in _layout_error({"boxes": [_tf(id="m", md="x", vAlign="center")]}), True)
check_eq("56: widows, orphans and word spacing are CSS, and checked",
         [text_css({"widows": 2, "orphans": 3, "wordSpacing": 0.1}),
          "widows" in _layout_error({"text": {"x": {"widows": 12}}})],
         ["widows:2;orphans:3;word-spacing:0.1em", True])

# --- INDESIGN_PARITY.md 59 and 53 (continued) -------------------------------------
_lk = _layout({"boxes": [_tf(id="s", md="Rents rose **12%**.\n\nNot this."),
                         _tf(id="u", md="Said: {text:text.s} {text:nope}", y=4)]})
check("59: linked text repeats the first paragraph, and a link to nothing stays as typed",
      _lk.text_boxes(1), "Said: Rents rose <b>12%</b>. {text:nope}")
check_eq("59: published, the words carry no marker", "ds-linked" in _lk.text_boxes(1), False)
_t53 = {"id": "t", "page": 1, "x": 1, "y": 1, "w": 4,
        "rows": [["", "Q1", "Q2"], ["Rent", "1", "2"], ["Total", "3", "4"]], "header": True,
        "headerCols": 1, "bandCols": "#EEEEEE", "bandColStroke": {"w": 2, "color": "#333333"},
        "cells": {"2,1": {"use": "Total"}, "2,2": {"use": "Total", "fill": "#FF0000"}}}
_h53 = _layout({"cellStyles": {"Total": {"fill": "#DDEEFF", "bold": True}}, "tables": [_t53]}).tables_html(1)
check_eq("53: header columns are th; alternate columns tinted and ruled; a cell style worn, the cell's own key winning",
         ["<th>Rent</th>" in _h53, "border-right:2px solid #333333;background:#EEEEEE" in _h53,
          "background:#DDEEFF;font-weight:700" in _h53, "background:#FF0000;font-weight:700" in _h53],
         [True, True, True, True])
check_eq("53: a cell style must exist, and holds only cell keys",
         ["no cell style" in _layout_error({"tables": [dict(_t53)]}),
          "only fill" in _layout_error({"cellStyles": {"T": {"wat": 1}}})], [True, True])

# --- INDESIGN_PARITY.md 82: internal links and cross-reference text ------------------
from docsync.content import md_inline as _mdx, link_refs as _lrefs   # noqa: E402
check_eq("82: [words](#key) is an internal link; a web link is untouched",
         _mdx("see [Who pays](#whopays.h1) or [it](https://a.gov/x?a=1&b=2)"),
         'see <a class="ds-xref" href="#whopays.h1">Who pays</a> or <a href="https://a.gov/x?a=1&b=2">it</a>')
check_eq("82: link_refs finds the places linked to", _lrefs("[a](#x.y) [b](#text.3) [c](https://q)"),
         {"x.y", "text.3"})
_xl = _layout({"boxes": [_tf(id="h", md="## Who *pays*[^n1]\n\nBody."),
                         _tf(id="r", md="See {titleof:text.h}, {titleof:nope}; [jump](#text.h)", y=4)]})
_xh = _xl.text_boxes(1)
check_eq("82: {titleof:} is the heading's words without its markup; one to nothing stays as typed",
         "See Who <i>pays</i>, {titleof:nope};" in _xh, True)
_xs = _xl.layer(1)
check_eq("82: a page with an internal link carries the link runtime, once",
         (_xs.count("a.ds-xref"), _xl.layer(1).count("a.ds-xref")), (1, 0))
check_eq("82: a report without one carries no runtime",
         "ds-xref" in _layout({"boxes": [_tf(id="a", md="x [y](https://a.gov)")]}).layer(1), False)
_xc = _content(_layout({}), body="[[a.h]]\n## Who pays\n\n[[a.p]]\nSee [it](#a.h).\n\n[[sources]]\n[x]: A. — https://a.gov\n")
check_eq("82: a slot an internal link names is hooked on the published page",
         'data-anc-host="a.h"' in _xc._style("a.h") and "data-anc-host" not in _xc._style("a.p"), True)

# --- INDESIGN_PARITY.md 55: numbered lists past one block ------------------------
from docsync.content import block_html as _bh55   # noqa: E402
from docsync.layout import list_css as _lcss      # noqa: E402
check_eq("55: a numbered list starts where its first item says",
         ('<ol class="extra-numbers" start="4">' in _bh55("4. four\n5. five"),
          "start=" in _bh55("1. one\n2. two")), (True, False))
check_eq("55: a nested numbered list keeps its own start",
         '<ol start="3">' in _bh55("1. one\n  3. three\n  4. four"), True)
_l55 = _layout({"textStyles": {"Recs": {"list": {"continue": "recs", "levels": "nested"}}},
                "boxes": [_tf(id="a", md="1. one\n2. two", style={"use": "Recs"}),
                          _tf(id="b", md="1. three", style={"use": "Recs"}, y=5)]})
_h55 = _l55.layer(1) + _l55.text_boxes(1)
check_eq("55: list.continue counts every list wearing it on, one counter for the document",
         ["--ds-lc:[recs]" in _h55, "body{counter-reset:ds-lc-recs}" in _h55,
          "counter-increment:ds-lc-recs" in _h55, "counters(list-item,'.')" in _h55], [True] * 4)
check_eq("55: a report with lists but neither key keeps TYPE_CSS's bytes",
         _lcss([{"list": {"marker": "decimal"}}]), "")
for _bad, _says in (({"continue": "9x"}, "names the list"), ({"levels": "deep"}, "one of nested, outline")):
    check_eq(f"55: list is refused: {_says}",
             _says in _layout_error({"text": {"x": {"list": _bad}}}), True)

# --- INDESIGN_PARITY.md 58: a picture inside a shape ------------------------------
_S58 = {"id": "s", "page": 1, "kind": "ellipse", "x": 1, "y": 1, "w": 2, "h": 2, "fill": "#EEEEEE",
        "stroke": "#000000", "image": {"src": "assets/a.jpg"}}
_h58 = _layout({"shapes": [_S58]}).layer(1)
check_eq("58: the picture is clipped to the shape's own outline, fill under and stroke over, one node",
         ['<clipPath id="ds-pi-s"><ellipse' in _h58, 'clip-path="url(#ds-pi-s)"' in _h58,
          _h58.count('data-shape="s"'), 'preserveAspectRatio="xMidYMid slice"' in _h58,
          _h58.index('stroke="none"') < _h58.index("<image") < _h58.index('fill="none"')],
         [True, True, 1, True, True])
check_eq("58: fit contain, aligned to the top",
         'preserveAspectRatio="xMidYMin meet"' in _layout({"shapes": [dict(_S58, image={"src": "a.png", "fit": "contain", "align": "top"})]}).layer(1),
         True)
check_eq("58: a shape without a picture draws what it did",
         _layout({"shapes": [dict(_S58, image=None)]}).layer(1),
         _layout({"shapes": [{k: v for k, v in _S58.items() if k != "image"}]}).layer(1))
for _bad, _says in (({"kind": "line"}, "closed shape"), ({"image": {"src": ""}}, "src"),
                    ({"image": {"src": "a.png", "fit": "stretch"}}, "fit must be"),
                    ({"image": {"src": 'a" onload="x'}}, "not a picture"),
                    ({"kind": "path", "points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]}, "closed shape")):
    check_eq(f"58: refused: {_says}", _says in _layout_error({"shapes": [dict(_S58, **_bad)]}), True)

# --- INDESIGN_PARITY.md 84: tabs and slides ---------------------------------------
from docsync.content import split_states as _ss   # noqa: E402
check_eq("84: {state:} lines split a box's words; what comes first is an intro",
         _ss("## Why\n\n{state:Home}\nOne.\n\n{state:The *bill*}\nTwo.\n"),
         ("## Why", [("Home", "One."), ("The *bill*", "Two.")]))
check_eq("84: words with no state line are not states", _ss("Just words {state:x} inline"), None)
_MD84 = "{state:Home}\nOne.\n\n{state:Evidence}\nTwo."
_h84 = _layout({"boxes": [_tf(id="t", md=_MD84)]})
_p84 = _h84.layer(1) + _h84.text_boxes(1)
check_eq("84: published, tabs: a tablist, the first panel shown, the rest hidden, all printable",
         ['role="tablist"' in _p84, 'aria-selected="true"' in _p84, '<div class="ds-st-panel" role="tabpanel" id="ds-st-t-p1" aria-labelledby="ds-st-t-t1" hidden>' in _p84,
          ".ds-st-panel[hidden]{display:block!important}" in _p84, _p84.count("<script>") == 1],
         [True] * 5)
_s84 = _layout({"boxes": [_tf(id="t", md=_MD84, states="slides")]}).text_boxes(1)
check_eq("84: slides: previous and next, and a dot per state named for screen readers",
         ['data-step="-1"' in _s84, 'aria-label="Evidence"' in _s84, "ds-st-slides" in _s84], [True] * 3)
check_eq("84: a box without states draws what it did, and carries no runtime",
         "ds-states" in _layout({"boxes": [_tf(id="t", md="One.")]}).layer(1), False)
check_eq("84: states is tabs or slides",
         "states must be one of" in _layout_error({"boxes": [_tf(id="t", md=_MD84, states="carousel")]}), True)

# --- INDESIGN_PARITY.md 68: flip on shapes, boxes and tables ----------------------
_fl = _layout({"boxes": [_tf(id="f", md="x", flip="h", skew=10)],
               "shapes": [{"id": "s", "page": 1, "kind": "rect", "x": 1, "y": 1, "w": 2, "h": 1, "flip": "v"}],
               "tables": [{"id": "t", "page": 1, "x": 1, "y": 4, "w": 3, "rows": [["a", "b"]], "flip": "hv"}]})
check_eq("68: a flip folds into the one transform a box, shape or table already has",
         ["transform:scale(-1,1) skewX(10deg)" in _fl.text_boxes(1),
          'transform="translate(2.0 1.5) scale(1 -1) translate(-2.0 -1.5)"' in _fl.layer(1),
          "transform:scale(-1,-1)" in _fl.tables_html(1)], [True, True, True])
check_eq("68: an unknown flip is refused",
         "must be h, v or hv" in _layout_error({"boxes": [_tf(id="f", md="x", flip="x")]}), True)

# --- span columns, default styles (INDESIGN_PARITY 78, 79) -------------------
from docsync.layout import SPAN_CSS                                          # noqa: E402
from docsync.content import uses_breaks as _ub, uses_vars as _uv            # noqa: E402

check_eq("{span} is a hidden marker the paragraph's CSS reads",
         _mdi("{span}Who pays"), '<span class="ds-brk" data-brk="span"></span>Who pays')
check_eq("a span needs CSS and no variables runtime",
         (_ub("{span}x"), _uv("{span}x")), ({"span"}, False))
_sp = _layout({"boxes": [_tf(id="a", md="{span}Head\n\nx"), _tf(id="b", md="{span}More")]})
_spl = _sp.layer(1) + _sp.layer(2)
check_eq("SPAN_CSS rides once when a span is used, and BREAK_CSS does not ride with it",
         (_spl.count(SPAN_CSS), BREAK_CSS in _spl), (1, False))
_cb = _layout({"boxes": [_tf(id="a", md="x{break:column}y")]}).layer(1)
check_eq("a column break alone emits exactly what it always did — no SPAN_CSS",
         (f"<style>{BREAK_CSS}</style>" in _cb, SPAN_CSS in _cb), (True, False))
_both = _layout({"boxes": [_tf(id="a", md="{span}x{break:column}y")]}).layer(1)
check_eq("a break and a span together carry both rules, in one style tag",
         f"<style>{BREAK_CSS}{SPAN_CSS}</style>" in _both, True)
check_eq("{span} spans columns: column-span all on the paragraph holding it",
         "{column-span:all}" in SPAN_CSS, True)

_DS_OS = {"Card": {"kind": "box", "fill": "#EEEEEE"}, "Rule": {"kind": "shape", "stroke": "#000000"},
          "Data": {"kind": "table", "headerFill": "#1E6194"}}
_DS_TS = {"Body": {"size": 15}}
_ds_ok = _layout({"textStyles": _DS_TS, "objectStyles": _DS_OS,
                  "defaultStyles": {"text": "Body", "box": "Card", "shape": "Rule", "table": "Data"},
                  "boxes": [_tf(id="a", md="x")]})
_ds_no = _layout({"textStyles": _DS_TS, "objectStyles": _DS_OS, "boxes": [_tf(id="a", md="x")]})
check_eq("defaultStyles loads, and draws nothing: the page is the same without it",
         (_ds_ok.default_styles["box"], _ds_ok.layer(1) == _ds_no.layer(1)), ("Card", True))
for _bad, _says in (({"defaultStyles": {"frame": "Body"}}, "one of text, box, shape, table"),
                    ({"defaultStyles": {"text": "Nope"}}, "no text style called 'Nope'"),
                    ({"defaultStyles": {"box": "Rule"}}, "'Rule' dresses a shape, not a box"),
                    ({"defaultStyles": {"table": ""}}, "expected the name of a style"),
                    ({"defaultStyles": ["Body"]}, "expected {text, box, shape, table")):
    check_eq(f"defaultStyles is refused: {_says}",
             _says in _layout_error(dict({"textStyles": _DS_TS, "objectStyles": _DS_OS}, **_bad)), True)

# ------------------------------------------------- publish: a public site in another repo
import shlex  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
from docsync import publish as _pub  # noqa: E402
from docsync.registry import Publish  # noqa: E402

with tempfile.TemporaryDirectory() as _td:
    _reg = Path(_td) / "docsync.yml"
    _body = ("bindings:\n  - id: t-site\n    content: content.md\n    publish:\n"
             "      repo: Hawaii-Appleseed/Some-Site\n"
             "      command: make-the-page\n"
             "      paths: [site/page.html]\n")
    _reg.write_text(_body)
    _p = load_registry(_reg)[0].publish
    check_eq("publish parses with its defaults",
             (_p.repo, _p.branch, _p.when, _p.paths, _p.url), ("Hawaii-Appleseed/Some-Site", "main", "push", ["site/page.html"], ""))
    _reg.write_text(_body + "      when: save\n      branch: gh-pages\n")
    _p = load_registry(_reg)[0].publish
    check_eq("publish takes when and branch", (_p.when, _p.branch), ("save", "gh-pages"))
    check_eq("publish home defaults to local", _p.home, "local")
    _reg.write_text(_body + "      home: hub\n")
    check_eq("publish takes home: hub", load_registry(_reg)[0].publish.home, "hub")
    _reg.write_text(_body + "      home: both\n")
    try:
        load_registry(_reg)
        FAILS.append("publish home: both must not load")
    except RegistryError as e:
        check("publish home must be local or hub", str(e), "must be local or hub")
    check_eq("a binding with no publish block has none", (
        _reg.write_text("bindings:\n  - id: t-plain\n    content: c.md\n"), load_registry(_reg)[0].publish)[1], None)
    for _bad, _says in (("      when: always\n", "must be save or push"),
                        ("      paths: [../outside.html]\n", "must be inside the site repo"),
                        ("      paths: [/etc/passwd]\n", "must be inside the site repo"),
                        ("      paths: []\n", "paths must be a list")):
        _reg.write_text(_body.replace("      paths: [site/page.html]\n", "") + _bad
                        if "paths" in _bad else _body + _bad)
        try:
            load_registry(_reg)
            FAILS.append(f"publish {_bad.strip()!r} must not load")
        except RegistryError as e:
            check(f"publish refuses {_bad.strip()!r}", str(e), _says)
    _reg.write_text(_body.replace("      command: make-the-page\n", ""))
    try:
        load_registry(_reg)
        FAILS.append("publish without a command must not load")
    except RegistryError as e:
        check("publish without a command names it", str(e), "missing required key 'command'")

with tempfile.TemporaryDirectory() as _td:
    _td = Path(_td)

    def _sh(*a, cwd=None):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd,
                              capture_output=True, text=True, check=True).stdout

    _origin, _seed = _td / "origin.git", _td / "seed"
    _sh("init", "-q", "--bare", "-b", "main", str(_origin))
    _sh("clone", "-q", str(_origin), str(_seed))
    (_seed / "site").mkdir()
    (_seed / "site" / "page.html").write_text("old")
    (_seed / "site" / "keep.txt").write_text("someone else's file")
    _sh("add", "-A", cwd=_seed)
    _sh("commit", "-q", "-m", "seed", cwd=_seed)
    _sh("push", "-q", "origin", "HEAD:main", cwd=_seed)
    os.environ["CIDER_PUBLISH_HOME"] = str(_td / "clones")

    def _remote(path):
        return _sh("show", f"main:{path}", cwd=_origin).strip()

    def _publish(cmd, paths=("site/page.html",)):
        return _pub.run(Publish(repo=str(_origin), command=cmd, paths=list(paths), url="https://x.test/p/"),
                        _td, "t-site", "save")

    _r = _publish('printf new > "$CIDER_SITE/site/page.html"; printf junk > "$CIDER_SITE/site/stray.txt"')
    check_eq("a publish commits the page to the site repo and pushes it",
             (_r.ok, _r.changed, _remote("site/page.html")), (True, True, "new"))
    check_eq("a publish takes ONLY the declared paths: a stray file is not committed",
             _sh("ls-tree", "--name-only", "-r", "main", cwd=_origin).split(), ["site/keep.txt", "site/page.html"])
    _n = len(_sh("rev-list", "main", cwd=_origin).split())
    _r = _publish('printf new > "$CIDER_SITE/site/page.html"')
    check_eq("publishing an unchanged page commits nothing",
             (_r.ok, _r.changed, len(_sh("rev-list", "main", cwd=_origin).split())), (True, False, _n))
    _sh("pull", "-q", "--rebase", "origin", "main", cwd=_seed)
    (_seed / "site" / "keep.txt").write_text("edited elsewhere")
    _sh("commit", "-q", "-am", "someone else pushed", cwd=_seed)
    _sh("push", "-q", "origin", "HEAD:main", cwd=_seed)
    _r = _publish('printf newer > "$CIDER_SITE/site/page.html"')
    check_eq("a publish builds on what the remote has gained since the last one",
             (_r.ok, _remote("site/page.html"), _remote("site/keep.txt")), (True, "newer", "edited elsewhere"))
    _r = _publish("exit 3")
    check_eq("a failing command is reported and publishes nothing",
             (_r.ok, "the publish command failed" in _r.message, _remote("site/page.html")), (False, True, "newer"))
    _r = _publish("true", paths=("site/never-made.html",))
    check_eq("a command that did not write a declared path is refused",
             (_r.ok, "did not write" in _r.message), (False, True))
    del os.environ["CIDER_PUBLISH_HOME"]

# ------------------------------------------------- publish: the hub route takes only a project's own files
from types import SimpleNamespace as _NS  # noqa: E402

with tempfile.TemporaryDirectory() as _td:
    _td = Path(_td)

    def _g(*a):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(_td), *a],
                              capture_output=True, text=True, check=True).stdout

    _g("init", "-q", "-b", "main")
    (_td / "projects" / "p").mkdir(parents=True)
    (_td / "projects" / "p" / "content.md").write_text("main words")
    (_td / "docsync.yml").write_text("command: trusted")
    _g("add", "-A")
    _g("commit", "-q", "-m", "main")
    _g("checkout", "-q", "-b", "hub/p")
    (_td / "projects" / "p" / "content.md").write_text("hub words")
    (_td / "docsync.yml").write_text("command: rm -rf /")           # what a hub branch must never get to run
    _g("commit", "-q", "-am", "hub version")
    _g("checkout", "-q", "main")
    _bind = _NS(id="p", content=_td / "projects" / "p" / "content.md", editor=None)
    _took = _pub._overlay(_td, _bind, "hub/p")
    check_eq("the hub route takes the project's content from the hub branch",
             (_took, (_td / "projects" / "p" / "content.md").read_text()), (["projects/p/content.md"], "hub words"))
    check_eq("and nothing else: the registry stays the trusted branch's",
             (_td / "docsync.yml").read_text(), "command: trusted")

# ------------------------------------------------- publish: what the staged manifest tells the editor
from docsync.registry import Binding as _Binding, Editor as _Editor  # noqa: E402
from docsync.stage import manifest as _manifest  # noqa: E402


def _staged(publish):
    from docsync.registry import ROOT as _ROOT
    td = _ROOT / "projects" / "ds-manifest-test"          # nothing is read from it: only paths are made relative
    return _manifest(_Binding(id="t", content=td / "c.md", publish=publish,
                              editor=_Editor(render=td / "r.py", engine=[], out=td / "out.html", dir=td / "d")))


check_eq("a project with no publish: block stages no site entry", _staged(None)["site"], None)
check_eq("a local-home page stages none either: only the hub follows the site job",
         _staged(Publish(repo="o/r", command="c", paths=["p"], home="local", url="https://x/"))["site"], None)
check_eq("a hub-home page stages its repository and public url for the Publish dialog and status",
         _staged(Publish(repo="o/r", command="c", paths=["p"], home="hub", url="https://x/"))["site"],
         {"repo": "o/r", "url": "https://x/"})
check_eq("a hub-home page with no url stages an empty one, not null",
         _staged(Publish(repo="o/r", command="c", paths=["p"], home="hub"))["site"], {"repo": "o/r", "url": ""})

# ------------------------------------------------- publish: any GitHub repo, one token minted per target
for _repo, _want in (("Hawaii-Appleseed/Census-Forecaster", ("Hawaii-Appleseed", "Census-Forecaster")),
                     ("https://github.com/Hawaii-Appleseed/Some.Site.git", ("Hawaii-Appleseed", "Some.Site")),
                     ("dtomkatsu/personal-site", ("dtomkatsu", "personal-site")),
                     ("git@example.com:team/site.git", ("", "")),
                     ("/tmp/not-github", ("", ""))):
    check_eq(f"github_target({_repo!r})", _pub.github_target(_repo), _want)
with tempfile.TemporaryDirectory() as _wd:
    _dest = _pub.init_workflow(Path(_wd))
    check_eq("init_workflow writes the caller into .github/workflows/",
             (_dest.relative_to(_wd).as_posix(), "uses: Hawaii-Appleseed/Cider/.github/workflows/publish-sites.yml@main" in _dest.read_text(),
              "secrets: inherit" in _dest.read_text()),
             (".github/workflows/publish-sites.yml", True, True))

# ------------------------------------------------- publish_page: a project back into its page
import base64 as _b64  # noqa: E402
from docsync.publish_page import into_shell, sheet_inner, swap_asset  # noqa: E402

_png = b"\x89PNG-bytes"
_uri = "data:image/png;base64," + _b64.b64encode(_png).decode()
_built = ('<html><head><style>.page{width:12.5in}</style></head><body>'
          '<section class="page" data-page="1"><header><img src="' + _uri + '"></header><p>Words</p>'
          '<style>@media screen and (max-width:12.5in){[data-placed]{position:static}}</style>'
          '</section>\n</body></html>')
_inner = sheet_inner(_built)
check_eq("sheet_inner takes what is inside the sheet and drops the engine's mobile rules",
         ("<p>Words</p>" in _inner, "max-width" in _inner), (True, False))
check_eq("swap_asset writes an inline image back as its URL",
         swap_asset(_inner, _png, "../assets/logo.png").count('src="../assets/logo.png"'), 1)
_shell = ('<!doctype html><html><head><link rel="stylesheet" href="../assets/site.css"></head>'
          '<body class="ha-est"><p>old</p></body></html>')
_page = into_shell(_shell, "<p>new</p>")
check_eq("into_shell keeps the shell's head and body tag and replaces what is between",
         ('href="../assets/site.css"' in _page, '<body class="ha-est">' in _page, "<p>new</p>" in _page, "old" in _page),
         (True, True, True, False))
from docsync.publish_page import semantic_tags  # noqa: E402
check_eq("semantic_tags writes <b>/<i> as <strong>/<em> and leaves <br>, <body>, <i class=..> alone",
         semantic_tags('<p><b>x</b> <i>y</i><br><i class="ic"></i></p><body>'),
         '<p><strong>x</strong> <em>y</em><br><i class="ic"></i></p><body>')
# ------------------------------------------------- publish: the report to the hub's Worker
import urllib.request as _ur  # noqa: E402
from docsync import publish as _pub  # noqa: E402

_seen = {}


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_open(req, timeout=None):
    _seen.update({k.lower(): v for k, v in req.header_items()}, url=req.full_url)
    return _Resp()


_real_open, _ur.urlopen = _ur.urlopen, _fake_open
try:
    _note = _pub._report("https://w.example/", "k", "O~R~p", {"ok": True})
finally:
    _ur.urlopen = _real_open
check_eq("publish report names itself, not Python-urllib (Cloudflare 403s that, error 1010)",
         (_note, _seen.get("url"), _seen.get("x-export-key"), "urllib" in _seen.get("user-agent", "urllib").lower()),
         ("reported to the hub (200)", "https://w.example/site/O~R~p", "k", False))

# ------------------------------------------------- fidelity: the markup check (no browser)
import tempfile as _tf  # noqa: E402
from docsync.fidelity import static_check  # noqa: E402

with _tf.TemporaryDirectory() as _d:
    _d = Path(_d)
    (_d / "assets").mkdir()
    (_d / "assets" / "site.css").write_text(
        ".lbl strong{color:teal} .note p:last-child{margin:0} /* .ghost{} */ @media (max-width:600px){.lbl{font-size:12px}}")
    _cur = _d / "page" / "index.html"
    _cur.parent.mkdir()
    _cur.write_text('<html><head><link rel="stylesheet" href="../assets/site.css?v=2"></head><body>'
                    '<div class="lbl">in <strong>lost</strong> credits</div><p>Act 163 raised it.</p>'
                    '<div class="note"><p>a</p><p>b</p></div></body></html>')
    _h = _cur.read_text()
    check_eq("fidelity: an identical page passes", static_check(_cur, _h), [])
    check_eq("fidelity: bare editable-sentence spans draw nothing and pass",
             static_check(_cur, _h.replace("<p>Act 163 raised it.</p>", "<p><span>Act 163</span> <span>raised it.</span></p>")), [])
    check("fidelity: <b> where the page had <strong> is caught (the credits-page bug)",
          " ".join(static_check(_cur, _h.replace("strong>", "b>"))), "never had: div.lbl > b")
    check_eq("fidelity: an editor's word change passes", static_check(_cur, _h.replace("raised", "lifted")), [])
    check_eq("fidelity: an editor bolding a word with a tag the page already uses passes",
             static_check(_cur, _h.replace("raised", "<strong>raised</strong>")), [])
    check("fidelity: a class dropped with the words unchanged is caught",
          " ".join(static_check(_cur, _h.replace('class="lbl"', ""))), "same words, different markup")
    check("fidelity: a span that moves a :last-child target is NOT inert",
          " ".join(static_check(_cur, _h.replace("<p>b</p></div>", "<span><p>b</p></span></div>"))),
          "same words, different markup")
    check_eq("fidelity: a class only named inside a CSS comment does not count",
             static_check(_cur, _h.replace("<p>Act", '<p class="ghost">Act')), [])

# ------------------------------------------------- website: a page made public from the editor
from docsync import website as _web  # noqa: E402

_sites = {"reports": _web.Site("reports", "Reports", "Org/reports", "main", "", "https://org.github.io/reports/"),
          "nested": _web.Site("nested", "Nested", "Org/site", "gh-pages", "pages", "https://org.github.io/site/pages/")}
check_eq("website: no record is not a website", _web.designation({"page": {}}, _sites), None)
_d = _web.designation({"website": {"site": "nested", "slug": "act-163", "home": "hub"}}, _sites)
check_eq("website: a good record names its site, address and home, and where it lands",
         (_d["site"].name, _d["slug"], _d["home"], _web.target_path(_d)), ("nested", "act-163", "hub", "pages/act-163"))
for _bad, _why in (({"site": "elsewhere", "slug": "ok-page"}, "not one of this repository's sites"),
                   ({"site": "reports", "slug": "Bad Slug"}, "lowercase words"),
                   ({"site": "reports", "slug": "../escape"}, "lowercase words"),
                   ({"site": "reports", "slug": "-edge"}, "lowercase words"),
                   ({"site": "reports", "slug": "ok-page", "home": "cloud"}, "must be local or hub"),
                   ("reports/ok-page", "must be {site, slug, home}")):
    try:
        _web.designation({"website": _bad}, _sites)
        FAILS.append(f"website: {_bad!r} must be refused")
    except _web.WebsiteError as e:
        check(f"website: {_bad!r} is refused", str(e), _why)


class _B:                       # just what publish_for reads off a binding
    id, publish, editor = "my-page", None, None


_p = _web.publish_for(_B(), {"website": {"site": "reports", "slug": "my-page", "home": "hub"}}, _sites)
check_eq("website: the publish settings come from the record, with the engine's own command",
         (_p.repo, _p.branch, _p.paths, _p.home, _p.when, _p.url, _p.command),
         ("Org/reports", "main", ["my-page"], "hub", "save", "https://org.github.io/reports/my-page/",
          "python3 -m docsync.website --id my-page --write"))
check_eq("website: a page with neither a publish: block nor a record publishes nowhere",
         _web.publish_for(_B(), {}, _sites), None)
_B2 = type("_B2", (), {"id": "x", "editor": None, "publish": "EXPLICIT"})
check_eq("website: an explicit publish: block wins over a record",
         _web.publish_for(_B2(), {"website": {"site": "reports", "slug": "x-page"}}, _sites), "EXPLICIT")
check_eq("website: script dependencies an import map or an import names are found",
         _web.script_refs('<script type="importmap">{"imports": {"three": "./assets/vendor/three.module.js",'
                          ' "cdn": "https://x/y.js"}}</script><script type=module>import a from "./lib/a.mjs";'
                          ' await import("./lib/b.js"); import c from "https://x/c.js"</script>'),
         {"assets/vendor/three.module.js", "lib/a.mjs", "lib/b.js"})

# The recipe, end to end, on a real project: the address is claimed, and a second
# project cannot take it.
from docsync.registry import get as _get  # noqa: E402
from docsync.registry import RegistryError as _RegErr  # noqa: E402

# retitc and dotax-itep are bound in Cider's own docsync.yml. A consumer repo
# runs this file too (BudgetPrimerFinal's build self-tests its vendored
# engine) and binds neither, so the end-to-end half is skipped there, said so.
try:
    _get("retitc"), _get("dotax-itep")
    _web_e2e = True
except _RegErr:
    _web_e2e = False
    print("note: retitc/dotax-itep are not bound here — skipped the website end-to-end checks")

if _web_e2e:

    with _tf.TemporaryDirectory() as _site:
        _site = Path(_site)
        _real = _web.read_layout
        _web.read_layout = lambda b: {**(_real(b) or {}), "website": {"site": "reports", "slug": "shared-address"}}
        try:
            check("website: a Cider report publishes as a page at its address",
                  _web.publish(_get("retitc"), _site, write=True, sites=_sites), "wrote https://org.github.io/reports/shared-address/")
            check_eq("website: the page and its claim are written",
                     ((_site / "shared-address" / "index.html").is_file(), (_site / "shared-address" / _web.MARKER).read_text()),
                     (True, "retitc\n"))
            check("website: publishing again is no change", _web.publish(_get("retitc"), _site, write=True, sites=_sites), "no change")
            try:
                _web.publish(_get("dotax-itep"), _site, write=True, sites=_sites)
                FAILS.append("website: a second project must not take an address another holds")
            except _web.WebsiteError as e:
                check("website: a second project is refused the address", str(e), "already retitc's")
            (_site / "foreign").mkdir()
            (_site / "foreign" / "index.html").write_text("someone else's")
            _web.read_layout = lambda b: {**(_real(b) or {}), "website": {"site": "reports", "slug": "foreign"}}
            try:
                _web.publish(_get("retitc"), _site, write=True, sites=_sites)
                FAILS.append("website: a folder Cider did not make must not be overwritten")
            except _web.WebsiteError as e:
                check("website: a folder Cider did not make is refused", str(e), "Cider did not put there")
        finally:
            _web.read_layout = _real

# ------------------------------------------- what Claude builds stays editable
# Found 2026-10-20 by having Claude build complicated charts and tables through
# every route (pilot verbs, layout.json, a renderer's blocks.chart) and then
# editing each one as a person would. The editor halves live in
# tests/editor/claude-creates.spec.js; these are the engine's.
from docsync.layout import chart_svg as _cs2                     # noqa: E402

_was_edit = os.environ.get("DOCSYNC_EDIT")
os.environ["DOCSYNC_EDIT"] = "1"
try:
    _rule = {"type": "bar", "labels": ["a", "b"], "series": [{"name": "s", "data": [5, 9]}],
             "rules": [{"value": 6, "label": "Spending cap"}, {"value": 8, "label": True}]}
    _rs = _cs2(_rule, 0, 0, 4, 2.5)
    check("a reference line's worded label edits on the page (and docsync.check sees a hook)",
          _rs, 'data-ch="rule:0"')
    check_eq("...but one that is the value itself has nothing to retype",
             'data-ch="rule:1"' in _rs, False)
    _cov_rule = _cov(f'<section class="page"><svg viewBox="0 0 4 2.5">{_rs}</svg></section>')
    check_eq("a hooked reference-line label is not a literal in a drawing",
             [t for t in _cov_rule.frozen_prose if "Spending" in t], [])
finally:
    if _was_edit is None:
        os.environ.pop("DOCSYNC_EDIT", None)
    else:
        os.environ["DOCSYNC_EDIT"] = _was_edit

# The hit area of a chart shape is its box: `all` hit-tested the stroke too,
# and an unset stroke-width is an INCH in the page's layer — every chart took
# clicks half an inch outside itself (a table's header row under it).
_hit = _layout({"shapes": [{"id": "c1", "page": 1, "kind": "chart", "x": 1, "y": 1,
                            "w": 3, "h": 2, "chart": {"type": "bar", "labels": ["a"],
                                                      "series": [{"name": "s", "data": [1]}]}}]})
_hl = _hit.layer(1)
check("a chart's hit rect takes the pointer over its box only", _hl, 'pointer-events="fill"')
check_eq("...never over a stroke an inch wide", 'pointer-events="all"' in _hl, False)

# null is a gap: a line breaks there instead of diving to zero.
_g = _cs2({"type": "line", "labels": list("abcde"),
           "series": [{"name": "s", "data": [3, 4, None, 5, 6]}]}, 0, 0, 4, 2.5)
check_eq("a line with a null draws two runs, not one through zero",
         _g.count("<polyline"), 2)
check_eq("...and no dot where there is no value", _g.count("<circle"), 4)
_g0 = _cs2({"type": "line", "labels": list("abc"),
            "series": [{"name": "s", "data": [60, None, 70]}], "axisMin": 50}, 0, 0, 4, 2.5)
_ys = [float(y) for y in re.findall(r'cy="([\d.]+)"', _g0)]
check_eq("a gap under a raised axisMin is not drawn below the plot",
         all(y <= 2.5 for y in _ys), True)
_ga = _cs2({"type": "area", "labels": list("abcde"),
            "series": [{"name": "s", "data": [1, 2, None, 2, 1]}]}, 0, 0, 4, 2.5)
check_eq("an area breaks at a gap too", _ga.count("<polygon"), 2)
_gs = _cs2({"type": "stacked-area", "labels": list("abc"),
            "series": [{"name": "s", "data": [1, None, 2]}, {"name": "t", "data": [1, 1, 1]}]},
           0, 0, 4, 2.5)
check_eq("a stack has no hole to show: it still reads the gap as 0",
         _gs.count("<polygon"), 2)
_sc = _cs2({"type": "scatter", "series": [{"name": "x", "data": [1, 2, None, 4]},
                                          {"name": "y", "data": [5, 6, 7, None]}]}, 0, 0, 4, 2.5)
check_eq("a scatter point with no x or no y is not drawn at zero", _sc.count("<circle"), 2)

# Labels: Tilted on a line chart (the panel offered it; only bars obeyed).
check("a line chart turns its labels when asked",
      _cs2({"type": "line", "labels": [f"Month {i}" for i in range(24)],
            "series": [{"name": "s", "data": list(range(24))}], "labelAngle": -45},
           0, 0, 7, 3), 'transform="rotate(-45 ')
check("...and so does an area chart",
      _cs2({"type": "area", "labels": [f"Month {i}" for i in range(12)],
            "series": [{"name": "s", "data": list(range(12))}], "labelAngle": -90},
           0, 0, 7, 3), 'transform="rotate(-90 ')

# Descriptions that ARE editable, which the check used to fail a report for.
check_eq("an image in a text box's markdown has an editable alt",
         _cov('<section class="page"><div class="ds-textbox" data-el="text.t1"><p>'
              '<img src="a.svg" alt="Chart placeholder"></p></div></section>').desc_paged, [])
check_eq("...and so does one in a slot's markdown",
         _cov('<section class="page"><p data-slot="k"><img src="a.png" alt="A photo">'
              '</p></section>').desc_paged, [])
check_eq("...but a renderer's own <img alt> is still a literal",
         _cov('<section class="page"><img data-el="cover.logo" alt="Appleseed logo">'
              '</section>').desc_paged, ["Appleseed logo"])
check_eq("a renderer chart named after its own title is wired (the title edits in the panel)",
         _cov('<section class="page"><span data-chart="c"><svg class="chart" role="img" '
              'aria-label="Revenue by source" data-desc-from="the chart title (Chart panel)">'
              '</svg></span></section>').desc_paged, [])
from docsync import blocks as _blocks2                            # noqa: E402
os.environ["DOCSYNC_EDIT"] = "1"
try:
    _bc = _blocks2.chart(_layout({}), "fig.x", {"type": "bar", "title": "Revenue",
                                                 "labels": ["a"], "series": [{"name": "s", "data": [1]}]})
    check("blocks.chart says its description is its title", _bc, 'data-desc-from=')
finally:
    if _was_edit is None:
        os.environ.pop("DOCSYNC_EDIT", None)
    else:
        os.environ["DOCSYNC_EDIT"] = _was_edit

# Chart polish (2026-10-20): an axis that crosses zero divides in round steps
# with zero on a tick; a tick label too long for its strip widens the strip;
# text on a pale funnel band or treemap block is dark; a name too long for its
# block shrinks or is left off. Every bound report renders byte-identically.
from docsync.layout import (_axis_bounds as _ab2, _tick_labels as _tl2,  # noqa: E402
                            _value_gutter as _vg2)
check_eq("a zero-crossing axis takes round steps, zero among them",
         (lambda b: (b, "0" in _tl2(b[0], b[1], b[2], {})))(_ab2({}, [178, -60, 82, -30])),
         ((-100.0, 200.0, 7), True))
check_eq("...billions with one small negative too",
         _tl2(*_ab2({}, [9.7, -0.2, 8.3]), {}), ["-2", "0", "2", "4", "6", "8", "10"])
check_eq("an axis above zero keeps the scale every published chart has",
         _ab2({}, [1, 2, 3]), (0.0, 3.0, 5))
check_eq("...and a pinned end is left alone", _ab2({"axisMin": -50}, [120, -45]), (-50.0, 150.0, 5))
_cmb = _cs2({"type": "bar", "labels": ["a", "b", "c"], "grid": True,
             "series": [{"name": "s", "data": [120, -45, 60]},
                        {"name": "t", "data": [1, 2, 3], "type": "line", "axis": "right"}]}, 0, 0, 5, 3)
from docsync.layout import _axis2_bounds as _a2b                    # noqa: E402
check_eq("a second axis divides as many times as a re-divided first one",
         (_a2b({}, [1, 2, 3], 7)[2], _a2b({}, [1, 2, 3], 5)), (7, (0.0, 3.0, 5)))
_cmb2 = _cs2({"type": "bar", "labels": ["a", "b", "c"], "grid": True,
              "series": [{"name": "s", "data": [178, -60, 82]},
                         {"name": "t", "data": [1, 2, 3], "type": "line", "axis": "right"}]}, 0, 0, 5, 3)
check_eq("...so a combo chart whose left axis crosses zero draws both scales",
         len(re.findall(r'stroke-width="0.006"', _cmb2)), 7)
check_eq("a short tick label keeps the strip it always had", _vg2(["$6B", "$12B"], 0.2, 0.52), 0.52)
check_eq("a long one widens it", _vg2(["$0M", "$15,000M"], 0.2, 0.52) > 0.52, True)
_fun = _cs2({"type": "funnel", "labels": list("ABCDE"), "values": True,
             "series": [{"name": "p", "data": [100, 72, 51, 43, 39]}]}, 0, 0, 3.5, 3)
check_eq("a funnel's pale band gets dark words, its dark ones stay white",
         re.findall(r'fill="(#2F3E46|#fff)" font-weight="600"', _fun), ["#2F3E46", "#fff", "#2F3E46", "#fff", "#2F3E46"])
_tm = _cs2({"type": "treemap", "labels": ["GET exemptions", "Income tax credits for working families", "P"],
            "series": [{"name": "c", "data": [640, 60, 30]}]}, 0, 0, 3.5, 3)
check_eq("a treemap name too long for its block is not drawn across the next one",
         "Income tax credits for working families" in _tm, False)
check("...while one that fits is", _tm, "GET exemptions")

# A scaffolded report links its citation markers to the endnotes.
from docsync import new as _new                                   # noqa: E402
check("the scaffold renderer linkifies its citation markers",
      Path(_new.__file__).read_text(), "body = C.fn.linkify(body)")

# build_parity: what counts as a report's OWN files (a change there explains
# its page moving) against the real registry, so a binding's shape is tested
# as written, not as imagined.
from docsync import build_parity as _bp                          # noqa: E402
_bpb = _bp.bindings(_bp.ROOT)
check_eq("build_parity reads every binding that has a renderer",
         "rxkids" in _bpb and "budget-primer" in _bpb, True)
check_eq("a report's content is its own", _bp.is_own("projects/rxkids/content.md", _bpb["rxkids"]["own"]), True)
check_eq("...so is anything in its folder", _bp.is_own("projects/rxkids/original.html", _bpb["rxkids"]["own"]), True)
check_eq("...and a file a watched glob names", _bp.is_own("projects/rxkids/assets/hero.png", _bpb["rxkids"]["own"]), True)
check_eq("...but the engine is not", _bp.is_own("docsync/layout.py", _bpb["rxkids"]["own"]), False)
check_eq("...nor another report", _bp.is_own("projects/retitc/content.md", _bpb["rxkids"]["own"]), False)
check_eq("the primer's data under docs/js is its own (engine: in its binding)",
         _bp.is_own("docs/js/historical_trends.json", _bpb["budget-primer"]["own"]), True)
check_eq("a folder prefix is a folder, not a string prefix",
         _bp.is_own("projects/rxkids-fiscal/content.md", _bpb["rxkids"]["own"]), False)

# --- a new report from a template carries the typed title everywhere ---------
for _tn in ("appleseed-report", "appleseed-brief", "appleseed-onepager"):
    _tl = TEMPLATES[_tn]["layout"]()
    _new._apply_title(_tl, "Kūpuna Care Gap")
    _mds = [b["md"] for b in _tl["boxes"]]
    check_eq(f"{_tn}: no placeholder title left",
             [m for m in _mds if "YOUR REPORT" in m or "title here" in m], [])
    check_eq(f"{_tn}: the cover title is the typed one",
             [b["md"] for b in _tl["boxes"] if b["id"] == "tpl-title"], ["Kūpuna Care Gap"])
_tl = TEMPLATES["appleseed-report"]["layout"]()
_new._apply_title(_tl, "Kūpuna Care Gap")
check_eq("report footers read page · TITLE",
         [b["md"] for b in _tl["boxes"] if b["id"] == "tpl-foot2"], ["2 · KŪPUNA CARE GAP"])
_tl = TEMPLATES["appleseed-report"]["layout"]()
_new._apply_title(_tl, "A very long report title that would wrap the footer")
check("a long title is shortened in footers only",
      [b["md"] for b in _tl["boxes"] if b["id"] == "tpl-foot2"][0], "…")

# --- a written document laid out on a template (docsync.compose) ------------
from docsync import compose as _cp                               # noqa: E402
from docsync.templates import section_pattern                    # noqa: E402

_TXT = """# Kūpuna Care Gap
Subtitle: What older adults pay for care
Date: March 2027
Author: Devin Thomas

Hawaiʻi has the longest life expectancy of any state, and the highest share
of households caring for a relative.[^ahr] This report measures the gap.

## Who is caring

More than 154,000 people provide unpaid care.[^aarp] Most hold a job.

### Where the money goes

- Home health aide: $36 an hour
- Nursing home: $14,200 a month

> Families are choosing which parent's savings to spend first.

Most families cover these costs by cutting back on work.

## Recommendations

The Legislature can close most of the gap.

[^ahr]: America's Health Rankings 2026 — https://example.org/ahr
[^aarp]: AARP, Valuing the Invaluable
"""
_d = _cp.parse(_TXT)
check_eq("compose: the # line is the title", _d.title, "Kūpuna Care Gap")
check_eq("compose: the lines under it are metadata",
         _d.meta, {"subtitle": "What older adults pay for care",
                   "date": "March 2027", "author": "Devin Thomas"})
check_eq("compose: paragraphs before the first ## are the summary",
         [b.kind for b in _d.intro], ["para"])
check_eq("compose: each ## is a section", [s.title for s in _d.sections],
         ["Who is caring", "Recommendations"])
check_eq("compose: ###, bullets and > inside a section are typed blocks",
         [b.kind for b in _d.sections[0].blocks],
         ["para", "sub", "list", "quote", "para"])
check_eq("compose: a wrapped bullet is one item",
         _d.sections[0].blocks[2].md.count("\n"), 1)
check_eq("compose: source lines are lifted out of the prose",
         sorted(_d.sources), ["aarp", "ahr"])
check_eq("compose: citations are found", sorted(_d.citations()), ["aarp", "ahr"])
check("compose: sources normalise to the engine's shape",
      _cp.sources_block(_d), "[ahr]: America's Health Rankings 2026 — https://example.org/ahr")
check_eq("compose: a lead-in is the first words in bold capitals",
         _cp._lead_in("More than 154,000 people provide unpaid care. Most hold a job."),
         "**MORE THAN 154,000 PEOPLE PROVIDE UNPAID** care. Most hold a job.")
check_eq("compose: a lead-in stops at a clause",
         _cp._lead_in("In total, the finding is this."), "**IN TOTAL,** the finding is this.")
check_eq("compose: an opening already in bold is left alone",
         _cp._lead_in("**Already** bold."), "**Already** bold.")
check_eq("compose: the one-pager has no page to repeat",
         section_pattern("appleseed-onepager"), None)

for _tn, _first in (("appleseed-report", 4), ("appleseed-brief", 2)):
    _pat = section_pattern(_tn, SCHEMES["teal"]["color"])
    _lay = TEMPLATES[_tn]["layout"](SCHEMES["teal"]["color"])
    _before = {b["id"] for b in _lay["boxes"]}
    _res = _cp.lay_out(_lay, _pat, _cp.parse(_TXT), "Kūpuna Care Gap")
    _ids = {b["id"] for b in _lay["boxes"]}
    check_eq(f"{_tn}: the pattern page's placeholders are gone",
             sorted(_before & set(_pat["drop"]) & _ids), [])
    _heads = [(b["page"], b["md"]) for b in _lay["boxes"] if b["id"].startswith("txt-h-")]
    check_eq(f"{_tn}: one headline per section, from the first pattern page on",
             _heads[0][0], _first)
    check_eq(f"{_tn}: sections in order",
             [m for _, m in _heads][-2:], ["Who is caring", "Recommendations"])
    _rules = [s for s in _lay["shapes"] if s["id"].startswith("txt-rule-")]
    check_eq(f"{_tn}: a hairline under every headline, in the pattern's fill",
             [(s["page"], s["fill"]) for s in _rules],
             [(p, _pat["rule"]["fill"]) for p, _ in _heads])
    _foot = sorted((b["page"], b["md"]) for b in _lay["boxes"] if b["id"].startswith("txt-foot-"))
    check_eq(f"{_tn}: every new page has a footer the title step rewrites",
             [m for _, m in _foot], [f"{p} · YOUR REPORT" for p, _ in _foot])
    check_eq(f"{_tn}: pages == the last page used", _res["pages"], max(p for p, _ in _foot))
    for b in _lay["boxes"]:
        if b["id"].startswith("txt-") and not b["id"].startswith("txt-foot-") \
                and b["y"] + 0.2 > _pat["footer"]["y"]:
            FAILS.append(f"{_tn}: {b['id']} starts at {b['y']}in, under the footer")
    _new._apply_title(_lay, "Kūpuna Care Gap")
    check_eq(f"{_tn}: no placeholder footer survives",
             [b["md"] for b in _lay["boxes"] if "YOUR REPORT" in b["md"] or "YOUR BRIEF" in b["md"]], [])
    # The result is a draft the engine accepts: every element on its page.
    import json as _json, tempfile as _tf
    _tp = Path(_tf.mktemp(suffix=".json"))
    _tp.write_text(_json.dumps(_lay))
    try:
        _L = Layout(_tp, page=(8.5, 11))
        check_eq(f"{_tn}: the laid-out draft passes check_bounds", _L.check_bounds(), [])
    finally:
        _tp.unlink()

_lay = TEMPLATES["appleseed-report"]["layout"]()
_pat = section_pattern("appleseed-report")
_cp.lay_out(_lay, _pat, _cp.parse(_TXT), "T")
_bx = {b["id"]: b for b in _lay["boxes"]}
check("report: a short summary lands in the template's own summary box",
      _bx["tpl-body3"]["md"], "**HAWAIʻI HAS THE LONGEST LIFE EXPECTANCY** of any state")
check_eq("report: the contents page lists the sections with their pages",
         _bx["tpl-toc2"]["md"], "Executive summary — 3\n\nWho is caring — 4\n\nRecommendations — 5")
check_eq("report: the cover takes the text's subtitle and date",
         (_bx["tpl-sub"]["md"], _bx["tpl-date"]["md"]),
         ("What older adults pay for care", "MARCH 2027"))
check_eq("report: the inside cover names the author", _bx["tpl-author2"]["md"], "Author: Devin Thomas")
# A two-line headline moves its hairline and body down, never under itself.
_lay = TEMPLATES["appleseed-report"]["layout"]()
_cp.lay_out(_lay, _pat, _cp.parse("## A headline long enough to wrap onto a second line\n\nBody."), "T")
_rule = [s for s in _lay["shapes"] if s["id"].startswith("txt-rule-")][0]
_body = [b for b in _lay["boxes"] if b["id"].startswith("txt-body-")][0]
check_eq("report: a wrapped headline pushes the hairline down", _rule["y"] > _pat["rule"]["y"], True)
check_eq("...and the body with it", _body["y"] > _pat["body_top"], True)
# A long summary does not squeeze into the narrow summary column: it gets a page.
_lay = TEMPLATES["appleseed-report"]["layout"]()
_long = "# T\n\n" + ("A sentence of ordinary length that says something. " * 60) + "\n\n## S\n\nBody."
_cp.lay_out(_lay, _pat, _cp.parse(_long), "T")
_bx = {b["id"]: b for b in _lay["boxes"]}
check("report: a long summary becomes a section page of its own",
      [b["md"] for b in _lay["boxes"] if b["id"].startswith("txt-h-")][0], "Executive summary")
check_eq("...and the template's summary box keeps its words", "YOUR FIRST WORDS" in _bx["tpl-body3"]["md"], True)
# Text that outruns a page continues on the next, under no headline.
_lay = TEMPLATES["appleseed-brief"]["layout"]()
_cp.lay_out(_lay, section_pattern("appleseed-brief"),
            _cp.parse("## S\n\n" + "\n\n".join(["Paragraph " * 40] * 12)), "T")
_pages = sorted({b["page"] for b in _lay["boxes"] if b["id"].startswith("txt-body-")})
check_eq("brief: a long section runs onto more pages", len(_pages) > 1, True)
check_eq("brief: the continuation pages carry no second headline",
         len([b for b in _lay["boxes"] if b["id"].startswith("txt-h-")]), 1)
# The scaffold refuses what it cannot lay out, and names the project for the hub.
try:
    _new.create("zz-x", "T", template="", text="## S\n\nBody.", root=Path("/nonexistent"))
    FAILS.append("new: text on a blank canvas must be refused")
except _new.NewProjectError as e:
    check("new: text needs a template", str(e), "needs a template")
check("new: the binding carries a name for the hub's list", _new._BINDING, '    name: "{name}"')

try:
    sheet_inner("<html><body><p>no sheet</p></body></html>")
    FAILS.append("a render with no sheet must be refused")
except ValueError as e:
    check("a render with no sheet says so", str(e), "no <section class=\"page\">")

if FAILS:
    print("\n\n".join("FAIL: " + f for f in FAILS))
    print(f"\n{len(FAILS)} failed")
    raise SystemExit(1)
print("docsync: all checks passed")
