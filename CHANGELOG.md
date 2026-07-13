# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **World Cup knockout bracket (cuadro de la fase eliminatoria)** (`/api/wc/bracket`): once the group stage ends, a horizontally scrollable bracket shows the knockout rounds left→right as the tournament narrows (Dieciseisavos → Octavos → Cuartos → Semifinales → Final, plus a trailing Tercer puesto). Each matchup is a condensed card with flags, teams, score, kickoff date/time and a live/final badge; the winner of a finished tie is bolded, and live/finished matches open the same result sheet as the fixtures. Every round always renders at full width ("Por definir" placeholders fill unresolved slots) so the cuadro shows its shape from the moment the groups end. Knockout fixtures are classified by TheSportsDB's team-count round code (32→dieciseisavos, 16→octavos, 8→cuartos, 4→semis, 2→final) gated on the knockout start date — so a group matchday whose number collides with a code is never misfiled — with the official FIFA 2026 date windows as the authoritative in-phase classifier (they also separate the third-place match from the final). The block reuses the merged, persisted match registro (so scores stay live) and unions in later rounds beyond the fixtures window from the registro. Hidden until there's knockout data
- **Unidad Editorial as a World Cup fixture/result fallback when TheSportsDB rate-limits**: TheSportsDB's free tier 429s easily, which could leave a day (and the bracket) empty. Each day TheSportsDB fails to serve is now backfilled from Unidad Editorial's per-day events feed (`api.unidadeditorial.es/sports/v1/events/preset/…`, the source behind Marca's results page) — complete and unthrottled, with English team names mapped to TheSportsDB's spelling so it merges seamlessly by team pair + date. Best-effort and targeted (only the failed days), so a healthy fetch adds no extra requests; not-started matches don't surface a phantom 0-0, and a finished match's TheSportsDB id is recovered later so its detail sheet still opens
- **Live auto-refresh for the World Cup section**: the fixtures and cuadro now refresh themselves every 60s (visibility-aware — paused while the tab is backgrounded, and refreshed immediately on returning) so results settle as matches finish, without a manual refresh and without a spinner flash over the current view
- **Trimmed the day filter bar**: past days piled up as the tournament ran, so the shared day bar now shows only the 2 most recent past days plus today and every future day
- **Single full-page loader on initial load**: the World Cup, TV schedule and padel sections used to load independently, so the page flashed "no hay datos" in one section before another filled in. A full-page overlay now covers the app until all three initial fetches settle (coordinated in `index.html` with `Promise.allSettled`), then fades out — with a 15s safety net so a hung request never traps the page. The per-section fetches are no longer self-invoked; the coordinator orchestrates them
- **Marca as a World Cup fixture source (fills the missing matches)**: the free TheSportsDB tier returns only a few matches per day, so the calendar was dropping fixtures. Marca's TV grid lists the full week with kickoff times and channels and is now merged into the calendar as a first-class source (not just a channel lookup), unioned with TheSportsDB by unordered team pair + date. Spanish→English team-name reverse map with aliases (e.g. "RD del Congo")
- **World Cup registro desde el inicio**: the calendar window now reaches back to the tournament start, and each fetch is merged into a per-day record in Redis so the history accumulates — a Marca fixture is retained after it scrolls out of Marca's week and a played score upgrades the record in place
- **Match summary slider for played *and live* matches** (`/api/wc/match/{id}`): tapping a finished or in-play match opens a bottom sheet with the result, scorers (minute + assist), cards, and basic info (venue, city, spectators). Live matches show a "● EN JUEGO" indicator and the sheet auto-refreshes every 30s while open (and isn't cached server-side so the score keeps settling); polling stops once the match finishes. A finished match's timeline is immutable, so it's cached in Redis with no expiry — that's the per-match registro
- **Complete scorers/cards via API-Football**: TheSportsDB's free tier truncates its timeline to ~5 events (so a 2-2 showed only some goals), so the match detail now enriches the goals/cards/subs from API-Football via the `idAPIfootball` every TheSportsDB event carries. Configure `API_FOOTBALL_KEY` (and optionally `API_FOOTBALL_HOST` for RapidAPI) to enable; falls back to TheSportsDB's truncated timeline when unset or when the fixture is unknown. The slider shows a "Datos parciales" warning when the goals shown don't reconcile with the final score. Complete (API-Football) details are cached forever in Redis; partial fallbacks get a 6h TTL so they can be completed later
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

- **A finished knockout match TheSportsDB never published at all left the next round blank (e.g. Argentina 3-1 Suiza never appeared, while the Semifinal card already showed "Inglaterra vs Argentina")**: score backfill only ever *corrected* a match that already existed in the fetched data — a fixture TheSportsDB dropped entirely (not just slow to score) had nothing to patch, so Marca's real result was silently discarded. The Cuartos draw-resolution now also checks Marca's *finished* results (not just its upcoming pairings, which stop listing a tie once it's been played) to resolve a penalty-shootout winner, filtered to the following jornada so an unrelated earlier meeting between the same teams can't be mistaken for advancement. The synthesized next-round card itself now carries the real score/status from that Marca result instead of sitting as a blank "upcoming" placeholder
- **The recovered Cuartos card (e.g. Argentina-Suiza) wasn't clickable**: the bracket's own synthesized/backfilled matches never went through the id-recovery step that runs on regularly fetched matches, so a finished card built from Marca's result kept `match_id: None` and couldn't open its detail sheet. The bracket now runs that id recovery (`searchevents.php` by team pair + date) on its own rounds too, right after the score backfill
- **Knockout winners stuck undecided, blocking the next round from ever appearing (e.g. Argentina vs Suiza)**: TheSportsDB's free tier is slow to flip a finished match to "FT" and never exposes a penalty-shootout score, so a drawn knockout match (e.g. Suiza 0-0 Colombia) kept `winner: None` forever — which blocked the next-round pairing from being synthesized at all. Score backfill now also corrects a stale non-"finished" status once Marca confirms the result, and a new Marca "upcoming fixtures" scrape resolves penalty-shootout winners from Marca's own next-round pairing and supplies the real kickoff date/time instead of relying only on the static `WC_CONFIRMED_UPCOMING` fallback
- **World Cup data appearing stuck even after a backend fix**: the service worker cached `/api/wc/*` responses cache-first indefinitely instead of network-first, so a browser that had already cached a stale response would never re-fetch it. World Cup endpoints now use the same network-first strategy as the TV/padel APIs; static cache version bumped to flush existing clients
- **Cuartos bracket ordering paired the wrong Semifinal crosses**: the feeder-slot chain that orders every round only works by inheriting the previous round's order, with nothing anchoring Cuartos to the *official* bracket half — so Morocco-France/Argentina-Suiza ended up on the wrong side. The confirmed FIFA bracket half is now hardcoded per Cuartos pairing so the Semifinal crosses come out right (Francia vs España/Bélgica winner, Argentina/Suiza winner vs Noruega/Inglaterra winner)
- **A synthesized knockout placeholder (e.g. Morocco-France) stayed "upcoming" forever even after it was actually played**: placeholder crosses created for a pairing the live sources hadn't published a real fixture for never went through the score/winner backfill that runs on real fetched matches. Backfill now runs again after synthesis, then bracket alignment re-runs so a freshly-resolved winner can cascade into the next round
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
