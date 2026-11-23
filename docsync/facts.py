"""The fact ledger, step one: find every figure a report states, and the places
where two reports state the same fact with different numbers.

    python3 -m docsync.facts scan DIR [DIR …]      # a folder of <id>.md (+ <id>.layout.json)
    python3 -m docsync.facts scan --json …         # the raw claims, for a ledger to seed from

A claim is one number in one sentence: its value, its unit (dollars, percent,
a count), the words around it, and the sources that sentence cites ([^id]).
Two claims from DIFFERENT reports are "the same fact" when the words around
them mostly agree; when their numbers then disagree beyond rounding, that is
drift: a figure fixed in one report and not in another, or two vintages of
one estimate circulating at once. That is the thing a ledger exists to stop,
so this pass is how we learn whether it is worth building.

Text comes from where a report keeps it: the slots of content.md, the placed
boxes of layout.json, and the cells of its tables (a cell's row label and
column head stand in for its sentence). Stdlib only, like the rest of the
engine.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

# --- reading a report ---------------------------------------------------------

_KEY_RE = re.compile(r"^\[\[([^\]\n]+)\]\]\s*$", re.M)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def slots(md: str) -> dict[str, str]:
    md = _COMMENT_RE.sub("", md)
    ms = list(_KEY_RE.finditer(md))
    out = {}
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(md)
        out[m.group(1)] = md[m.end():end].strip()
    return out


def texts(report_md: str, layout: dict | None) -> list[tuple[str, str]]:
    """(where, text) for every run of words a report shows."""
    out = [(f"slot:{k}", v) for k, v in slots(report_md).items() if k != "sources"]
    for b in (layout or {}).get("boxes") or []:
        if isinstance(b, dict) and isinstance(b.get("md"), str):
            out.append((f"box:{b.get('id')}", b["md"]))
    for t in (layout or {}).get("tables") or []:
        rows = t.get("rows") or [] if isinstance(t, dict) else []
        head = rows[0] if rows and t.get("header", True) else []
        for r in rows[1:] if head else rows:
            for j, cell in enumerate(r[1:], 1):
                col = head[j] if j < len(head) else ""
                out.append((f"table:{t.get('id')}", f"{r[0]}, {col}: {cell}".strip(", ")))
    return out


# --- finding numbers ------------------------------------------------------------

_SCALE = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "mil": 1e6,
          "billion": 1e9, "b": 1e9, "bn": 1e9, "trillion": 1e12}

_NUM = r"(?<![\w.#§/-])(\$\s?)?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?"
_TAIL = r"(?:\s?(%|percent\b|per\s?cent\b)|\s?(thousand|million|billion|trillion|bn|mil|[kKmMbB])\b)?"
NUM_RE = re.compile(_NUM + _TAIL)

# What a number next to these is: an identifier, a date, a page — never a fact.
_ID_BEFORE = re.compile(r"(?:\b(?:HB|SB|HR|SR|HCR|SCR|Act|Acts|Section|Sec\.?|Table|Figure|Fig\.?|"
                        r"Chapter|Ch\.?|Part|page|p\.|pp\.|No\.?|Title|Rule|District|Dist\.?|"
                        r"Tier|step|Step|Room|Line|line|Form|Schedule|item|Item|CD\d?|HD\d?|SD\d?|"
                        r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|"
                        r"January|February|March|April|June|July|August|September|October|"
                        r"November|December|FY|TY|tax year|fiscal year)\s*[-–#.]?\s*|§\s*[\d-]*|"
                        r"\[\^?[^\]]*|\d-|[A-Za-z]-)$")

_STOP = set("""a an the of to in on for and or by with from at as is are was were be been being
that this these those it its their there than then which who whom whose what when where how
our we you they he she his her them us i my your not no nor but if so do does did done has
have had will would can could should may might must about over under more less most least
per each every all any some such only also just into out up down off same other another
both either neither one two three four five six seven eight nine ten s t
approximately about roughly nearly almost around estimated estimate estimates up
year years percent million billion thousand trillion dollars dollar""".split())

_WORD = re.compile(r"[A-Za-zʻ’'][A-Za-zʻ’'-]+")


def _stem(w: str) -> str:
    w = w.lower().replace("’", "'").strip("'")
    for suf in ("ies", "es", "s"):
        if w.endswith(suf) and len(w) > len(suf) + 3:
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


def _plain(s: str) -> str:
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", s)              # images
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)           # links keep their words
    s = re.sub(r"\{[a-z:]+[^{}]*\}", " ", s)                 # {page}, {span}, …
    s = re.sub(r"[*_`#>|]+", " ", s)
    return s


_SENT = re.compile(r"(?<=[.!?;])\s+(?=[A-Z\"“(])|\n{2,}|\n(?=\s*[-*•]\s)")
_CITE = re.compile(r"\[\^([^\]\s]+)\]")


@dataclass
class Claim:
    report: str
    where: str
    value: float
    unit: str            # usd | pct | count
    shown: str           # the number as printed
    sentence: str
    cites: list[str]
    words: list[str] = field(default_factory=list)
    precision: float = 1.0   # the rounding step the printed form implies


def _precision(digits: str, frac: str | None, scale: float) -> float:
    if frac:
        return 10 ** -(len(frac) - 1) * scale
    d = digits.replace(",", "")
    z = len(d) - len(d.rstrip("0")) if d != "0" else 0
    return (10 ** z) * scale


def claims(report: str, where: str, text: str) -> list[Claim]:
    out: list[Claim] = []
    for sent in _SENT.split(text):
        cites = _CITE.findall(sent)
        plain = _CITE.sub(" ", _plain(sent))
        if re.search(r"\b(?:Suite|Street|St\.|Ave|Avenue|Honolulu, HI|P\.?O\.? Box|\(\d{3}\)|\d{3}-\d{4})", plain):
            continue                                          # an address or phone line
        nums = list(NUM_RE.finditer(plain))
        for m in nums:
            dollar, digits, frac, pct, scale_w = m.groups()
            before = plain[max(0, m.start() - 24): m.start()]
            if _ID_BEFORE.search(before):
                continue
            after = plain[m.end(): m.end() + 3]
            if re.match(r"\s?[-–/]\s?\d", after) and not dollar:   # 2025-26, 3/4
                continue
            v = float(digits.replace(",", "") + (frac or ""))
            scale = _SCALE.get((scale_w or "").lower(), 1.0)
            if pct:
                unit = "pct"
            elif dollar:
                unit = "usd"
            elif scale_w:
                unit = "usd" if re.search(r"\$|dollar|revenue|cost|budget|fund", plain, re.I) and False else "count"
            else:
                unit = "count"
            if unit == "count" and not scale_w:
                if 1900 <= v <= 2100 and "," not in digits and not frac:
                    continue                                  # a year
                if v < 100:
                    continue                                  # 3 families, step 2: too common to mean anything
            # The words that describe THIS number: the ones nearest it, stopping
            # at the next number on either side, so "44% food, 56% EITC" gives
            # each figure its own label instead of the whole sentence's.
            i = nums.index(m)
            lo = nums[i - 1].end() if i else 0
            hi = nums[i + 1].start() if i + 1 < len(nums) else len(plain)
            left = _WORD.findall(plain[lo:m.start()])[-WINDOW:]
            right = _WORD.findall(plain[m.end():hi])[:WINDOW]
            words = sorted({_stem(w) for w in left + right} - _STOP)
            out.append(Claim(report, where, v * scale, unit, m.group(0).strip(), sent.strip()[:400],
                             cites, words, _precision(digits, frac, scale)))
    return out


WINDOW = 8


def load(dirs: list[Path]) -> list[Claim]:
    out: list[Claim] = []
    for d in dirs:
        for md in sorted(d.glob("*.md")):
            rid = md.stem
            lay = d / f"{rid}.layout.json"
            layout = json.loads(lay.read_text()) if lay.is_file() else None
            for where, t in texts(md.read_text(), layout):
                out += claims(rid, where, t)
    return out


# --- comparing them ------------------------------------------------------------------

def idf(cs: list[Claim]) -> dict[str, float]:
    """How telling a word is: "capital" and "gains" say which fact this is;
    "tax", "household" and "income" are in half the figures we have."""
    df: dict[str, int] = {}
    for c in cs:
        for w in set(c.words):
            df[w] = df.get(w, 0) + 1
    n = len(cs) or 1
    return {w: math.log(n / d) for w, d in df.items()}


def _sim(a: Claim, b: Claim, w: dict[str, float]) -> float:
    A, B = set(a.words), set(b.words)
    if not A or not B:
        return 0.0
    num = sum(w.get(t, 0) ** 2 for t in A & B)
    da = math.sqrt(sum(w.get(t, 0) ** 2 for t in A))
    db = math.sqrt(sum(w.get(t, 0) ** 2 for t in B))
    return num / (da * db) if da and db else 0.0


def near(a: Claim, b: Claim) -> bool:
    """Close enough to be two versions of one number. Drift is 70% against
    77%, or $122M against $130M; $3,000 against $315 million is two facts."""
    lo, hi = sorted((abs(a.value), abs(b.value)))
    if lo == 0:
        return False
    if a.unit == "pct" and hi - lo > 15:
        return False
    return hi / lo <= 2.0


def agree(a: Claim, b: Claim) -> bool:
    """Same number, allowing for one of them being the other rounded."""
    tol = max(a.precision, b.precision) / 2 + 1e-9
    return abs(a.value - b.value) <= tol


def pairs(cs: list[Claim], min_sim: float = 0.45):
    """(sim, a, b) for claims in different reports that read as the same fact."""
    out = []
    w = idf(cs)
    by_unit: dict[str, list[Claim]] = {}
    for c in cs:
        by_unit.setdefault(c.unit, []).append(c)
    for group in by_unit.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a.report == b.report:
                    continue
                if not (agree(a, b) or near(a, b)):
                    continue
                s = _sim(a, b, w)
                if s >= min_sim:
                    out.append((s, a, b))
    out.sort(key=lambda t: -t[0])
    # One line per pair of places, its best match; and a figure the other
    # place states too is not a conflict, whatever else sits beside it.
    by_place: dict = {}
    for c in cs:
        by_place.setdefault((c.report, c.where, c.sentence), []).append(c)
    seen, kept = set(), []
    for s, a, b in out:
        k = (a.report, a.where, b.report, b.where)
        if k in seen:
            continue
        if not agree(a, b) and (any(agree(a, o) for o in by_place[(b.report, b.where, b.sentence)])
                                or any(agree(b, o) for o in by_place[(a.report, a.where, a.sentence)])):
            continue
        seen.add(k)
        kept.append((s, a, b))
    return kept


# --- the ledger -------------------------------------------------------------------
# facts.yml: one entry per fact the organisation states, with the number it
# stands behind, where that number comes from and as of when. `match` says
# which sentences are about it (a regex, any case); `unless` drops sentences
# that match but are about something else.

@dataclass
class Fact:
    id: str
    says: str
    value: float
    unit: str
    shown: str = ""
    as_of: str = ""
    source: str = ""
    url: str = ""
    match: str = ""
    unless: str = ""
    status: str = "proposed"
    note: str = ""


def read_ledger(path: Path) -> list[Fact]:
    import yaml                                         # noqa: PLC0415
    raw = yaml.safe_load(path.read_text()) or []
    out = []
    for r in raw:
        f = Fact(**{k: v for k, v in r.items() if k in Fact.__dataclass_fields__})
        f.value = float(f.value)
        re.compile(f.match)
        out.append(f)
    return out


@dataclass
class Use:
    fact: str
    report: str
    where: str
    shown: str
    sentence: str
    ok: bool


def check(ledger: list[Fact], cs: list[Claim]) -> list[Use]:
    """Every place a report states a ledgered fact, and whether its number is
    the ledger's. A sentence about the fact with no number near the ledger's
    is left alone: it is saying something else about the same subject."""
    by_sent: dict = {}
    for c in cs:
        by_sent.setdefault((c.report, c.where, c.sentence), []).append(c)
    out = []
    for f in ledger:
        rx = re.compile(f.match, re.I)
        nx = re.compile(f.unless, re.I) if f.unless else None
        probe = Claim("", "", f.value, f.unit, f.shown, "", [], [], 0.0)
        for (rep, where, sent), group in by_sent.items():
            if not rx.search(sent) or (nx and nx.search(sent)):
                continue
            mine = [c for c in group if c.unit == f.unit]
            if any(agree(c, probe) or agree(probe, c) for c in mine):
                hit = next(c for c in mine if agree(c, probe) or agree(probe, c))
                out.append(Use(f.id, rep, where, hit.shown, sent, True))
                continue
            close = [c for c in mine if near(c, probe)]
            if close:
                best = min(close, key=lambda c: abs(math.log(abs(c.value) / abs(f.value))))
                out.append(Use(f.id, rep, where, best.shown, sent, False))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="docsync.facts")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ck = sub.add_parser("check", help="where each ledgered fact is stated, and where a report says otherwise")
    ck.add_argument("dirs", nargs="+", type=Path)
    ck.add_argument("--ledger", type=Path, default=Path(__file__).resolve().parents[1] / "facts.yml")
    ck.add_argument("--json", action="store_true")
    sc = sub.add_parser("scan")
    sc.add_argument("dirs", nargs="+", type=Path)
    sc.add_argument("--json", action="store_true")
    sc.add_argument("--min-sim", type=float, default=0.45)
    sc.add_argument("--agree", action="store_true", help="also list figures two reports state alike")
    a = ap.parse_args(argv)
    cs = load(a.dirs)
    if a.cmd == "check":
        ledger = read_ledger(a.ledger)
        uses = check(ledger, cs)
        if a.json:
            json.dump([asdict(u) for u in uses], sys.stdout, ensure_ascii=False, indent=1)
            return 0
        bad = 0
        for f in ledger:
            us = [u for u in uses if u.fact == f.id]
            wrong = [u for u in us if not u.ok]
            bad += len(wrong)
            print(f"{f.id}: {f.shown or f.value} ({f.as_of}) [{f.status}]")
            print(f"   stated alike in {len({u.report for u in us if u.ok})} report(s)"
                  f"{': ' + ', '.join(sorted({u.report for u in us if u.ok})) if us else ''}")
            for u in wrong:
                print(f"   DIFFERS  {u.report} {u.where}: {u.shown} — {u.sentence[:200]}")
            print()
        print(f"{bad} place(s) state a ledgered fact with another number")
        return 1 if bad else 0
    if a.json:
        json.dump([asdict(c) for c in cs], sys.stdout, ensure_ascii=False, indent=1)
        return 0
    print(f"{len(cs)} figures in {len({c.report for c in cs})} reports\n")
    ps = pairs(cs, a.min_sim)
    drift = [p for p in ps if not agree(p[1], p[2])]
    same = [p for p in ps if agree(p[1], p[2])]
    print(f"{len(drift)} possible conflicts, {len(same)} figures restated alike\n")
    for s, x, y in drift:
        print(f"[{s:.2f}] {x.shown}  vs  {y.shown}")
        print(f"   {x.report} {x.where}: {x.sentence[:220]}")
        print(f"   {y.report} {y.where}: {y.sentence[:220]}\n")
    if a.agree:
        for s, x, y in same:
            print(f"= [{s:.2f}] {x.shown} ({x.report}) / {y.shown} ({y.report})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
