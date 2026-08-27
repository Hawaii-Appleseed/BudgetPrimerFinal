#!/usr/bin/env python3
"""Watch the Hawaii Tax Review Commission page for new material.

The TRC meets roughly monthly through 2025-2027 and posts, per meeting: a
notice, a video, draft then final minutes, and any presentations given. Its
recommendations feed directly into the tax debate Appleseed works in, so a new
presentation or set of minutes is worth knowing about the week it lands rather
than the next time someone thinks to check the page.

Same shape and exit codes as the Council on Revenues monitor:
    0   checked, nothing material
    10  material change — write a packet, open an issue
    1   fetch failed or the page parsed to nothing (a real failure: a monitor
        that silently stops noticing is the thing this exists to prevent)
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from monitor_common import (  # noqa: E402
    archive_pdf, diff_documents, fetch, links, parse_date, strip_tags,
)

TRC_URL = "https://tax.hawaii.gov/stats/tax-review-commission/"
USER_AGENT = ("BudgetPrimer-TRC-Monitor/1.0 "
              "(+https://github.com/Hawaii-Appleseed/BudgetPrimerFinal)")

REPO_ROOT = Path(__file__).resolve().parent.parent
TRC_DIR = REPO_ROOT / "data" / "raw" / "trc"
INDEX_PATH = TRC_DIR / "trc_index.json"
HEARTBEAT_PATH = TRC_DIR / "last_checked.json"
PDF_DIR = TRC_DIR / "pdfs"
TEXT_DIR = TRC_DIR / "extracted"
PACKET_DIR = TRC_DIR / "changes"

# Filenames encode the meeting date, in two shapes: minutes and notices under
# mins2025/ use 2026trc09-01.pdf, while presentations under docs2025/ use a
# plain 2026-08-11_Title.pdf prefix.
URL_DATE_RE = re.compile(
    r"(20\d{2})trc(\d{2})[-_](\d{2})|(?:^|/)(20\d{2})-(\d{2})-(\d{2})", re.I)

# Only these get downloaded and text-extracted. A notice says a meeting will
# happen; minutes and presentations say what was actually argued.
SUBSTANTIVE = {"minutes_final", "minutes_draft", "presentation", "resolution"}


def classify(title: str, url: str) -> str:
    t, u = title.lower(), url.lower()
    if "youtube.com" in u or "youtu.be" in u or "video" in t:
        return "video"
    if "_min-draft" in u or "draft" in t:
        return "minutes_draft"
    if "_min" in u or "minutes" in t:
        return "minutes_final"
    if "presentation" in t or "proposal" in t or "/docs" in u:
        return "presentation"
    if re.search(r"\b(sr|hcr|hb|sb)\s?\d", t) or "resolution" in t:
        return "resolution"
    if re.search(r"at \d{1,2}:\d{2}", t):
        return "notice"
    return "other"


def parse_page(raw: bytes) -> dict:
    page = raw.decode("utf-8", errors="replace")
    body = re.sub(r"(?is)<(script|style).*?</\1>", "", page)

    docs: dict[str, dict] = {}
    for href, text in links(body):
        # Only this commission's own files, plus its meeting videos. The page
        # also links prior commissions (a9_2trc_2022 and friends) — those are
        # closed archives, not news, and indexing them would make every run
        # look enormous.
        is_file = "files.hawaii.gov/tax/stats/trc/" in href
        is_video = ("youtube.com" in href or "youtu.be" in href)
        if not (is_file or is_video):
            continue
        doc_type = classify(text, href)
        if is_video and doc_type != "video":
            doc_type = "video"
        docs[href] = {
            "title": " ".join(text.split()),
            "doc_type": doc_type,
            "meeting_date": parse_date(text, href, URL_DATE_RE),
        }

    m = re.search(r"(?is)Page Last Updated\s*:?\s*([^<]{4,40})", body)
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "page_last_updated": strip_tags(m.group(1)) if m else None,
        "documents": docs,
    }


def is_material(changes: dict) -> bool:
    """A bare 'Page Last Updated' bump is not news — DOTAX touches it often."""
    return bool(changes["added"] or changes["removed"] or changes["retitled"])


def write_packet(changes: dict, archived: list[dict], today: str) -> Path:
    PACKET_DIR.mkdir(parents=True, exist_ok=True)
    out = [f"# Tax Review Commission — change detected {today}", "",
           f"Source: {TRC_URL}", ""]

    if changes["added"]:
        out += ["## New documents", ""]
        for d in changes["added"]:
            when = f" ({d['meeting_date']})" if d.get("meeting_date") else ""
            out.append(f"- **{d['title']}**{when} — `{d['doc_type']}`")
            out.append(f"  {d['url']}")
        out.append("")
    if changes["retitled"]:
        out += ["## Retitled", "",
                "_A draft becoming final is the usual cause._", ""]
        for d in changes["retitled"]:
            out.append(f"- {d['from']!r} → {d['to']!r}")
            out.append(f"  {d['url']}")
        out.append("")
    if changes["removed"]:
        out += ["## Removed", ""]
        for d in changes["removed"]:
            out.append(f"- {d['title']} — {d['url']}")
        out.append("")

    for rec in archived:
        txt = REPO_ROOT / rec["text"]
        if not txt.is_file():
            continue
        body = txt.read_text(encoding="utf-8", errors="replace")
        # Enough for a reader (or a model) to say what changed without the
        # packet becoming a whole PDF.
        if len(body) > 30_000:
            body = body[:30_000] + "\n\n[…truncated…]"
        out += [f"## Extracted text — {rec['title']}", "",
                f"`{rec['text']}`", "", "```", body, "```", ""]

    path = PACKET_DIR / f"{today}-trc-change.md"
    path.write_text("\n".join(out), encoding="utf-8")
    return path


def main() -> int:
    TRC_DIR.mkdir(parents=True, exist_ok=True)
    try:
        raw = fetch(TRC_URL, USER_AGENT)
    except Exception as exc:  # noqa: BLE001
        print(f"fetch failed: {exc}", file=sys.stderr)
        return 1

    cur = parse_page(raw)
    if not cur["documents"]:
        print("parsed zero documents — page markup probably changed",
              file=sys.stderr)
        return 1
    print(f"indexed {len(cur['documents'])} documents")

    prev = {}
    if INDEX_PATH.is_file():
        try:
            prev = json.loads(INDEX_PATH.read_text())
        except Exception:  # noqa: BLE001 — a corrupt index should not wedge us
            prev = {}

    HEARTBEAT_PATH.write_text(
        json.dumps({"checked_at": cur["checked_at"]}, indent=2) + "\n")

    if not prev:
        # First run: record the baseline without claiming everything is new.
        INDEX_PATH.write_text(json.dumps(cur, indent=2, sort_keys=True) + "\n")
        print("baseline written — no packet on the first run")
        return 0

    changes = diff_documents(prev, cur)
    if not is_material(changes):
        # Carry forward the archive metadata so it is not lost on rewrite.
        for url, meta in cur["documents"].items():
            old = prev.get("documents", {}).get(url, {})
            for k in ("pdf", "text"):
                if k in old:
                    meta[k] = old[k]
        INDEX_PATH.write_text(json.dumps(cur, indent=2, sort_keys=True) + "\n")
        print("no material change")
        return 0

    archived = []
    for doc in changes["added"]:
        if doc["doc_type"] not in SUBSTANTIVE or not doc["url"].lower().endswith(".pdf"):
            continue
        rec = archive_pdf(doc, PDF_DIR, TEXT_DIR, REPO_ROOT, USER_AGENT)
        if rec:
            archived.append(rec)
            cur["documents"][doc["url"]].update(
                {"pdf": rec["pdf"], "text": rec["text"]})

    for url, meta in cur["documents"].items():
        old = prev.get("documents", {}).get(url, {})
        for k in ("pdf", "text"):
            if k in old and k not in meta:
                meta[k] = old[k]

    today = datetime.now(timezone.utc).date().isoformat()
    packet = write_packet(changes, archived, today)
    INDEX_PATH.write_text(json.dumps(cur, indent=2, sort_keys=True) + "\n")

    print(f"material change: +{len(changes['added'])} "
          f"-{len(changes['removed'])} ~{len(changes['retitled'])}")
    print(f"packet: {packet.relative_to(REPO_ROOT)}")
    return 10


if __name__ == "__main__":
    sys.exit(main())
