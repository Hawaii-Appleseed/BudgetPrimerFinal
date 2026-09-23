"""Prose layer for the Budget Primer.

The primer's authored text lives in content.md ([[key]] slots), which is kept
in sync with the Google Doc via `make pull-doc`. This module parses that file,
converts inline Markdown to the exact HTML the report expects, and resolves
footnote refs.

Footnotes: prose carries stable IDs ([^act99]). Numbering is assigned in order of
first appearance across the assembled document, so a source can be inserted or
removed in the doc without renumbering anything by hand.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_KEY_RE = re.compile(r"^\[\[([A-Za-z0-9._-]+)\]\]\s*$", re.M)
_HEADING_RE = re.compile(r"^#{1,6}\s+", re.M)   # doc headings are styling, not content
_SOURCE_RE = re.compile(r"^\[([^\]]+)\]:\s*(\S.*?)\s*$", re.M)
# The " — https://…" tail, which is OPTIONAL: a book, an interview or a
# document somebody handed over has no link, and requiring one meant the only
# way to cite it was to invent a URL. A citation's own text may contain an em
# dash, so only a tail that is actually a URL counts as one — which is also
# why the scheme is required rather than "any word at the end of the line".
_SOURCE_URL_RE = re.compile(r"^(.*?)\s+—\s+(https?://\S+)$", re.S)


class ContentError(RuntimeError):
    """Raised when content.md is missing a key or a source — never fail silently."""


def _strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def parse_content(path: Path) -> dict:
    """content.md -> {key: raw markdown block}. Blocks keep their line breaks."""
    text = _strip_comments(path.read_text())
    out, matches = {}, list(_KEY_RE.finditer(text))
    if not matches:
        raise ContentError(f"{path.name}: no '[[key]]' markers found")
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        key = m.group(1)
        if key in out:
            raise ContentError(f"{path.name}: duplicate key '[[{key}]]'")
        out[key] = text[m.end():end].strip("\n").strip()
    return out


def parse_sources(block: str) -> dict:
    """[[sources]] block -> {id: (text, url)} preserving declaration order."""
    src = {}
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _SOURCE_RE.match(line)
        if not m:
            raise ContentError(
                f"source line is not '[id]: text' (optionally '… — "
                f"https://url'):\n  {line}")
        sid, rest = m.group(1), m.group(2).strip()
        u = _SOURCE_URL_RE.match(rest)
        txt, url = (u.group(1).strip(), u.group(2)) if u else (rest, "")
        if sid in src:
            raise ContentError(f"duplicate source id '[{sid}]'")
        src[sid] = (txt, url)
    if not src and not os.environ.get("DOCSYNC_EDIT"):
        # Empty is a state to pass THROUGH while editing, not a wall: deleting
        # your last source (or writing the sources of a new report one at a
        # time) made the whole draft stop building, and a document that will
        # not render is a document you cannot get back into to fix it. The
        # same bargain raw() strikes for a missing slot — publishing still
        # refuses here, and a citation with no source still renders as the red
        # '?' marker resolve() draws in the editor.
        raise ContentError("[[sources]] section is empty")
    return src


# A link destination, with its parentheses BALANCED — one nesting level, which
# is what real URLs use ("…/wiki/Act_(2026)"). Stopping at the first ')', as
# this did, cut that URL at "Act_(2026" and left the stray bracket sitting in
# the prose. CommonMark balances too, so what people paste from a browser
# behaves the way they expect it to.
_DEST = r"(?:[^()\s]+|\([^()\s]*\))+"


def md_inline(s: str) -> str:
    """Inline Markdown -> the report's HTML. Deliberately minimal: only the
    constructs the primer actually uses, emitting <b>/<i>/<a> (not <strong>).

    Prose '&' is escaped to '&amp;'; URLs are pulled out first so query strings
    (?a=1&b=2) keep their raw ampersands, matching the report's existing markup.
    """
    urls: list[str] = []
    imgs: list[tuple[str, str]] = []

    def stash(m):
        urls.append(m.group(2))
        return f"\x00LINK{len(urls) - 1}\x00{m.group(1)}\x01"

    def stash_img(m):
        imgs.append((m.group(2), m.group(1)))
        return f"\x00IMG{len(imgs) - 1}\x00"

    # Images first: ![alt](src) also matches the link pattern, so a link pass
    # would leave a stray '!' in front of an anchor. Unlike links, src may be
    # repo-relative (assets/…), which is how uploaded images are referenced.
    s = re.sub(r"!\[([^\]]*)\]\((" + _DEST + r")\)", stash_img, s)
    s = re.sub(r"\[([^\]^][^\]]*)\]\((https?://" + _DEST + r")\)", stash, s)
    s = s.replace("&", "&amp;").replace("<", "&lt;")
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s, flags=re.S)
    # Underline. Markdown has no underline of its own and raw <u> cannot get
    # through the escaping above, so it needs a token: '__' is unused by this
    # grammar (bold is '**', italic '*'), and reads as underline to anyone
    # hand-editing content.md. Note CommonMark would call '__x__' strong — this
    # engine has never implemented '__' at all, so nothing changes meaning.
    s = re.sub(r"__(.+?)__", r"<u>\1</u>", s, flags=re.S)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s, flags=re.S)
    s = re.sub(r"\x00LINK(\d+)\x00(.*?)\x01",
               lambda m: f'<a href="{urls[int(m.group(1))]}">{m.group(2)}</a>', s, flags=re.S)
    s = re.sub(r"\x00IMG(\d+)\x00",
               lambda m: '<img class="inline-img" src="{}" alt="{}">'.format(*imgs[int(m.group(1))]),
               s)
    return s


# Two spaces per level, Markdown's own convention. A line indented further
# than one level past its predecessor is clamped rather than rejected: hand-typed
# lists indent by 2, 3 or 4 spaces indifferently, and refusing to render is a
# worse answer than reading the obvious intent.
_ITEM_RE = re.compile(r"^(\s*)(?:-\s+|(\d+)[.)]\s+)(.*)$")


def _list_items(text: str) -> list[tuple[int, bool, str]]:
    """List lines -> [(depth, ordered, inline_html)], depth 0 at the margin.

    Non-item lines are ignored, which is what every caller wants: bullets()
    rejects a block with no items at all, and block_html() has already peeled
    off headings and prose before it gets here.
    """
    out: list[tuple[int, bool, str]] = []
    for line in text.splitlines():
        m = _ITEM_RE.match(line.replace("\t", "  "))
        if not m or not line.strip():
            continue
        depth = len(m.group(1)) // 2
        if out:
            depth = min(depth, out[-1][0] + 1)   # no skipping a level
        else:
            depth = 0                            # the first item is the margin
        out.append((depth, m.group(2) is not None, md_inline(m.group(3).strip())))
    return out


def _list_html(items: list[tuple[int, bool, str]], i: int, depth: int,
               top: bool) -> tuple[str, int]:
    """One <ul>/<ol> and everything nested under it, from items[i:].

    Returns the markup and the index of the first item that does NOT belong to
    it — either a shallower item, or one of the other kind at the same depth,
    which starts a sibling list. Only the outermost list carries the report's
    class: a nested list inherits its look from the CSS rather than repeating
    a class that means "this is an overflow slot's list".
    """
    ordered = items[i][1]
    tag = "ol" if ordered else "ul"
    cls = ""
    if top:
        cls = ' class="extra-numbers"' if ordered else ' class="extra-bullets"'
    parts = [f"<{tag}{cls}>"]
    while i < len(items):
        d, o, html = items[i]
        if d < depth:
            break
        if d > depth:
            # A deeper run belongs INSIDE the <li> just written, so reopen it.
            sub, i = _list_html(items, i, d, False)
            if len(parts) > 1:
                parts[-1] = parts[-1][: -len("</li>")] + sub + "</li>"
            else:
                parts.append(f"<li>{sub}</li>")   # indented first item, no parent
            continue
        if o != ordered:
            break
        parts.append(f"<li>{html}</li>")
        i += 1
    parts.append(f"</{tag}>")
    return "".join(parts), i


def _lists_html(items: list[tuple[int, bool, str]], top: bool = True) -> str:
    """Every top-level list in a run — a bulleted run followed by a numbered
    one is two lists, not one with mixed markers."""
    out, i = [], 0
    while i < len(items):
        html, i = _list_html(items, i, items[i][0], top)
        out.append(html)
    return "".join(out)


def bullets(block: str) -> list[str]:
    """'- item' lines -> [html, ...], one string per TOP-LEVEL item. Errors if
    the block isn't a list.

    An item with sub-items carries them as a nested <ul>/<ol> appended to its
    own html, so a caller that wraps each string in <li>…</li> gets correct
    nesting without knowing anything about it.

    In EDIT mode an empty list is a placeholder instead of an error: cutting
    the last bullet to move it elsewhere is a normal mid-move state, and
    crashing the live preview over it blocked exactly that move. Publishing
    still refuses, so a list left empty doesn't quietly ship a hollow card.
    """
    parsed = _list_items(_unhead(block))
    items: list[str] = []
    i = 0
    while i < len(parsed):
        html = parsed[i][2]
        i += 1
        start = i
        while i < len(parsed) and parsed[i][0] > 0:
            i += 1
        if i > start:
            sub = [(d - 1, o, h) for d, o, h in parsed[start:i]]
            html += _lists_html(sub, top=False)
        items.append(html)
    if not items:
        if os.environ.get("DOCSYNC_EDIT"):
            return ['<i style="opacity:.55">empty list — add a "- " bullet '
                    '(publishing will refuse until one exists)</i>']
        raise ContentError(f"expected a '- ' bullet list, got:\n  {block[:80]}")
    return items


# What a slot the RENDERER asks for but the document has not got looks like,
# in edit mode. Plain text, deliberately: it flows out through paragraph(),
# text(), t() and bullets() alike, half of which escape markup — an <i> here
# would render as literal tags in the other half.
NEW_SLOT = "\u26a0 new slot [[{key}]] — click and type to fill it"
# What an emptied slot shows in the editor, so it stays a target. Styled like
# bullets()' empty-list note, which says the same kind of thing.
EMPTY_SLOT = '<i style="opacity:.55">empty — click and type</i>'

_STYLE_ATTR_RE = re.compile(
    r"""\s+style\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", re.I)


def split_style(attrs: str) -> tuple[str, str]:
    """(the attribute text without its style attribute, that style's
    declarations) — for attribute strings the engine's own helpers emit, and
    the start tags of a page this engine ingested."""
    m = _STYLE_ATTR_RE.search(attrs or "")
    if not m:
        return attrs or "", ""
    val = next((g for g in m.groups() if g is not None), "")
    return attrs[:m.start()] + attrs[m.end():], val


def merge_attrs(*parts: str) -> str:
    """Several helpers' attribute strings for ONE tag, with every style
    declaration in one style attribute, in the order given — so a later part
    wins, as it would inside one declaration list.

    Two style attributes on a tag is a silent loss: the parser keeps the
    first. A text style emitted beside a position, or either beside a style
    the markup already had, simply never reached the page. With at most one
    style among the parts the text is unchanged — the bytes a report emitted
    before any of this was merged are the bytes it emits now."""
    rest, css = "", []
    for p in parts:
        p, c = split_style(p or "")
        rest += p
        c = c.strip().strip(";").strip()
        if c:
            css.append(c)
    if len(css) <= 1:
        return "".join(p or "" for p in parts)
    val = ";".join(css).replace('"', "&quot;")
    return rest + f' style="{val}"'


def _unhead(block: str) -> str:
    return _HEADING_RE.sub("", block)


def paragraph(block: str) -> str:
    """A prose block -> one HTML string (soft line wraps collapse to spaces)."""
    return md_inline(" ".join(l.strip() for l in _unhead(block).splitlines() if l.strip()))


def block_html(block: str) -> str:
    """Generic renderer for overflow slots: headings, bullet/numbered lists and
    paragraphs in source order. '##' -> section heading, '###' -> subheading;
    '- ' -> bulleted list; '1. ' (or '1) ') -> numbered list; matching the
    report's existing styles."""
    out: list[str] = []
    para: list[str] = []
    items: list[str] = []          # the raw list LINES of the current run

    def flush_para():
        if para:
            out.append(f"<p>{md_inline(' '.join(para))}</p>")
            para.clear()

    def flush_items():
        # Parsed as one run, so leading indentation survives to _list_items and
        # a sub-item nests instead of joining its parent as a sibling. Mixed
        # bulleted/numbered runs split into sibling lists in _lists_html.
        if items:
            out.append(_lists_html(_list_items("\n".join(items))))
            items.clear()

    for line in block.splitlines():
        s = line.strip()
        if not s:
            flush_para(); flush_items()
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            flush_para(); flush_items()
            txt = md_inline(m.group(2))
            out.append(f'<h2 class="sub">{txt}</h2>' if len(m.group(1)) <= 2
                       else f'<h3 class="sub2">{txt}</h3>')
            continue
        # A list item is either "- text" (bulleted) or "N. text" / "N) text"
        # (numbered), at any indentation.
        if _ITEM_RE.match(line.replace("\t", "  ")):
            flush_para()
            items.append(line)
            continue
        flush_items()
        para.append(s)
    flush_para(); flush_items()
    return "".join(out)


def paragraphs(block: str) -> list[str]:
    """A block -> one HTML string per blank-line-separated paragraph, so an
    editor can add a paragraph inside a slot and have it flow into the report."""
    chunks = [c for c in re.split(r"\n\s*\n", _unhead(block).strip()) if c.strip()]
    return [md_inline(" ".join(l.strip() for l in c.splitlines() if l.strip()))
            for c in chunks]


class Footnotes:
    """Assigns numbers to [^id] refs in order of first appearance."""

    # What Layout.text_boxes() drops where an endnotes box sits when the
    # numbering is not settled yet; resolve() swaps it for the real list.
    MOUNT = "<!--ds-endnotes-mount-->"

    def __init__(self, sources: dict):
        self.sources = sources
        self.order: list[str] = []          # ids, in first-appearance order
        self.settled = False                # order_by()/resolve() has run
        self.layout = None                  # set by Layout.bind_footnotes

    def _number(self, sid: str) -> int:
        if sid not in self.sources:
            raise ContentError(
                f"footnote [^{sid}] has no entry under [[sources]] in content.md")
        if sid not in self.order:
            self.order.append(sid)
        return self.order.index(sid) + 1

    @staticmethod
    def cited(html: str) -> list[str]:
        """Every [^id] in the text, first appearance first, without numbering
        anything. The pre-pass that lets an explicit order be applied BEFORE
        resolve() starts handing out numbers — resolve() assigns them as it
        walks, so by the time it has seen a ref it is too late to reorder."""
        return list(dict.fromkeys(re.findall(r"\[\^([^\]]+)\]", html)))

    def order_by(self, preferred: list[str], cited: list[str]) -> None:
        """Seed the numbering from an explicit order (the editor's drag-to-
        reorder on the Endnotes page), keeping first-appearance order for
        everything the override does not name.

        Only CITED ids are seeded: an id numbered here would otherwise count
        as used, and a source nothing cites has to keep failing the build.
        """
        lead = [s for s in preferred if s in cited and s in self.sources]
        self.order = lead + [s for s in cited if s not in lead and s in self.sources]
        # Seeded from the whole cited body, so the numbering IS the final one
        # from here on — resolve() only hands out numbers already decided. An
        # endnotes box rendered after this point can be filled on the spot.
        self.settled = True

    def resolve(self, html: str) -> str:
        """Replace [^id] runs with <sup>N</sup> (adjacent refs share one <sup>,
        thin-space separated, matching the primer's multi-ref style).

        In EDIT mode a citation naming no source renders as a red ? marker
        (hover names the missing id) instead of crashing the build — a token
        that lost a bracket or gained a space in a copy/paste is a mid-move
        state to show, not a wall to hit. Publishing still refuses via
        _number's ContentError, so a typo can't ship."""
        def one(i):
            if i not in self.sources and os.environ.get("DOCSYNC_EDIT"):
                return ('<a class="fn" style="color:#C0392B" title="no source named '
                        f'[{i}] under [[sources]] — fix the citation or add the source">?</a>')
            return str(self._number(i))
        def run(m):
            ids = re.findall(r"\[\^([^\]]+)\]", m.group(0))
            return "<sup>" + "&thinsp;".join(one(i) for i in ids) + "</sup>"
        out = re.sub(r"(?:\[\^[^\]]+\])+", run, html)
        # Numbering is complete NOW — every ref in this body has been walked —
        # so an endnotes box that rendered before it can be filled in. A
        # renderer that emits its boxes AFTER resolve() (rxkids) never leaves a
        # mount here; text_boxes() sees `settled` and renders the list outright.
        self.settled = True
        if self.MOUNT in out:
            out = out.replace(self.MOUNT, self.endnotes_html())
        return out

    def unused(self) -> list[str]:
        return [s for s in self.sources if s not in self.order]

    def endnotes(self) -> list[tuple[str, str]]:
        """(text, url) in numbered order — feeds the Endnotes page."""
        return [self.sources[sid] for sid in self.order]

    def endnotes_with_ids(self) -> list[tuple[str, str, str]]:
        """(id, text, url) in numbered order — like endnotes(), plus the
        source id the draft editor needs to route an in-place edit on the
        Endnotes page back to its own [[sources]] line."""
        return [(sid, *self.sources[sid]) for sid in self.order]

    def endnotes_html(self, layout=None) -> str:
        """The numbered <ol> for an endnotes section the EDITOR placed.

        A report whose renderer builds its own endnotes page (report2027's
        page 12) never comes through here — this is for the ones that had
        none, where an editor asked for the section and the engine has to
        supply the markup. So it carries the same editing hooks that page
        hand-rolls: data-el per <li>, so each endnote can be dragged to
        reorder (Layout.endnote_order() reads the result back), and the
        anchor ids the in-prose <sup> links point at.

        Zero-stylesheet, like docsync.blocks — a scaffolded report with no CSS
        of its own still gets a readable list.
        """
        layout = layout if layout is not None else self.layout
        rows = self.endnotes_with_ids()
        if not rows:
            # An empty list still renders its heading: the section was asked
            # for explicitly, and a surface that vanishes when the last
            # citation is cut reads as the editor having lost it.
            return ('<ol class="ds-endnotes" style="padding-left:1.4em;margin:0">'
                    '</ol>')
        items = []
        for i, (sid, txt, url) in enumerate(rows, 1):
            hook = layout.attr(f"endnote.{sid}") if layout is not None else ""
            # A source need not have a link — a book, an interview, a document
            # somebody handed over. Without this an empty url drew an empty
            # <a href="">: a link to the page itself, styled as a citation.
            link = (f' <a href="{url}" style="word-break:break-all">{url}</a>'
                    if url else "")
            items.append(
                f'<li id="en{i}"{hook} style="margin-bottom:.6em">{txt}'
                f'{link}</li>')
        return ('<ol class="ds-endnotes" style="padding-left:1.4em;margin:0">'
                + "".join(items) + "</ol>")


# ---- what each slot actually served -------------------------------------------
# docsync.check's edit-mode pass counts text under a data-slot as editable. A
# hook is only as good as what stands behind it, though: slot_attr() takes any
# key, so `<p data-slot="made.up">…literal…</p>` looked wired, and a slot's
# element could carry renderer words beside the slot's own ("Title — FY27
# edition") that no edit reaches. So under DOCSYNC_SLOTLOG=<path> every key a
# renderer READS is written out with the words it was given, and the check
# holds the page to it. Nothing is recorded otherwise (the browser editor
# never sets it), and a key served twice keeps every version it served.
_SERVED: dict[str, list[str]] = {}


def _served(key: str, words: str) -> None:
    if not os.environ.get("DOCSYNC_SLOTLOG"):
        return
    if not _SERVED:
        import atexit
        atexit.register(_dump_served)
    seen = _SERVED.setdefault(key, [])
    if words not in seen:
        seen.append(words)


def _dump_served() -> None:
    import json
    path = os.environ.get("DOCSYNC_SLOTLOG")
    if path:
        Path(path).write_text(json.dumps(_SERVED, ensure_ascii=False),
                              encoding="utf-8")


class Content:
    """Key lookup with a loud failure when a key is missing."""

    def __init__(self, path: Path, styles=None):
        """`styles` is anything with text_attr(key) — a Layout, in practice.

        A constructor argument rather than an attribute set afterwards: the
        renderer builds some slots at import time, so a later assignment would
        work only by luck of line order. This makes using it unbound impossible
        rather than merely unlikely.
        """
        self.path = path
        self._styles = styles
        self._styleable: set[str] = set()
        self._raw = parse_content(path)
        if "sources" not in self._raw:
            raise ContentError(f"{path.name}: missing the '[[sources]]' section")
        self.sources = parse_sources(self._raw.pop("sources"))
        self.fn = Footnotes(self.sources)
        self._used: set[str] = set()
        self._new: set[str] = set()
        # The editor can place an endnotes list as a BOX (layout.json), and a
        # box is rendered by Layout — which has no footnotes of its own. Hand
        # it this one, so text_boxes() can render the list without every
        # report's renderer growing a call it would have to remember. Same
        # reach argument as _page_style_once(); see Layout.endnotes_html().
        if styles is not None and hasattr(styles, "bind_footnotes"):
            styles.bind_footnotes(self.fn)
        # And the document itself, for a copied page (pagecopy.py) that the
        # layout draws and has to fill with words.
        if styles is not None and hasattr(styles, "bind_content"):
            styles.bind_content(self)

    def raw(self, key: str) -> str:
        if key not in self._raw:
            # EDIT MODE: a slot the renderer asks for that this document has
            # not got is a mid-move state, not a wall.
            #
            # A collab room IS the document once seeded and never re-reads
            # git, so a renderer that GROWS a section can reach an open room
            # no other way — and raising here took the whole report down for
            # everyone who opened it, with no way back but a hand reseed
            # (rxkids-fiscal's page 2, 2026-09-17: every open for a day
            # rendered nothing but this exception). Draw the slot instead. The
            # page builds, the new slot is visible and editable, and the first
            # edit writes it into the document — writeSlot() in the editor
            # creates a block it cannot find.
            #
            # Publishing is untouched: renderClean() pops DOCSYNC_EDIT, so an
            # export still hits the raise below and an unfilled slot still
            # cannot ship. Same bargain as the missing-source marker in
            # Footnotes.resolve() and the empty-list note in bullets().
            if os.environ.get("DOCSYNC_EDIT"):
                self._new.add(key)
                _served(key, NEW_SLOT.format(key=key))
                return NEW_SLOT.format(key=key)
            raise ContentError(
                f"{self.path.name}: missing '[[{key}]]'.\n"
                f"  The Google Doc must keep every [[key]] marker intact.")
        self._used.add(key)
        _served(key, self._raw[key])
        return self._raw[key]

    def __call__(self, key: str) -> str:
        """Prose block -> HTML paragraph text (footnote refs left for resolve())."""
        return paragraph(self.raw(key))

    def text(self, key: str) -> str:
        """Raw single-line value (titles, labels) with no Markdown conversion."""
        return " ".join(l.strip() for l in _unhead(self.raw(key)).splitlines() if l.strip())

    def has(self, key: str) -> bool:
        """Whether the document carries this slot at all."""
        return key in self._raw

    def text_or(self, key: str, default: str) -> str:
        """text(), or `default` when the document has not got the slot.

        For a label the RENDERER used to hard-code — a chart's legend entry,
        a step name under a circle — and now offers as a slot so the person
        can rephrase it on the page. The renderer's own wording is the
        default, so a document written before the slot existed (a collab
        room seeded from an older content.md, a report whose author never
        listed every label) renders exactly as it did instead of a "new
        slot" marker in the middle of a chart, and publishing does not
        raise. The editor seeds its first edit from the words on the page
        and writes the slot into content.md then. Marked used either way,
        so the slot is never reported as unused when the document does
        carry it."""
        if key in self._raw:
            return self.text(key)
        _served(key, default)
        return default

    def _style(self, key: str) -> str:
        """The style for a slot, and a note that this slot can carry one.

        Every call site that can take a style goes through here, so the set it
        records is exactly the set of styleable slots — which is what lets the
        build say so when someone styles one that cannot.
        """
        self._styleable.add(key)
        if not self._styles:
            return ""
        out = self._styles.text_attr(key)
        # A slot some placed object FOLLOWS gets a hook the published page can
        # measure — data-slot is edit-only, and the anchor runtime has to find
        # its paragraph on the page a reader sees. Only for the slots named,
        # so every other report emits exactly what it did.
        if key in getattr(self._styles, "anchor_hosts", ()):
            out += f' data-anc-host="{key}"'
        return out

    def styleable(self) -> set:
        return set(self._styleable)

    def slot_attr(self, key: str) -> str:
        """data-slot + style for an element the CALLER builds.

        Preferred over t() wherever the element already exists (<h1>, <th>, an
        SVG <text>): no wrapper span, and it works where a span is invalid.
        A generalisation of ul_attr(), which was this idea for one tag.
        """
        slot = f' data-slot="{key}"' if os.environ.get("DOCSYNC_EDIT") else ""
        return slot + self._style(key)

    def derived(self, source: str) -> str:
        """Mark a span whose text is DERIVED DATA, not editable prose.

        A tally, a rank, a computed share: text a person must not retype,
        because the number's authority comes from whatever produced it and a
        hand-edit would only make the page disagree with its own source.

        This is the third answer to "why can't I edit this?", beside
        data-slot (you can) and data-el (you can move it). Without it such
        text is indistinguishable from text somebody forgot to wire, which
        is exactly how it reads to docsync.check's editability pass — and
        the alternative, naming every value in a binding's editability_ok,
        goes stale the moment the numbers are regenerated.

        `source` is how the value is REMADE — the command, script or model
        that prints it — so the answer travels with the markup instead of
        living in a comment nobody reads. The editor shows it when someone
        clicks the number.

        Edit-mode only: the published page carries no scaffolding, exactly
        like slot_attr().
        """
        if not os.environ.get("DOCSYNC_EDIT"):
            return ""
        if not str(source or "").strip():
            # An empty declaration used to clear the whole subtree beneath it
            # in docsync.check — an escape hatch that named no reason, so there
            # was nothing to review. Refused in edit mode, like graphic()'s
            # literals; publishing never emitted the attribute anyway.
            raise ContentError(
                "C.derived() needs the command, script or model that remakes "
                "the value — an empty source declares nothing")
        safe = (str(source).replace("&", "&amp;").replace('"', "&quot;")
                .replace("<", "&lt;"))
        return f' data-fixed="{safe}"'

    def slot_span(self, key: str, inner: str) -> str:
        """Wrap markup the CALLER built in the editor's slot span.

        For a slot whose element must carry something else — a movable
        heading's own data-el, which cannot share the tag with data-slot
        without one style attribute silently eating the other. Same
        discipline as t(): the span exists only when it carries something
        (the editor's hook, or a style someone set), so published and
        unstyled the bytes are exactly the markup that came in.
        """
        attr = self.slot_attr(key)
        return f"<span{attr}>{inner}</span>" if attr else inner

    def t(self, key: str, esc: bool = False) -> str:
        """text(), but tagged for the editor when DOCSYNC_EDIT is set.

        Headings, card titles and captions are single strings the templates drop
        straight into markup, so there is no element to hang data-slot on — this
        supplies one. Outside edit mode it returns exactly what text() does, so
        the published HTML is untouched.

        Not usable inside an attribute or SVG <text>, where a span is invalid;
        those call sites stay on text().
        """
        v = self.text(key)
        if esc:
            v = v.replace("&", "&amp;").replace("<", "&lt;")
        style = self._style(key)
        if not os.environ.get("DOCSYNC_EDIT"):
            # A bare string, as always — unless this slot has actually been
            # styled, which is the only case where the published build needs a
            # span to hang the style on. Unstyled slots are untouched.
            return f"<span{style}>{v}</span>" if style else v
        safe = v if esc else v.replace("&", "&amp;").replace("<", "&lt;")
        return f'<span data-slot="{key}" data-inline="1"{style}>{safe}</span>'

    def html(self, key: str, cls: str | None = None) -> str:
        """Slot -> one or more <p> elements (multi-paragraph slots supported).

        DOCSYNC_EDIT stamps the slot name on each paragraph so the draft editor
        can map a click back to the text that produced it. Off by default: the
        published HTML carries no editing scaffolding.

        A prose block is also a MOVABLE unit — one wrapper for the whole slot,
        so a two-paragraph slot travels together. The wrapper exists only when
        it means something: in edit mode (the editor's handle) or once the slot
        has actually been moved (the committed position). Unmoved and
        published, the bytes are exactly the bare paragraphs they always were.
        The editor treats a wrapper nested inside another movable (a callout's
        own paragraphs) as part of that object, not as a second one.
        """
        attr = f' class="{cls}"' if cls else ""
        slot = f' data-slot="{key}"' if os.environ.get("DOCSYNC_EDIT") else ""
        style = self._style(key)
        body = "".join(f"<p{attr}{slot}{style}>{h}</p>"
                       for h in paragraphs(self.raw(key)))
        # An EMPTIED slot still needs something to click. Deleting the last
        # words of a paragraph leaves the block in content.md and nothing in
        # the page, so the slot had no element at all — no way back into it,
        # and the text could only be restored by editing content.md by hand.
        # A placeholder paragraph in edit mode only; published, an empty slot
        # goes on printing nothing, which is what emptying it asked for. Same
        # bargain as bullets()' empty-list note and raw()'s new-slot marker:
        # the editor reads its text from content.md, never from the page, so
        # a placeholder can never become content.
        if not body and os.environ.get("DOCSYNC_EDIT"):
            body = f"<p{attr}{slot}{style}>{EMPTY_SLOT}</p>"
        return self.movable(key, body)

    def movable(self, key: str, inner: str) -> str:
        """`inner` — markup for slot `key` that the caller built — held in the
        movable block html() gives a paragraph (`para.<key>`), so it drags and
        takes a width like one: a bullet list (<ul{C.ul_attr(key)}>…), a card's
        body, any block of one slot's words. A list the renderer builds for
        itself had no such block, so its words could be typed into and the
        list could never be moved (docsync.check: IMMOVABLE TEXT).

        The wrapper exists only while editing or once moved, exactly as
        html()'s does: an unmoved published page is the bare markup, byte for
        byte. The editor treats a block nested inside another movable (a
        callout's own paragraphs) as part of that object, not a second one."""
        if self._styles and hasattr(self._styles, "wrap"):
            return self._styles.wrap(f"para.{key}", inner)
        return inner

    def list(self, key: str) -> list[str]:
        raw = self.raw(key)
        # A NEW slot would otherwise fall into bullets()' generic "empty list"
        # note, which is true but says nothing about why this list is empty.
        # Every new slot says the same sentence, whatever shape it renders in.
        if key in self._new:
            return [NEW_SLOT.format(key=key)]
        return bullets(raw)

    def ul_attr(self, key: str) -> str:
        """data-slot + style for a <ul> the caller builds itself."""
        return self.slot_attr(key)

    def lines(self, key: str) -> list[str]:
        return [l.strip() for l in _unhead(self.raw(key)).splitlines() if l.strip()]

    def extras(self, page: str) -> str:
        """Render every overflow slot for a page — keys named
        [[extra.<page>.<slug>]] — in content.md order. This is what lets an
        editor add a whole new prose section from the Google Doc alone:
        no renderer change, the slot just appears at the end of that page.

        Under DOCSYNC_EDIT each section is wrapped in a tagged, clickable
        container so the draft editor can edit it (block_html emits bare
        <h2>/<p>/<ul> with no slot to hang a handler on) and offer reorder,
        move and delete controls. An empty slot still gets a placeholder so a
        just-added section is never an invisible target. Off by default: the
        published HTML is unwrapped."""
        prefix = f"extra.{page}."
        edit = os.environ.get("DOCSYNC_EDIT")
        out: list[str] = []
        for k in self._raw:
            if not k.startswith(prefix):
                continue
            html = block_html(self.raw(k))
            # An added section is a slot like any other: it can be styled, and
            # something can be anchored to it. Both were written by the editor
            # and thrown away here — the type panel set a size the page never
            # showed, and a figure told to follow the section found no host to
            # measure, because only _style emits data-anc-host. It also
            # registers the key as styleable, without which the build reports
            # a style on an added section as aimed at a slot that cannot carry
            # one. On the WRAPPER, so a heading, its paragraphs and its list
            # are all covered by the one declaration.
            attrs = self._style(k)
            # And it moves and resizes like every other block of words: an
            # added section is a field (`field.<key>`, the id fill_markers
            # gives an imported page's), not a paragraph you can only type in.
            if self._styles is not None and hasattr(self._styles, "attr"):
                attrs = merge_attrs(attrs, self._styles.attr(f"field.{k}"))
            if edit:
                html = (f'<div class="extra-section" data-slot="{k}" '
                        f'data-extra="1"{attrs}>'
                        + (html or '<p><em>New section — click to write.</em></p>')
                        + '</div>')
            elif attrs:
                # Published, the wrapper exists only when it carries something
                # — the same rule html() follows, so a report whose sections
                # are unstyled, unfollowed and unmoved emits exactly the bytes
                # it did.
                html = f"<div{attrs}>{html}</div>"
            out.append(html)
        return "".join(out)

    def unused_keys(self) -> list[str]:
        return sorted(set(self._raw) - self._used)

    def new_keys(self) -> list[str]:
        """Slots the renderer asked for that this document has not got — the
        ones standing as NEW_SLOT placeholders. Empty outside edit mode, where
        a missing slot raises instead."""
        return sorted(self._new)
