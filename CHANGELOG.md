# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Marca as a World Cup fixture source (fills the missing matches)**: the free TheSportsDB tier returns only a few matches per day, so the calendar was dropping fixtures. Marca's TV grid lists the full week with kickoff times and channels and is now merged into the calendar as a first-class source (not just a channel lookup), unioned with TheSportsDB by unordered team pair + date. Spanish→English team-name reverse map with aliases (e.g. "RD del Congo")
- **World Cup registro desde el inicio**: the calendar window now reaches back to the tournament start, and each fetch is merged into a per-day record in Redis so the history accumulates — a Marca fixture is retained after it scrolls out of Marca's week and a played score upgrades the record in place
- **Match summary slider for played matches** (`/api/wc/match/{id}`): tapping a finished match opens a bottom sheet with the result, scorers (minute + assist), cards, and basic info (venue, city, spectators) from TheSportsDB's `lookupevent`/`lookuptimeline`. A finished match's timeline is immutable, so it's cached in Redis with no expiry — that's the per-match registro
- **Shared cache via Upstash Redis (optional)**: Vercel's per-instance in-memory caches die on cold start; when `KV_REST_API_URL`/`KV_REST_API_TOKEN` are configured, the World Cup calendar/standings/match-details use Redis as a shared cache and historical store. Degrades gracefully to in-memory/live when Redis isn't configured (no new dependency — uses the Upstash REST API over `requests`)
- **World Cup kickoff times in Spanish time**: fixtures from TheSportsDB (UTC) are converted to Europe/Madrid, recomputing the day too so a kickoff that crosses midnight is filed under the correct local date
- **Broadcasting channel on World Cup matches**: each fixture is cross-referenced against Marca's TV schedule by date + team names (EN→ES name map) to attach the channel when it's listed; shown on the match card and used when adding to the calendar
- **Padel tracking** via the Premier Padel (Qatar Airways Premier Padel Tour) API:
  - `/api/padel/tournaments` — season calendar of all tournaments with normalized status (live/upcoming/finished), category, country, dates, and prize money
  - `/api/padel/schedule?slug=<slug>` — order of play for a tournament, grouped by day then court, with teams, set scores, round, and live/completed status
  - New "Pádel" tab in the header that toggles between the TV schedule and padel. The calendar groups tournaments by status; when a tournament is live it opens straight into its schedule
  - Data comes from the `api-prod.premierpadel.com` JSON API directly (the site is a JS-rendered SPA, so HTML scraping returns nothing)
  - Separate caches: 1h TTL for the calendar, 5min TTL for live schedules, both with stale fallback on failure
  - Service worker: padel API calls are network-first (so live scores stay fresh) and `padel.js` is added to the offline static cache
- `/api/ics` backend endpoint that serves `.ics` files with proper `text/calendar` content type
- On iOS, "Añadir al calendario" is now a direct link to `/api/ics` — skips the share sheet and permission prompt, going straight to the native calendar add screen
- Server-side in-memory cache with 15-minute TTL and stale fallback on scrape failure
- Service worker now caches API responses and serves them offline (503 with cached data and a clear user message)
- SW static cache key changed from `Date.now()` to a deterministic version string to prevent unnecessary cache busting
- Race condition protection in Python cache under concurrent requests (asyncio.Lock with double-check pattern)

### Changed

- Filter rows (date + sport) are now grouped into a single rounded card with a divider between rows; filter chips have refined resting/hover/active states
- Removed the redundant "📺 Programación TV" section heading — the page header already identifies the app (reclaims vertical space, notably on iOS)
- TV schedule now defaults to showing **all** events when no day is selected; picking a day/sport filters to that selection (previously defaulted to today)

### Fixed

- **Missing World Cup matches (whole days were dropped, then finished games disappeared)**: no single free-tier TheSportsDB source is complete — `eventsday.php` returns a day's scheduled slate but omits some finished games (e.g. Sweden–Tunisia drops out once it ends), while `eventspastleague`/`eventsseason` carry the results but are truncated to a handful of events. Fixtures are now collected by unioning the per-UTC-day `eventsday.php` calls with the recent-results + season endpoints, deduped by *logical match identity* (home/away/round) rather than event id — the API reuses different ids for a fixture's scheduled vs played records — keeping the most informative copy (finished > live > not-started). Best-effort per source
- **World Cup calendar window**: the fixtures view now spans yesterday → next week (today + `WC_DAYS_AHEAD`, default 8 days, Europe/Madrid local dates) instead of the whole tournament archive. Standings scan separately from `WC_SEASON_START` through today so every finished group match still counts
- **World Cup group draw was hand-entered and wrong**: the 12 groups (A–L) are now the official FIFA draw, verified against the round-1 fixtures returned by the API. This also fixes group-stage results being dropped from the standings (e.g. South Korea–Czech Republic) when the two teams were pinned to different groups
- **World Cup team names now display in Spanish** in both fixtures and standings (was showing TheSportsDB's English names); internal English keys are kept for group membership and Marca channel cross-reference. Added Iraq/Algeria (missing flag + translation) and fixed "RD Congo"
- Refresh button now also refreshes the padel section; previously the iOS shortcut had to be closed and reopened to get fresh padel data

- iOS calendar flow no longer triggers a two-step share sheet + permission dialog; now opens the native "Add to Calendar" screen in one tap
- Fire-and-forget SW cache writes are now awaited with proper error handling
- `activeDateFilter` now uses `null` for "not yet set" so a page refresh no longer overrides an explicit "Todos" selection

## [1.2.0] - 2026-03-12

### Added
- Date filter bar to navigate between days; "Todos" view groups events by day
- Collapsible sport groups with chevron toggle
- Quick-nav FAB: navigates by day in multi-day view, by sport in single-day view
- Back-to-top button
- Service worker cache auto-invalidation (replaces manual version bumps)

### Fixed
- Wrong date parsing: marca.com day-name label had no space before the day number
- Today's events were showing the full week instead of just today (daylist-first HTML parsing)
- Duplicate events caused by the full-week dump block being parsed alongside per-day blocks
- `activeDateFilter` was storing the display label instead of the value, breaking the "Hoy" active state

## [1.1.0] - 2026-03-10

### Added
- Drag-to-reorder sport filter chips on both desktop (mouse) and mobile (touch)
- Filter chip order persisted to localStorage across sessions

## [1.0.0] - 2026-03-10

### Added
- Initial Vercel deployment
- FastAPI backend scraping sport events from marca.com
- Vanilla JS/CSS/HTML progressive web app (PWA)
