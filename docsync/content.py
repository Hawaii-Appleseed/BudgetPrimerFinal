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
_SOURCE_RE = re.compile(r"^\[([^\]]+)\]:\s*(.*?)\s+—\s+(\S+)\s*$", re.M)


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
                f"source line is not '[id]: text — https://url':\n  {line}")
        sid, txt, url = m.group(1), m.group(2).strip(), m.group(3).strip()
        if sid in src:
            raise ContentError(f"duplicate source id '[{sid}]'")
        src[sid] = (txt, url)
    if not src:
        raise ContentError("[[sources]] section is empty")
    return src


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
    s = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", stash_img, s)
    s = re.sub(r"\[([^\]^][^\]]*)\]\((https?://[^)\s]+)\)", stash, s)
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
            items.append(
                f'<li id="en{i}"{hook} style="margin-bottom:.6em">{txt} '
                f'<a href="{url}" style="word-break:break-all">{url}</a></li>')
        return ('<ol class="ds-endnotes" style="padding-left:1.4em;margin:0">'
                + "".join(items) + "</ol>")


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
        # The editor can place an endnotes list as a BOX (layout.json), and a
        # box is rendered by Layout — which has no footnotes of its own. Hand
        # it this one, so text_boxes() can render the list without every
        # report's renderer growing a call it would have to remember. Same
        # reach argument as _page_style_once(); see Layout.endnotes_html().
        if styles is not None and hasattr(styles, "bind_footnotes"):
            styles.bind_footnotes(self.fn)

    def raw(self, key: str) -> str:
        if key not in self._raw:
            raise ContentError(
                f"{self.path.name}: missing '[[{key}]]'.\n"
                f"  The Google Doc must keep every [[key]] marker intact.")
        self._used.add(key)
        return self._raw[key]

    def __call__(self, key: str) -> str:
        """Prose block -> HTML paragraph text (footnote refs left for resolve())."""
        return paragraph(self.raw(key))

    def text(self, key: str) -> str:
        """Raw single-line value (titles, labels) with no Markdown conversion."""
        return " ".join(l.strip() for l in _unhead(self.raw(key)).splitlines() if l.strip())

    def _style(self, key: str) -> str:
        """The style for a slot, and a note that this slot can carry one.

        Every call site that can take a style goes through here, so the set it
        records is exactly the set of styleable slots — which is what lets the
        build say so when someone styles one that cannot.
        """
        self._styleable.add(key)
        return self._styles.text_attr(key) if self._styles else ""

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
        if self._styles and hasattr(self._styles, "attr"):
            el_id = f"para.{key}"
            pos = self._styles.attr(el_id)
            spacer = self._styles.spacer(el_id)
            if pos or spacer:
                return f"{spacer}<div{pos}>{body}</div>"
        return body

    def list(self, key: str) -> list[str]:
        return bullets(self.raw(key))

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
            if edit:
                html = (f'<div class="extra-section" data-slot="{k}" data-extra="1">'
                        + (html or '<p><em>New section — click to write.</em></p>')
                        + '</div>')
            out.append(html)
        return "".join(out)

    def unused_keys(self) -> list[str]:
        return sorted(set(self._raw) - self._used)
