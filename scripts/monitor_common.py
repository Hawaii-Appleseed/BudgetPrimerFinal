"""Shared pieces for the DOTAX page monitors.

Extracted while adding the Tax Review Commission watcher, which needs the same
fetch / date-parse / diff / PDF-extract machinery the Council on Revenues
monitor already had. cor_monitor.py deliberately still carries its own copies:
it is load-bearing, runs weekly, and rewiring it was not worth the regression
risk for no behaviour change. It can adopt this module whenever it is next
touched for another reason.

Monitor convention, shared by both:
    exit 0   checked, nothing material changed
    exit 10  material change — the workflow writes a packet and opens an issue
    exit 1   the fetch failed or the page parsed to nothing. This is a real
             failure: a monitor that silently stops noticing is the exact
             thing these exist to prevent.
"""

from __future__ import annotations

import datetime as dt
import html
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
DATE_RE = re.compile(
    r"(january|february|march|april|may|june|july|august|september|october"
    r"|november|december)\s+(\d{1,2}),?\s+(20\d{2})",
    re.I,
)


def fetch(url: str, user_agent: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def strip_tags(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def parse_date(text: str, url: str = "", url_re: re.Pattern | None = None) -> str | None:
    """ISO date from a link title, falling back to a date encoded in the URL."""
    m = DATE_RE.search(text)
    if m:
        try:
            return dt.date(int(m.group(3)), MONTHS[m.group(1).lower()],
                           int(m.group(2))).isoformat()
        except ValueError:
            return None
    if url_re:
        m = url_re.search(url)
        if m:
            # A pattern may offer several alternative shapes; take the first
            # group triple that actually matched.
            g = [x for x in m.groups() if x is not None]
            if len(g) >= 3:
                try:
                    return dt.date(int(g[0]), int(g[1]), int(g[2])).isoformat()
                except ValueError:
                    return None
    return None


def links(section: str) -> list[tuple[str, str]]:
    """(href, visible text) for every anchor in a chunk of HTML."""
    out = []
    for href, label in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                                  section, re.S | re.I):
        text = strip_tags(label)
        if text:
            out.append((html.unescape(href.strip()), text))
    return out


def diff_documents(prev: dict, cur: dict) -> dict:
    """Compare two document indexes keyed by URL."""
    old_docs, new_docs = prev.get("documents", {}), cur["documents"]
    added = [dict(meta, url=u) for u, meta in new_docs.items() if u not in old_docs]
    removed = [dict(meta, url=u) for u, meta in old_docs.items() if u not in new_docs]
    # A retitle is not cosmetic: "Minutes – Draft" becoming "Minutes" is the
    # signal that a meeting's record has been finalized.
    retitled = [
        {"url": u, "from": old_docs[u].get("title"), "to": meta["title"]}
        for u, meta in new_docs.items()
        if u in old_docs and old_docs[u].get("title") != meta["title"]
    ]
    return {
        "added": sorted(added, key=lambda d: (d.get("meeting_date") or "", d["title"])),
        "removed": removed,
        "retitled": retitled,
    }


def slug(url: str) -> str:
    name = url.rsplit("/", 1)[-1]
    name = re.sub(r"\.pdf$", "", name, flags=re.I)
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")


def extract_pdf(pdf_path: Path) -> str:
    """Text plus whitespace-joined tables — tables carry most of the numbers."""
    import pdfplumber

    chunks: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            chunks.append(f"\n===== page {i} =====")
            chunks.append(page.extract_text() or "")
            for j, table in enumerate(page.extract_tables(), start=1):
                chunks.append(f"\n--- page {i} table {j} ---")
                for row in table:
                    cells = ["" if c is None else re.sub(r"\s+", " ", c).strip()
                             for c in row]
                    if any(cells):
                        chunks.append(" | ".join(cells))
    return "\n".join(chunks)


def archive_pdf(doc: dict, pdf_dir: Path, text_dir: Path, repo_root: Path,
                user_agent: str) -> dict | None:
    """Download one PDF and extract its text. None if either step fails."""
    url = doc["url"]
    pdf_dir.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)
    stem = slug(url)
    pdf_path, txt_path = pdf_dir / f"{stem}.pdf", text_dir / f"{stem}.txt"
    try:
        # The page URL-encodes some filenames and not others.
        pdf_path.write_bytes(fetch(urllib.parse.quote(url, safe=":/%?=&"), user_agent))
        txt_path.write_text(extract_pdf(pdf_path), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — one bad PDF must not kill the run
        print(f"  ! could not archive {url}: {exc}", file=sys.stderr)
        return None
    return {**doc, "pdf": str(pdf_path.relative_to(repo_root)),
            "text": str(txt_path.relative_to(repo_root))}
