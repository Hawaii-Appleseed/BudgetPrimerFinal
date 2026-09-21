"""A copied designed page: the editor's Duplicate page for a sheet the RENDERER
drew.

A designed page exists because render_report.py says so — its headings, its
stat cards, its charts are Python, and nothing here can ask a renderer for a
second copy of a page. So Duplicate used to copy the movable layer (boxes,
shapes, tables) and hand back an otherwise blank sheet, with a status line
explaining that the designed content stayed where it was. That is an honest
answer and a useless one: the person asked for another page like this one.

This is the other half. The editor snapshots the page's PRISTINE edit-mode
markup — rendered with nothing of the person's own (no positions, no fills,
no hidden list) so what it stores is the design and only the design —
rewrites every id and slot key under `copy.<blank id>.`, writes each slot's
words into content.md under the new key, and files the markup on the blank
page's entry in layout.json:

    "pages": {"order": [1, "p1", 2],
              "blanks": [{"id": "p1", "copy": {"of": 1, "html": "<div …"}}]}

fill() is what a render does with that. It is called from Layout.layer(),
which every renderer already emits inside each sheet (designed or blank), so
no renderer changes and a copy renders wherever the engine does — the local
editor, the hub's Pyodide, the published build. It walks the stored markup
and, on every hook the editor knows:

    data-el="X"     -> the strut and attributes Layout.attr(X) would give a
                       renderer-emitted element with that id: the copy's
                       elements move, hide, resize and lock exactly like the
                       original's, because they ARE elements with ids.
    data-slot="K"   -> the slot's CURRENT words, re-rendered from content.md
                       the way the original element rendered them (inline
                       text, paragraphs, a list, an added section), plus the
                       style the type panel gave the key. Editing the copy
                       edits content.md, the same as anywhere.
    data-fill="X"   -> Layout.fill_attr(X): the copy's tiles recolour.
    data-sec="X"    -> Layout.sec(X): a band still stretches.

Publishing strips the edit-only hooks (data-el, data-slot, data-inline,
data-fixed …) exactly as the renderer's own helpers do, so a published copy
carries no scaffolding either.

What a copy is NOT: the renderer. A chart in the copy is the SVG the
original drew, with its labels re-read from content.md where the renderer
made them slots; its bars do not move when a DATA constant changes, and a
static endnote list copied along is a list of words, not the numbered
endnotes (those ride the prose's [^id] tokens, which DO re-resolve). The
status line in the editor says so.
"""
from __future__ import annotations

import os
import re
from html.parser import HTMLParser

from . import content as _content

PREFIX = "copy."

# Attributes the renderer stamps only under DOCSYNC_EDIT. Every helper in
# layout.py / content.py withholds these when publishing, and the stored
# snapshot was taken in edit mode, so a publish render strips them here.
_EDIT_ONLY = ("data-el", "data-slot", "data-inline", "data-extra", "data-fill",
              "data-sec", "data-hidden", "data-fixed", "data-master", "data-new")
# Attributes fill() re-derives from layout.json on every render; a stale copy
# in the stored markup would fight the fresh one.
_REDERIVED = ("data-placed", "data-reserve-for", "data-reserve", "data-reserve-w",
              "data-anc-host", "data-anc", "data-anc-edge", "data-anc-gap",
              "data-spacer-for")
_VOID = frozenset("area base br col embed hr img input link meta param source "
                  "track wbr".split())
# html.parser lowercases every name. A browser's HTML parser restores the
# SVG ones from the spec's adjustment tables when it reads the built page,
# so the rendering is right either way — but the built file is ALSO read by
# regex (docsync.check's viewBox test, chart_scroll's wrapper) and by anyone
# opening it, so the case goes back here, from the same tables.
_SVG_TAGS = {t.lower(): t for t in (
    "altGlyph altGlyphDef altGlyphItem animateColor animateMotion animateTransform "
    "clipPath feBlend feColorMatrix feComponentTransfer feComposite feConvolveMatrix "
    "feDiffuseLighting feDisplacementMap feDistantLight feDropShadow feFlood feFuncA "
    "feFuncB feFuncG feFuncR feGaussianBlur feImage feMerge feMergeNode feMorphology "
    "feOffset fePointLight feSpecularLighting feSpotLight feTile feTurbulence "
    "foreignObject glyphRef linearGradient radialGradient textPath").split()}
_SVG_ATTRS = {a.lower(): a for a in (
    "attributeName attributeType baseFrequency baseProfile calcMode clipPathUnits "
    "contentScriptType contentStyleType diffuseConstant edgeMode externalResourcesRequired "
    "filterRes filterUnits glyphRef gradientTransform gradientUnits kernelMatrix "
    "kernelUnitLength keyPoints keySplines keyTimes lengthAdjust limitingConeAngle "
    "markerHeight markerUnits markerWidth maskContentUnits maskUnits numOctaves "
    "pathLength patternContentUnits patternTransform patternUnits pointsAtX pointsAtY "
    "pointsAtZ preserveAlpha preserveAspectRatio primitiveUnits refX refY repeatCount "
    "repeatDur requiredExtensions requiredFeatures specularConstant specularExponent "
    "spreadMethod startOffset stdDeviation stitchTiles surfaceScale systemLanguage "
    "tableValues targetX targetY textLength viewBox viewTarget xChannelSelector "
    "yChannelSelector zoomAndPan").split()}


def _esc_attr(v: str) -> str:
    return v.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def _esc_text(v: str) -> str:
    return v.replace("&", "&amp;").replace("<", "&lt;")


def _merge_style(attrs: list, css: str) -> list:
    """One style attribute. An element with two keeps only the first, so a
    fresh declaration joins the existing one instead of standing beside it."""
    if not css:
        return attrs
    out, done = [], False
    for k, v in attrs:
        if k == "style" and not done:
            v = ";".join(x for x in ((v or "").strip().rstrip(";"), css) if x)
            done = True
        out.append((k, v))
    if not done:
        out.append(("style", css))
    return out


def _parse_attr_string(s: str) -> list:
    """The ` key="v" key2="v2"` string a Layout/Content hook returns, as pairs.
    Their values are hook-made — an id, an inch, a colour — never something
    that needs a real HTML tokenizer."""
    out = []
    for m in re.finditer(r'([A-Za-z_:][-A-Za-z0-9_:.]*)(?:="([^"]*)")?', s or ""):
        v = m.group(2)
        # A bare attribute (PLACED is ` data-placed`) has no value to unescape.
        out.append((m.group(1), None if v is None
                    else v.replace("&quot;", '"').replace("&amp;", "&")))
    return out


class _Filler(HTMLParser):
    def __init__(self, L, C, edit: bool):
        # convert_charrefs=False so entity and char references pass through
        # exactly as stored; a re-escape of already-escaped text is the bug
        # this avoids.
        super().__init__(convert_charrefs=False)
        self.L, self.C, self.edit = L, C, edit
        self.out: list[str] = []
        # (tag, mode): 0 = passes through, 1 = a slot element whose stored
        # content is replaced (its close tag is emitted), 2 = dropped whole
        # (a later <p> of a block slot already rendered in full).
        self.stack: list[tuple[str, int]] = []
        self.skip = 0          # >0 while inside a slot whose content is replaced
        self.keys: list[str] = []
        self._pending_paras: list[str] = []
        self._para_attrs = None
        self._blocks_done: set[str] = set()

    # ---- emit ----------------------------------------------------------
    def _emit(self, s: str) -> None:
        if not self.skip:
            self.out.append(s)

    def _tag(self, tag: str, attrs: list, close: bool) -> str:
        bits = [_SVG_TAGS.get(tag, tag)]
        for k, v in attrs:
            k = _SVG_ATTRS.get(k, k)
            bits.append(k if v is None else f'{k}="{_esc_attr(v)}"')
        return "<" + " ".join(bits) + ("/>" if close else ">")

    def _close(self, tag: str) -> str:
        return f"</{_SVG_TAGS.get(tag, tag)}>"

    # ---- the hooks -----------------------------------------------------
    def _rewrite(self, tag: str, attrs: list) -> tuple[list, str, str | None]:
        """attrs -> (new attrs, markup to emit BEFORE the tag, slot key or
        None). The key is what the caller renders in place of the element's
        stored content."""
        a = dict(attrs)
        pre = ""
        el = a.get("data-el")
        slot = a.get("data-slot")
        fill = a.get("data-fill")
        sec = a.get("data-sec")
        keep = [(k, v) for k, v in attrs
                if k not in _EDIT_ONLY and k not in _REDERIVED]
        # data-fixed says what a value is derived from; it rides in edit mode.
        if self.edit and a.get("data-fixed") is not None:
            keep.append(("data-fixed", a["data-fixed"]))
        if el:
            style = dict(keep).get("style") or ""
            keep = [(k, v) for k, v in keep if k != "style"]
            pre += self.L.spacer(el)
            keep += _parse_attr_string(self.L.attr(el, style))
        if fill:
            got = _parse_attr_string(self.L.fill_attr(fill))
            for k, v in got:
                if k == "style":
                    keep = _merge_style(keep, v)
                else:
                    keep.append((k, v))
        if sec:
            got = _parse_attr_string(self.L.sec(sec))
            for k, v in got:
                if k == "style":
                    keep = _merge_style(keep, v)
                else:
                    keep.append((k, v))
        if slot:
            if self.edit:
                keep.append(("data-slot", slot))
                if a.get("data-inline") is not None:
                    keep.append(("data-inline", a["data-inline"]))
            for k, v in _parse_attr_string(self.C._style(slot)):
                if k == "style":
                    keep = _merge_style(keep, v)
                else:
                    keep.append((k, v))
        return keep, pre, slot

    def _slot_html(self, key: str, tag: str, inline: bool, extra: bool) -> str:
        self.keys.append(key)
        if not self.C.has(key):
            # The editor writes every slot of a copy the moment it makes one,
            # so this is a hand-edited document. Say so while editing; ship
            # nothing rather than refuse the whole build over a copy.
            return (_esc_text(_content.NEW_SLOT.format(key=key))
                    if self.edit else "")
        raw = self.C.raw(key)
        if extra:
            html = _content.block_html(raw)
            if not html and self.edit:
                html = "<p><em>New section — click to write.</em></p>"
            return html
        if tag in ("ul", "ol"):
            items = _content.bullets(raw)
            return "".join(f"<li>{i}</li>" for i in items)
        if tag == "p" and not inline:
            # paragraphs() for a prose block — the FIRST paragraph lands in
            # this <p>; any further ones are appended as siblings after it
            # by handle_endtag (see _pending_paras).
            self._blocks_done.add(key)
            paras = _content.paragraphs(raw)
            if not paras:
                return _content.EMPTY_SLOT if self.edit else ""
            self._pending_paras = paras[1:]
            return paras[0]
        if key in getattr(self.C, "_new", ()):
            return _esc_text(raw)          # the NEW_SLOT marker, verbatim
        # Inline text, as C.t() renders it: the single-line value, escaped,
        # no markdown — what a heading, a stat, a caption, an SVG label is.
        return _esc_text(self.C.text(key))

    # ---- parser callbacks --------------------------------------------------
    def handle_starttag(self, tag, attrs):
        if self.skip:
            self.stack.append((tag, 1))
            self.skip += 1
            return
        a = dict(attrs)
        key = a.get("data-slot")
        inline = a.get("data-inline") is not None
        # The renderer emits one <p data-slot> per paragraph of a prose slot;
        # the first one here rendered them ALL, so the rest are dropped.
        if key and tag == "p" and not inline and key in self._blocks_done:
            self.stack.append((tag, 2))
            self.skip = 1
            return
        keep, pre, key = self._rewrite(tag, attrs)
        if pre:
            self.out.append(pre)
        self._pending_paras = []
        self._para_attrs = None
        if key:
            extra = a.get("data-extra") is not None
            if extra and self.edit:
                keep.append(("data-extra", "1"))
            self.out.append(self._tag(tag, keep, False))
            self.out.append(self._slot_html(key, tag, inline, extra))
            if self._pending_paras:
                self._para_attrs = keep
            self.stack.append((tag, 1))
            self.skip = 1
            return
        self.out.append(self._tag(tag, keep, False))
        self.stack.append((tag, 0))

    def handle_startendtag(self, tag, attrs):
        if self.skip:
            return
        keep, pre, key = self._rewrite(tag, attrs)
        if pre:
            self.out.append(pre)
        if key:
            # A self-closed slot element is nonsense; keep it as a tag pair.
            a = dict(attrs)
            self.out.append(self._tag(tag, keep, False))
            self.out.append(self._slot_html(key, tag, a.get("data-inline") is not None,
                                            a.get("data-extra") is not None))
            self.out.append(self._close(tag))
            return
        self.out.append(self._tag(tag, keep, tag not in _VOID))

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        if self.stack:
            t, mode = self.stack.pop()
            if mode:
                self.skip -= 1
                if self.skip == 0 and mode == 1:
                    # The slot element closes: now its extra paragraphs.
                    self.out.append(self._close(tag))
                    for h in self._pending_paras:
                        self.out.append(self._tag(tag, self._para_attrs or [], False))
                        self.out.append(h)
                        self.out.append(self._close(tag))
                    self._pending_paras = []
                return
        self.out.append(self._close(tag))

    def handle_data(self, data):
        self._emit(data)

    def handle_entityref(self, name):
        self._emit(f"&{name};")

    def handle_charref(self, name):
        self._emit(f"&#{name};")

    def handle_comment(self, data):
        pass                              # editor notes never reach a page

    def handle_decl(self, decl):
        pass


def fill(html: str, L, C, edit: bool | None = None) -> str:
    """Render a stored page copy against the CURRENT content and layout.

    `L` is the report's Layout, `C` its Content (bound to L by Content's
    constructor). `edit` defaults to DOCSYNC_EDIT, like every other hook."""
    if edit is None:
        edit = bool(os.environ.get("DOCSYNC_EDIT"))
    f = _Filler(L, C, edit)
    f.feed(html or "")
    f.close()
    return "".join(f.out)


def copy_keys(html: str) -> list[str]:
    """Every slot key a stored copy reads — what its content.md must carry."""
    return [m.group(1) for m in re.finditer(r'data-slot="([^"]+)"', html or "")]
