from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import asyncio
import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Scraper ---

MARCA_URL = "https://www.marca.com/programacion-tv.html"

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


@app.get("/api/health")
def health():
    return {"status": "ok"}
