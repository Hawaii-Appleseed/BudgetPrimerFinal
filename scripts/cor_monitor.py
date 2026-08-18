#!/usr/bin/env python3
"""Watch the Council on Revenues page for new forecasts.

The COR publishes General Fund and Total Personal Income forecasts to
https://tax.hawaii.gov/useful/a9_1cor/ a handful of times a year (the statutory
reporting dates are June 1, September 10, January 10, and March 15 for the
general fund; August 5 and November 5 for total personal income). This script
runs weekly from CI, notices when something new appears, archives it, and leaves
behind a "change packet" describing what moved.

It does the deterministic half of the job only: fetch, diff, download, extract.
Interpreting the numbers is left to the caller — see .github/workflows/cor-monitor.yml.

Exit codes are meaningful, because the workflow branches on them:
    0  ran fine, nothing new
    10 ran fine, something changed (a packet was written)
    1  something went wrong
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

COR_URL = "https://tax.hawaii.gov/useful/a9_1cor/"
USER_AGENT = "BudgetPrimer-COR-Monitor/1.0 (+https://github.com/Hawaii-Appleseed/BudgetPrimerFinal)"

REPO_ROOT = Path(__file__).resolve().parent.parent
COR_DIR = REPO_ROOT / "data" / "raw" / "cor"
INDEX_PATH = COR_DIR / "cor_index.json"
HEARTBEAT_PATH = COR_DIR / "last_checked.json"
PDF_DIR = COR_DIR / "pdfs"
TEXT_DIR = COR_DIR / "extracted"
PACKET_DIR = COR_DIR / "changes"

# Only these get downloaded and text-extracted. Attachments and slide decks are
# indexed so we can see them appear, but they're supporting material — pulling
# every one of them would bloat the repo without telling us much.
SUBSTANTIVE = {"gf_forecast", "tpi_forecast"}

MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"],
        start=1,
    )
}
DATE_RE = re.compile(
    r"\b(" + "|".join(MONTHS) + r")\.?\s+(\d{1,2}),?\s+(\d{4})\b", re.I
)
# Filenames encode the meeting too: 2026gf05-21_attach_1.pdf, 2026tpi07_30_min.pdf
URL_DATE_RE = re.compile(r"(20\d{2})(?:gf|tpi)(\d{2})[-_](\d{2})", re.I)


# ---------------------------------------------------------------- fetch/parse

def fetch(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def parse_date(text: str, url: str = "") -> str | None:
    """Return an ISO date from a link title, falling back to the filename."""
    m = DATE_RE.search(text)
    if m:
        month, day, year = MONTHS[m.group(1).lower()], int(m.group(2)), int(m.group(3))
        try:
            return dt.date(year, month, day).isoformat()
        except ValueError:
            return None
    m = URL_DATE_RE.search(url)
    if m:
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    return None


def classify(title: str, url: str) -> str:
    t = title.lower()
    if "general fund forecast" in t:
        return "gf_forecast"
    if "personal income" in t:
        return "tpi_forecast"
    if "minutes" in t:
        return "minutes"
    if "attachment" in t:
        return "attachment"
    if "presentation" in t:
        return "presentation"
    if "youtube.com" in url or "video" in t:
        return "video"
    return "other"


def parse_page(raw: bytes) -> dict:
    """Pull the document index, the next-meeting notice, and the page date."""
    page = raw.decode("utf-8", errors="replace")
    body = re.sub(r"(?is)<(script|style).*?</\1>", "", page)

    # Everything we care about sits between the <h1> and the "About the Council"
    # boilerplate. Narrowing here keeps site-chrome links out of the index.
    start = re.search(r"(?is)<h1[^>]*>\s*Council on Revenues", body)
    end = re.search(r"(?is)About the Council on Revenues", body)
    section = body[start.start() if start else 0 : end.start() if end else len(body)]

    documents: dict[str, dict] = {}
    inherited: str | None = None
    for m in re.finditer(r"(?is)<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", section):
        url, title = html.unescape(m.group(1)).strip(), _text(m.group(2))
        if not title or url.startswith("#"):
            continue
        if "a9_1corarchive" in url:  # the archive index, not a document
            continue
        doc_type = classify(title, url)
        date = parse_date(title, url)
        # Attachments and decks are listed under the forecast they belong to and
        # often carry no date of their own — inherit from the entry above them.
        if date:
            inherited = date
        else:
            date = inherited
        documents[url] = {
            "title": title,
            "doc_type": doc_type,
            "meeting_date": date,
        }

    updated = re.search(r"(?is)Page Last Updated:\s*([A-Za-z]+\s+\d{1,2},\s*\d{4})", body)

    # The upcoming-meeting notice lives in its own heading block above the year
    # sections; it's the earliest signal that a new forecast is coming.
    notice = None
    nm = re.search(r"(?is)Council on Revenues Meeting(.*?)(<h2|$)", section)
    if nm:
        link = re.search(r"(?is)<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", nm.group(1))
        if link:
            n_url, n_title = html.unescape(link.group(1)).strip(), _text(link.group(2))
            notice = {"title": n_title, "url": n_url, "date": parse_date(n_title, n_url)}

    return {
        "documents": documents,
        "page_last_updated": _text(updated.group(1)) if updated else None,
        "next_meeting": notice,
    }


# ----------------------------------------------------------------- diffing

def diff(prev: dict, cur: dict) -> dict:
    old_docs = prev.get("documents", {})
    new_docs = cur["documents"]

    added = [dict(meta, url=u) for u, meta in new_docs.items() if u not in old_docs]
    removed = [dict(meta, url=u) for u, meta in old_docs.items() if u not in new_docs]
    # A retitle is not cosmetic here: "minutes – Draft" becoming "minutes" is the
    # signal that the record of a meeting has been finalized.
    retitled = [
        {"url": u, "from": old_docs[u].get("title"), "to": meta["title"]}
        for u, meta in new_docs.items()
        if u in old_docs and old_docs[u].get("title") != meta["title"]
    ]

    meeting_changed = None
    if prev and prev.get("next_meeting") != cur.get("next_meeting"):
        meeting_changed = {"from": prev.get("next_meeting"), "to": cur.get("next_meeting")}

    return {
        "added": sorted(added, key=lambda d: (d.get("meeting_date") or "", d["title"])),
        "removed": removed,
        "retitled": retitled,
        "next_meeting_changed": meeting_changed,
        "page_last_updated": {
            "from": prev.get("page_last_updated"),
            "to": cur.get("page_last_updated"),
        }
        if prev and prev.get("page_last_updated") != cur.get("page_last_updated")
        else None,
    }


def is_material(changes: dict) -> bool:
    """Did anything happen that's worth writing a packet about?

    A bare "Page Last Updated" bump is excluded — DOTAX touches that for
    unrelated edits, and treating it as news would cry wolf several times a year.
    """
    return bool(
        changes["added"] or changes["removed"] or changes["retitled"]
        or changes["next_meeting_changed"]
    )


# -------------------------------------------------------------- pdf handling

def slug(url: str) -> str:
    name = url.rsplit("/", 1)[-1]
    name = re.sub(r"\.pdf$", "", name, flags=re.I)
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")


def extract_pdf(pdf_path: Path) -> str:
    """Text plus whitespace-joined tables. Tables carry the actual forecast."""
    import pdfplumber

    chunks: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            chunks.append(f"\n===== page {i} =====")
            chunks.append(page.extract_text() or "")
            for j, table in enumerate(page.extract_tables(), start=1):
                chunks.append(f"\n--- page {i} table {j} ---")
                for row in table:
                    cells = ["" if c is None else re.sub(r"\s+", " ", c).strip() for c in row]
                    if any(cells):
                        chunks.append(" | ".join(cells))
    return "\n".join(chunks)


def archive(doc: dict) -> dict | None:
    """Download a forecast and extract its text. Returns a record, or None."""
    url = doc["url"]
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    TEXT_DIR.mkdir(parents=True, exist_ok=True)

    stem = slug(url)
    pdf_path, txt_path = PDF_DIR / f"{stem}.pdf", TEXT_DIR / f"{stem}.txt"
    try:
        # The page URL-encodes some filenames ("GF Rpt2") and not others.
        pdf_path.write_bytes(fetch(urllib.parse.quote(url, safe=":/%?=&")))
        txt_path.write_text(extract_pdf(pdf_path), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — one bad PDF must not kill the run
        print(f"  ! could not archive {url}: {exc}", file=sys.stderr)
        return None

    return {
        **doc,
        "pdf": str(pdf_path.relative_to(REPO_ROOT)),
        "text": str(txt_path.relative_to(REPO_ROOT)),
    }


def prior_forecast(index: dict, doc_type: str, before: str | None) -> tuple[str, Path] | None:
    """The most recent already-archived forecast of the same kind, for comparison."""
    candidates = []
    for url, meta in index.get("documents", {}).items():
        if meta.get("doc_type") != doc_type or not meta.get("text"):
            continue
        date = meta.get("meeting_date")
        if before and date and date >= before:
            continue
        candidates.append((date or "", meta))
    if not candidates:
        return None
    _, best = max(candidates, key=lambda c: c[0])
    path = REPO_ROOT / best["text"]
    return (best.get("title", "prior forecast"), path) if path.exists() else None


# ------------------------------------------------------------------ packet

def write_packet(changes: dict, cur: dict, index: dict, archived: list[dict], today: str) -> Path:
    PACKET_DIR.mkdir(parents=True, exist_ok=True)
    path = PACKET_DIR / f"{today}-cor-change.md"

    L: list[str] = [
        f"# Council on Revenues — change detected {today}",
        "",
        f"Source: <{COR_URL}>  ",
        f"Page last updated: {cur.get('page_last_updated') or 'unknown'}",
        "",
    ]

    if changes["next_meeting_changed"]:
        to = changes["next_meeting_changed"]["to"] or {}
        frm = changes["next_meeting_changed"]["from"] or {}
        L += ["## Next meeting", "",
              f"- was: {frm.get('title') or '(none listed)'}",
              f"- now: {to.get('title') or '(none listed)'}", ""]

    if changes["added"]:
        L += ["## New documents", ""]
        for d in changes["added"]:
            L.append(f"- **{d['title']}** — `{d['doc_type']}`, meeting {d.get('meeting_date') or '?'}  ")
            L.append(f"  <{d['url']}>")
        L.append("")

    if changes["retitled"]:
        L += ["## Retitled (e.g. draft minutes finalized)", ""]
        L += [f"- `{r['from']}` → `{r['to']}`  \n  <{r['url']}>" for r in changes["retitled"]]
        L.append("")

    if changes["removed"]:
        L += ["## Removed from the page", ""]
        L += [f"- {d['title']} — <{d['url']}>" for d in changes["removed"]]
        L.append("")

    # The extracted text is what makes an unattended narrative possible: the new
    # forecast and the one it supersedes, side by side, in the same file.
    for rec in archived:
        L += ["---", "", f"## Extracted: {rec['title']}", "",
              f"Archived to `{rec['pdf']}`.", "", "```text",
              (REPO_ROOT / rec["text"]).read_text(encoding="utf-8")[:20000], "```", ""]
        prior = prior_forecast(index, rec["doc_type"], rec.get("meeting_date"))
        if prior:
            title, ppath = prior
            L += [f"### Prior forecast for comparison: {title}", "", "```text",
                  ppath.read_text(encoding="utf-8")[:20000], "```", ""]
        else:
            L += ["_No earlier forecast of this type is archived yet, so there is "
                  "nothing to diff against on this run._", ""]

    path.write_text("\n".join(L), encoding="utf-8")
    return path


# -------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", action="store_true",
                    help="Record current state as the baseline without reporting it as news.")
    ap.add_argument("--dry-run", action="store_true", help="Write nothing.")
    args = ap.parse_args()

    today = dt.date.today().isoformat()
    COR_DIR.mkdir(parents=True, exist_ok=True)

    try:
        cur = parse_page(fetch(COR_URL))
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"could not reach {COR_URL}: {exc}", file=sys.stderr)
        return 1

    if not cur["documents"]:
        # An empty index means the page moved or its markup changed. Failing here
        # is deliberate: silently writing an empty index would erase the baseline
        # and then report every document as "new" on the following run.
        print("parsed zero documents — the page layout probably changed", file=sys.stderr)
        return 1

    index = json.loads(INDEX_PATH.read_text()) if INDEX_PATH.exists() else {}
    changes = diff(index, cur)
    material = is_material(changes) and not args.seed and bool(index)

    print(f"{len(cur['documents'])} documents on the page; "
          f"{len(changes['added'])} new, {len(changes['retitled'])} retitled, "
          f"{len(changes['removed'])} removed")

    if args.dry_run:
        print(json.dumps(changes, indent=2))
        return 10 if material else 0

    # Archive new forecasts. On the seeding run this pulls the current forecast so
    # the very first real change has something to compare against.
    archived: list[dict] = []
    to_archive = changes["added"] if index else [
        dict(meta, url=u) for u, meta in cur["documents"].items()
        if meta["doc_type"] in SUBSTANTIVE
    ]
    for doc in to_archive:
        if doc["doc_type"] not in SUBSTANTIVE:
            continue
        print(f"  archiving {doc['title']}")
        rec = archive(doc)
        if rec:
            archived.append(rec)

    # Carry forward pdf/text pointers so the index stays the record of what we hold.
    merged = {}
    for url, meta in cur["documents"].items():
        old = index.get("documents", {}).get(url, {})
        merged[url] = {**meta, "first_seen": old.get("first_seen", today)}
        for key in ("pdf", "text"):
            if old.get(key):
                merged[url][key] = old[key]
    for rec in archived:
        merged[rec["url"]].update({"pdf": rec["pdf"], "text": rec["text"]})

    packet = None
    if material:
        packet = write_packet(changes, cur, {"documents": merged}, archived, today)
        print(f"  wrote {packet.relative_to(REPO_ROOT)}")

    INDEX_PATH.write_text(json.dumps({
        "source": COR_URL,
        "generated": today,
        "page_last_updated": cur["page_last_updated"],
        "next_meeting": cur["next_meeting"],
        "documents": merged,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Committed on every run, change or not. Two jobs: it proves the monitor is
    # alive, and the commit keeps the repo active so GitHub doesn't disable the
    # schedule after 60 quiet days.
    HEARTBEAT_PATH.write_text(json.dumps({
        "last_checked": today,
        "documents_tracked": len(merged),
        "page_last_updated": cur["page_last_updated"],
        "next_meeting": cur["next_meeting"],
        "change_detected": bool(material),
        "packet": str(packet.relative_to(REPO_ROOT)) if packet else None,
    }, indent=2) + "\n", encoding="utf-8")

    return 10 if material else 0


if __name__ == "__main__":
    sys.exit(main())
