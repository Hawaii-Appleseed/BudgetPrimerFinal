# Tax Review Commission archive

Written by `scripts/trc_monitor.py`, run weekly by
`.github/workflows/trc-monitor.yml`. Nothing here is edited by hand.

| Path | What |
|---|---|
| `trc_index.json` | Every document currently linked from the TRC page, keyed by URL. The diff baseline. |
| `last_checked.json` | Heartbeat — updated every run, including quiet ones. |
| `pdfs/`, `extracted/` | Downloaded minutes and presentations, plus their text. Only substantive documents are archived; meeting notices and videos are indexed but not downloaded. |
| `changes/` | One `<date>-trc-change.md` packet per change, plus the `<date>-trc-summary.md` narrative when the Claude step ran. |

The monitor indexes only this Commission's own files
(`files.hawaii.gov/tax/stats/trc/`) and its meeting videos. The page also links
prior commissions — TRC 2020-2022 and older — which are closed archives, not
news; indexing them would make every run look enormous.
