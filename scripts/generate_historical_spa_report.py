#!/usr/bin/env python3
"""Generate the docs/js/historical_trends.json data file consumed by the SPA.

Joins the parsed historical allocations × CPI deflators and produces the
single JSON file the new "History" tab loads.

Inputs (default paths):
  data/processed/historical_allocations.csv  (from parse_historical_budgets.py)
  data/processed/cpi_honolulu.json           (from fetch_cpi.py)

Output:
  docs/js/historical_trends.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402

from budgetprimer.historical import HISTORICAL_BIENNIAL_BILLS  # noqa: E402
from budgetprimer.parsers.fast_parser import DEPARTMENT_NAMES  # noqa: E402

DEFAULT_ALLOCATIONS = PROJECT_ROOT / "data" / "processed" / "historical_allocations.csv"
DEFAULT_CPI = PROJECT_ROOT / "data" / "processed" / "cpi_honolulu.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "js" / "historical_trends.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(message)s",
)
logger = logging.getLogger("generate_historical_spa_report")


# ---------------------------------------------------------------------------
# CPI deflator helpers (with projection for missing future FYs)
# ---------------------------------------------------------------------------

def project_missing_deflators(deflators: dict[str, float],
                              required_fys: list[int]) -> tuple[dict[str, float], set[int]]:
    """Extend the deflator series to cover all required FYs.

    For any FY beyond the latest available, project linearly using the 3-year
    average year-over-year inflation rate.  Returns (extended, projected_set).
    """
    have = {int(k): v for k, v in deflators.items()}
    if not have:
        return deflators, set()

    sorted_fys = sorted(have)
    latest = sorted_fys[-1]
    # Average annual inflation = geometric mean of last 3 deflator ratios
    # (deflator goes DOWN over time as we approach base year)
    if len(sorted_fys) >= 4:
        ratios = [
            have[sorted_fys[i - 1]] / have[sorted_fys[i]]
            for i in range(-3, 0)
        ]
        avg_inflation = sum(ratios) / len(ratios)  # ~1.025 means ~2.5% annual
    else:
        avg_inflation = 1.025  # safe default

    projected: set[int] = set()
    for fy in sorted(set(required_fys)):
        if fy in have:
            continue
        if fy > latest:
            # Each year past latest, deflator divides by avg_inflation
            steps = fy - latest
            new_d = have[latest] / (avg_inflation ** steps)
            have[fy] = round(new_d, 4)
            projected.add(fy)

    return {str(k): v for k, v in sorted(have.items())}, projected


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------

def build_totals_by_fy(df: pd.DataFrame, deflators: dict[str, float]) -> list[dict]:
    """Sum operating + capital totals per fiscal year, with real-dollar conversion."""
    rows: list[dict] = []
    for fy in sorted(df["fiscal_year"].unique()):
        sub = df[df["fiscal_year"] == fy]
        op = sub[sub["section"] == "Operating"]["amount"].sum()
        cap = sub[sub["section"] == "Capital Improvement"]["amount"].sum()
        total = op + cap
        d = float(deflators.get(str(int(fy)), 1.0))
        rows.append({
            "fy": int(fy),
            "operating_nominal": round(op, 0),
            "operating_real": round(op * d, 0),
            "capital_nominal": round(cap, 0),
            "capital_real": round(cap * d, 0),
            "total_nominal": round(total, 0),
            "total_real": round(total * d, 0),
            "deflator": d,
        })
    return rows


def build_by_department(df: pd.DataFrame, deflators: dict[str, float]) -> list[dict]:
    """For each department × FY, total appropriation plus the operating/
    capital split.  The split lets the SPA stack op + cap on the
    per-department history chart instead of showing only the combined
    total line.

    Rows in the source CSV carry a ``section`` column with values
    "Operating" or "Capital Improvement" (the parser drops anything
    else).  We pivot on that.
    """
    out: list[dict] = []
    # Pivot department × fiscal_year × section  →  amount.
    by_dept = (
        df.groupby(["department_code", "fiscal_year", "section"])["amount"]
        .sum()
        .unstack("section", fill_value=0.0)
        .reset_index()
    )
    # Normalize column names — older pandas can leave the columns index
    # named "section" and we rename the two sections to friendly keys.
    by_dept.columns.name = None
    by_dept = by_dept.rename(columns={
        "Operating": "operating",
        "Capital Improvement": "capital",
    })
    if "operating" not in by_dept.columns:
        by_dept["operating"] = 0.0
    if "capital" not in by_dept.columns:
        by_dept["capital"] = 0.0
    by_dept["total"] = by_dept["operating"] + by_dept["capital"]

    for dept_code, sub in by_dept.groupby("department_code"):
        dept_name = DEPARTMENT_NAMES.get(dept_code, dept_code)
        series = []
        for _, row in sub.sort_values("fiscal_year").iterrows():
            fy = int(row["fiscal_year"])
            op = float(row["operating"])
            cap = float(row["capital"])
            tot = float(row["total"])
            d = float(deflators.get(str(fy), 1.0))
            series.append({
                "fy": fy,
                # Combined totals — kept for backward compatibility with
                # any consumer that only reads ``nominal`` / ``real``.
                "nominal": round(tot, 0),
                "real": round(tot * d, 0),
                # Operating / capital split, both nominal and real.
                "operating_nominal": round(op, 0),
                "operating_real": round(op * d, 0),
                "capital_nominal": round(cap, 0),
                "capital_real": round(cap * d, 0),
            })
        out.append({
            "dept_code": dept_code,
            "dept_name": dept_name,
            "series": series,
        })
    # Sort departments by their most-recent-FY total (descending) for consistent UI ordering
    def latest_total(d: dict) -> float:
        return d["series"][-1]["nominal"] if d["series"] else 0
    out.sort(key=latest_total, reverse=True)
    return out


def build_by_fund_category(df: pd.DataFrame, deflators: dict[str, float]) -> list[dict]:
    """Per-fund-category totals over time (General, Federal, Special, etc.)."""
    out: list[dict] = []
    by_fund = (
        df.groupby(["fund_category", "fiscal_year"])["amount"]
        .sum()
        .reset_index()
    )
    for fund_cat, sub in by_fund.groupby("fund_category"):
        series = []
        for _, row in sub.sort_values("fiscal_year").iterrows():
            fy = int(row["fiscal_year"])
            amt = float(row["amount"])
            d = float(deflators.get(str(fy), 1.0))
            series.append({
                "fy": fy,
                "nominal": round(amt, 0),
                "real": round(amt * d, 0),
            })
        out.append({
            "fund_category": fund_cat,
            "series": series,
        })
    # Sort by most-recent-FY total (descending)
    def latest_total(d: dict) -> float:
        return d["series"][-1]["nominal"] if d["series"] else 0
    out.sort(key=latest_total, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate docs/js/historical_trends.json from parsed CSV + CPI."
    )
    ap.add_argument("--allocations", type=Path, default=DEFAULT_ALLOCATIONS)
    ap.add_argument("--cpi", type=Path, default=DEFAULT_CPI)
    ap.add_argument("--output", "-o", type=Path, default=DEFAULT_OUTPUT)
    args = ap.parse_args()

    if not args.allocations.exists():
        logger.error(f"Missing allocations CSV: {args.allocations}")
        logger.error("Run scripts/parse_historical_budgets.py first.")
        return 1
    if not args.cpi.exists():
        logger.error(f"Missing CPI JSON: {args.cpi}")
        logger.error("Run scripts/fetch_cpi.py first.")
        return 1

    df = pd.read_csv(args.allocations)

    # ── Resolve each fiscal year to its FINAL enacted appropriation ──────────
    # A biennium is enacted by a MAIN bill (odd-year session) covering both
    # fiscal years, then amended by an even-year SUPPLEMENTAL. The CSV holds
    # both, and each restates the SAME program-years, so summing them
    # double-counts (FY2016 → $26.5B, FY2024 → $40.3B). But we can't just drop
    # the supplementals either: a supplemental's SECOND year is the complete,
    # amended budget (the whole point of the bill — federal funds revised UP),
    # while its FIRST year re-estimates federal/special grant authority DOWNWARD
    # mid-year and understates the enacted appropriation. So resolve per year:
    #   • First year of a biennium (even FY)  → the MAIN bill.
    #   • Second year (odd FY), if a supplemental exists → the SUPPLEMENTAL.
    #   • Second year with no supplemental (FY2021, and FY2027 whose Act 175
    #     lives in a separate dataset) → the MAIN bill.
    # This yields the final enacted budget for each year without double-count.
    if "bill_scope" in df.columns:
        df["fiscal_year"] = df["fiscal_year"].astype(int)
        is_supp = df["bill_scope"] == "supplemental"
        supp_fys = set(df.loc[is_supp, "fiscal_year"])
        # A row is kept when it is the authoritative source for its year:
        #   second year with a supplemental → keep supplemental rows only
        #   otherwise (first year, or no supplemental) → keep main rows only
        second_year = df["fiscal_year"] % 2 == 1          # odd FY = biennium year 2
        supp_covers = df["fiscal_year"].isin(supp_fys)
        use_supp = second_year & supp_covers
        keep = (use_supp & is_supp) | (~use_supp & ~is_supp)
        before = len(df)
        df = df[keep].copy()
        logger.info(
            f"Resolved to final-enacted rows: kept {len(df)} of {before} "
            f"(year-1 from main bill, year-2 from supplemental where present)"
        )

    # Current biennium (FY2026-27): the final enacted budget is Act 175 (the
    # HB1800 supplemental), which superseded Act 250. Unlike older supplementals
    # it fully restates BOTH years (its year-1 federal matches the main bill),
    # so it is authoritative for FY2026 and FY2027. Act 175 lives in the
    # parallel By-Department datasets rather than this CSV, so splice it in:
    # drop the Act 250 FY2026-27 rows and add Act 175's program-level rows.
    act175 = {2026: PROJECT_ROOT / "docs" / "js" / "departments_act175_fy2026.json",
              2027: PROJECT_ROOT / "docs" / "js" / "departments_act175_fy2027.json"}
    if all(p.exists() for p in act175.values()):
        df = df[~df["fiscal_year"].isin([2026, 2027])].copy()
        spliced = []
        for fy, path in act175.items():
            for d in json.loads(path.read_text()):
                for p in d.get("programs", []):
                    spliced.append({
                        "department_code": d["code"],
                        "department_name": d["name"],
                        "fiscal_year": fy,
                        "section": p["section"],
                        "fund_category": p["fund_category"],
                        "amount": p["amount"],
                    })
        df = pd.concat([df, pd.DataFrame(spliced)], ignore_index=True)
        logger.info(f"Spliced in {len(spliced)} Act 175 rows for FY2026-27 (final enacted supplemental)")

    cpi = json.loads(args.cpi.read_text())
    base_fy = int(cpi["base_fy"])

    # Project deflators forward to cover any FY in our data
    required_fys = sorted(int(fy) for fy in df["fiscal_year"].unique())
    deflators, projected = project_missing_deflators(cpi["deflators"], required_fys)
    if projected:
        logger.warning(
            f"Projected deflators (based on recent inflation trend): {sorted(projected)}"
        )

    # Build aggregations
    totals = build_totals_by_fy(df, deflators)
    by_dept = build_by_department(df, deflators)
    by_fund = build_by_fund_category(df, deflators)

    # Build acts metadata in chronological order.  Each session may have
    # ONE or MORE bills (e.g. 2019 split operating → HB2 and capital →
    # HB1259).  Emit one entry per bill so the UI can list them all.
    acts = []
    for sy in sorted(HISTORICAL_BIENNIAL_BILLS):
        info = HISTORICAL_BIENNIAL_BILLS[sy]
        for bill_info in info["bills"]:
            acts.append({
                "session": sy,
                "bill": bill_info["number"],
                "act": bill_info["act"],
                "scope": bill_info.get("scope", "combined"),
                "fy_covered": list(info["fy_covered"]),
                "source_url": (
                    f"https://data.capitol.hawaii.gov/sessions/session{sy}"
                    f"/bills/{bill_info['number']}_CD1_.HTM"
                ),
            })

    # Per-session notes for any anomalies the UI should surface.  Today the
    # only surviving quirk is that the 2019 biennium was enacted as TWO
    # bills (HB2 operating + HB1259 capital) rather than one omnibus — the
    # chart totals are still complete, but the footnote explains the split.
    bill_notes: list[dict] = []
    for sy in sorted(HISTORICAL_BIENNIAL_BILLS):
        info = HISTORICAL_BIENNIAL_BILLS[sy]
        if len(info["bills"]) > 1:
            names = " and ".join(b["number"] for b in info["bills"])
            bill_notes.append({
                "session": sy,
                "note": (
                    f"The {sy} biennial budget was enacted as two bills: "
                    f"{names}. Totals here combine both."
                ),
            })

    output = {
        "metadata": {
            "base_fy": base_fy,
            "fy_range": [min(required_fys), max(required_fys)],
            "acts": acts,
            "cpi_source": cpi.get("source", "BLS Urban Hawaii CPI-U"),
            "cpi_fetched": cpi.get("fetched"),
            "projected_fys": sorted(projected),
            "bill_notes": bill_notes,
            "generated": datetime.now().isoformat(timespec="seconds"),
            "notes": (
                "Figures reflect CD1 (legislatively-passed) totals. "
                "Line-item vetoes are not netted out."
            ),
        },
        "totals_by_fy": totals,
        "by_department": by_dept,
        "by_fund_category": by_fund,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    size_kb = args.output.stat().st_size / 1024
    logger.info(f"Wrote {args.output.relative_to(PROJECT_ROOT)} ({size_kb:.1f} KB)")
    logger.info(f"  fy_range: {output['metadata']['fy_range']}")
    logger.info(f"  base_fy: {base_fy}")
    logger.info(f"  acts: {len(acts)}, departments: {len(by_dept)}, funds: {len(by_fund)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
