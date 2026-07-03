from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import asyncio
import json
import logging
import os
import time
import unicodedata

# All user-facing schedules are for a Spanish audience, so kickoff times and the
# day a match is filed under are expressed in Europe/Madrid (handles CEST/CET DST).
MADRID_TZ = ZoneInfo("Europe/Madrid")


def _strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Shared cache (Upstash Redis REST, optional) ---
#
# Vercel runs this as stateless functions, so the in-memory caches further down
# are per-instance and die on every cold start — never shared between them. When
# an Upstash Redis integration is provisioned (KV_REST_API_URL/TOKEN, the names
# Vercel's Upstash Marketplace integration injects) we use it as a *shared* cache
# across instances and as the historical match-detail store. It degrades
# gracefully: with no Redis configured every helper is a no-op and callers fall
# back to the in-memory caches / live fetches.

KV_URL = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
KV_TOKEN = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")


def kv_enabled() -> bool:
    return bool(KV_URL and KV_TOKEN)


def _kv_cmd(command: list):
    """Run one Upstash REST command; return its 'result' or None on any error.

    Upstash's REST API takes a command as a JSON array (e.g. ["GET", key]) posted
    to the base URL. A short timeout and broad except keep a flaky cache from ever
    blocking a request.
    """
    if not kv_enabled():
        return None
    try:
        resp = requests.post(
            KV_URL,
            json=command,
            headers={"Authorization": f"Bearer {KV_TOKEN}"},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json().get("result")
    except Exception as e:
        logger.warning(f"Redis {command[0]} failed: {e}")
        return None


def kv_get_json(key: str):
    raw = _kv_cmd(["GET", key])
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def kv_set_json(key: str, value, ttl: int | None = None) -> None:
    """Store JSON under key. ttl=None means no expiry (immutable records)."""
    cmd = ["SET", key, json.dumps(value, ensure_ascii=False)]
    if ttl:
        cmd += ["EX", str(ttl)]
    _kv_cmd(cmd)


# --- Scraper ---

MARCA_URL = "https://www.marca.com/programacion-tv.html"
# Marca's World Cup calendar carries *final scores* per matchday (the TV grid
# above does not). Used as the score backup when TheSportsDB's free tier omits a
# played game. See _wc_marca_results().
MARCA_WC_CALENDAR_URL = "https://www.marca.com/futbol/mundial/calendario.html"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

SPORT_EMOJIS = {
    "baloncesto": "🏀",
    "futbol": "⚽",
    "tenis": "🎾",
    "ciclismo": "🚴",
    "atletismo": "🏃",
    "natacion": "🏊",
    "motor": "🏎️",
    "rugby": "🏉",
    "balonmano": "🤾",
    "golf": "⛳",
    "boxeo": "🥊",
    "triatlon": "🏋️",
    "voleibol": "🏐",
    "padel": "🎾",
    "hockey": "🏑",
}


def get_sport_emoji(icon_class: str) -> str:
    for key, emoji in SPORT_EMOJIS.items():
        if key in icon_class.lower():
            return emoji
    return "🏅"


def _format_label(label) -> str:
    strong = label.find("strong")
    if strong:
        rest = label.get_text(strip=True).replace(strong.get_text(strip=True), "").strip()
        return f"{strong.get_text(strip=True)} {rest}".strip()
    return label.get_text(strip=True)


def _parse_events(day_block, date_str: str) -> list:
    events = []
    for li in day_block.select("li.dailyevent"):
        sport_icon_el = li.select_one("i[class*='icon-']")
        icon_class = sport_icon_el["class"][0] if sport_icon_el else ""

        event = {
            "date": date_str,
            "sport": li.select_one(".dailyday").get_text(strip=True) if li.select_one(".dailyday") else "",
            "time": li.select_one(".dailyhour").get_text(strip=True) if li.select_one(".dailyhour") else "",
            "competition": li.select_one(".dailycompetition").get_text(strip=True) if li.select_one(".dailycompetition") else "",
            "match": li.select_one(".dailyteams").get_text(strip=True) if li.select_one(".dailyteams") else "",
            "channel": li.select_one(".dailychannel").get_text(strip=True) if li.select_one(".dailychannel") else "",
            "emoji": get_sport_emoji(icon_class),
        }
        events.append(event)
    return events


def scrape_events() -> dict:
    response = requests.get(MARCA_URL, headers=HEADERS, timeout=10)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    now = datetime.now()
    today_day = now.day

    events = []
    today_date_str = None

    # The daylist carousel has correctly split per-day blocks — use those as the source of truth.
    # The non-daylist block labeled "today" is a full-week dump and must be skipped.
    daylist_dates = set()
    for day_block in soup.select("ol.daylist li.content-item"):
        label = day_block.select_one("span.title-section-widget")
        if not label or not day_block.select("li.dailyevent"):
            continue
        date_str = _format_label(label)
        daylist_dates.add(date_str)
        day_events = _parse_events(day_block, date_str)
        events.extend(day_events)
        if f"{today_day} de" in label.get_text(strip=True) and today_date_str is None:
            today_date_str = date_str

    # If today is not in daylist (edge case), fall back to the main block
    if today_date_str is None:
        for day_block in soup.select("li.content-item"):
            if day_block.parent and "daylist" in (day_block.parent.get("class") or []):
                continue
            label = day_block.select_one("span.title-section-widget")
            if not label or not day_block.select("li.dailyevent"):
                continue
            if f"{today_day} de" not in label.get_text(strip=True):
                continue
            date_str = _format_label(label)
            events.extend(_parse_events(day_block, date_str))
            today_date_str = date_str
            break

    return {
        "date": today_date_str or (events[0]["date"] if events else ""),
        "events": events,
        "scraped_at": datetime.now().isoformat(),
    }


# --- Premier Padel client ---
#
# premierpadel.com is a Next.js SPA that loads everything from a JSON API at
# api-prod.premierpadel.com, so we hit that API directly instead of scraping the
# (empty) server-rendered HTML.

PADEL_API = "https://api-prod.premierpadel.com/api"

PADEL_HEADERS = {**HEADERS, "Content-Type": "application/json"}

PADEL_TOURNAMENT_STATUS = {"U": "upcoming", "P": "live", "F": "finished", "C": "finished"}

PADEL_CATEGORY_EMOJI = {"MAJOR": "🏆", "P1": "🥇", "P2": "🥈", "FINALS": "👑"}


def _padel_post(path: str, body: dict) -> dict:
    resp = requests.post(f"{PADEL_API}{path}", json=body, headers=PADEL_HEADERS, timeout=10)
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("status"):
        raise RuntimeError(payload.get("message", "Premier Padel API error"))
    return payload.get("data")


def _padel_parse_utc(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _padel_tournament_status(t: dict) -> str:
    raw = (t.get("status") or "").upper()
    if raw in PADEL_TOURNAMENT_STATUS:
        return PADEL_TOURNAMENT_STATUS[raw]
    now = datetime.now(timezone.utc)
    start = _padel_parse_utc(t.get("start_date_utc"))
    end = _padel_parse_utc(t.get("end_date_utc"))
    if start and end:
        if now < start:
            return "upcoming"
        if now > end:
            return "finished"
        return "live"
    return "upcoming"


def get_padel_tournaments(year: int | None = None) -> dict:
    year = year or datetime.now(timezone.utc).year
    raw = _padel_post("/tournament/getTournaments", {"year": year, "type": "ALL"})

    tournaments = []
    live_slug = None
    for t in raw or []:
        status = _padel_tournament_status(t)
        category = (t.get("type") or "").upper()
        tournaments.append({
            "slug": t.get("slug"),
            "name": t.get("display_name") or t.get("name") or t.get("full_name"),
            "country": t.get("country"),
            "city": t.get("city"),
            "category": category,
            "emoji": PADEL_CATEGORY_EMOJI.get(category, "🎾"),
            "flag_url": t.get("flag_url"),
            "start_date": t.get("start_date_utc"),
            "end_date": t.get("end_date_utc"),
            "prize_money": t.get("prize_money"),
            "status": status,
            "ticket_url": t.get("ticket_url"),
            "where_to_watch_url": t.get("where_to_watch_url"),
        })
        if status == "live" and live_slug is None:
            live_slug = t.get("slug")

    tournaments.sort(key=lambda x: x.get("start_date") or "")
    return {
        "year": year,
        "live_slug": live_slug,
        "tournaments": tournaments,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _padel_player_name(player: dict) -> str:
    first = (player.get("first_name") or "").strip()
    last = (player.get("last_name") or "").strip()
    return f"{first} {last}".strip() or "TBD"


def _padel_team_label(team: dict) -> str:
    names = [_padel_player_name(p) for p in (team.get("players") or [])]
    return " / ".join(n for n in names if n) or "TBD"


def _padel_score_label(team: dict) -> str:
    score = team.get("score") or {}
    sets = []
    for i in range(1, 6):
        s = score.get(f"set{i}")
        if s is None:
            continue
        tie = score.get(f"tie{i}")
        sets.append(f"{s}({tie})" if tie is not None and tie >= 0 else str(s))
    return " ".join(sets)


import re

_TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([AP]M)?", re.IGNORECASE)


def _to_24h(hour: int, minute: int, ampm: str | None) -> str:
    if ampm:
        ampm = ampm.upper()
        if ampm == "PM" and hour != 12:
            hour += 12
        elif ampm == "AM" and hour == 12:
            hour = 0
    return f"{hour:02d}:{minute:02d}"


def _padel_slot(header: str, court_start: str, status: str | None):
    """Map a match `header` to a display label + a sort key.

    Returns (label, sort_key). sort_key is a tuple (bucket, time) so within a
    court: opener (0) < timed "not before" (1, time) < followed-by (2).
    Played/live matches keep their natural place via the same buckets.
    """
    h = (header or "").strip()
    low = h.lower()

    if low.startswith("starting at") or low.startswith("a partir"):
        m = _TIME_RE.search(h)
        t = _to_24h(int(m.group(1)), int(m.group(2) or 0), m.group(3)) if m else court_start
        return (f"Desde {t}", (0, t))

    if low.startswith("not before") or low.startswith("no antes"):
        m = _TIME_RE.search(h)
        if m:
            t = _to_24h(int(m.group(1)), int(m.group(2) or 0), m.group(3))
            return (f"No antes de {t}", (1, t))
        return ("No antes de", (1, "99:99"))

    if low.startswith("followed by") or low.startswith("a continua"):
        return ("A continuación", (2, ""))

    # Fallbacks: an explicit court start with no header, or anything unknown.
    if court_start:
        return (f"Desde {court_start}", (0, court_start))
    return (h or "", (2, ""))


def get_padel_schedule(slug: str) -> dict:
    raw = _padel_post("/tournament/getTournamentMatches", {"slug": slug})
    courts = raw.get("courts") or []

    days: dict = {}
    for court in courts:
        court_name = (court.get("court_name") or "").strip()
        court_start = (court.get("start_of_play") or "")[:5]  # "HH:MM"
        # Padel doesn't schedule each match at a clock time — only the court's
        # start, then matches run back-to-back. `start_time` is therefore the
        # same (the court start) for every match and is misleading to show
        # per-match. The real timing lives in `header`:
        #   "Starting at 11:00 AM" -> the court opener (one per court)
        #   "Not before 7:00 PM"   -> guaranteed earliest start
        #   "Followed by"          -> next available, no fixed time
        for m in court.get("matches") or []:
            teams = m.get("teams") or []
            team_a = teams[0] if len(teams) > 0 else {}
            team_b = teams[1] if len(teams) > 1 else {}
            # winner_id holds the winning team_no (1 or 2); "0" means undecided.
            winner_no = str(m.get("winner_id") or "0")
            header = (m.get("header") or "").strip()
            slot, slot_sort = _padel_slot(header, court_start, m.get("status"))
            match = {
                "match_id": m.get("match_id"),
                "court": court_name,
                "court_start": court_start,
                "date": m.get("date"),
                # `slot` is the human timing label shown on the card; `slot_sort`
                # orders the order-of-play within a court.
                "slot": slot,
                "slot_sort": slot_sort,
                "round": m.get("round_name") or m.get("current_round"),
                "draw_type": m.get("draw_type"),
                "status": m.get("status"),
                "status_title": m.get("status_title"),
                "team_a": _padel_team_label(team_a),
                "team_b": _padel_team_label(team_b),
                "score_a": _padel_score_label(team_a),
                "score_b": _padel_score_label(team_b),
                "winner": "a" if winner_no == "1" else "b" if winner_no == "2" else None,
                "broadcast_url": m.get("broadcast_url"),
            }
            days.setdefault(match["date"] or "?", []).append(match)

    # Group by court (in court-start order), and within a court order by the
    # reconstructed order of play: opener first, then "not before" anchors by
    # time, then the remaining "followed by" matches.
    for day_matches in days.values():
        day_matches.sort(key=lambda x: (
            x.get("court_start") or "99:99",
            x.get("court") or "",
            x.get("slot_sort", (5, "")),
        ))

    return {
        "slug": slug,
        "type": raw.get("type"),
        "status": raw.get("status"),
        "days": [{"date": d, "matches": days[d]} for d in sorted(days.keys())],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# --- FIFA World Cup client (TheSportsDB) ---
#
# TheSportsDB exposes the World Cup (league id 4429) for free. The test key "3"
# works but is rate-limited; override it with THESPORTSDB_KEY in production.
# Unlike Premier Padel, the season endpoint already carries played scores, so the
# same payload serves both the upcoming fixtures and the results of past days.

WC_KEY = os.environ.get("THESPORTSDB_KEY", "3")
WC_API = f"https://www.thesportsdb.com/api/v1/json/{WC_KEY}"
WC_LEAGUE_ID = "4429"  # FIFA World Cup
WC_SEASON = os.environ.get("WC_SEASON", "2026")

# API-Football (api-sports.io) — used to enrich a match's detail with the full
# goals/cards/subs list, because TheSportsDB's free tier truncates its timeline to
# ~5 events. We reach it via the idAPIfootball every TheSportsDB event carries.
# Configure API_FOOTBALL_KEY to enable; defaults target the direct api-sports.io
# host (header x-apisports-key). Set API_FOOTBALL_HOST to a *.rapidapi.com host to
# go through RapidAPI instead (the client switches to the x-rapidapi-* headers).
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY", "")
API_FOOTBALL_HOST = os.environ.get("API_FOOTBALL_HOST", "v3.football.api-sports.io")


# Map TheSportsDB national-team names to ISO-3166 alpha-2 codes so we can render
# a flag emoji instead of the federation crest. Covers every nation that can
# realistically appear at the 2026 World Cup (48 teams) plus common qualifiers.
WC_TEAM_ISO = {
    "Algeria": "DZ", "Argentina": "AR", "Australia": "AU", "Austria": "AT",
    "Belgium": "BE", "Bolivia": "BO", "Bosnia-Herzegovina": "BA", "Brazil": "BR",
    "Cameroon": "CM", "Canada": "CA", "Cape Verde": "CV", "Chile": "CL",
    "Colombia": "CO", "Costa Rica": "CR", "Croatia": "HR", "Curaçao": "CW",
    "Czech Republic": "CZ", "Denmark": "DK", "DR Congo": "CD", "Ecuador": "EC",
    "Egypt": "EG", "England": "GB-ENG", "France": "FR", "Germany": "DE",
    "Ghana": "GH", "Greece": "GR", "Haiti": "HT", "Honduras": "HN",
    "Hungary": "HU", "Iran": "IR", "Iraq": "IQ", "Italy": "IT",
    "Ivory Coast": "CI", "Jamaica": "JM",
    "Japan": "JP", "Jordan": "JO", "Mexico": "MX", "Morocco": "MA",
    "Netherlands": "NL", "New Zealand": "NZ", "Nigeria": "NG", "Norway": "NO",
    "Panama": "PA", "Paraguay": "PY", "Peru": "PE", "Poland": "PL",
    "Portugal": "PT", "Qatar": "QA", "Republic of Ireland": "IE", "Romania": "RO",
    "Saudi Arabia": "SA", "Scotland": "GB-SCT", "Senegal": "SN", "Serbia": "RS",
    "Slovakia": "SK", "Slovenia": "SI", "South Africa": "ZA", "South Korea": "KR",
    "Spain": "ES", "Sweden": "SE", "Switzerland": "CH", "Tunisia": "TN",
    "Turkey": "TR", "Ukraine": "UA", "Uruguay": "UY", "USA": "US",
    "Uzbekistan": "UZ", "Venezuela": "VE", "Wales": "GB-WLS",
}

# Regional-indicator flags only exist for 2-letter country codes; the home
# nations (England/Scotland/Wales) need their subdivision emoji instead.
WC_SUBDIVISION_FLAG = {
    "GB-ENG": "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F",  # 🏴 England
    "GB-SCT": "\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F",  # 🏴 Scotland
    "GB-WLS": "\U0001F3F4\U000E0067\U000E0062\U000E0077\U000E006C\U000E0073\U000E007F",  # 🏴 Wales
}


def _wc_flag(team: str | None) -> str:
    """Return a flag emoji for a national team, or '' if unknown."""
    iso = WC_TEAM_ISO.get((team or "").strip())
    if not iso:
        return ""
    if iso in WC_SUBDIVISION_FLAG:
        return WC_SUBDIVISION_FLAG[iso]
    # Two-letter code → regional indicator symbols (A=0x1F1E6).
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in iso)


# The free TheSportsDB tier doesn't expose the 2026 group draw, so we pin the
# 12 groups (A–L, 48 teams) here from the official FIFA draw and compute each
# table from played results. Team names must match TheSportsDB's spelling (see
# WC_TEAM_ISO keys) for the results to attach; an unmatched name simply won't
# appear in a table. Verified against the round-1 fixtures returned by the API.
WC_GROUPS = {
    "A": ["Mexico", "South Africa", "South Korea", "Czech Republic"],
    "B": ["Canada", "Bosnia-Herzegovina", "Qatar", "Switzerland"],
    "C": ["Brazil", "Morocco", "Haiti", "Scotland"],
    "D": ["USA", "Paraguay", "Australia", "Turkey"],
    "E": ["Germany", "Curaçao", "Ivory Coast", "Ecuador"],
    "F": ["Netherlands", "Japan", "Sweden", "Tunisia"],
    "G": ["Belgium", "Egypt", "Iran", "New Zealand"],
    "H": ["Spain", "Cape Verde", "Saudi Arabia", "Uruguay"],
    "I": ["France", "Senegal", "Iraq", "Norway"],
    "J": ["Argentina", "Algeria", "Austria", "Jordan"],
    "K": ["Portugal", "DR Congo", "Uzbekistan", "Colombia"],
    "L": ["England", "Croatia", "Ghana", "Panama"],
}

# Reverse lookup: team name -> group letter (last group wins on duplicates,
# which only matters for placeholder/qualifier names that repeat).
WC_TEAM_GROUP = {team: g for g, teams in WC_GROUPS.items() for team in teams}


def _wc_get(path: str, params: dict) -> dict:
    resp = requests.get(f"{WC_API}{path}", params=params, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json() or {}


def _wc_int(value):
    """Coerce TheSportsDB's stringly-typed numbers, tolerating None/''."""
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _wc_match_status(ev: dict, home_score, away_score) -> str:
    """Normalize a fixture to upcoming / live / finished.

    `strStatus` is "NS" (not started), "Match Finished"/"FT", or a live code
    (e.g. "1H", "2H", "HT"). Fall back to scores + kickoff time when it's blank.
    """
    raw = (ev.get("strStatus") or "").strip().upper()
    if raw in ("FT", "MATCH FINISHED", "AET", "PEN"):
        return "finished"
    if raw in ("1H", "2H", "HT", "ET", "LIVE", "P"):
        return "live"
    if raw == "NS":
        return "upcoming"

    # No usable status: infer from scores, then from kickoff time.
    if home_score is not None and away_score is not None:
        return "finished"
    ts = _padel_parse_utc(ev.get("strTimestamp"))
    if ts:
        # strTimestamp is naive UTC-ish; compare against now in UTC.
        now = datetime.now(timezone.utc)
        return "upcoming" if ts.replace(tzinfo=timezone.utc) > now else "finished"
    return "upcoming"


def _wc_local_datetime(ev: dict) -> tuple:
    """Convert a fixture's kickoff to Europe/Madrid.

    TheSportsDB reports dateEvent/strTime in UTC and also gives strTimestamp.
    We anchor on the UTC instant and re-express it in Madrid time, which can roll
    the local date forward/back (a 23:00 UTC kickoff is 01:00 next day in Spain).
    Returns (date 'YYYY-MM-DD', time 'HH:MM'); falls back to the raw UTC values if
    no timestamp is available.
    """
    raw_date = ev.get("dateEvent")
    raw_time = (ev.get("strTime") or "")[:5]

    ts = _padel_parse_utc(ev.get("strTimestamp"))
    if ts is None and raw_date and raw_time:
        try:
            ts = datetime.fromisoformat(f"{raw_date}T{raw_time}:00")
        except ValueError:
            ts = None
    if ts is None:
        return raw_date, raw_time

    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    local = ts.astimezone(MADRID_TZ)
    return local.strftime("%Y-%m-%d"), local.strftime("%H:%M")


def _wc_normalize_match(ev: dict) -> dict:
    home_en = ev.get("strHomeTeam")
    away_en = ev.get("strAwayTeam")
    home_score = _wc_int(ev.get("intHomeScore"))
    away_score = _wc_int(ev.get("intAwayScore"))
    status = _wc_match_status(ev, home_score, away_score)
    local_date, local_time = _wc_local_datetime(ev)
    return {
        "match_id": ev.get("idEvent"),
        "date": local_date,
        "time": local_time,  # "HH:MM" in Europe/Madrid
        "timestamp": ev.get("strTimestamp"),
        "round": _wc_int(ev.get("intRound")),
        # Display names in Spanish; *_en keep TheSportsDB's English spelling for
        # internal lookups (group membership, Marca channel cross-reference).
        "home": WC_TEAM_ES.get(home_en, home_en),
        "away": WC_TEAM_ES.get(away_en, away_en),
        "home_en": home_en,
        "away_en": away_en,
        "home_flag": _wc_flag(home_en),
        "away_flag": _wc_flag(away_en),
        "home_score": home_score,
        "away_score": away_score,
        "status": status,
        "venue": ev.get("strVenue"),
        "country": ev.get("strCountry"),
        "postponed": (ev.get("strPostponed") or "").lower() == "yes",
        "channel": None,  # filled from Marca's TV schedule when the match is listed
        "source": "thesportsdb",
    }


# TheSportsDB names teams in English; Marca's TV schedule uses Spanish. Map the
# nations so we can cross-reference the two and attach the broadcasting channel.
WC_TEAM_ES = {
    "Algeria": "Argelia", "Argentina": "Argentina", "Australia": "Australia",
    "Austria": "Austria", "Belgium": "Bélgica", "Bolivia": "Bolivia",
    "Bosnia-Herzegovina": "Bosnia", "Brazil": "Brasil", "Cameroon": "Camerún",
    "Canada": "Canadá", "Cape Verde": "Cabo Verde", "Chile": "Chile",
    "Colombia": "Colombia", "Costa Rica": "Costa Rica", "Croatia": "Croacia",
    "Curaçao": "Curazao", "Czech Republic": "República Checa", "Denmark": "Dinamarca",
    "DR Congo": "RD Congo", "Ecuador": "Ecuador", "Egypt": "Egipto",
    "England": "Inglaterra", "France": "Francia", "Germany": "Alemania",
    "Ghana": "Ghana", "Greece": "Grecia", "Haiti": "Haití", "Honduras": "Honduras",
    "Hungary": "Hungría", "Iran": "Irán", "Iraq": "Irak", "Italy": "Italia",
    "Ivory Coast": "Costa de Marfil", "Jamaica": "Jamaica",
    "Japan": "Japón", "Jordan": "Jordania", "Mexico": "México", "Morocco": "Marruecos",
    "Netherlands": "Países Bajos", "New Zealand": "Nueva Zelanda", "Nigeria": "Nigeria",
    "Norway": "Noruega", "Panama": "Panamá", "Paraguay": "Paraguay", "Peru": "Perú",
    "Poland": "Polonia", "Portugal": "Portugal", "Qatar": "Catar",
    "Republic of Ireland": "Irlanda", "Romania": "Rumanía", "Saudi Arabia": "Arabia Saudí",
    "Scotland": "Escocia", "Senegal": "Senegal", "Serbia": "Serbia",
    "Slovakia": "Eslovaquia", "Slovenia": "Eslovenia", "South Africa": "Sudáfrica",
    "South Korea": "Corea del Sur", "Spain": "España", "Sweden": "Suecia",
    "Switzerland": "Suiza", "Tunisia": "Túnez", "Turkey": "Turquía",
    "Ukraine": "Ucrania", "Uruguay": "Uruguay", "USA": "Estados Unidos",
    "Uzbekistan": "Uzbekistán", "Venezuela": "Venezuela", "Wales": "Gales",
}


def _es_date_to_iso(date_str: str) -> str | None:
    """Turn a Marca Spanish date label ('Jueves 11 de Junio') into 'YYYY-MM-DD'."""
    parts = (date_str or "").lower().split()
    try:
        day = int(parts[1])
        month = MONTHS_ES.get(parts[3])
        year = int(parts[5]) if len(parts) >= 6 else datetime.now().year
    except (IndexError, ValueError):
        return None
    if not month:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


# Reverse of WC_TEAM_ES (accent-insensitive) so we can turn Marca's Spanish match
# labels back into TheSportsDB's English names. A few Marca spellings don't round
# trip cleanly ("RD del Congo" vs our "RD Congo"), so patch those explicitly.
WC_ES_EN = {_strip_accents(es): en for en, es in WC_TEAM_ES.items()}
WC_ES_EN.update({
    "rd del congo": "DR Congo",
    "republica checa": "Czech Republic",
})


def _wc_team_pair(home_en, away_en) -> frozenset:
    """Unordered identity for a fixture, so a match survives home/away swaps
    between Marca and TheSportsDB."""
    return frozenset((
        (home_en or "").strip().lower(),
        (away_en or "").strip().lower(),
    ))


def _wc_marca_match(ev: dict) -> dict | None:
    """Parse one Marca World Cup listing into English team names + iso date.

    Returns {date, time, home_en, away_en, channel} or None when the row isn't a
    two-team match or a side can't be mapped to TheSportsDB's naming.
    """
    text = ev.get("match") or ""
    if " - " not in text:
        return None
    home_es, away_es = (s.strip() for s in text.split(" - ", 1))
    home_en = WC_ES_EN.get(_strip_accents(home_es))
    away_en = WC_ES_EN.get(_strip_accents(away_es))
    iso = _es_date_to_iso(ev.get("date"))
    if not (home_en and away_en and iso):
        return None
    return {
        "date": iso,
        "time": (ev.get("time") or "").strip()[:5],
        "home_en": home_en,
        "away_en": away_en,
        "channel": (ev.get("channel") or "").strip() or None,
    }


def _wc_marca_fixtures() -> list:
    """Scrape Marca's TV schedule and return its World Cup matches (English names).

    Marca lists the *full* week with kickoff times and channels — far more
    complete than the free TheSportsDB tier, which returns only a few matches per
    day — so it doubles as a fixture source, not just a channel lookup. Reuses the
    warm /api/events cache to avoid a second scrape. A failure is non-fatal.
    """
    now = time.time()
    if _cache["data"] and (now - _cache["ts"]) < CACHE_TTL:
        data = _cache["data"]
    else:
        try:
            data = scrape_events()
        except Exception as e:
            logger.warning(f"Marca scrape for World Cup failed: {e}")
            return []

    out = []
    for ev in data.get("events", []):
        if ev.get("emoji") != "⚽":
            continue
        # Marca labels the World Cup "Campeonato del Mundo" (and sometimes
        # "Mundial"); "mund" matches both while still excluding club football.
        if "mund" not in _strip_accents(ev.get("competition")):
            continue
        m = _wc_marca_match(ev)
        if m:
            out.append(m)
    return out


def _wc_marca_channel(m: dict, fixtures: list) -> str | None:
    """Find a match's channel among the parsed Marca fixtures (date + team pair)."""
    if not fixtures or not m.get("date"):
        return None
    pair = _wc_team_pair(m.get("home_en"), m.get("away_en"))
    for f in fixtures:
        if f["date"] == m["date"] and _wc_team_pair(f["home_en"], f["away_en"]) == pair:
            return f.get("channel")
    return None


def _wc_match_from_marca(f: dict) -> dict:
    """Build a normalized (upcoming) match record from a Marca-only fixture —
    one TheSportsDB didn't return. No id/score; it's a not-yet-played match."""
    home_en, away_en = f["home_en"], f["away_en"]
    return {
        "match_id": None,
        "date": f["date"],
        "time": f.get("time") or "",
        "timestamp": None,
        "round": None,  # Marca doesn't expose the matchday
        "home": WC_TEAM_ES.get(home_en, home_en),
        "away": WC_TEAM_ES.get(away_en, away_en),
        "home_en": home_en,
        "away_en": away_en,
        "home_flag": _wc_flag(home_en),
        "away_flag": _wc_flag(away_en),
        "home_score": None,
        "away_score": None,
        "status": "upcoming",
        "venue": None,
        "country": None,
        "postponed": False,
        "channel": f.get("channel"),
        "source": "marca",
    }


_WC_SCORE_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")


def _wc_marca_results() -> list:
    """Scrape Marca's World Cup calendar for *played* results (home/away/score).

    The score backup: TheSportsDB's free tier truncates finished games (e.g. it
    never returns Uruguay–Cape Verde), but Marca's calendario.html lists every
    matchday's final score in a per-jornada table. Cached on its own TTL and fully
    best-effort — any failure returns the last good copy (or []), so it can never
    sink the primary fetch. Team names are Marca's Spanish spellings, mapped back
    to TheSportsDB's English via WC_ES_EN so the records line up with the rest.
    """
    now = time.time()
    cache = _wc_marca_results_cache
    if cache["data"] is not None and (now - cache["ts"]) < WC_MATCHES_TTL:
        return cache["data"]

    try:
        resp = requests.get(MARCA_WC_CALENDAR_URL, headers=HEADERS, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Marca World Cup calendar fetch failed: {e}")
        return cache["data"] or []

    soup = BeautifulSoup(resp.text, "html.parser")
    out: list = []
    for table in soup.select("table.jor.agendas"):
        # Table id is "jornadaN"; the matchday doubles as the fixture's round.
        rnd = _wc_int((table.get("id") or "").lower().replace("jornada", ""))
        for tr in table.select("tbody tr"):
            score_el = tr.select_one("td.resultado .resultado-partido")
            if not score_el:
                continue  # upcoming row: shows fecha/hora instead of a score
            m = _WC_SCORE_RE.match(score_el.get_text(strip=True))
            if not m:
                continue
            local = tr.select_one("td.local img")
            visit = tr.select_one("td.visitante img")
            home_en = WC_ES_EN.get(_strip_accents((local.get("alt") if local else "") or ""))
            away_en = WC_ES_EN.get(_strip_accents((visit.get("alt") if visit else "") or ""))
            if not (home_en and away_en):
                continue  # a team we don't map; skip rather than guess
            out.append({
                "home_en": home_en,
                "away_en": away_en,
                "round": rnd,
                "home_score": int(m.group(1)),
                "away_score": int(m.group(2)),
            })

    cache["data"] = out
    cache["ts"] = now
    return out


def _wc_kickoff_passed(m: dict) -> bool:
    """True when a fixture's Madrid kickoff is in the past — i.e. it should carry
    a result by now. Used to decide a missing-score match is worth backfilling."""
    d, t = m.get("date"), m.get("time")
    if not d:
        return False
    try:
        dt = datetime.strptime(f"{d} {t or '00:00'}", "%Y-%m-%d %H:%M")
    except ValueError:
        return False
    return dt.replace(tzinfo=MADRID_TZ) <= datetime.now(MADRID_TZ)


def _wc_backfill_scores(fresh: dict) -> None:
    """Fill scores TheSportsDB didn't provide from Marca's calendar (in place).

    Stays a genuine fallback: Marca's results page is only fetched when at least
    one already-kicked-off match is still missing a score, so the steady state
    (TheSportsDB healthy) adds no extra request. Backfilled scores flow into the
    persisted registro like any other, so they survive once written.
    """
    pending = [m for ms in fresh.values() for m in ms
               if m.get("home_score") is None and _wc_kickoff_passed(m)]
    if not pending:
        return

    by_pair = {}
    for r in _wc_marca_results():
        by_pair[_wc_team_pair(r["home_en"], r["away_en"])] = r

    for m in pending:
        r = by_pair.get(_wc_team_pair(m.get("home_en"), m.get("away_en")))
        if not r:
            continue
        # Align Marca's home/away orientation to this fixture's (sources can swap).
        if (m.get("home_en") or "").strip().lower() == (r["home_en"] or "").strip().lower():
            m["home_score"], m["away_score"] = r["home_score"], r["away_score"]
        else:
            m["home_score"], m["away_score"] = r["away_score"], r["home_score"]
        if m.get("round") is None:
            m["round"] = r["round"]
        if m.get("status") != "live":
            m["status"] = "finished"
        m["source"] = f"{m.get('source') or ''}+marca_result"


def _wc_search_event_id(home_en: str, away_en: str, date_iso: str | None) -> str | None:
    """Recover a fixture's TheSportsDB idEvent via searchevents.php.

    TheSportsDB's free *listing* endpoints (eventsday/eventsround/eventsseason) are
    truncated to ~5 events, so a finished match they omit reaches us from Marca with
    no id — and without an id the card can't open its goals/cards detail. The
    per-event search endpoint is NOT truncated, so we use it to fill that id back
    in. Orientation can differ between sources, so we try both home/away spellings
    and match on the World Cup league + date. Best-effort: any failure returns None.
    """
    pair = _wc_team_pair(home_en, away_en)
    for h, a in ((home_en, away_en), (away_en, home_en)):
        try:
            data = _wc_get("/searchevents.php", {"e": f"{h}_vs_{a}", "s": WC_SEASON})
        except Exception as e:
            logger.warning(f"World Cup searchevents {h} vs {a} failed: {e}")
            continue
        for ev in data.get("event") or data.get("events") or []:
            if str(ev.get("idLeague")) != WC_LEAGUE_ID:
                continue
            if _wc_team_pair(ev.get("strHomeTeam"), ev.get("strAwayTeam")) != pair:
                continue
            if date_iso and ev.get("dateEvent") and ev["dateEvent"] != date_iso:
                # Tolerate the Madrid/UTC date offset of ±1 day before rejecting.
                try:
                    delta = abs((datetime.fromisoformat(ev["dateEvent"]).date()
                                 - datetime.fromisoformat(date_iso).date()).days)
                except ValueError:
                    delta = 0
                if delta > 1:
                    continue
            return ev.get("idEvent")
    return None


def _wc_backfill_event_ids(fresh: dict) -> None:
    """Fill missing TheSportsDB ids on finished matches so their detail can open.

    Only finished fixtures still lacking a match_id are searched (Marca-only
    records that TheSportsDB's truncated listings dropped), so a healthy fetch adds
    no extra requests. The recovered id flows into the persisted registro, so the
    lookup happens once per match and the card stays clickable thereafter.
    """
    pending = [m for ms in fresh.values() for m in ms
               if not m.get("match_id") and m.get("status") == "finished"
               and m.get("home_en") and m.get("away_en")]
    # Cap per-call lookups so a cold start over a wide window can't fan out into
    # dozens of requests against the rate-limited free tier. Any not reached this
    # time stay pending and are picked up on a later refresh.
    for m in pending[:WC_ID_SEARCH_LIMIT]:
        eid = _wc_search_event_id(m["home_en"], m["away_en"], m.get("date"))
        if eid:
            m["match_id"] = eid
            m["source"] = f"{m.get('source') or ''}+search_id"


# Max finished-match id lookups per fetch (searchevents.php), to bound the extra
# requests a wide cold start makes against the rate-limited free tier.
WC_ID_SEARCH_LIMIT = int(os.environ.get("WC_ID_SEARCH_LIMIT", "8"))
# How many days ahead the calendar reaches (so "next week" is covered).
WC_DAYS_AHEAD = int(os.environ.get("WC_DAYS_AHEAD", "8"))
# When the tournament started — the lower bound for the registro and standings.
WC_SEASON_START = os.environ.get("WC_SEASON_START", "2026-06-11")
# How long a persisted *current/future* day stays cached before a refresh; past
# days are written with no expiry since their results no longer change.
WC_PERSIST_TTL = int(os.environ.get("WC_PERSIST_TTL", "900"))


def _wc_season_start_date():
    """Parse WC_SEASON_START, falling back to the official 2026 kickoff."""
    try:
        return datetime.strptime(WC_SEASON_START, "%Y-%m-%d").date()
    except ValueError:
        logger.warning(f"Invalid WC_SEASON_START {WC_SEASON_START!r}; defaulting to 2026-06-11")
        return datetime(2026, 6, 11).date()


def _wc_event_key(ev: dict):
    """A logical match identity that survives the API reusing the same fixture
    under different event ids for its scheduled (NS) and played (FT) records."""
    return (
        (ev.get("strHomeTeam") or "").strip().lower(),
        (ev.get("strAwayTeam") or "").strip().lower(),
        ev.get("intRound"),
    )


def _wc_event_rank(ev: dict) -> int:
    """Prefer the most informative copy of a fixture when merging sources:
    a finished/scored record beats a live one, which beats a not-started one."""
    status = (ev.get("strStatus") or "").strip().upper()
    has_score = ev.get("intHomeScore") not in (None, "") and ev.get("intAwayScore") not in (None, "")
    if status in ("FT", "MATCH FINISHED", "AET", "PEN") or has_score:
        return 2
    if status in ("1H", "2H", "HT", "ET", "LIVE", "P"):
        return 1
    return 0


def _wc_collect_events(start_date, end_date) -> list:
    """Gather fixtures for [start_date, end_date] (UTC dates, inclusive).

    The free TheSportsDB tier serves no single complete source:
    - eventsday.php returns a day's *scheduled* slate but omits some finished
      games (e.g. Sweden–Tunisia drops out once it ends);
    - eventspastleague/eventsseason carry those finished results but are
      truncated to a handful of events.
    So we union the per-day calls with the season + recent-results endpoints and
    dedupe by logical match identity (not event id, which differs between a
    fixture's NS and FT records), keeping the most informative copy. Every source
    is best-effort: a failed request never sinks the rest.
    """
    raw: list = []
    d = start_date
    while d <= end_date:
        iso = d.isoformat()
        try:
            raw += _wc_get("/eventsday.php", {"d": iso, "l": WC_LEAGUE_ID}).get("events") or []
        except Exception as e:
            logger.warning(f"World Cup eventsday {iso} failed: {e}")
        d += timedelta(days=1)

    # Results endpoints fill in finished games that eventsday omits.
    for path, params in (
        ("/eventspastleague.php", {"id": WC_LEAGUE_ID}),
        ("/eventsseason.php", {"id": WC_LEAGUE_ID, "s": WC_SEASON}),
    ):
        try:
            raw += _wc_get(path, params).get("events") or []
        except Exception as e:
            logger.warning(f"World Cup fetch {path} failed: {e}")

    merged: dict = {}
    for ev in raw:
        key = _wc_event_key(ev)
        cur = merged.get(key)
        if cur is None or _wc_event_rank(ev) > _wc_event_rank(cur):
            merged[key] = ev
    return list(merged.values())


# --- Per-day persistence (registro desde el inicio del Mundial) ---
#
# TheSportsDB's free tier returns only a few matches per day and Marca only lists
# the current week, so no single fetch sees the whole tournament. We merge each
# fresh fetch into a per-day record in Redis so the calendar *accumulates*: a
# Marca fixture stays in the registro after it scrolls out of Marca's week, and a
# played score upgrades the record in place. Past days never expire.

_WC_STATUS_RANK = {"finished": 2, "live": 1, "upcoming": 0}


def _wc_day_key(iso: str) -> str:
    return f"wc:day:{iso}"


def _wc_richer(a: dict, b: dict) -> dict:
    """Combine two records of the same fixture, keeping the most informative.

    The higher-status record (finished > live > upcoming) is the base; missing
    fields are then backfilled from the other copy, so e.g. a finished record
    from TheSportsDB still inherits a kickoff time or channel that only Marca had.
    """
    base, other = (a, b) if _WC_STATUS_RANK.get(a["status"], 0) >= _WC_STATUS_RANK.get(b["status"], 0) else (b, a)
    base = dict(base)
    for field in ("channel", "time", "venue", "country", "match_id", "round", "timestamp"):
        if not base.get(field) and other.get(field):
            base[field] = other[field]
    if base.get("home_score") is None and other.get("home_score") is not None:
        base["home_score"] = other["home_score"]
        base["away_score"] = other["away_score"]
    return base


def _wc_dedup_day(matches: list) -> list:
    """Collapse duplicate fixtures within a day by unordered team pair."""
    by_pair: dict = {}
    for m in matches:
        pair = _wc_team_pair(m.get("home_en"), m.get("away_en"))
        by_pair[pair] = _wc_richer(by_pair[pair], m) if pair in by_pair else m
    return list(by_pair.values())


def _wc_load_history(start_date, end_date) -> dict:
    """Read the persisted per-day records for [start, end] in one MGET."""
    if not kv_enabled():
        return {}
    isos = []
    d = start_date
    while d <= end_date:
        isos.append(d.isoformat())
        d += timedelta(days=1)
    results = _kv_cmd(["MGET", *[_wc_day_key(i) for i in isos]]) or []
    out: dict = {}
    for iso, raw in zip(isos, results):
        if not raw:
            continue
        try:
            out[iso] = json.loads(raw)
        except (TypeError, ValueError):
            pass
    return out


def get_wc_matches() -> dict:
    """Return World Cup fixtures+results grouped by day (chronological).

    Three sources are unioned: TheSportsDB (scores, status, match ids), Marca's TV
    grid (the *complete* week with kickoff times and channels — it fills the
    matches TheSportsDB omits), and the persisted per-day registro built up from
    earlier fetches. Each played day already carries its final scores, so picking
    a past day in the UI shows that day's results without an extra request.
    """
    today = datetime.now(MADRID_TZ).date()
    season_start = _wc_season_start_date()
    yesterday = today - timedelta(days=1)
    last = today + timedelta(days=WC_DAYS_AHEAD)

    # Fresh fetch stays a narrow window to protect the rate-limited free tier; the
    # deep history comes from the persisted registro. Query one extra UTC day on
    # each side: a kickoff stored under a UTC date can land on the adjacent Madrid
    # day. Don't fetch days before the tournament started.
    fetch_from = max(season_start, yesterday - timedelta(days=1))
    events = _wc_collect_events(fetch_from, last + timedelta(days=1))
    marca = _wc_marca_fixtures()

    today_iso = today.isoformat()
    min_date, max_date = season_start.isoformat(), last.isoformat()

    # Build today's fresh per-day slate from both live sources.
    fresh: dict = {}
    for ev in events:
        m = _wc_normalize_match(ev)
        if min_date <= (m.get("date") or "") <= max_date:
            fresh.setdefault(m["date"], []).append(m)
    seen = {(d, _wc_team_pair(m["home_en"], m["away_en"])) for d, ms in fresh.items() for m in ms}
    for f in marca:
        if not (min_date <= f["date"] <= max_date):
            continue
        if (f["date"], _wc_team_pair(f["home_en"], f["away_en"])) in seen:
            continue
        fresh.setdefault(f["date"], []).append(_wc_match_from_marca(f))

    # Attach channels (Marca grid, else DAZN fallback for upcoming) and dedup.
    for iso, day_matches in fresh.items():
        for m in day_matches:
            if not m.get("channel"):
                m["channel"] = _wc_marca_channel(m, marca)
            # DAZN holds the full World Cup rights in Spain; RTVE (La 1) only
            # carries a subset. A played result doesn't need a broadcaster.
            if not m.get("channel") and m.get("status") != "finished":
                m["channel"] = "DAZN MUNDIAL"
        fresh[iso] = _wc_dedup_day(day_matches)

    # Score backup: fill any played-but-scoreless fixture from Marca's calendar
    # (only fetched when something needs it). Runs before persistence so the
    # recovered score is written into the registro.
    _wc_backfill_scores(fresh)

    # Id backup: recover TheSportsDB ids for finished matches its truncated
    # listings dropped, so their goals/cards detail can open. Runs after the score
    # backfill so a Marca-recovered match (now finished) is searched too, and
    # before persistence so the id is written into the registro once.
    _wc_backfill_event_ids(fresh)

    # Overlay the fresh slate onto the persisted registro, then write it back so
    # the history keeps growing. Past days never expire; today/future refresh.
    days = _wc_load_history(season_start, last)
    for iso, day_matches in fresh.items():
        days[iso] = _wc_dedup_day(days.get(iso, []) + day_matches)
        ttl = None if iso < today_iso else WC_PERSIST_TTL
        kv_set_json(_wc_day_key(iso), days[iso], ttl)

    # Within a day, order by kickoff time then home team for a stable read.
    for day_matches in days.values():
        day_matches.sort(key=lambda x: (x.get("time") or "99:99", x.get("home") or ""))

    return {
        "season": WC_SEASON,
        "days": [{"date": d, "matches": days[d]} for d in sorted(days.keys()) if min_date <= d <= max_date],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _wc_blank_row(team: str) -> dict:
    return {
        "team": team, "flag": _wc_flag(team),
        "played": 0, "win": 0, "draw": 0, "loss": 0,
        "gf": 0, "ga": 0, "gd": 0, "points": 0,
    }


# Marca's standings come pre-computed (every played match counted) from Unidad
# Editorial's sports API — the same feed marca.com/futbol/mundial/clasificacion
# renders client-side. We prefer it over recomputing from TheSportsDB's free tier,
# which truncates finished games and so undercounts a team's played matches.
# site=2 (Marca), type=10 (general table), tournament=0117 (World Cup). The
# season id is the calendar year the tournament *draw* belongs to, not WC_SEASON.
WC_STANDINGS_URL = "https://api.unidadeditorial.es/sports/v1/classifications/current/"
WC_STANDINGS_SEASON = os.environ.get("WC_STANDINGS_SEASON", "2025")

# Map the API's English team names to the keys used in WC_GROUPS / WC_TEAM_ES /
# WC_TEAM_ISO, so flags, Spanish names and group letters line up. Only the names
# that don't match verbatim need an entry.
WC_API_EN = {
    "Bosnia and Herzegovina": "Bosnia-Herzegovina",
    "Cape Verde Islands": "Cape Verde",
    "Congo DR": "DR Congo",
    "Côte d'Ivoire": "Ivory Coast",
    "Korea Republic": "South Korea",
}


def _wc_standings_row(rank: dict) -> dict:
    """Turn one Unidad Editorial rank entry into our table-row shape."""
    s = rank.get("standing") or {}
    api_en = (rank.get("alternateCommonNames") or {}).get("enEN") or rank.get("name") or ""
    team_en = WC_API_EN.get(api_en, api_en)

    def _i(key):
        return _wc_int(s.get(key)) or 0

    return {
        # Display name in Spanish (our spelling); fall back to the API's own.
        "team": WC_TEAM_ES.get(team_en, rank.get("name") or team_en),
        "flag": _wc_flag(team_en),
        "played": _i("played"),
        "win": _i("won"),
        "draw": _i("drawn"),
        "loss": _i("lost"),
        "gf": _i("for"),
        "ga": _i("against"),
        "gd": _wc_int(s.get("difference")) or (_i("for") - _i("against")),
        "points": _i("points"),
    }


def _wc_standings_from_marca() -> dict | None:
    """Fetch the 12 pre-computed group tables from Marca's classifications API.

    Returns the standings payload, or None on any failure / unexpected shape so
    the caller can fall back to recomputing from results.
    """
    params = {
        "site": "2", "type": "10", "tournament": "0117",
        "season": WC_STANDINGS_SEASON,
    }
    try:
        resp = requests.get(WC_STANDINGS_URL, params=params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        payload = resp.json() or {}
    except Exception as e:
        logger.warning(f"Marca World Cup standings fetch failed: {e}")
        return None

    data = payload.get("data")
    if not data:
        return None

    groups_out = []
    for grp in data:
        head = grp.get("classificationHead") or {}
        name = ((head.get("group") or {}).get("name")) or ""
        ranks = grp.get("rank") or []
        if not name or not ranks:
            continue
        table = [_wc_standings_row(r) for r in ranks]
        # The API already orders the table, but rank it ourselves so the field is
        # always present and consistent with the computed fallback.
        for i, row in enumerate(table, 1):
            row["rank"] = i
        groups_out.append({"name": name, "table": table})

    if len(groups_out) < len(WC_GROUPS):
        # A partial table (e.g. mid-publish) is worse than recomputing; bail out.
        logger.warning(f"Marca standings returned {len(groups_out)} groups; falling back")
        return None

    groups_out.sort(key=lambda g: g["name"])
    return {
        "season": WC_SEASON,
        "groups": groups_out,
        "source": "marca",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def get_wc_standings() -> dict:
    """Return the 12 World Cup group tables.

    Primary source is Marca's pre-computed classifications API, which counts every
    played match; we fall back to recomputing from results only when it's
    unavailable. See _wc_standings_computed for the fallback details.
    """
    marca = _wc_standings_from_marca()
    if marca:
        return marca
    return _wc_standings_computed()


def _wc_standings_computed() -> dict:
    """Build the 12 group tables from the played group-stage results.

    The free TheSportsDB tier doesn't expose the group draw, so we seed each
    group from WC_GROUPS and accumulate played fixtures into it. Only finished
    matches with both scores count; teams start at zero so the tables show even
    before a ball is kicked.
    """
    # Standings span the whole tournament. Prefer the persisted registro (built by
    # get_wc_matches) so we reuse already-fetched results instead of re-scanning
    # the rate-limited API; fall back to a live scan when Redis isn't configured.
    start = _wc_season_start_date()
    today = datetime.now(MADRID_TZ).date()
    history = _wc_load_history(start, today)
    if history:
        all_matches = [m for day in history.values() for m in day]
    else:
        # One extra UTC day on each side absorbs the Madrid offset (see get_wc_matches).
        events = _wc_collect_events(start - timedelta(days=1), today + timedelta(days=1))
        all_matches = [_wc_normalize_match(ev) for ev in events]

    # Seed every group with its teams at zero.
    tables: dict = {g: {t: _wc_blank_row(t) for t in teams} for g, teams in WC_GROUPS.items()}

    for m in all_matches:
        if m["status"] != "finished" or m["home_score"] is None or m["away_score"] is None:
            continue
        group = WC_TEAM_GROUP.get(m["home_en"]) or WC_TEAM_GROUP.get(m["away_en"])
        # Only score it as a group match when both teams share the same group.
        if not group or WC_TEAM_GROUP.get(m["home_en"]) != WC_TEAM_GROUP.get(m["away_en"]):
            continue
        hs, as_ = m["home_score"], m["away_score"]
        home, away = tables[group][m["home_en"]], tables[group][m["away_en"]]
        for row, gf, ga in ((home, hs, as_), (away, as_, hs)):
            row["played"] += 1
            row["gf"] += gf
            row["ga"] += ga
            row["gd"] = row["gf"] - row["ga"]
        if hs > as_:
            home["win"] += 1; home["points"] += 3; away["loss"] += 1
        elif hs < as_:
            away["win"] += 1; away["points"] += 3; home["loss"] += 1
        else:
            home["draw"] += 1; away["draw"] += 1
            home["points"] += 1; away["points"] += 1

    # Rank each group: points, then goal difference, then goals for.
    groups_out = []
    for g in sorted(tables.keys()):
        table = sorted(
            tables[g].values(),
            key=lambda r: (-r["points"], -r["gd"], -r["gf"], r["team"]),
        )
        for i, row in enumerate(table, 1):
            row["rank"] = i
            # Seeded from WC_GROUPS' English keys; show the name in Spanish.
            row["team"] = WC_TEAM_ES.get(row["team"], row["team"])
        groups_out.append({"name": f"Grupo {g}", "table": table})

    return {
        "season": WC_SEASON,
        "groups": groups_out,
        "source": "computed",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# --- Knockout bracket (fase eliminatoria / cuadro) ---
#
# Once the group stage ends the tournament becomes a single-elimination bracket.
# TheSportsDB tags each knockout fixture with a special intRound code (Final=125,
# 3rd place=150, Semi=160, QF=170, R16=180, R32=200), but the free tier is
# unreliable, so each round also carries the official FIFA 2026 date window as a
# fallback classifier. Group-stage matchdays (round 1–3, all before 28 Jun) never
# fall in a knockout window, so date bucketing can't misfile them.
#
# `slots` is how many matches the round holds when full (16→8→4→2→1); the frontend
# pads the round to that many cards with "Por definir" placeholders so the cuadro
# always shows its full shape even before the draw resolves.
WC_KO_ROUNDS = [
    {"key": "r32",   "name": "Dieciseisavos", "code": 200, "from": "2026-06-28", "to": "2026-07-03", "slots": 16},
    {"key": "r16",   "name": "Octavos",       "code": 180, "from": "2026-07-04", "to": "2026-07-07", "slots": 8},
    {"key": "qf",    "name": "Cuartos",       "code": 170, "from": "2026-07-09", "to": "2026-07-11", "slots": 4},
    {"key": "sf",    "name": "Semifinales",   "code": 160, "from": "2026-07-14", "to": "2026-07-15", "slots": 2},
    {"key": "third", "name": "Tercer puesto", "code": 150, "from": "2026-07-18", "to": "2026-07-18", "slots": 1},
    {"key": "final", "name": "Final",         "code": 125, "from": "2026-07-19", "to": "2026-07-19", "slots": 1},
]

_WC_KO_BY_CODE = {spec["code"]: spec["key"] for spec in WC_KO_ROUNDS}


def _wc_ko_round_key(m: dict) -> str | None:
    """Return the knockout-round key a match belongs to, or None if it's not a
    knockout fixture. Prefers TheSportsDB's round code, falls back to the official
    date window; group-stage rounds (1–3) are never treated as knockout."""
    code = m.get("round")
    if code in _WC_KO_BY_CODE:
        return _WC_KO_BY_CODE[code]
    if isinstance(code, int) and 1 <= code <= 3:
        return None  # a group matchday, even if its date somehow overlaps
    date = m.get("date")
    if not date:
        return None
    for spec in WC_KO_ROUNDS:
        if spec["from"] <= date <= spec["to"]:
            return spec["key"]
    return None


def _wc_apply_winner(m: dict) -> None:
    """Tag the winning side ('home'/'away') on a finished knockout match so the
    cuadro can bold it. A draw (decided on penalties, which the free tier doesn't
    expose) stays undecided."""
    winner = None
    if (m.get("status") == "finished"
            and m.get("home_score") is not None and m.get("away_score") is not None):
        if m["home_score"] > m["away_score"]:
            winner = "home"
        elif m["away_score"] > m["home_score"]:
            winner = "away"
    m["winner"] = winner


def get_wc_bracket() -> dict:
    """Return the knockout bracket grouped by round (dieciseisavos → final).

    Reuses the merged, persisted match registro (get_wc_matches) so scores stay
    live, and unions in any knockout fixtures further out than that window from the
    persisted history. Every round is always present — empty ones render as
    placeholders — so the cuadro shows its full shape from the moment the group
    stage ends.
    """
    data = get_wc_matches()
    matches = [m for day in data["days"] for m in day["matches"]]

    # get_wc_matches only reaches WC_DAYS_AHEAD out, so later rounds (semis, final)
    # can sit beyond it. Pull the whole tournament span from the persisted registro
    # and union any knockout fixtures the near window didn't include.
    start = _wc_season_start_date()
    history = _wc_load_history(start, start + timedelta(days=45))
    if history:
        seen = {(m.get("date"), _wc_team_pair(m.get("home_en"), m.get("away_en"))) for m in matches}
        for day in history.values():
            for m in day:
                key = (m.get("date"), _wc_team_pair(m.get("home_en"), m.get("away_en")))
                if key not in seen:
                    matches.append(m)
                    seen.add(key)

    rounds = []
    live = False
    for spec in WC_KO_ROUNDS:
        rms = _wc_dedup_day([m for m in matches if _wc_ko_round_key(m) == spec["key"]])
        rms.sort(key=lambda x: (x.get("date") or "9999", x.get("time") or "99:99", x.get("home") or ""))
        for m in rms:
            _wc_apply_winner(m)
            if m.get("status") == "live":
                live = True
        rounds.append({
            "key": spec["key"], "name": spec["name"], "slots": spec["slots"],
            "date_from": spec["from"], "date_to": spec["to"], "matches": rms,
        })

    return {
        "season": WC_SEASON,
        "has_matches": any(r["matches"] for r in rounds),
        "live": live,
        "rounds": rounds,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# --- Match detail (resumen de partido pasado: goleadores, tarjetas, info) ---
#
# The free TheSportsDB tier exposes lookupevent.php (venue, group, spectators) and
# lookuptimeline.php (goals/cards/subs with minute, player and assist). A finished
# match's timeline never changes, so once we've built a detail for an FT match we
# store it in Redis with no expiry — that's the per-match registro the slider reads.


def _apifootball_enabled() -> bool:
    return bool(API_FOOTBALL_KEY)


def _apifootball_get(path: str, params: dict) -> dict:
    """Call API-Football, handling both the direct and RapidAPI hosts.

    Direct (v3.football.api-sports.io) authenticates with x-apisports-key and has
    no path prefix; RapidAPI hosts prefix the path with /v3 and use x-rapidapi-*.
    """
    is_rapid = "rapidapi.com" in API_FOOTBALL_HOST
    base = f"https://{API_FOOTBALL_HOST}/v3" if is_rapid else f"https://{API_FOOTBALL_HOST}"
    headers = (
        {"x-rapidapi-key": API_FOOTBALL_KEY, "x-rapidapi-host": API_FOOTBALL_HOST}
        if is_rapid else {"x-apisports-key": API_FOOTBALL_KEY}
    )
    resp = requests.get(f"{base}{path}", params=params, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json() or {}


def _apifootball_event(it: dict, home_norm: str) -> dict | None:
    """Normalize one API-Football fixture event into our compact shape.

    `home_norm` is the accent-insensitive home-team name; events whose team
    matches it are flagged as home, so they render on the left rail.
    """
    type_ = (it.get("type") or "").strip().lower()
    detail = (it.get("detail") or "").strip().lower()
    player = ((it.get("player") or {}).get("name") or "").strip()
    if not player:
        return None
    if type_ == "goal":
        kind = "own_goal" if "own" in detail else ("penalty" if "penalty" in detail else "goal")
    elif type_ == "card":
        # A second yellow is effectively a sending-off — show it as red.
        kind = "red" if ("red" in detail or "second yellow" in detail) else "yellow"
    elif type_ == "subst":
        kind = "sub"
    else:
        return None
    t = it.get("time") or {}
    minute = _wc_int(t.get("elapsed"))
    if minute is not None and t.get("extra") not in (None, "", 0):
        extra = _wc_int(t.get("extra"))
        if extra:
            minute += extra
    assist = ((it.get("assist") or {}).get("name") or "").strip()
    team = ((it.get("team") or {}).get("name") or "").strip()
    return {
        "kind": kind,
        "minute": minute,
        "player": player,
        "assist": assist or None,
        "home": bool(home_norm) and _strip_accents(team) == home_norm,
        "team": team,
    }


def _apifootball_events(fixture_id: str, home_en: str | None = None) -> list:
    """Full goals/cards/subs for a fixture from API-Football, sorted by minute.

    Returns [] on any failure or when the fixture is unknown there, so the caller
    can fall back to TheSportsDB's (truncated) timeline. One request only: the home
    side is inferred from the team name we already have, not a second fixture call.
    """
    if not _apifootball_enabled() or not fixture_id:
        return []
    try:
        data = _apifootball_get("/fixtures/events", {"fixture": fixture_id})
    except Exception as e:
        logger.warning(f"API-Football events {fixture_id} failed: {e}")
        return []
    # API-Football replies 200 even on auth/quota problems, surfacing them in an
    # `errors` object with an empty `response`. Log it so a misconfigured key or a
    # spent quota is visible instead of silently degrading to the fallback.
    errors = data.get("errors")
    if errors:
        logger.warning(f"API-Football events {fixture_id} returned errors: {errors}")
    raw = data.get("response") or []
    logger.info(f"API-Football events {fixture_id}: results={data.get('results')} events={len(raw)}")
    home_norm = _strip_accents(home_en or "")
    events = [e for e in (_apifootball_event(it, home_norm) for it in raw) if e]
    events.sort(key=lambda e: (e["minute"] is None, e["minute"] or 0))
    return events


def _wc_timeline_entry(it: dict) -> dict | None:
    """Normalize one timeline row to a compact, display-ready event."""
    kind = (it.get("strTimeline") or "").strip().lower()
    detail = (it.get("strTimelineDetail") or "").strip()
    minute = _wc_int(it.get("intTime"))
    player = (it.get("strPlayer") or "").strip()
    if not player:
        return None
    if kind == "goal":
        own = "own goal" in detail.lower()
        kind_out = "own_goal" if own else ("penalty" if "penalty" in detail.lower() else "goal")
    elif kind == "card":
        kind_out = "red" if "red" in detail.lower() else "yellow"
    elif kind == "subst":
        kind_out = "sub"
    else:
        return None
    assist = (it.get("strAssist") or "").strip()
    return {
        "kind": kind_out,
        "minute": minute,
        "player": player,
        "assist": assist if assist and assist != "0" else None,
        "home": (it.get("strHome") or "").strip().lower() == "yes",
        "team": (it.get("strTeam") or "").strip(),
    }


# Goals don't reconcile with the score until we have the full event list, so when
# the scorers come from TheSportsDB's truncated timeline we cache the detail only
# briefly — leaving room for a later API-Football fetch (or replay) to complete it.
WC_DETAIL_PARTIAL_TTL = int(os.environ.get("WC_DETAIL_PARTIAL_TTL", "21600"))  # 6h


def get_wc_match_detail(event_id: str) -> dict:
    """Return a finished match's summary: score, scorers, cards and basic info.

    Event detail (score, venue) comes from TheSportsDB; the goals/cards list comes
    from API-Football when configured (complete) and falls back to TheSportsDB's
    truncated timeline otherwise. A finished match enriched from a *complete*
    source is cached forever in Redis (the per-match registro); a partial one gets
    a short TTL so it can be completed later. Live matches are never persisted.
    """
    # v3: the key is versioned so detail records cached by an earlier build (which
    # stored TheSportsDB's truncated timeline, or lacked the kickoff time) are
    # ignored, not served forever.
    cache_key = f"wc:match:v3:{event_id}"
    cached = kv_get_json(cache_key)
    # Serve the cache unless it's a partial (TheSportsDB) record while API-Football
    # is now configured — in that case retry so the key can upgrade it to complete.
    if cached and not (_apifootball_enabled() and cached.get("events_source") != "apifootball"):
        return cached

    detail = (_wc_get("/lookupevent.php", {"id": event_id}).get("events") or [None])[0]
    if not detail:
        raise HTTPException(status_code=404, detail="Partido no encontrado.")

    home_en, away_en = detail.get("strHomeTeam"), detail.get("strAwayTeam")
    home_score = _wc_int(detail.get("intHomeScore"))
    away_score = _wc_int(detail.get("intAwayScore"))
    status = _wc_match_status(detail, home_score, away_score)

    # Prefer API-Football's complete event list; fall back to TheSportsDB's.
    events = _apifootball_events(detail.get("idAPIfootball"), home_en)
    source = "apifootball"
    if not events:
        timeline_raw = _wc_get("/lookuptimeline.php", {"id": event_id}).get("timeline") or []
        events = [e for e in (_wc_timeline_entry(it) for it in timeline_raw) if e]
        events.sort(key=lambda e: (e["minute"] is None, e["minute"] or 0))
        source = "thesportsdb"

    out = {
        "match_id": event_id,
        "time": _wc_local_datetime(detail)[1],  # "HH:MM" kickoff, Europe/Madrid
        "home": WC_TEAM_ES.get(home_en, home_en),
        "away": WC_TEAM_ES.get(away_en, away_en),
        "home_flag": _wc_flag(home_en),
        "away_flag": _wc_flag(away_en),
        "home_score": home_score,
        "away_score": away_score,
        "status": status,
        "round": _wc_int(detail.get("intRound")),
        "venue": detail.get("strVenue"),
        "city": detail.get("strCity"),
        "country": detail.get("strCountry"),
        "spectators": _wc_int(detail.get("intSpectators")),
        "events": events,
        "events_source": source,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    # Persist only finished matches. Forever when the event list is complete
    # (API-Football); briefly when it's the truncated TheSportsDB fallback.
    if status == "finished":
        ttl = None if source == "apifootball" else WC_DETAIL_PARTIAL_TTL
        kv_set_json(cache_key, out, ttl)
    return out


# --- In-memory cache ---

_cache: dict = {"data": None, "ts": 0.0}
_cache_lock = asyncio.Lock()
CACHE_TTL = 900  # 15 minutes

# Padel caches: tournament calendar (shared) and per-slug schedules.
_padel_tournaments_cache: dict = {"data": None, "ts": 0.0}
_padel_schedule_cache: dict = {}  # slug -> {"data": ..., "ts": ...}
_padel_lock = asyncio.Lock()
PADEL_TOURNAMENTS_TTL = 3600  # 1 hour — the calendar rarely changes
PADEL_SCHEDULE_TTL = 300  # 5 minutes — live scores move fast

# World Cup caches: full-season fixtures (with results) and standings.
_wc_matches_cache: dict = {"data": None, "ts": 0.0}
_wc_standings_cache: dict = {"data": None, "ts": 0.0}
_wc_bracket_cache: dict = {"data": None, "ts": 0.0}
# Marca calendar results — the score backup, fetched only on demand.
_wc_marca_results_cache: dict = {"data": None, "ts": 0.0}
_wc_lock = asyncio.Lock()
WC_MATCHES_TTL = 300  # 5 minutes — live scores move fast
WC_STANDINGS_TTL = 900  # 15 minutes — tables update less often
WC_BRACKET_TTL = 300  # 5 minutes — mirrors the live match cadence


# --- FastAPI app ---

app = FastAPI(title="TV Sports API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/events")
async def get_events():
    now = time.time()
    if _cache["data"] and (now - _cache["ts"]) < CACHE_TTL:
        return _cache["data"]
    async with _cache_lock:
        # Re-check after acquiring lock — another request may have populated it
        now = time.time()
        if _cache["data"] and (now - _cache["ts"]) < CACHE_TTL:
            return _cache["data"]
        try:
            data = await asyncio.to_thread(scrape_events)
            _cache["data"] = data
            _cache["ts"] = now
            logger.info(f"Scraped {len(data['events'])} events for {data['date']}")
            return data
        except Exception as e:
            logger.error(f"Scrape failed: {e}")
            if _cache["data"]:
                logger.warning("Returning stale cache after scrape failure")
                return _cache["data"]
            raise HTTPException(status_code=500, detail="Failed to fetch schedule. Try again later.")


MONTHS_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def _parse_es_date(date_str: str, time_str: str) -> datetime:
    """Parse Spanish date like 'Jueves 13 de Marzo' or 'Sábado 4 de Abril de 2026' + 'HH:MM'."""
    parts = date_str.lower().split()
    # parts: [weekday, day, 'de', month, ('de', year)?]
    try:
        day = int(parts[1])
        month_name = parts[3]
        month = MONTHS_ES.get(month_name)
        if month is None:
            raise ValueError(f"Unknown month: {month_name}")
        year = int(parts[5]) if len(parts) >= 6 else datetime.now().year
        hours, minutes = map(int, time_str.split(":"))
        return datetime(year, month, day, hours, minutes)
    except (IndexError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date/time: {exc}")


@app.get("/api/ics")
def get_ics(
    summary: str = Query(...),
    date: str = Query(...),
    time: str = Query(...),
    description: str = Query(""),
):
    dt = _parse_es_date(date, time)

    end = dt + timedelta(hours=2)

    def fmt(d: datetime) -> str:
        return d.strftime("%Y%m%dT%H%M%S")

    uid = f"{int(dt.timestamp())}@sports-tv"
    ics = "\r\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Sports TV//ES",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTART:{fmt(dt)}",
        f"DTEND:{fmt(end)}",
        f"SUMMARY:{summary}",
        f"DESCRIPTION:{description}",
        "END:VEVENT",
        "END:VCALENDAR",
    ])

    return Response(
        content=ics,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="evento.ics"'},
    )


@app.get("/api/padel/tournaments")
async def padel_tournaments():
    now = time.time()
    c = _padel_tournaments_cache
    if c["data"] and (now - c["ts"]) < PADEL_TOURNAMENTS_TTL:
        return c["data"]
    async with _padel_lock:
        now = time.time()
        if c["data"] and (now - c["ts"]) < PADEL_TOURNAMENTS_TTL:
            return c["data"]
        try:
            data = await asyncio.to_thread(get_padel_tournaments)
            c["data"], c["ts"] = data, now
            logger.info(f"Padel: {len(data['tournaments'])} tournaments, live={data['live_slug']}")
            return data
        except Exception as e:
            logger.error(f"Padel tournaments fetch failed: {e}")
            if c["data"]:
                logger.warning("Returning stale padel tournaments after failure")
                return c["data"]
            raise HTTPException(status_code=500, detail="No se pudo cargar el calendario de pádel.")


@app.get("/api/padel/schedule")
async def padel_schedule(slug: str = Query(...)):
    now = time.time()
    c = _padel_schedule_cache.get(slug)
    if c and c["data"] and (now - c["ts"]) < PADEL_SCHEDULE_TTL:
        return c["data"]
    async with _padel_lock:
        now = time.time()
        c = _padel_schedule_cache.get(slug)
        if c and c["data"] and (now - c["ts"]) < PADEL_SCHEDULE_TTL:
            return c["data"]
        try:
            data = await asyncio.to_thread(get_padel_schedule, slug)
            _padel_schedule_cache[slug] = {"data": data, "ts": now}
            total = sum(len(d["matches"]) for d in data["days"])
            logger.info(f"Padel schedule {slug}: {len(data['days'])} days, {total} matches")
            return data
        except Exception as e:
            logger.error(f"Padel schedule fetch failed for {slug}: {e}")
            if c and c["data"]:
                logger.warning("Returning stale padel schedule after failure")
                return c["data"]
            raise HTTPException(status_code=500, detail="No se pudo cargar el cuadro de partidos.")


@app.get("/api/wc/matches")
async def wc_matches():
    now = time.time()
    c = _wc_matches_cache
    if c["data"] and (now - c["ts"]) < WC_MATCHES_TTL:
        return c["data"]
    async with _wc_lock:
        now = time.time()
        if c["data"] and (now - c["ts"]) < WC_MATCHES_TTL:
            return c["data"]
        try:
            data = await asyncio.to_thread(get_wc_matches)
            c["data"], c["ts"] = data, now
            total = sum(len(d["matches"]) for d in data["days"])
            logger.info(f"World Cup: {len(data['days'])} days, {total} matches")
            return data
        except Exception as e:
            logger.error(f"World Cup matches fetch failed: {e}")
            if c["data"]:
                logger.warning("Returning stale World Cup matches after failure")
                return c["data"]
            raise HTTPException(status_code=500, detail="No se pudo cargar el calendario del Mundial.")


@app.get("/api/wc/standings")
async def wc_standings():
    now = time.time()
    c = _wc_standings_cache
    if c["data"] and (now - c["ts"]) < WC_STANDINGS_TTL:
        return c["data"]
    async with _wc_lock:
        now = time.time()
        if c["data"] and (now - c["ts"]) < WC_STANDINGS_TTL:
            return c["data"]
        try:
            data = await asyncio.to_thread(get_wc_standings)
            c["data"], c["ts"] = data, now
            logger.info(f"World Cup standings: {len(data['groups'])} groups")
            return data
        except Exception as e:
            logger.error(f"World Cup standings fetch failed: {e}")
            if c["data"]:
                logger.warning("Returning stale World Cup standings after failure")
                return c["data"]
            raise HTTPException(status_code=500, detail="No se pudo cargar la clasificación del Mundial.")


@app.get("/api/wc/bracket")
async def wc_bracket():
    now = time.time()
    c = _wc_bracket_cache
    if c["data"] and (now - c["ts"]) < WC_BRACKET_TTL:
        return c["data"]
    async with _wc_lock:
        now = time.time()
        if c["data"] and (now - c["ts"]) < WC_BRACKET_TTL:
            return c["data"]
        try:
            data = await asyncio.to_thread(get_wc_bracket)
            c["data"], c["ts"] = data, now
            total = sum(len(r["matches"]) for r in data["rounds"])
            logger.info(f"World Cup bracket: {total} knockout matches, live={data['live']}")
            return data
        except Exception as e:
            logger.error(f"World Cup bracket fetch failed: {e}")
            if c["data"]:
                logger.warning("Returning stale World Cup bracket after failure")
                return c["data"]
            raise HTTPException(status_code=500, detail="No se pudo cargar el cuadro del Mundial.")


@app.get("/api/wc/match/{event_id}")
async def wc_match_detail(event_id: str):
    if not event_id.isdigit():
        raise HTTPException(status_code=400, detail="Identificador de partido no válido.")
    try:
        return await asyncio.to_thread(get_wc_match_detail, event_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"World Cup match detail {event_id} failed: {e}")
        raise HTTPException(status_code=500, detail="No se pudo cargar el resumen del partido.")


@app.get("/api/health")
def health():
    return {"status": "ok"}
