"""
City & County of Honolulu budget parser.

Source: data.honolulu.gov (Socrata). The operating budget is published as a
line-item dataset at object-code granularity (~12.5k rows):

    department_name, division_name, fund_name, object_code_name,
    department (e.g. "OP_DES"), division (e.g. "DES2901"), fund, object_code,
    fy_2023_actual, fy_2024_actual, fy_2025_budget,
    fy_2026_current_services, fy_2026_budget_issues, fy_2026_proposed_budget

Amounts are strings like "$   8,200", zeros appear as "$   - 0", and
negatives as "$  (1,234)". We aggregate to department × division × fund for
output. CIP is not in the open data portal (PDF ordinance only) and is a
planned follow-up.

Datasets (raw JSON committed under data/raw/counties/honolulu/):
    fy2026: 7sq2-y9vx (proposed)   fy2027: puaw-t6sa (proposed)
"""
from __future__ import annotations
import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import List, Optional

from budgetprimer.models.budget_allocation import BudgetSection
from budgetprimer.models.county_allocation import CountyAllocation, normalize_fund

from .base import BaseCountyParser

logger = logging.getLogger(__name__)

# Socrata dataset ids per fiscal year (proposed budgets; swap in adopted
# dataset ids when data.honolulu.gov publishes them).
DATASET_IDS = {
    2026: '7sq2-y9vx',
    2027: 'puaw-t6sa',
}

# --- Capital Improvement Program (CIP) parsing -----------------------------
# CIP is published only in the executive capital budget ordinance PDF (a
# scanned, OCR'd document — Ord. 25-39 for FY2026). It is organized as
# SECTION per government FUNCTION → projects, each funded from one or more
# sources. We parse the project appropriation pages by word position and
# emit one allocation per (project × fund) source line, mirroring how the
# operating data is broken out to line items.
#
# Fund codes and their full names come from the ordinance's Section 1 revenue
# summary (page 1). The seven CIP functions double as the CIP "departments"
# in the output hierarchy (capital is budgeted by function, not by the
# operating departments).
CIP_FUND_NAMES = {
    'SR': 'Sewer Revenue Bond Improvement Fund',
    'GI': 'General Improvement Bond Fund',
    'HI': 'Highway Improvement Bond Fund',
    'TB': 'Taxable General Improvement Bond Fund',
    'WB': 'Solid Waste Improvement Bond Fund',
    'AF': 'Affordable Housing Fund',
    'BK': 'Bikeway Fund',
    'CF': 'Clean Water and Natural Lands Fund',
    'CP': 'Capital Projects Fund',
    'GN': 'General Fund',
    'PP': 'Parks and Playgrounds Fund',
    'SW': 'Sewer Fund',
    'CD': 'Community Development Fund',
    'FG': 'Federal Grants Fund',
}
# Recurring OCR misreads of the two-letter fund codes (the source is scanned).
_CIP_FUND_OCR = {
    'ILI': 'HI', 'CI': 'GI', 'GL': 'GI', 'GB': 'GI', '01': 'GI', '0I': 'GI',
    'SVV': 'SW', 'SVI': 'SW',
}
CIP_FUNCTIONS = [
    'GENERAL GOVERNMENT', 'PUBLIC SAFETY', 'HIGHWAYS AND STREETS',
    'SANITATION', 'HUMAN SERVICES', 'CULTURE-RECREATION',
    'UTILITIES OR OTHER ENTERPRISES',
]
# Published authoritative CIP grand total (Section 1, TOTAL ALL FUNDS) used to
# sanity-check the parse.
CIP_GRAND_TOTAL = {2026: 1286783101}


def _cip_amount(text: str) -> Optional[int]:
    """Parse an OCR'd whole-dollar amount. Amounts are always integers, so any
    stray '.' is a misread comma (e.g. '28.075,000' → 28075000)."""
    s = re.sub(r'[,.\s$]', '', text)
    return int(s) if re.fullmatch(r'-?\d+', s) else None


def _cip_fund_code(token: str) -> Optional[str]:
    """Normalize an OCR'd fund-code token to a known two-letter code."""
    t = re.sub(r'[^A-Za-z0-9]', '', token or '').upper()
    if t in CIP_FUND_NAMES:
        return t
    if t in _CIP_FUND_OCR:
        return _CIP_FUND_OCR[t]
    sub = t.translate(str.maketrans({'L': 'I', '1': 'I', 'B': 'I', '0': 'G'}))
    if sub in CIP_FUND_NAMES:
        return sub
    if sub[:2] in CIP_FUND_NAMES:
        return sub[:2]
    return None


def _slug(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')

# Explicit slugs for Honolulu departments; anything unlisted falls back to a
# generic slug of the name.
DEPT_SLUGS = {
    'Police': 'hpd',
    'Honolulu Police Department': 'hpd',
    'Fire': 'hfd',
    'Honolulu Fire Department': 'hfd',
    'Emergency Services': 'hesd',
    'Emergency Management': 'dem',
    'Budget and Fiscal Services': 'bfs',
    'Community Services': 'dcs',
    'Corporation Counsel': 'cor',
    'Customer Services': 'csd',
    'Design and Construction': 'ddc',
    'Enterprise Services': 'des',
    'Environmental Services': 'env',
    'Facility Maintenance': 'dfm',
    'Human Resources': 'dhr',
    'Information Technology': 'dit',
    'Land Management': 'dlm',
    'Mayor': 'mayor',
    'Managing Director': 'md',
    'Medical Examiner': 'med',
    'Parks and Recreation': 'dpr',
    'Planning and Permitting': 'dpp',
    'Prosecuting Attorney': 'pat',
    'Transportation Services': 'dts',
    'City Council': 'council',
    'City Clerk': 'clerk',
    'City Auditor': 'auditor',
    'Council Services': 'councilsvc',
    'Royal Hawaiian Band': 'rhb',
    'Ocean Safety': 'ocean',
}


def slugify_department(name: str) -> str:
    if name in DEPT_SLUGS:
        return DEPT_SLUGS[name]
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


def clean_amount(value) -> Optional[float]:
    """Parse Socrata amount strings like "$   8,200", "$   - 0", "$ (1,234)".

    Returns None for blank/unparseable values (callers treat None as 0 when
    aggregating but can distinguish missing data if needed).
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    negative = '(' in s and ')' in s
    s = re.sub(r'[$,()\s]', '', s)
    # Zeros come through as "- 0" → "-0"; bare "-" means blank
    if s in ('', '-'):
        return None
    try:
        amount = float(s)
    except ValueError:
        return None
    if negative:
        amount = -abs(amount)
    return amount


class HonoluluParser(BaseCountyParser):
    county = 'honolulu'
    source_label = 'FY2026 Proposed Operating Budget · data.honolulu.gov'
    source_url = 'https://data.honolulu.gov/d/7sq2-y9vx'
    # The open-data line items cover executive departments only (~$2.3B of the
    # ~$3.9B total operating budget). Debt service, employee benefits/
    # provisions, the legislative branch, and CIP appear only in the budget
    # ordinance PDFs (Ord. 25-38 / 25-39).
    coverage_note = (
        "Operating figures cover executive departments only (from the open-data "
        "portal) — they exclude debt service, employee benefits and provisions, "
        "and the legislative branch, which appear only in the operating budget "
        "ordinance. Capital (CIP) is the full executive capital budget parsed "
        "from the ordinance PDF."
    )

    def parse(self, raw_dir: Path, fiscal_year: int) -> List[CountyAllocation]:
        dataset_id = DATASET_IDS.get(fiscal_year)
        if not dataset_id:
            logger.warning(f"No Honolulu dataset known for FY{fiscal_year}")
            return []

        # The displayed source must name the year actually parsed — each FY is
        # its own Socrata dataset, so a static class label mislabels FY2027.
        self.source_label = f'FY{fiscal_year} Proposed Operating Budget · data.honolulu.gov'
        self.source_url = f'https://data.honolulu.gov/d/{dataset_id}'

        raw_file = raw_dir / f'fy{fiscal_year}_operating_{dataset_id}.json'
        if not raw_file.exists():
            logger.warning(f"Missing raw file {raw_file} — run scripts/fetch_county_budgets.py")
            return []

        with open(raw_file) as f:
            records = json.load(f)

        amount_col = f'fy_{fiscal_year}_proposed_budget'

        # Aggregate object-code rows up to department × division × fund
        totals: dict = defaultdict(float)
        names: dict = {}
        skipped = 0
        for rec in records:
            amount = clean_amount(rec.get(amount_col))
            if amount is None:
                skipped += 1
                continue
            dept = (rec.get('department_name') or '').strip()
            division = (rec.get('division_name') or '').strip()
            fund = (rec.get('fund_name') or '').strip()
            if not dept:
                skipped += 1
                continue
            key = (dept, division, fund)
            totals[key] += amount
            names[key] = (dept, division, fund)

        allocations: List[CountyAllocation] = []
        for (dept, division, fund), amount in totals.items():
            if amount == 0:
                continue
            allocations.append(CountyAllocation(
                county=self.county,
                department_code=slugify_department(dept),
                department_name=dept,
                section=BudgetSection.OPERATING,
                fund_name=fund or 'Unspecified',
                fund_category=normalize_fund(fund),
                fiscal_year=fiscal_year,
                amount=amount,
                program_name=division or None,
                source=f'data.honolulu.gov/{dataset_id}',
            ))

        logger.info(
            f"Honolulu FY{fiscal_year}: {len(records)} raw rows → "
            f"{len(allocations)} dept×division×fund allocations "
            f"({skipped} rows without amounts skipped)"
        )

        # Capital Improvement Program from the ordinance PDF (committed by
        # fetch_county_budgets.py --pdfs). Absent for years not yet published.
        allocations.extend(self.parse_cip(raw_dir, fiscal_year))
        return allocations

    # -- CIP -----------------------------------------------------------------
    def parse_cip(self, raw_dir: Path, fiscal_year: int) -> List[CountyAllocation]:
        """Parse the executive capital budget ordinance PDF into per-(project ×
        fund) capital allocations. Returns [] if the PDF isn't present."""
        pdfs = sorted(raw_dir.glob(f'fy{fiscal_year}_cip_*.pdf'))
        if not pdfs:
            logger.info(f"Honolulu FY{fiscal_year}: no CIP ordinance PDF — skipping capital")
            return []
        try:
            import pdfplumber
        except ImportError:
            logger.warning("pdfplumber not installed — cannot parse Honolulu CIP")
            return []

        pdf_path = pdfs[0]
        source = pdf_path.stem.replace(f'fy{fiscal_year}_cip_', '')

        def lines_of(page):
            """Group a page's words into lines (by rounded y) sorted left→right."""
            buckets: dict = defaultdict(list)
            for w in page.extract_words(use_text_flow=False):
                buckets[round(w['top'] / 3) * 3].append(w)
            return [sorted(buckets[t], key=lambda w: w['x0']) for t in sorted(buckets)]

        def left_text(ws):
            return ' '.join(w['text'] for w in ws
                            if w['x0'] < 345 and _cip_amount(w['text']) is None).strip()

        def fund_token(ws):
            # The SOURCE OF FUNDS code sits just left of the TOTAL ALL FUNDS
            # column (x≈470–506): a short token that isn't a thousands amount.
            cand = [w for w in ws if 470 <= w['x0'] <= 506 and len(w['text']) <= 4
                    and ',' not in w['text'] and not re.fullmatch(r'\d{3,}', w['text'])]
            return cand[-1] if cand else None

        def source_amount(ws):
            # The SOURCE OF FUNDS amount column sits at x≈426–468.
            cand = [w for w in ws if 426 <= w['x0'] <= 468
                    and _cip_amount(w['text']) is not None
                    and abs(_cip_amount(w['text'])) >= 500]
            return cand[-1] if cand else None

        def total_funds(ws):
            cand = [(w['x0'], _cip_amount(w['text'])) for w in ws if w['x0'] >= 505
                    and _cip_amount(w['text']) is not None and abs(_cip_amount(w['text'])) >= 1000]
            return max(cand, key=lambda t: t[0])[1] if cand else None

        def is_label(ws):
            up = left_text(ws).upper().replace(' ', '')
            return up.startswith('TOTAL') or 'SOURCEOFFUNDS' in up or 'WORKPHASE' in up

        allocations: List[CountyAllocation] = []
        function = 'General Government'
        project = None  # current project name
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                L = lines_of(page)
                for i, ws in enumerate(L):
                    if not ws:
                        continue
                    top = ws[0]['top']
                    if top < 200 or top > 720:   # page header/footer band
                        continue

                    name = left_text(ws)
                    upname = name.upper()
                    # Function section header (also doubles as the CIP "dept").
                    matched_fn = next((f for f in CIP_FUNCTIONS
                                       if f.replace(' ', '') in upname.replace(' ', '')), None)
                    if matched_fn and source_amount(ws) is None and total_funds(ws) is None:
                        function = matched_fn.title()
                        continue
                    if is_label(ws):            # function/program subtotal lines
                        continue

                    # New project: a line carrying the TOTAL ALL FUNDS for a
                    # named project (with or without a 7-digit project number).
                    if total_funds(ws) is not None and name and not name.upper().startswith('TOTAL'):
                        project = re.sub(r'^\d{7}\s*', '', name).strip() or project

                    # Emit one allocation per source line: amount + fund code.
                    code_tok = fund_token(ws)
                    if not code_tok:
                        continue
                    amt_tok = source_amount(ws)
                    if amt_tok is None and i > 0:    # wrapped: amount on prior line
                        amt_tok = source_amount(L[i - 1])
                    if amt_tok is None:
                        continue
                    code = _cip_fund_code(code_tok['text'])
                    if not code:
                        continue
                    amount = _cip_amount(amt_tok['text'])
                    if amount is None or amount == 0:
                        continue
                    fund_name = CIP_FUND_NAMES[code]
                    allocations.append(CountyAllocation(
                        county=self.county,
                        department_code=_slug(function),
                        department_name=function,
                        section=BudgetSection.CAPITAL_IMPROVEMENT,
                        fund_name=fund_name,
                        fund_category=normalize_fund(fund_name),
                        fiscal_year=fiscal_year,
                        amount=float(amount),
                        program_name=(project or 'Capital Projects').title(),
                        source=f'Honolulu CIP ordinance ({source})',
                    ))

        parsed = sum(a.amount for a in allocations)
        control = CIP_GRAND_TOTAL.get(fiscal_year)
        pct = f"{(parsed - control) / control * 100:+.2f}% vs published" if control else ""
        logger.info(
            f"Honolulu FY{fiscal_year} CIP: {len(allocations)} project×fund rows, "
            f"${parsed/1e6:.1f}M total {pct}"
        )
        if control and abs(parsed - control) / control > 0.03:
            logger.warning(
                f"Honolulu CIP parse off by >3% from published total "
                f"(${parsed:,.0f} vs ${control:,}) — check OCR/layout drift"
            )
        return allocations
