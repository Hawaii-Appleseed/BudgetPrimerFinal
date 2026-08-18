# Council on Revenues archive

Written automatically by [`scripts/cor_monitor.py`](../../../scripts/cor_monitor.py),
run weekly by [`.github/workflows/cor-monitor.yml`](../../../.github/workflows/cor-monitor.yml).
Nothing here is hand-maintained.

The Council forecasts general fund revenue growth and reports to the governor and
legislature on statutory dates — June 1, September 10, January 10, March 15, plus
total personal income forecasts each August 5 and November 5. In practice that is
about five updates a year, and those revisions move the six-year financial plan
the Budget Primer is built on.

## What's here

| Path | What it is |
|---|---|
| `cor_index.json` | Every document on the COR page: title, type, meeting date, first seen, and where we archived it. **This is the monitor's memory** — delete it and the next run reports all 26 documents as new. |
| `last_checked.json` | Heartbeat, rewritten every run whether or not anything changed. |
| `pdfs/` | Forecast PDFs as published (general fund and total personal income only). |
| `extracted/` | `pdfplumber` text + tables from each PDF, so forecasts are greppable and diffable. |
| `changes/YYYY-MM-DD-cor-change.md` | Change packet: what appeared, plus the new forecast's text alongside the one it supersedes. |
| `changes/YYYY-MM-DD-cor-summary.md` | The written analysis of that change. |

## Why the heartbeat commits

`last_checked.json` is committed on every run even when nothing changed. That is
deliberate: GitHub disables scheduled workflows in repositories with 60 days of
no activity, and the COR routinely goes quiet for two or three months. Without a
commit on quiet weeks the monitor would eventually switch itself off — silently,
which is the worst way for a monitor to fail.

## Running it by hand

```bash
python3 scripts/cor_monitor.py --dry-run   # report, write nothing
python3 scripts/cor_monitor.py             # check, archive, write a packet
python3 scripts/cor_monitor.py --seed      # adopt current state as baseline, no packet
```

Exit codes: `0` nothing new, `10` something changed, `1` failed. The workflow
branches on these, and treats `1` as a hard failure rather than a quiet skip — a
parse returning zero documents means the page markup changed and the monitor
needs fixing, not ignoring.
