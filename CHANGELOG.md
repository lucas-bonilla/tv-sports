# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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

### Fixed

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
