"""The fact ledger: the figures Hawaiʻi Appleseed stands behind, one place.

A report says a figure once, in the ledger, and points at it everywhere else:

    Over {fact:capgains-share-over-400k|percent} of capital gains flow to …

The engine writes the number in at build time, so the published page, the PDF
and the Squarespace block carry plain words, and every report that points at a
fact says the same number. When the ledger changes, every draft that points at
it changes with it; nothing already published moves until it is published again.

Token grammar (content.py's VAR_RE family, so the editor shows it as a chip):

    {fact:<id>}                  the fact as the ledger shows it (`shown`)
    {fact:<id>|<format>}         written another way: pct, percent, n, $M, $M1,
                                 $million, $billion (FORMATS below)
    {fact:<id>@<date>}           PINNED: the value the ledger stood behind on that
                                 date (YYYY, YYYY-MM or YYYY-MM-DD), for words
                                 that must not change once said, like testimony
    {fact:<id>@<date>|<format>}  both

A citation of the fact's own source is `[^fact.<id>]`; it needs no line under
[[sources]], because the ledger carries the source.

The ledger is data in this package, one JSON file per issue area, named by the
staff hub's issue keys (ledger/tax-and-budget.json …). Each file:

    {"area": "tax-and-budget", "name": "Taxes & Budget", "facts": [ … ]}

and each fact: id, says, value, unit (pct | usd | count), shown, as_of, source,
url, status (proposed | confirmed), checked, owner, since, note, match/unless
(the regexes docsync.facts uses to find sentences that state it), and history:
earlier values, each {value, shown, as_of, since, until, note}.

Pure stdlib (json, re): it runs in Pyodide inside the editor as well as here.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

# The staff hub's issue areas (its issue_brief keys), in the hub's order.
AREAS = {
    "tax-and-budget": "Taxes & Budget",
    "housing": "Affordable Housing",
    "food-equity": "Food Equity",
    "labor": "Wages & Labor",
    "transportation": "Transportation Equity",
    "public-health": "Public Health",
    "education": "Education",
    "social-justice": "Social Justice",
    "criminal-justice": "Criminal Justice",
    "native-hawaiian": "Native Hawaiian Equity",
}
UNITS = ("pct", "usd", "count")
STATUSES = ("proposed", "confirmed")
DIR = Path(__file__).resolve().parent / "ledger"

ID = r"[a-z0-9][a-z0-9-]{0,63}"
DATE = r"\d{4}(?:-\d{2}(?:-\d{2})?)?"
FMT = r"[A-Za-z$%0-9]{1,12}"
FACT_RE = re.compile(r"\{fact:(" + ID + r")(?:@(" + DATE + r"))?(?:\|(" + FMT + r"))?\}")
CITE_PREFIX = "fact."


class LedgerError(ValueError):
    pass


def _g(v: float) -> str:
    """77.0 -> '77', 41.7 -> '41.7'."""
    return f"{v:,.10g}" if abs(v) >= 1000 else f"{v:.10g}"


def _money(v: float, div: float, digits: int, word: str) -> str:
    s = f"{v / div:,.{digits}f}"
    if digits and s.endswith("." + "0" * digits):
        s = s[: -(digits + 1)]
    return f"${s}{word}"


FORMATS = {
    "pct": lambda v: f"{_g(v)}%",
    "percent": lambda v: f"{_g(v)} percent",
    "n": lambda v: f"{v:,.0f}",
    "$M": lambda v: _money(v, 1e6, 0, "M"),
    "$M1": lambda v: _money(v, 1e6, 1, "M"),
    "$million": lambda v: _money(v, 1e6, 1, " million"),
    "$billion": lambda v: _money(v, 1e9, 1, " billion"),
}
# Which formats make sense for which unit; a mismatch is refused, not guessed.
FORMAT_UNITS = {"pct": "pct", "percent": "pct", "n": "count",
                "$M": "usd", "$M1": "usd", "$million": "usd", "$billion": "usd"}


def load(directory: Path | None = None) -> dict:
    """{id: fact dict} across every area file, each fact carrying `area` and
    `area_name`. Refuses a malformed ledger loudly: a wrong number here would
    reach every report at once."""
    d = Path(directory) if directory else DIR
    out: dict = {}
    if not d.is_dir():
        return out
    for path in sorted(d.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise LedgerError(f"{path.name}: not valid JSON ({e})") from None
        area = data.get("area")
        if area not in AREAS:
            raise LedgerError(f"{path.name}: area {area!r} is not one of the hub's issue areas "
                              f"({', '.join(AREAS)})")
        if path.stem != area:
            raise LedgerError(f"{path.name}: the file for area {area!r} must be named {area}.json")
        for f in data.get("facts") or []:
            fid = f.get("id", "")
            if not re.fullmatch(ID, fid):
                raise LedgerError(f"{path.name}: fact id {fid!r} must be lowercase words joined by hyphens")
            if fid in out:
                raise LedgerError(f"{path.name}: fact {fid!r} is also in {out[fid]['area']}.json")
            if f.get("unit") not in UNITS:
                raise LedgerError(f"{fid}: unit must be one of {', '.join(UNITS)}")
            if f.get("status", "proposed") not in STATUSES:
                raise LedgerError(f"{fid}: status must be proposed or confirmed")
            if not isinstance(f.get("value"), (int, float)):
                raise LedgerError(f"{fid}: value must be a number")
            for k in ("match", "unless"):
                if f.get(k):
                    re.compile(f[k])
            for h in f.get("history") or []:
                if not isinstance(h.get("value"), (int, float)):
                    raise LedgerError(f"{fid}: every history entry needs a numeric value")
            out[fid] = {**f, "area": area, "area_name": AREAS[area]}
    return out


_CACHE: dict = {}


def facts() -> dict:
    """The packaged ledger, read once per process."""
    if "all" not in _CACHE:
        _CACHE["all"] = load()
    return _CACHE["all"]


def reset() -> None:
    _CACHE.clear()


def value_on(f: dict, date: str | None) -> dict:
    """The entry (the fact itself or one of its history entries) the ledger
    stood behind on `date`. No date: the current one. A date before every
    entry: the earliest known."""
    if not date:
        return f
    if not f.get("since") or date >= f["since"]:
        return f
    hist = sorted(f.get("history") or [], key=lambda h: h.get("since") or "")
    for h in reversed(hist):
        if (h.get("since") or "") <= date:
            return h
    return hist[0] if hist else f


def show(f: dict, entry: dict, fmt: str | None) -> str:
    if not fmt:
        if entry.get("shown"):
            return entry["shown"]
        fmt = {"pct": "percent", "usd": "$million", "count": "n"}[f["unit"]]
    if fmt not in FORMATS:
        raise LedgerError(f"{{fact:{f['id']}|{fmt}}}: no format {fmt!r} "
                          f"(one of {', '.join(FORMATS)})")
    if FORMAT_UNITS[fmt] != f["unit"]:
        raise LedgerError(f"{{fact:{f['id']}|{fmt}}}: {fmt} is for {FORMAT_UNITS[fmt]} "
                          f"figures and this fact is {f['unit']}")
    return FORMATS[fmt](float(entry["value"]))


def render(m: re.Match, ledger: dict | None = None) -> str:
    """One token -> its words. An unknown fact or a bad format refuses the
    build when publishing; in the editor it shows, marked, so it can be fixed
    (the same bargain as a citation that names no source)."""
    led = facts() if ledger is None else ledger
    fid, date, fmt = m.group(1), m.group(2), m.group(3)
    try:
        f = led.get(fid)
        if f is None:
            raise LedgerError(f"{{fact:{fid}}}: no fact with that id in the ledger")
        return show(f, value_on(f, date), fmt)
    except LedgerError:
        if os.environ.get("DOCSYNC_EDIT"):
            return f"⚠ {m.group(0)}"
        raise


def resolve(text: str, ledger: dict | None = None) -> str:
    if "{fact:" not in text:
        return text
    return FACT_RE.sub(lambda m: render(m, ledger), text)


def uses(text: str) -> list[tuple[str, str | None, str | None]]:
    """(id, pin date, format) for every token in a text, in order."""
    return [(m.group(1), m.group(2), m.group(3)) for m in FACT_RE.finditer(text or "")]


def source_line(f: dict) -> tuple[str, str]:
    """A fact's source as a [[sources]] entry: (text, url)."""
    text = f.get("source") or f.get("says") or f["id"]
    if f.get("as_of") and f["as_of"] not in text:
        text = f"{text} ({f['as_of']})"
    return text, f.get("url") or ""


def editor_json(ledger: dict | None = None) -> str:
    """The ledger as the editor's menus and checks read it."""
    led = facts() if ledger is None else ledger
    return json.dumps({"areas": AREAS, "formats": list(FORMATS),
                       "format_units": FORMAT_UNITS, "facts": led}, ensure_ascii=False)
