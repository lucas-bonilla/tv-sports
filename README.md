# 📺 TV Sports PWA

A Progressive Web App with the Spanish sports TV schedule (scraped from marca.com)
and the Qatar Airways Premier Padel Tour calendar. Deployed on Vercel as a Python
serverless function plus static files — no build step, no bundler, no npm.

## Stack

- **Backend**: Python 3.11 + FastAPI + BeautifulSoup
- **Frontend**: Vanilla JS + CSS, PWA with a service worker
- **Hosting**: Vercel (serverless function + static assets)

## Architecture — read this first

`api/index.py` **is the production backend.** It is the Vercel serverless function
and holds all the logic: the Marca scrape, the Premier Padel client, caching and
every endpoint.

`backend/` **is legacy local tooling and is not deployed.** `backend/main.py` only
exposes `/events` and `/health`, and `backend/scraper.py` is an older copy of the
scraping logic. Editing them changes nothing in production — a common trap. The one
file there that still matters day to day is `backend/dev_server.py` (see below).

`vercel.json` rewrites `/api/(.*)` to the function and serves `frontend/` statically.
The frontend fetches relative `/api/...` paths, so nothing hardcodes a host.

### Endpoints

| Endpoint | Returns |
| --- | --- |
| `GET /api/events` | TV schedule grouped by day, scraped from Marca |
| `GET /api/ics` | The same schedule as an `.ics` calendar (`text/calendar`) |
| `GET /api/padel/tournaments` | Season calendar with status, category, country, prize money |
| `GET /api/padel/schedule?slug=<slug>` | Order of play for one tournament, by day and court |
| `GET /api/health` | Liveness check |

### Layout

```text
tv-sports/
├── api/
│   └── index.py          # ← production backend: scraping, padel, cache, endpoints
├── frontend/
│   ├── index.html        # loads both sections and coordinates the initial loader
│   ├── app.js            # TV schedule
│   ├── padel.js          # padel calendar and order of play
│   ├── style.css
│   ├── manifest.json     # PWA manifest
│   └── sw.js             # service worker — see "Cache versioning"
├── backend/              # legacy, NOT deployed
│   └── dev_server.py     # local proxy that mimics vercel.json
├── requirements.txt      # the dependencies Vercel installs
└── vercel.json
```

`.claude/agents/` holds the agent definitions used to develop this project
(implementation, QA, review, docs, deploy and data-source diagnosis). They are
prompts, not runtime code.

## Running locally

The frontend needs `/api/...` to resolve, which a plain static server cannot do. So
local development takes two processes: the API, and a proxy that reproduces what
`vercel.json` does in production.

Use Python 3.11 to match the Vercel runtime (`backend/runtime.txt`):

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt      # the root file, not backend/
```

Then, in two terminals:

```bash
# 1. the production API
cd api && ../.venv/bin/python -m uvicorn index:app --port 8077 --reload

# 2. the static proxy
.venv/bin/python backend/dev_server.py
```

Open **<http://127.0.0.1:8078>** — port 8078, the proxy. Hitting 8077 directly gives
you the API but no frontend, and serving `frontend/` with something like
`python3 -m http.server` loads the page while every API call 404s.

Any Python 3.11 environment works; adjust the interpreter if you use pyenv, conda or
another manager.

## Cache versioning — the one thing that bites

**If you change anything under `frontend/`, bump `STATIC_CACHE` in `frontend/sw.js`.**
Without it the service worker keeps serving the old cached JS and your change never
reaches users, which looks exactly like a broken deploy.

Equally important: every path in the `STATIC` array must exist. `cache.addAll`
rejects on a single 404, which fails the `install` event, so the new worker never
activates and users stay pinned to the old version indefinitely. Add a file to the
array when you create it, and remove it when you delete it.

## Data sources and failure modes

- **Marca** — HTML scraping, so any layout change on their side breaks it. Covers a few days ahead.
- **Premier Padel** — the `api-prod.premierpadel.com` JSON API. Their site is a JS-rendered SPA, so scraping the HTML returns nothing.

Both are third parties that fail. Every network call has a timeout, exception
handling and a fallback (stale cache or a controlled empty result) so a dead source
degrades the page instead of blanking it.

Caching is in-memory with a stale fallback: 15 minutes for the TV schedule, 1 hour
for the padel calendar, 5 minutes for live schedules. It is per-instance and dies on
every cold start. Optional Upstash Redis helpers exist behind
`KV_REST_API_URL`/`KV_REST_API_TOKEN`, but nothing consumes them right now.

## Deploying

Manual, from a local checkout — there is no CI.

```bash
vercel          # preview deploy, returns a URL
vercel --prod   # production
```

Smoke-test the preview URL before promoting: check that the endpoints return 200
with non-empty data, that `/api/ics` comes back as `text/calendar`, and that the
service worker being served carries the new cache version.

## Notes

There is no test suite and no CI. Verify changes by running the app and hitting the
endpoints with `curl`. See `CHANGELOG.md` for the change history.
