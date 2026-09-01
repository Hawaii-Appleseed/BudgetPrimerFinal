"""Link a project to a Google Doc, and bring that doc's text into it.

    python3 -m docsync.docimport link --id my-report --url https://docs.google.com/…
    python3 -m docsync.docimport show --id my-report        # what would land where

A report's prose is written in `content.md`, keyed by `[[slot]]`. Most people
draft somewhere else first, and for this organisation that somewhere is a
Google Doc — with comments, suggestions and three colleagues in it. This module
is the one-way bridge: the doc is the draft, the project is the document, and
the text moves doc -> project when the person at the editor asks it to.

One-way on purpose. The old two-way sync (docsync.sync) had to reason about
which side moved last and refuse when both did; that machinery is what made a
Doc binding expensive to own. Here the doc is never written to, so there is no
conflict to detect and nothing to lose: the worst case is an import you undo.

Two shapes of doc are understood, and which one you have is decided by looking,
not by asking:

* **A doc that carries `[[key]]` markers** — an export of a content.md, or one
  written against the report's own keys. Each marker's block goes to that slot;
  keys the project does not have are named rather than silently dropped.
* **Ordinary prose** — headings and paragraphs. Headings are matched against
  what the slots CURRENTLY say (a slot reading "Who pays" is what the heading
  "Who pays" is looking for), and the paragraphs under a heading fill that
  group's remaining slots in order. A doc with no headings at all falls back to
  position, which is a guess and is labelled as one.

Nothing here writes a slot. It returns PROPOSALS — key, new markdown, how the
match was made — and the editor applies the ones a person approved through the
pilot's `batch`, so an import is one undo step with the same validation as
typing the text in.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "docsync.yml"

# A doc id out of any of the URL forms Docs hands out — /edit, /preview, a
# copy link, one with a #heading or ?usp= tail. The id itself is the only part
# that matters and it is always the segment after /d/.
_URL_RE = re.compile(r"/(?:document|file)/d/([A-Za-z0-9_-]+)")
# A bare id pasted on its own. Short strings are almost certainly a mistyped
# URL, and calling that out beats fetching a 404.
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,}$")
_MARKER_RE = re.compile(r"^\[\[([A-Za-z0-9._-]+)\]\][ \t]*$", re.M)
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.M)
_WORD_RE = re.compile(r"[a-z0-9]+")

# Below this a heading and a slot's text are not the same section. Tuned so
# "Who pays?" still finds a slot reading "Who pays" (and so that two unrelated
# section headings sharing one common word do not).
MATCH_FLOOR = 0.5
# At or above this a slot's CURRENT text is the heading, so the heading line
# belongs in that slot rather than being dropped as chrome.
TITLE_FLOOR = 0.75
# Last key segments that name a section's heading rather than its prose.
_TITLE_WORDS = {"title", "heading", "head", "h", "h1", "h2", "h3", "label",
                "name", "hed", "subtitle", "sub"}


class DocLinkError(RuntimeError):
    pass


# ---------------------------------------------------------------- the link

def parse_doc_id(s: str) -> str:
    """A Doc id out of whatever the person pasted. Raises rather than guessing:
    a wrong id fetches someone else's document, which is worse than an error."""
    s = (s or "").strip()
    if not s:
        raise DocLinkError("no link given")
    m = _URL_RE.search(s)
    if m:
        return m.group(1)
    if _ID_RE.match(s):
        return s
    if "docs.google.com" in s:
        raise DocLinkError(
            "that is a Google Docs URL but there is no /d/<id> in it — open "
            "the doc and copy the address bar, or use Share ▸ Copy link")
    raise DocLinkError(
        "that does not look like a Google Doc link. Paste the doc's URL "
        "(https://docs.google.com/document/d/…)")


def doc_url(doc_id: str) -> str:
    return f"https://docs.google.com/document/d/{doc_id}/edit" if doc_id else ""


def set_doc(binding_id: str, doc_id: str, path: Path = REGISTRY) -> str:
    """Write (or clear, with "") a binding's `doc:` in docsync.yml.

    Surgical on purpose. docsync.yml is a hand-edited file whose comments carry
    as much of the reasoning as the code does, and a parse-and-rewrite through
    a YAML dumper would drop every one of them. So this finds the binding's own
    block and touches one line inside it.
    """
    if not path.is_file():
        raise DocLinkError(f"{path} does not exist")
    lines = path.read_text().splitlines(keepends=True)
    start = indent = None
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)-\s+id:\s*([A-Za-z0-9._-]+)\s*$", line)
        if m and m.group(2) == binding_id:
            start, indent = i, len(m.group(1))
            break
    if start is None:
        raise DocLinkError(f"no binding with id '{binding_id}' in {path.name}")

    # The block runs to the next item at the same indent, or to the end.
    end = len(lines)
    for i in range(start + 1, len(lines)):
        m = re.match(r"^(\s*)-\s+id:\s*", lines[i])
        if m and len(m.group(1)) <= indent:
            end = i
            break

    field = " " * (indent + 2)          # "  - id:" puts fields two in from '-'
    new = f"{field}doc: {doc_id}\n" if doc_id else ""
    for i in range(start + 1, end):
        if re.match(rf"^{field}doc:\s*", lines[i]):
            lines[i:i + 1] = [new] if new else []
            break
    else:
        if new:
            lines.insert(start + 1, new)
    path.write_text("".join(lines))
    return doc_id


# ------------------------------------------------------------- reading a doc

def clean(md: str) -> str:
    """Google's Markdown export, minus its cosmetics.

    The same unescaping docsync.normalise does for a canonical file, without
    the marker-specific repairs — prose has no markers to repair.
    """
    md = md.replace("\r\n", "\n").replace("\u00a0", " ")
    md = re.sub(r"\\([^\w\s]|_)", r"\1", md)
    md = re.sub(r"\[(https?://[^\]\s]+)\]\(\1\)", r"\1", md)
    md = re.sub(r"[ \t]+$", "", md, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", md).strip()


def markers(md: str) -> dict[str, str]:
    """`[[key]]` blocks in a doc, in document order. Empty when it has none —
    which is the test for whether this doc is a content file or prose."""
    out, ms = {}, list(_MARKER_RE.finditer(md))
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(md)
        out.setdefault(m.group(1), md[m.end():end].strip("\n").strip())
    return out


def blocks(md: str) -> list[dict]:
    """Prose split at its headings: [{heading, level, body}] in order."""
    hs = list(_HEADING_RE.finditer(md))
    out = []
    lead = md[:hs[0].start()] if hs else md
    if lead.strip():
        out.append({"heading": "", "level": 0, "body": lead.strip()})
    for i, m in enumerate(hs):
        end = hs[i + 1].start() if i + 1 < len(hs) else len(md)
        out.append({"heading": m.group(2).strip(), "level": len(m.group(1)),
                    "body": md[m.end():end].strip()})
    return out


def paragraphs(body: str) -> list[str]:
    """A block's body as the paragraphs a slot each holds. A run of list items
    stays one paragraph — a bulleted list is one slot's worth of markdown, and
    splitting it would scatter a list across four slots."""
    return [p.strip() for p in re.split(r"\n[ \t]*\n", body) if p.strip()]


# --------------------------------------------------------------- the matching

def _tokens(s: str) -> set[str]:
    return set(_WORD_RE.findall(s.lower()))


def similarity(a: str, b: str) -> float:
    """How much two short strings are the same section. Word overlap catches
    "Who pays?" vs "Who pays"; character ratio catches a retitled section that
    kept most of its wording. The better of the two wins, so neither one's
    blind spot decides the answer alone."""
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    if not a or not b:
        return 0.0
    ta, tb = _tokens(a), _tokens(b)
    jaccard = len(ta & tb) / len(ta | tb) if (ta or tb) else 0.0
    return max(jaccard, difflib.SequenceMatcher(None, a, b).ratio())


def _group_of(key: str) -> str:
    return key.rsplit(".", 1)[0] if "." in key else key


def _is_title_key(key: str) -> bool:
    return key.rsplit(".", 1)[-1].lower() in _TITLE_WORDS


def _words_of(key: str) -> str:
    return re.sub(r"([a-z])([A-Z])", r"\1 \2", key).replace(".", " ") \
             .replace("-", " ").replace("_", " ")


def _first_line(md: str) -> str:
    for line in (md or "").splitlines():
        if line.strip():
            return re.sub(r"^#+\s*", "", line).strip()
    return ""


def _row(key, md, how, was, score=1.0, note="") -> dict:
    return {"key": key, "md": md, "how": how, "score": round(score, 2),
            "was": (was or "").strip(), "note": note}


def _pair_headings(bs: list[dict], groups: dict[str, list[str]],
                   slots: dict[str, str]) -> dict[int, tuple[str, float]]:
    """Which doc block belongs to which slot group.

    Scored for every pair and then taken best-first, rather than walking the
    blocks in order and taking the first group that clears the bar: two
    sections whose headings share a word would otherwise let whichever came
    first claim the other's slots.
    """
    scored = []
    for bi, b in enumerate(bs):
        if not b["heading"]:
            continue
        for g, keys in groups.items():
            best = max([similarity(b["heading"], _first_line(slots[k])) for k in keys]
                       + [similarity(b["heading"], _words_of(g))])
            if best >= MATCH_FLOOR:
                scored.append((best, bi, g))
    scored.sort(key=lambda t: (-t[0], t[1]))
    pairs, took_b, took_g = {}, set(), set()
    for score, bi, g in scored:
        if bi in took_b or g in took_g:
            continue
        took_b.add(bi)
        took_g.add(g)
        pairs[bi] = (g, score)
    return pairs


def proposals(doc_md: str, slots: dict[str, str]) -> dict:
    """What this doc would put where. Writes nothing.

    `slots` is {key: current markdown} in content.md order. Returns
    {mode, rows, unknown, slots} where each row is one proposed slot write —
    `how` says whether it came from a marker, a matched heading, position, or
    nothing at all (a row with no key, for the person to aim by hand).
    """
    md = clean(doc_md)
    if not md.strip():
        return {"mode": "empty", "rows": [], "unknown": [], "slots": list(slots)}

    marked = markers(md)
    if marked:
        rows = [_row(k, v, "marker", slots.get(k, ""))
                for k, v in marked.items() if k in slots and v]
        return {"mode": "markers", "rows": rows,
                "unknown": [k for k in marked if k not in slots],
                "slots": list(slots)}

    bs = blocks(md)
    groups: dict[str, list[str]] = {}
    for k in slots:
        groups.setdefault(_group_of(k), []).append(k)
    pairs = _pair_headings(bs, groups, slots)

    rows, used = [], set()
    for bi, b in enumerate(bs):
        pair = pairs.get(bi)
        paras = paragraphs(b["body"])
        if not pair:
            # Nothing to aim it at. Kept as a row with no key rather than
            # dropped, so the person can see what did not land and place it.
            whole = ((f"{'#' * b['level']} {b['heading']}\n\n" if b["heading"] else "")
                     + b["body"]).strip()
            if whole:
                rows.append(_row("", whole, "none", "", 0.0,
                                 "no slot matched this section"))
            continue
        g, score = pair
        free = [k for k in groups[g] if k not in used]
        # The heading line goes into a slot only when that slot is ALREADY a
        # heading — either by its key or because it currently reads as one.
        # Otherwise it is chrome the report draws itself, and writing it into
        # a prose slot would duplicate it on the page.
        if b["heading"] and free and (
                _is_title_key(free[0])
                or similarity(b["heading"], _first_line(slots[free[0]])) >= TITLE_FLOOR):
            k = free.pop(0)
            used.add(k)
            rows.append(_row(k, b["heading"], "heading", slots[k], score))
        for i, k in enumerate(free):
            if i >= len(paras):
                break
            used.add(k)
            # The last slot of a group soaks up whatever paragraphs are left,
            # so a section that grew in the doc arrives whole instead of
            # arriving truncated at the slot count.
            body = ("\n\n".join(paras[i:]) if i == len(free) - 1 else paras[i])
            rows.append(_row(k, body, "heading", slots[k], score))
        if len(paras) > len(free) and not free:
            rows.append(_row("", "\n\n".join(paras), "none", "", 0.0,
                             f"'{b['heading']}' matched {g}, which has no free slots"))

    # A doc with no headings gives the matcher nothing to work with. Rather
    # than hand back a page of unplaced text, fall back to order — and say so,
    # because order is a guess and the dialog defaults guesses to Skip.
    if not pairs:
        free = [k for k in slots if not _is_title_key(k)]
        paras = [p for b in bs for p in paragraphs(b["body"])]
        if paras and free:
            rows = [_row(k, p, "position", slots[k], 0.0, "matched by position only")
                    for k, p in zip(free, paras)]
            rows += [_row("", p, "none", "", 0.0, "more paragraphs than slots")
                     for p in paras[len(free):]]

    return {"mode": "prose", "rows": rows, "unknown": [], "slots": list(slots)}


# ------------------------------------------------------------------- the CLI

def _slots(binding_id: str) -> dict[str, str]:
    from docsync.content import parse_content            # noqa: PLC0415
    from docsync.registry import get                     # noqa: PLC0415
    return parse_content(get(binding_id).content)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    link = sub.add_parser("link", help="point a project at a Google Doc")
    link.add_argument("--id", required=True)
    link.add_argument("--url", required=True, help='the doc URL, or "" to unlink')
    show = sub.add_parser("show", help="what an import would put where")
    show.add_argument("--id", required=True)
    show.add_argument("--file", help="read the doc's markdown from a file instead")
    a = ap.parse_args(argv)

    try:
        if a.cmd == "link":
            doc = parse_doc_id(a.url) if a.url.strip() else ""
            set_doc(a.id, doc)
            print(f"{a.id}: {doc_url(doc) if doc else 'unlinked'}")
            return 0

        from docsync.registry import get                 # noqa: PLC0415
        if a.file:
            md = Path(a.file).read_text()
        else:
            from docsync import fetch                    # noqa: PLC0415
            b = get(a.id)
            if not b.doc:
                raise DocLinkError(f"'{a.id}' has no doc: — link one first")
            md = fetch.fetch_markdown(b.doc)
        print(json.dumps(proposals(md, _slots(a.id)), indent=2))
        return 0
    except DocLinkError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
