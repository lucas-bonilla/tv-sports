from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import asyncio
import json
import logging
import os
import time

# All user-facing schedules are for a Spanish audience, so kickoff times and the
# day a match is filed under are expressed in Europe/Madrid (handles CEST/CET DST).
# Kept deliberately although nothing references it right now: scrape_events()
# still emits naive datetimes, and this is the zone they must be anchored to
# when that is fixed.
MADRID_TZ = ZoneInfo("Europe/Madrid")


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

    # FIP national-team events (the World Cup) live outside the Premier Padel
    # calendar; add them and, when one is on, open the section straight into it.
    for t in fip_tournament_entries():
        if t["start_date"][:4] != str(year):
            continue
        tournaments.append(t)
        if t["status"] == "live":
            live_slug = t["slug"]

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


# --- FIP events (World Cup) ---
#
# FIP national-team events (the padel "Mundial") are not part of the Premier
# Padel calendar. padelfip.com embeds server-rendered widgets from
# widget.matchscorerlive.com, so we scrape those: the order of play by day
# (`oopbyday/<id>/<n>`) and the group standings (`groups/<id>`). Each event is
# listed in FIP_EVENTS and surfaced as one more tournament in the padel section
# under its own slug.

FIP_WIDGET = "https://widget.matchscorerlive.com/screen"

FIP_EVENTS = {
    "fip-world-cup-2026": {
        "widget_id": "FIP-2026-4516",
        "name": "MUNDIAL FIP",
        "city": "Doha",
        "country": "Qatar",
        "category": "MUNDIAL",
        "emoji": "🌍",
        "start_date": "2026-11-02T00:00:00.000Z",
        "end_date": "2026-11-07T23:59:59.000Z",
        "tz": "Asia/Qatar",
        "ticket_url": None,
        "where_to_watch_url": "https://www.padelfip.com/events/fip-world-cup-2026-2/",
    },
}

# matchscorerlive flags use IOC-style codes; map them to ISO 3166 alpha-2 so we
# can render emoji flags. Unknown codes fall back to the raw code.
FIP_IOC_TO_ISO2 = {
    "ARG": "AR", "AUS": "AU", "BEL": "BE", "BRA": "BR", "CHI": "CL", "EGY": "EG",
    "ESP": "ES", "FRA": "FR", "GBR": "GB", "GER": "DE", "INA": "ID", "ITA": "IT",
    "MEX": "MX", "NED": "NL", "PAR": "PY", "POR": "PT", "QAT": "QA", "SWE": "SE",
    "UAE": "AE", "USA": "US", "URU": "UY", "JPN": "JP", "KUW": "KW", "KSA": "SA",
    "SUI": "CH", "AUT": "AT", "DEN": "DK", "FIN": "FI", "IRL": "IE", "POL": "PL",
}

# Standings come with English team names ("Spain", "Uae"); show Spanish ones.
FIP_COUNTRY_ES = {
    "ARG": "Argentina", "AUS": "Australia", "BEL": "Bélgica", "BRA": "Brasil",
    "CHI": "Chile", "EGY": "Egipto", "ESP": "España", "FRA": "Francia",
    "GBR": "Gran Bretaña", "GER": "Alemania", "INA": "Indonesia", "ITA": "Italia",
    "MEX": "México", "NED": "Países Bajos", "PAR": "Paraguay", "POR": "Portugal",
    "QAT": "Catar", "SWE": "Suecia", "UAE": "Emiratos Árabes", "USA": "Estados Unidos",
}

# The FIP event page titles its streams in English ("🇳🇱 Netherlands vs USA 🇺🇸").
FIP_COUNTRY_EN_TO_CODE = {
    "argentina": "ARG", "australia": "AUS", "belgium": "BEL", "brazil": "BRA",
    "chile": "CHI", "egypt": "EGY", "spain": "ESP", "france": "FRA",
    "great britain": "GBR", "germany": "GER", "indonesia": "INA", "italy": "ITA",
    "mexico": "MEX", "netherlands": "NED", "paraguay": "PAR", "portugal": "POR",
    "qatar": "QAT", "sweden": "SWE", "uae": "UAE", "usa": "USA",
}

FIP_GENDER_ES = {"women": "Femenino", "men": "Masculino"}

FIP_ROUND_ES = {
    "group stage": "Fase de grupos",
    "quarter-finals": "Cuartos de final", "quarterfinals": "Cuartos de final",
    "semi-finals": "Semifinales", "semifinals": "Semifinales",
    "final": "Final", "third place": "Tercer puesto", "3rd place": "Tercer puesto",
}


def fip_tournament_entries() -> list:
    """FIP events shaped like get_padel_tournaments() entries."""
    out = []
    for slug, ev in FIP_EVENTS.items():
        status = _padel_tournament_status({
            "start_date_utc": ev["start_date"], "end_date_utc": ev["end_date"],
        })
        out.append({
            "slug": slug,
            "name": ev["name"],
            "country": ev["country"],
            "city": ev["city"],
            "category": ev["category"],
            "emoji": ev["emoji"],
            "flag_url": None,
            "start_date": ev["start_date"],
            "end_date": ev["end_date"],
            "prize_money": None,
            "status": status,
            "ticket_url": ev["ticket_url"],
            "where_to_watch_url": ev["where_to_watch_url"],
        })
    return out


def _fip_get(path: str) -> BeautifulSoup:
    resp = requests.get(f"{FIP_WIDGET}/{path}", params={"t": "tol"}, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def _fip_flag(code: str) -> str:
    iso = FIP_IOC_TO_ISO2.get((code or "").upper())
    if not iso:
        return code or ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in iso)


def _fip_team(row) -> dict:
    names = [" ".join(n.get_text(" ", strip=True).split()) for n in row.select(".player-names .line-thin")]
    img = row.select_one("img.flags")
    code = img["src"].rsplit("/", 1)[-1].split(".")[0] if img and img.get("src") else ""
    sets = []
    for cell in row.select("td.set"):
        sup = cell.find("sup")
        tie = sup.extract().get_text(strip=True) if sup else None
        games = cell.get_text(strip=True)
        if not games or games == "-":
            continue
        sets.append(f"{games}({tie})" if tie else games)
    label = " / ".join(n for n in names if n) or "TBD"
    flag = _fip_flag(code)
    return {
        "code": code.upper(),
        "label": f"{flag} {label}".strip(),
        "score": " ".join(sets),
        "won": bool(row.select_one(".check-primary")),
    }


def _fip_local_to_madrid(day, hhmm: str, tz: ZoneInfo):
    """Venue-local date + 'HH:MM' -> (Madrid 'YYYY-MM-DD', Madrid 'HH:MM')."""
    h, m = (int(x) for x in hhmm.split(":"))
    local = datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
    mad = local.astimezone(MADRID_TZ)
    return mad.strftime("%Y-%m-%d"), mad.strftime("%H:%M")


def _fip_parse_day(soup: BeautifulSoup, day, tz: ZoneInfo) -> list:
    matches = []
    for court_order, header in enumerate(soup.select(".schedule-header")):
        court_el = header.select_one(".oop-court")
        court = court_el.get_text(" ", strip=True).title() if court_el else ""
        start_el = header.select_one(".oop-court-start")
        tm = _TIME_RE.search(start_el.get_text(" ", strip=True)) if start_el else None
        court_start_local = _to_24h(int(tm.group(1)), int(tm.group(2) or 0), tm.group(3)) if tm else ""
        date_str, court_start = (
            _fip_local_to_madrid(day, court_start_local, tz) if court_start_local
            else (day.isoformat(), "")
        )

        # The court's matches are the tables that follow its header inside the
        # same column.
        column = header.find_parent(class_="col-12") or header.parent
        for table in column.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 3:
                continue
            head_el = rows[0].select_one(".court-name")
            head = head_el.get_text(" ", strip=True) if head_el else ""
            # Rewrite the venue-local time in the header to Madrid time so
            # _padel_slot labels it ("Desde 12:00", "No antes de 16:00").
            hm = _TIME_RE.search(head)
            if hm and (head.lower().startswith("starting") or head.lower().startswith("not before")):
                local_t = _to_24h(int(hm.group(1)), int(hm.group(2) or 0), hm.group(3))
                _, mad_t = _fip_local_to_madrid(day, local_t, tz)
                head = ("Starting at " if head.lower().startswith("starting") else "Not before ") + mad_t
            slot, slot_sort = _padel_slot(head, court_start, None)

            round_el = rows[0].select_one(".round-name")
            gender = round_text = ""
            if round_el:
                b = round_el.find("b")
                gender = b.get_text(strip=True) if b else ""
                rd = round_el.find("div")
                round_text = rd.get_text(" ", strip=True) if rd else ""

            team_a, team_b = _fip_team(rows[1]), _fip_team(rows[2])
            summary = table.select_one(".live-status-summary")
            summary_txt = summary.get_text(" ", strip=True).lower() if summary else ""
            finished = any(k in summary_txt for k in ("completed", "retired", "walkover", "w.o."))
            started = bool(re.search(r"\d{2}:\d{2}", summary_txt))
            status = "F" if finished else "P" if started else None
            stats = table.select_one("a.open[data-id]")

            matches.append({
                "match_id": stats.get("data-id") if stats else None,
                "court": court,
                "court_order": court_order,
                "court_start": court_start,
                "date": date_str,
                "slot": slot,
                "slot_sort": slot_sort,
                "round": FIP_ROUND_ES.get(round_text.lower(), round_text),
                "draw_type": FIP_GENDER_ES.get(gender.lower(), gender),
                "status": status,
                "status_title": None,
                "team_a": team_a["label"],
                "team_b": team_b["label"],
                "country_a": team_a["code"],
                "country_b": team_b["code"],
                "score_a": team_a["score"],
                "score_b": team_b["score"],
                "winner": "a" if team_a["won"] else "b" if team_b["won"] else None,
                "broadcast_url": None,
            })
    return matches


def _fip_parse_groups(soup: BeautifulSoup) -> list:
    draws = []
    for draw in soup.select("[data-draw]"):
        title_el = draw.select_one(".h4 span")
        title = title_el.get_text(" ", strip=True) if title_el else ""
        gender = "Femenino" if "WOMEN" in title.upper() else "Masculino" if "MEN" in title.upper() else title
        groups = []
        for caption in draw.select(".group-caption"):
            body = caption.find_next_sibling(class_="group-body")
            if not body:
                continue
            rows = []
            for team_el in body.select(".group-team"):
                line = team_el.parent
                img = team_el.select_one("img.group-flag")
                code = img["src"].rsplit("/", 1)[-1].split(".")[0] if img and img.get("src") else ""
                pts = line.select_one(".group-pts")
                rubs = line.select_one(".group-rubs")
                rows.append({
                    "team": FIP_COUNTRY_ES.get(code.upper(), team_el.get_text(" ", strip=True)),
                    "flag": _fip_flag(code) if code else "",
                    "points": pts.get_text(strip=True) if pts else "",
                    "rubbers": rubs.get_text(" ", strip=True) if rubs else "",
                })
            name = caption.get_text(" ", strip=True).replace("Group", "Grupo")
            groups.append({"name": name, "rows": rows})
        if groups:
            draws.append({"title": gender, "groups": groups})
    return draws


def _fip_stream_team(name: str) -> str:
    code = FIP_COUNTRY_EN_TO_CODE.get(name.strip().lower())
    if not code:
        return name.strip()
    return f"{_fip_flag(code)} {FIP_COUNTRY_ES.get(code, name.strip())}"


_EN_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}


def _fip_parse_streams(soup: BeautifulSoup, year: int) -> list:
    """Ties from the event page's live-streaming list, one card per tie.

    matchscorerlive only publishes a day's order of play once the referees load
    it, often just before play starts. The event page lists every streamed tie,
    so it backfills days the widget hasn't covered yet (country vs country, no
    players or scores) and gives every tie its YouTube link. The list has three
    sections and each says when a tie is played differently:
      "Coming up" — a <time datetime> per card
      "Live now"  — no time; it is being played today
      "Replays"   — grouped under a day title ("Monday 2 November")
    Each card gets a `kind` of upcoming / live / replay. Ties FIP doesn't stream
    never appear here.
    """
    today = datetime.now(MADRID_TZ).strftime("%Y-%m-%d")
    matches = []
    for card in soup.select("a[data-yt]"):
        name = card.select_one(".bw-yt-slot__name, .bw-yt-title")
        title = card.get("data-title") or (name.get_text(" ", strip=True) if name else "")
        title = re.sub(r"^\[[A-Z]+\]\s*", "", title)
        title = re.sub("[\U0001F1E6-\U0001F1FF]", "", title).strip()
        if " vs " not in title:
            continue
        home, away = title.split(" vs ", 1)
        code_a = FIP_COUNTRY_EN_TO_CODE.get(home.strip().lower(), "")
        code_b = FIP_COUNTRY_EN_TO_CODE.get(away.strip().lower(), "")

        when = card.select_one("time[datetime]")
        day_title = card.find_parent(class_="bw-yt-day")
        live_section = card.find_parent(class_="bw-yt-group--live")
        hhmm = ""
        if when:
            try:
                start = datetime.fromisoformat(when["datetime"]).astimezone(MADRID_TZ)
            except ValueError:
                continue
            date, hhmm, kind = start.strftime("%Y-%m-%d"), start.strftime("%H:%M"), "upcoming"
        elif live_section and "bw-yt-group--soon" not in (live_section.get("class") or []):
            date, kind = today, "live"
        elif day_title:
            head = day_title.select_one(".bw-yt-day__title")
            parts = (head.get_text(" ", strip=True) if head else "").lower().split()
            month = _EN_MONTHS.get(parts[-1]) if parts else None
            if not month or not parts[-2].isdigit():
                continue
            date, kind = f"{year}-{month:02d}-{int(parts[-2]):02d}", "replay"
        else:
            continue

        tags_el = card.select_one(".bw-yt-tags")
        tags = [t.strip() for t in tags_el.get_text("·").split("·")] if tags_el else []
        tags = [t for t in tags if t]
        gender = FIP_GENDER_ES.get(tags[0].lower(), tags[0].title()) if tags else ""
        rest = [t.title().replace("Group Phase", "Fase de grupos").replace("Group", "Grupo") for t in tags[1:3]]

        matches.append({
            "kind": kind,
            "match_id": card.get("data-yt"),
            "court": f"Eliminatorias · {gender}" if gender else "Eliminatorias",
            "court_order": 0 if gender == "Femenino" else 1,
            "court_start": hhmm,
            "date": date,
            "slot": f"Desde {hhmm}" if hhmm else "",
            "slot_sort": (0, hhmm),
            "round": " · ".join(rest),
            "draw_type": gender,
            "status": "P" if kind == "live" else None,
            "status_title": None,
            "team_a": _fip_stream_team(home),
            "team_b": _fip_stream_team(away),
            "country_a": code_a,
            "country_b": code_b,
            "score_a": "",
            "score_b": "",
            "winner": None,
            "broadcast_url": card.get("href"),
        })
    return matches


def get_fip_schedule(slug: str) -> dict:
    ev = FIP_EVENTS[slug]
    wid = ev["widget_id"]
    tz = ZoneInfo(ev["tz"])
    start = datetime.fromisoformat(ev["start_date"].replace("Z", "+00:00")).date()
    end = datetime.fromisoformat(ev["end_date"].replace("Z", "+00:00")).date()
    n_days = (end - start).days + 1

    # One page per tournament day plus the standings; fetch them in parallel so
    # the serverless call isn't n_days round trips long. A day that fails or
    # isn't published yet just contributes no matches.
    def fetch(path):
        try:
            return _fip_get(path)
        except Exception as e:
            logger.warning(f"FIP widget {path} failed: {e}")
            return None

    def fetch_event_page():
        try:
            resp = requests.get(ev["where_to_watch_url"], headers=HEADERS, timeout=15)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            logger.warning(f"FIP event page failed: {e}")
            return None

    paths = [f"oopbyday/{wid}/{i}" for i in range(1, n_days + 1)] + [f"groups/{wid}"]
    with ThreadPoolExecutor(max_workers=len(paths) + 1) as pool:
        event_future = pool.submit(fetch_event_page)
        pages = list(pool.map(fetch, paths))
        event_page = event_future.result()
    if all(p is None for p in pages) and event_page is None:
        raise RuntimeError("FIP widgets unreachable")

    days: dict = {}
    for i, page in enumerate(pages[:n_days]):
        if page is None:
            continue
        for m in _fip_parse_day(page, start + timedelta(days=i), tz):
            days.setdefault(m["date"], []).append(m)

    # Backfill days the order-of-play widget hasn't published yet from the
    # event page's streaming list. A day the widget covers is never mixed with
    # stream cards: the widget has players and live scores.
    if event_page is not None:
        stream_cards = _fip_parse_streams(event_page, start.year)
        streamed: dict = {}
        for m in stream_cards:
            # Replays only lend their link: a played tie without its score
            # would be worse than the widget's day, which exists by then.
            if m["kind"] != "replay":
                streamed.setdefault(m["date"], []).append(m)
        for d, ms in streamed.items():
            if d not in days:
                days[d] = ms

        # The widget carries no stream links: give each widget match the link
        # of its tie (same day, same two countries) so "▶ Ver" survives once
        # the full order of play replaces the stream cards.
        links = {
            (m["date"], frozenset((m["country_a"], m["country_b"]))): m["broadcast_url"]
            for m in stream_cards if m["country_a"] and m["country_b"] and m["broadcast_url"]
        }
        for d, ms in days.items():
            for m in ms:
                if not m.get("broadcast_url"):
                    m["broadcast_url"] = links.get((d, frozenset((m.get("country_a"), m.get("country_b")))))

    for day_matches in days.values():
        # Keep FIP's own court order (Center Court, Grand Stand 1, ...) rather
        # than an alphabetical one.
        day_matches.sort(key=lambda x: (
            x.get("court_start") or "99:99",
            x.get("court_order", 99),
            x.get("slot_sort", (5, "")),
        ))

    return {
        "slug": slug,
        "type": ev["category"],
        "status": None,
        "days": [{"date": d, "matches": days[d]} for d in sorted(days.keys())],
        "standings": _fip_parse_groups(pages[-1]) if pages[-1] is not None else [],
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
            fetcher = get_fip_schedule if slug in FIP_EVENTS else get_padel_schedule
            data = await asyncio.to_thread(fetcher, slug)
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
