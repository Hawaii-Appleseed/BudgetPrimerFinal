"""Does a Cider-published page look like the page it replaces?

    python3 -m docsync.fidelity CURRENT.html CANDIDATE.html

Two checks. The first always runs and needs nothing but Python, so it holds on
the hub's Actions job as well as on a laptop; the second adds a real browser
where one is installed.

  MARKUP  (stdlib only, always) Both pages are parsed and read the way their own
          stylesheets — the page's <style> blocks and every local <link>ed sheet
          — can see them: an element is its tag plus only the classes, ids and
          attributes some rule names, and a bare <span> no rule can reach is
          dissolved (Cider's editable-sentence wrappers draw nothing). Then:
          an element of a kind the current page never had (a `b` where the
          page only ever used `strong`) fails — an editor bolding a word adds
          a kind the page already has, and passes; and when the words are
          unchanged, the arrangement must be unchanged too.
  PIXELS  (node + the `playwright` npm package, when present) Both pages are
          drawn at a desktop and a phone width — the candidate from a sibling
          temp file, so relative assets resolve as they will once published.
          Every distinct computed text style must survive, neither width may
          start scrolling sideways, and with the words unchanged the
          screenshots must match.

`check()` returns the problems (empty = faithful). A browser that cannot be
started is said out loud and skipped; the markup check still decides.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = Path(__file__).with_name("fidelity_probe.js")
WIDTHS = (1280, 390)
# Anti-aliasing noise only: fraction of pixels allowed to differ when the text is the same.
PIXEL_TOLERANCE = 0.0001


def _probe(url: str, width: int, shot: Path) -> dict:
    r = subprocess.run(["node", str(PROBE), url, str(width), str(shot)], capture_output=True,
                       text=True, timeout=120, env={**os.environ, "CIDER_ROOT": str(ROOT)})
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip()[-400:])
    return json.loads(r.stdout.strip().splitlines()[-1])


def _pixel_fraction(a: Path, b: Path) -> float:
    from PIL import Image, ImageChops
    ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    if ia.size != ib.size:
        return 1.0
    diff = ImageChops.difference(ia, ib).convert("L").point(lambda v: 255 if v > 30 else 0)
    return sum(1 for v in diff.getdata() if v) / (ia.size[0] * ia.size[1])


# --------------------------------------------------------------- the static check
# Stdlib only, so it runs wherever publish does (the hub's Actions job installs
# nothing but PyYAML). It cannot see pixels; it sees what decides them: which
# elements the page's own stylesheets can reach, and in what arrangement.

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "source", "track", "wbr"}
_SKIP = {"script", "style", "noscript", "template"}
_STRUCTURAL = re.compile(r":(first-|last-|nth-|only-|empty)")


class _Node:
    __slots__ = ("tag", "attrs", "kids", "parent")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.kids, self.parent = tag, dict(attrs), [], parent


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.cur = _Node("#root", [], None)

    def handle_starttag(self, tag, attrs):
        n = _Node(tag, [(k, v or "") for k, v in attrs], self.cur)
        self.cur.kids.append(n)
        if tag not in _VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        self.cur.kids.append(_Node(tag, [(k, v or "") for k, v in attrs], self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:   # an unmatched close is ignored
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.kids.append(data)


def _body(html: str) -> _Node:
    t = _Tree()
    t.feed(html)
    t.close()
    stack = [t.root]
    while stack:
        n = stack.pop()
        if n.tag == "body":
            return n
        stack.extend(k for k in n.kids if isinstance(k, _Node))
    return t.root


class _Vocab:
    """What a page's stylesheets can name: the only things that can change its look."""

    def __init__(self, css: str):
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        sels = [s.strip() for s in re.findall(r"([^{};]+)\{", css) if not s.strip().startswith("@")]
        joined = ",".join(sels)
        self.tags = set(re.findall(r"(?:^|[\s>+~,(])([a-zA-Z][a-zA-Z0-9-]*)", joined))
        self.classes = set(re.findall(r"\.(-?[_a-zA-Z][\w-]*)", joined))
        self.ids = set(re.findall(r"#(-?[_a-zA-Z][\w-]*)", joined))
        self.attrs = set(re.findall(r"\[\s*([\w-]+)", joined))
        # Compounds whose match depends on their siblings or parent: the subject of
        # a > + ~ combinator, or one carrying :first-child / :nth-of-type / ...
        self.positional: list[tuple[str, set[str]]] = []
        for sel in ",".join(sels).split(","):
            parts = re.split(r"\s*([>+~])\s*|\s+", sel.strip())
            prev_comb = False
            for p in parts:
                if p is None or not p:
                    continue
                if p in ">+~":
                    prev_comb = True
                    continue
                if prev_comb or _STRUCTURAL.search(p):
                    m = re.match(r"([a-zA-Z][\w-]*|\*)?", p)
                    self.positional.append(((m.group(1) or "*").lower(),
                                            set(re.findall(r"\.(-?[_a-zA-Z][\w-]*)", p))))
                prev_comb = False

    def sig(self, n: _Node) -> str:
        """The element as its stylesheets can tell it apart."""
        out = n.tag
        cls = sorted(set(n.attrs.get("class", "").split()) & self.classes)
        out += "".join("." + c for c in cls)
        if n.attrs.get("id") in self.ids:
            out += "#" + n.attrs["id"]
        for a in sorted(set(n.attrs) & self.attrs):
            out += f"[{a}={n.attrs[a]}]"
        if n.attrs.get("style"):
            out += "{" + re.sub(r"\s+", "", n.attrs["style"]) + "}"
        return out

    def _positional(self, n: _Node) -> bool:
        cls = set(n.attrs.get("class", "").split())
        return any((t == "*" or t == n.tag) and c <= cls for t, c in self.positional)

    def inert(self, n: _Node) -> bool:
        """A bare <span> no rule can see: the browser draws nothing for it.

        Not inert when a stylesheet names `span`, or when an element inside it is
        matched by position (a > child, a + sibling, :first-child ...) — wrapping
        moves that element to a new parent and new siblings."""
        if n.tag != "span" or "span" in self.tags or self.sig(n) != "span":
            return False
        stack = [k for k in n.kids if isinstance(k, _Node)]
        while stack:
            k = stack.pop()
            if self._positional(k):
                return False
            stack.extend(x for x in k.kids if isinstance(x, _Node))
        return True


def _stylesheets(html: str, base: Path) -> str:
    """Every <style> block, and every LOCAL <link rel=stylesheet> the page loads."""
    css = re.findall(r"<style[^>]*>(.*?)</style>", html, re.S | re.I)
    for tag in re.findall(r"<link\b[^>]*>", html, re.I):
        if not re.search(r"""rel\s*=\s*["']?stylesheet""", tag, re.I):
            continue
        m = re.search(r"""href\s*=\s*["']([^"']+)""", tag, re.I)
        if not m or re.match(r"(?:[a-z]+:)?//", m.group(1), re.I):
            continue
        f = (base / m.group(1).split("?")[0].split("#")[0])
        if f.is_file():
            css.append(f.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(css)


def _flatten(n: _Node, v: _Vocab, out: list, pairs: set, parent_sig: str) -> None:
    """The body as its stylesheets see it: inert spans dissolved, text merged."""
    for k in n.kids:
        if isinstance(k, str):
            if out and isinstance(out[-1], str):
                out[-1] += k
            else:
                out.append(k)
        elif k.tag in _SKIP:
            continue
        elif v.inert(k):
            _flatten(k, v, out, pairs, parent_sig)
        else:
            s = v.sig(k)
            pairs.add(f"{parent_sig} > {s}")
            out.append(("<" + s + ">",))         # a tag is a 1-tuple, text a str
            _flatten(k, v, out, pairs, s)
            if k.tag not in _VOID:
                out.append(("</" + k.tag + ">",))


def _shape(html: str, v: _Vocab) -> tuple[list[str], set[str], str]:
    out: list = []
    pairs: set = set()
    body = _body(html)
    _flatten(body, v, out, pairs, "body")
    # Words, not runs: bolding one word must read as two tags inserted between
    # words, not as a paragraph of text replaced.
    toks: list[str] = []
    for t in out:
        toks.extend([t[0]] if isinstance(t, tuple) else t.split())
    text = " ".join(t for t in toks if not t.startswith("<"))
    return toks, pairs, text


def static_check(current: Path, candidate_html: str) -> list[str]:
    """Problems a stylesheet would show, found without drawing the page."""
    cur_html = current.read_text(encoding="utf-8")
    v = _Vocab(_stylesheets(cur_html, current.parent) + "\n" + _stylesheets(candidate_html, current.parent))
    a_toks, a_pairs, a_text = _shape(cur_html, v)
    b_toks, b_pairs, b_text = _shape(candidate_html, v)
    # A kind of element the current page never had (a <b> on a page that only
    # ever used <strong>) is a renderer change. A kind it already has, somewhere
    # new (a word an editor bolded), is an edit, and is left alone.
    a_kinds = {p.rsplit(" > ", 1)[1] for p in a_pairs}
    problems = [f"an element the page never had: {p}" for p in sorted(b_pairs - a_pairs)
                if p.rsplit(" > ", 1)[1] not in a_kinds]
    if a_text == b_text and a_toks != b_toks:
        # Same words, so any markup change is the engine's — except tags of a kind
        # the page already uses, wrapped around words that were there (an editor
        # bolding or linking something). Anything REPLACED is never an edit.
        kinds = {"<" + k + ">" for k in a_kinds} | {"</" + k.split(".")[0].split("#")[0].split("[")[0].split("{")[0] + ">"
                                                     for k in a_kinds}
        sm = difflib.SequenceMatcher(None, a_toks, b_toks, autojunk=False)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                continue
            changed = a_toks[i1:i2] + b_toks[j1:j2]
            if op != "replace" and all(t in kinds for t in changed):
                continue
            ctx = lambda t, i, j: " ".join(t[max(0, i - 3):j + 3])[:200]   # noqa: E731
            problems.append(f"same words, different markup: was …{ctx(a_toks, i1, i2)}… "
                            f"now …{ctx(b_toks, j1, j2)}…")
            break
    return problems


# --------------------------------------------------------------- the browser check
def browser_check(current: Path, candidate_html: str) -> list[str] | None:
    """Problems seen by drawing both pages; None when no browser can be started."""
    if not shutil.which("node"):
        print("fidelity: no node here — checked the markup only, not the pixels", file=sys.stderr)
        return None
    cand = current.with_name(f".{current.stem}.fidelity{current.suffix}")
    problems: list[str] = []
    try:
        cand.write_text(candidate_html, encoding="utf-8")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            for w in WIDTHS:
                a = _probe(current.resolve().as_uri(), w, td / f"a{w}.png")
                b = _probe(cand.resolve().as_uri(), w, td / f"b{w}.png")
                for s in sorted(set(a["styles"]) - set(b["styles"])):
                    problems.append(f"{w}px: style lost — {s}")
                for s in sorted(set(b["styles"]) - set(a["styles"])):
                    problems.append(f"{w}px: style appeared — {s}")
                if b["scrollWidth"] > max(a["scrollWidth"], w):
                    problems.append(f"{w}px: page now scrolls sideways ({b['scrollWidth']}px wide)")
                if a["text"] == b["text"]:
                    frac = _pixel_fraction(td / f"a{w}.png", td / f"b{w}.png")
                    if frac > PIXEL_TOLERANCE:
                        problems.append(f"{w}px: same text but {frac:.4%} of pixels differ")
    except (RuntimeError, OSError, subprocess.SubprocessError, ImportError) as e:
        print(f"fidelity: no browser ({str(e).splitlines()[0][:120]}) — checked the markup only, "
              "not the pixels", file=sys.stderr)
        return None
    finally:
        cand.unlink(missing_ok=True)
    return problems


def check(current: Path, candidate_html: str) -> list[str]:
    """Problems with `candidate_html` as a replacement for the page at `current`."""
    problems = static_check(current, candidate_html)
    drawn = browser_check(current, candidate_html)
    return problems + (drawn or [])


def main(argv: list[str] | None = None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) != 2:
        raise SystemExit(__doc__)
    probs = check(Path(a[0]), Path(a[1]).read_text(encoding="utf-8"))
    for p in probs:
        print("  " + p)
    print("faithful" if not probs else f"{len(probs)} difference(s)")
    return 1 if probs else 0


if __name__ == "__main__":
    raise SystemExit(main())
