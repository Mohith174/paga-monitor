# PAGA Monitor

California's PAGA labor-law filings are public record, but they sit behind a
Salesforce search portal with no API, no feed, and no alerts. For plaintiff-side
employment firms, seeing a new filing first is the whole game. PAGA Monitor
polls the portal, turns each filing into a structured lead, and scores it so
the highest-value cases surface immediately.

## How it works

```
LWDA filing portal (Salesforce Visualforce app)
        |
        v
scraper.py: submit the search form, then call the portal's own
            remoting API (PAGAResultsController.getAllCases) for JSON
        |
        v
database.py: content-hash each case -> new / amended / duplicate
        |
        v
lead_scorer.py: heuristic score (employee count, violation keywords, filing age)
        |
        v
app.py: Flask dashboard (leads, priority queue, analytics, CSV export)
```

The portal has no public API. `scraper.py` drives a headless browser through
the real search form (required to get a signed session), then calls the
site's own internal remoting endpoint directly instead of parsing rendered
HTML — a full month of filings comes back as structured JSON in under 3
seconds.

Scoring is a plain weighted heuristic (employee count + violation keywords +
filing recency), not a model call — deterministic, free, and good enough to
rank ~40 filings a day.

## Files

| File | Responsibility |
|---|---|
| `scraper.py` | Drives the LWDA search form, calls the remoting API, parses results |
| `database.py` | SQLite schema, dedup, dashboard queries |
| `lead_scorer.py` | Heuristic lead scoring (0-100, no AI/API required) |
| `app.py` | Flask dashboard: `/`, `/leads`, `/priority`, `/analytics`, `/export/csv` |
| `scheduler.py` | Polls the scraper every 5 minutes during business hours |
| `backfill.py` | One-off/gap-fill historical scrape, chunked by month |

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

python lead_scorer.py   # score any unscored cases
./start.sh              # starts the dashboard (:5001) + scheduler together
```
