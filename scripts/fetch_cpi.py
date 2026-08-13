#!/usr/bin/env python3
"""Fetch Urban Hawaii CPI-U from BLS and write deflators keyed by fiscal year.

Hawaii CPI-U series: CUURS49FSA0
  - Urban Hawaii, all items, NSA (bimonthly: odd months)
  - Public BLS API allows up to 25 series/day with no key
  - The older area code A426 was retired in BLS' 2018 area redesign;
    S49F (Urban Hawaii) is the current statewide code. NOTE: the
    similar-looking CUURS49ASA0 is Los Angeles-Long Beach-Anaheim — this
    script mistakenly used it until 2026-08-12.

Output: data/processed/cpi_honolulu.json
{
  "base_fy": 2027,
  "source": "BLS CUURS49FSA0 (Urban Hawaii CPI-U, all items, NSA)",
  "fetched": "2026-04-22",
  "deflators": { "2016": 1.314, ..., "2027": 1.000 }
}

Multiplying a nominal FY-X amount by deflators[X] yields constant base_fy
dollars.  Hawaii fiscal year FY-X covers the calendar period
July X-1 → June X, so we use the average of H2 of (X-1) and H1 of X.

If the BLS API is unreachable we leave any existing JSON in place rather
than overwriting it with an empty file (so the SPA still has data).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from budgetprimer.historical import fiscal_years_covered  # noqa: E402

OUTPUT_PATH = PROJECT_ROOT / "data" / "processed" / "cpi_honolulu.json"
SERIES_ID = "CUURS49FSA0"  # Urban Hawaii CPI-U, all items, NSA (bimonthly)
BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
DEFAULT_BASE_FY = 2026  # FY2027 needs CY2027 data, not yet available


# ---------------------------------------------------------------------------
# BLS fetch
# ---------------------------------------------------------------------------

def fetch_bls_series(series_id: str, start_year: int, end_year: int) -> list[dict]:
    """Return a list of {year, period, value} dicts for the given series.

    Uses the public (no-key) BLS API endpoint.  Splits the request into
    ≤10-year chunks because the v2 public endpoint enforces that limit.
    """
    out: list[dict] = []
    chunk_start = start_year
    while chunk_start <= end_year:
        chunk_end = min(chunk_start + 9, end_year)
        payload = json.dumps({
            "seriesid": [series_id],
            "startyear": str(chunk_start),
            "endyear": str(chunk_end),
        }).encode("utf-8")
        req = Request(
            BLS_API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "BudgetPrimer-Historical/1.0",
            },
        )
        with urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if body.get("status") != "REQUEST_SUCCEEDED":
            raise RuntimeError(
                f"BLS request failed for {chunk_start}-{chunk_end}: "
                f"{body.get('message')}"
            )
        series = body["Results"]["series"]
        if not series:
            raise RuntimeError(f"BLS returned no series for {series_id}")
        for entry in series[0]["data"]:
            raw_value = entry.get("value", "")
            # BLS uses "-" for missing/suppressed observations — skip them
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            out.append({
                "year": int(entry["year"]),
                "period": entry["period"],     # M01..M12 or S01/S02 for semi
                "value": value,
            })
        chunk_start = chunk_end + 1

    return out


def calendar_year_average(observations: list[dict]) -> dict[int, float]:
    """Collapse semi-annual or monthly observations to a calendar-year average.

    Averaging whatever periods the series publishes (Urban Hawaii is
    bimonthly; some series use S01/S02 half-years) yields a usable annual index.
    """
    by_year: dict[int, list[float]] = {}
    for obs in observations:
        # The series sometimes also includes M13 (annual) — skip it to avoid
        # double-counting if both M and annual exist.
        if obs["period"] == "M13":
            continue
        by_year.setdefault(obs["year"], []).append(obs["value"])
    return {year: sum(values) / len(values) for year, values in by_year.items()}


def fiscal_year_index(annual: dict[int, float]) -> dict[int, float]:
    """Convert calendar-year averages to fiscal-year averages.

    Hawaii FY-X = July (X-1) → June X.  We approximate with the simple
    midpoint average of the two calendar years it spans.
    """
    fys: dict[int, float] = {}
    for fy in sorted(annual):
        prev = annual.get(fy - 1)
        curr = annual.get(fy)
        if prev is None or curr is None:
            continue
        fys[fy] = (prev + curr) / 2.0
    return fys


# ---------------------------------------------------------------------------
# Deflator construction
# ---------------------------------------------------------------------------

def build_deflators(fy_index: dict[int, float], base_fy: int) -> dict[str, float]:
    """deflators[fy] = base_fy_index / fy_index — multiplier to constant $."""
    if base_fy not in fy_index:
        raise ValueError(
            f"Base fiscal year {base_fy} not in CPI series "
            f"(have {sorted(fy_index)})"
        )
    base = fy_index[base_fy]
    return {str(fy): round(base / val, 4) for fy, val in fy_index.items()}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Fetch Urban Hawaii CPI-U and write per-FY deflators."
    )
    ap.add_argument(
        "--base-fy",
        type=int,
        default=DEFAULT_BASE_FY,
        help=f"Base fiscal year for constant-dollar conversion (default: {DEFAULT_BASE_FY}).",
    )
    ap.add_argument(
        "--start-year",
        type=int,
        default=2010,
        help="First calendar year to fetch from BLS (default: 2010).",
    )
    ap.add_argument(
        "--end-year",
        type=int,
        default=datetime.now().year,
        help="Last calendar year to fetch (default: current year).",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_PATH,
        help=f"Output JSON path (default: {OUTPUT_PATH.relative_to(PROJECT_ROOT)})",
    )
    args = ap.parse_args()

    print(
        f"Fetching BLS series {SERIES_ID} (Urban Hawaii CPI-U) "
        f"for {args.start_year}–{args.end_year}..."
    )
    try:
        observations = fetch_bls_series(SERIES_ID, args.start_year, args.end_year)
    except Exception as e:
        print(f"ERROR: BLS fetch failed: {e}")
        if args.output.exists():
            print(f"  Existing {args.output} preserved.")
        else:
            print("  No existing CPI file; downstream steps will fail.")
        return 1

    print(f"  Got {len(observations)} observations.")
    annual = calendar_year_average(observations)
    fy_index = fiscal_year_index(annual)
    deflators = build_deflators(fy_index, args.base_fy)

    needed_fys = fiscal_years_covered()
    missing = [fy for fy in needed_fys if str(fy) not in deflators]
    if missing:
        print(
            f"  WARNING: missing deflators for required fiscal years: {missing}"
        )

    output = {
        "base_fy": args.base_fy,
        "series_id": SERIES_ID,
        "source": f"BLS {SERIES_ID} (Urban Hawaii CPI-U, all items, NSA)",
        "fetched": datetime.now().isoformat(timespec="seconds"),
        "method": "FY = average of H2(prev) + H1(curr); deflator = base_fy / fy_index",
        "fy_index": {str(fy): round(val, 3) for fy, val in fy_index.items()},
        "deflators": deflators,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print(f"Wrote {args.output.relative_to(PROJECT_ROOT)}")

    print()
    print(f"{'FY':<6} {'CPI':>8}  Deflator (×nominal → FY{args.base_fy}$)")
    print("-" * 50)
    for fy in sorted(int(k) for k in deflators):
        ix = fy_index[fy]
        d = deflators[str(fy)]
        marker = "  ← base" if fy == args.base_fy else ""
        print(f"{fy:<6} {ix:>8.2f}  {d:>8.4f}{marker}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
