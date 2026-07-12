# PAGA Monitor

California's PAGA labor-law filings are public record, but they sit behind a
Salesforce search portal with no API, no feed, and no alerts. For plaintiff-side
employment firms, seeing a new filing first is the whole game. PAGA Monitor
polls the portal, turns each filing into a structured lead, and scores it so
the highest-value cases surface immediately.

## Architecture

The system is split into an **ingestion worker** (runs wherever a browser can
launch) and a **stateless dashboard** (deployed to Vercel), sharing one
Postgres database:

```
LWDA filing portal (Salesforce Visualforce app)
        |
        v
scraper.py: submit the search form, then call the portal's own
            remoting API (PAGAResultsController.getAllCases) for JSON
        |
        v
database.py: content-hash each case -> new / amended / duplicate  ---> Postgres (Neon)
        |                                                                  ^
        v                                                                 |
lead_scorer.py: heuristic score (employee count, violations, filing age)  |
                                                                           |
                                                           app.py: Flask dashboard,
                                                           reads the same Postgres,
                                                           deployed on Vercel
```

The portal has no public API. `scraper.py` drives a headless browser through
the real search form (required to get a signed session), then calls the
site's own internal remoting endpoint directly instead of parsing rendered
HTML — a full month of filings comes back as structured JSON in under 3
seconds.

Scoring is a plain weighted heuristic (employee count + violation keywords +
filing recency), not a model call — deterministic, free, and good enough to
rank ~40 filings a day.

**Why split ingestion from serving:** Vercel Functions don't give you a
persistent local disk or a headless-browser-friendly environment, so the
Playwright-driven scraper runs as a worker (a laptop, a small VM, a cron
box — anything that can run Python and launch Chromium) instead of inside
the deployed app. The dashboard itself is stateless and only reads Postgres,
so it deploys cleanly to Vercel with zero knowledge of how the data got
there.

## Files

| File | Responsibility |
|---|---|
| `scraper.py` | Drives the LWDA search form, calls the remoting API, parses results |
| `database.py` | Postgres schema, dedup, dashboard queries |
| `lead_scorer.py` | Heuristic lead scoring (0-100, no AI/API required) |
| `app.py` | Flask dashboard: `/`, `/leads`, `/priority`, `/analytics`, `/export/csv` |
| `scheduler.py` | Polls the scraper every 5 minutes during business hours |
| `backfill.py` | One-off/gap-fill historical scrape, chunked by month |

## Running it

Dashboard only (what's deployed to Vercel):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgres://...
python app.py
```

Full stack, including the scraper (needs a real browser, so it stays local):

```bash
pip install -r requirements-scraper.txt
playwright install chromium
export DATABASE_URL=postgres://...

python lead_scorer.py   # score any unscored cases
./start.sh              # starts the dashboard (:5001) + scheduler together
```
