"""A Google Doc as the text docsync.compose lays out, so "format this Doc as a
report" is one command:

    python3 -m docsync.new --id wealth-tax --template appleseed-report \\
        --scheme slate --from-gdoc https://docs.google.com/document/d/<id>/edit --build

It reads the Docs API's document JSON (the Doc's structure, not an export's
guess at it) and writes the composer's grammar:

  TITLE paragraph            # title       (else the Doc's own name)
  SUBTITLE paragraph         Subtitle: …
  HEADING_1                  ## a section page
  HEADING_2 … 6              ### a subheading
  a short paragraph, all bold   ### a subheading (how many Docs set them)
  a footnote                 [^nN] in the words, and its [^nN]: line: the
                             footnote's words, its last link as the source's
  a picture                  ![its description](figure-K.png), fetched
  a table                    a pipe table, its first row the header
  a 1x1 table                > [!NOTE] a callout; a bold first line its title
  a bulleted list            - items (1. when numbered)

A "Figure N." paragraph before a picture and a "Source:" one after it become
the figure's caption and source, as the Doc already sets them.

Reading a Doc needs a Docs API token: GOOGLE_OAUTH_TOKEN (an access token for
an account that can read it), or DOCSYNC_GOOGLE_SA (a service-account key, a
path or the JSON) for an account the Doc is shared with. A saved document JSON
(the API's own answer) is read as it is, offline.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

_DOC_ID = re.compile(r"(?:/document/d/|^)([A-Za-z0-9_-]{25,})")
_ORDERED = ("DECIMAL", "ZERO_DECIMAL", "UPPER_ALPHA", "ALPHA", "UPPER_ROMAN", "ROMAN")
_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif",
        "image/webp": "webp", "image/svg+xml": "svg"}


class GdocError(RuntimeError):
    pass


def doc_id(ref: str) -> str:
    """The id in a Doc's address (or the id itself)."""
    m = _DOC_ID.search(ref.strip())
    if not m:
        raise GdocError(f"not a Google Doc address or id: {ref!r}")
    return m.group(1)


def _token() -> str | None:
    if os.environ.get("GOOGLE_OAUTH_TOKEN"):
        return os.environ["GOOGLE_OAUTH_TOKEN"]
    sa = os.environ.get("DOCSYNC_GOOGLE_SA")
    if not sa:
        return None
    from .fetch import FetchError, access_token          # noqa: PLC0415
    p = Path(sa).expanduser()
    try:
        return access_token(p.read_text() if p.is_file() else sa)
    except FetchError as e:
        raise GdocError(f"the service-account key did not give a token: {e}") from e


def load(ref: str) -> dict:
    """The Doc's document JSON: read from a saved file, or asked of the Docs
    API for an address or id."""
    p = Path(ref).expanduser()
    if p.suffix == ".json" and p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    did = doc_id(ref)
    tok = _token()
    if not tok:
        raise GdocError(
            "reading a Google Doc needs a Docs API token: set GOOGLE_OAUTH_TOKEN "
            "(an access token for an account that can read the Doc) or "
            "DOCSYNC_GOOGLE_SA (a service-account key the Doc is shared with), "
            "or pass the document's saved JSON instead of its address")
    req = urllib.request.Request(f"https://docs.googleapis.com/v1/documents/{did}",
                                 headers={"Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise GdocError(f"the Docs API answered {e.code} for {did}: "
                        f"{e.read()[:300].decode('utf-8', 'replace')}") from e
    except urllib.error.URLError as e:
        raise GdocError(f"could not reach the Docs API: {e.reason}") from e


def _runs(elements: list) -> list:
    out = []
    for pe in elements:
        if "textRun" in pe:
            tr = pe["textRun"]
            ts = tr.get("textStyle", {})
            out.append({"t": tr.get("content", "").replace("\u000b", " "),
                        "b": bool(ts.get("bold")), "i": bool(ts.get("italic")),
                        "u": (ts.get("link") or {}).get("url"),
                        "sup": ts.get("baselineOffset") == "SUPERSCRIPT"})
        elif "footnoteReference" in pe:
            out.append({"fn": str(pe["footnoteReference"]["footnoteNumber"])})
        elif "inlineObjectElement" in pe:
            out.append({"img": pe["inlineObjectElement"]["inlineObjectId"]})
    return out


def _md(runs: list, links: bool = True) -> str:
    """Runs as markdown: neighbours that look alike merged (a bold phrase in
    two runs is one **…**), spaces kept outside the marks, a footnote as its
    [^nN], a stray typed superscript digit dropped (the footnote is the mark)."""
    merged: list = []
    for r in runs:
        if "t" in r and merged and "t" in merged[-1] and all(
                merged[-1][k] == r[k] for k in ("b", "i", "u", "sup")):
            merged[-1] = {**merged[-1], "t": merged[-1]["t"] + r["t"]}
        else:
            merged.append(dict(r))
    out = []
    for r in merged:
        if "fn" in r:
            out.append(f"[^n{r['fn']}]")
            continue
        if "img" in r:
            continue
        t = r["t"]
        core = t.strip()
        if not core or (r["sup"] and core.isdigit()):
            out.append(t if not core else "")
            continue
        lead, trail = t[:len(t) - len(t.lstrip())], t[len(t.rstrip()):]
        if r["u"] and links:
            core = f"[{core}]({r['u']})"
        if r["b"] and r["i"]:
            core = f"***{core}***"
        elif r["b"]:
            core = f"**{core}**"
        elif r["i"]:
            core = f"*{core}*"
        out.append(lead + core + trail)
    return re.sub(r"\s*\n\s*", " ", "".join(out)).strip()


def _plain(runs: list) -> str:
    return re.sub(r"\s+", " ", "".join(r.get("t", "") for r in runs)).strip()


def _subhead(runs: list) -> bool:
    """A short paragraph all in bold, with no footnote: a subheading."""
    words = [r for r in runs if r.get("t", "").strip()]
    return bool(words) and all(r["b"] for r in words) and not any("fn" in r for r in runs) \
        and len(_plain(runs)) < 90


def _cell_paras(cell: dict) -> list:
    return [_runs(x["paragraph"].get("elements", []))
            for x in cell.get("content", []) if "paragraph" in x]


def to_text(doc: dict) -> tuple[str, dict]:
    """(the composer's markdown, {picture id: {"uri", "alt"}}). Pictures are
    written as `gdoc:<id>` until fetch_pictures() names their files."""
    body = doc.get("body", {}).get("content", [])
    lists = doc.get("lists", {})
    inline = doc.get("inlineObjects", {})
    lines: list = []
    pics: dict = {}
    order: list = []                          # footnote numbers, first reference first

    def block(s: str) -> None:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(s)

    styles = [el.get("paragraph", {}).get("paragraphStyle", {}).get("namedStyleType")
              for el in body]
    if "TITLE" not in styles and doc.get("title"):
        block("# " + doc["title"].strip())
    in_list = False
    for el in body:
        if "table" in el:
            in_list = False
            t = el["table"]
            rows = [[_cell_paras(c) for c in row.get("tableCells", [])]
                    for row in t.get("tableRows", [])]
            for row in rows:
                for cell in row:
                    for runs in cell:
                        order += [r["fn"] for r in runs if "fn" in r]
            if len(rows) == 1 and len(rows[0]) == 1:
                paras = [p for p in rows[0][0] if _plain(p)]
                title = _plain(paras[0]) if paras and _subhead(paras[0]) else ""
                block(f"> [!NOTE] {title}".rstrip())
                for k, p in enumerate(paras[1:] if title else paras):
                    if k:
                        lines.append(">")
                    lines.append("> " + _md(p))
            else:
                cells = [[" ".join(_md(p) for p in cell if _plain(p)).replace("|", "\\|")
                          for cell in row] for row in rows]
                # The header row's look is the table's (bold on the topic colour).
                cells[0] = [re.sub(r"^\*\*(.+)\*\*$", r"\1", c) for c in cells[0]]
                block("| " + " | ".join(cells[0]) + " |")
                lines.append("|" + "---|" * len(cells[0]))
                lines += ["| " + " | ".join(r) + " |" for r in cells[1:]]
            continue
        if "paragraph" not in el:
            continue
        p = el["paragraph"]
        style = p.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT")
        runs = _runs(p.get("elements", []))
        order += [r["fn"] for r in runs if "fn" in r]
        for r in runs:
            if "img" in r:
                eo = inline.get(r["img"], {}).get("inlineObjectProperties", {}) \
                    .get("embeddedObject", {})
                pics[r["img"]] = {"uri": eo.get("imageProperties", {}).get("contentUri"),
                                  "alt": (eo.get("description") or eo.get("title") or "").strip()}
                block(f"![{pics[r['img']]['alt']}](gdoc:{r['img']})")
                in_list = False
        text, plain = _md(runs), _plain(runs)
        if not plain or not text:             # empty, or only a stray typed digit
            in_list = False
            continue
        if style == "TITLE":
            block("# " + plain)
        elif style == "SUBTITLE":
            block("Subtitle: " + plain)
        elif style.startswith("HEADING_"):
            block(("## " if style == "HEADING_1" else "### ") + plain)
        elif p.get("bullet"):
            b = p["bullet"]
            lvl = (lists.get(b.get("listId"), {}).get("listProperties", {})
                   .get("nestingLevels") or [{}])[min(b.get("nestingLevel", 0), 8)] \
                if lists.get(b.get("listId")) else {}
            item = ("1. " if lvl.get("glyphType") in _ORDERED else "- ") + text
            lines.append(item) if in_list else block(item)
            in_list = True
            continue
        elif _subhead(runs):
            block("### " + plain)
        else:
            block(text)
        in_list = False

    # The footnotes, as the composer's source lines: the footnote's words,
    # its last link the source's link (the engine prints it after the words).
    notes = doc.get("footnotes", {})
    by_number: dict = {}
    for fid, fn in notes.items():
        runs = [r for x in fn.get("content", []) if "paragraph" in x
                for r in _runs(x["paragraph"].get("elements", []))]
        by_number.setdefault(_fn_number(body, fid), runs)
    for n in dict.fromkeys(order):
        runs = by_number.get(n)
        if runs is None:
            continue
        urls = [r["u"] for r in runs if r.get("u")]
        words = _md(runs, links=False)
        # A footnote that ENDS with its link gives the source that link; one
        # in the middle stays in the words (it would print twice otherwise).
        if urls and words.rstrip(" .,;:").endswith(urls[-1]):
            head = words.rstrip(" .,;:")[:-len(urls[-1])].rstrip(" .,;:")
            words = f"{head} — {urls[-1]}" if head else urls[-1]
        block(f"[^n{n}]: {words}")
    return "\n".join(lines) + "\n", pics


def _fn_number(body: list, fid: str) -> str:
    """The number the body's reference gives footnote `fid`."""
    def walk(content):
        for el in content:
            if "paragraph" in el:
                for pe in el["paragraph"].get("elements", []):
                    ref = pe.get("footnoteReference")
                    if ref and ref.get("footnoteId") == fid:
                        return str(ref["footnoteNumber"])
            elif "table" in el:
                for row in el["table"].get("tableRows", []):
                    for c in row.get("tableCells", []):
                        got = walk(c.get("content", []))
                        if got:
                            return got
        return None
    return walk(body) or fid


def fetch_pictures(pics: dict, opener=urllib.request.urlopen) -> dict:
    """{picture id: (file name, bytes)}, named figure-1.png… in the order
    they appear. A Doc's picture address is short-lived (the Docs API's
    contentUri) and needs no credentials; one that no longer answers is
    left out, so the composer places its frame and says it is missing."""
    out = {}
    for k, (pid, p) in enumerate(pics.items(), 1):
        if not p.get("uri"):
            continue
        try:
            with opener(urllib.request.Request(p["uri"], headers={"User-Agent": "docsync"}),
                        timeout=60) as r:
                data = r.read()
                ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip()
        except (urllib.error.URLError, OSError):
            continue
        out[pid] = (f"figure-{k}.{_EXT.get(ctype, 'png')}", data)
    return out


def with_files(text: str, pics: dict, files: dict) -> str:
    """The markdown with each picture's `gdoc:<id>` swapped for its file name
    (figure-K.png for one that could not be fetched)."""
    for k, pid in enumerate(pics, 1):
        name = files[pid][0] if pid in files else f"figure-{k}.png"
        text = text.replace(f"(gdoc:{pid})", f"({name})")
    return text
