"""Premier Padel (Qatar Airways Premier Padel Tour) data client.

The premierpadel.com site is a Next.js SPA that loads everything from a JSON
API at api-prod.premierpadel.com. We hit that API directly instead of trying to
scrape the rendered HTML (which is empty without JS).

Two things are exposed:
  - the season tournament calendar (all tournaments for a year, with status)
  - the match schedule for a given tournament, grouped by day and court
"""

import requests
from datetime import datetime, timezone

PADEL_API = "https://api-prod.premierpadel.com/api"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
}

# Premier Padel status codes seen on the API. "P" = in progress / playing.
TOURNAMENT_STATUS = {
    "U": "upcoming",
    "P": "live",
    "F": "finished",
    "C": "finished",  # completed
}

CATEGORY_EMOJI = {
    "MAJOR": "🏆",
    "P1": "🥇",
    "P2": "🥈",
    "FINALS": "👑",
}


def _post(path: str, body: dict) -> dict:
    resp = requests.post(f"{PADEL_API}{path}", json=body, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("status"):
        raise RuntimeError(payload.get("message", "Premier Padel API error"))
    return payload.get("data")


def _tournament_status(t: dict) -> str:
    """Derive a status from the raw record, falling back to the dates."""
    raw = (t.get("status") or "").upper()
    if raw in TOURNAMENT_STATUS:
        return TOURNAMENT_STATUS[raw]

    now = datetime.now(timezone.utc)
    start = _parse_utc(t.get("start_date_utc"))
    end = _parse_utc(t.get("end_date_utc"))
    if start and end:
        if now < start:
            return "upcoming"
        if now > end:
            return "finished"
        return "live"
    return "upcoming"


def _parse_utc(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def get_tournaments(year: int | None = None) -> dict:
    """Return the season calendar of tournaments with a normalized status."""
    year = year or datetime.now(timezone.utc).year
    raw = _post("/tournament/getTournaments", {"year": year, "type": "ALL"})

    tournaments = []
    live_slug = None
    for t in raw or []:
        status = _tournament_status(t)
        category = (t.get("type") or "").upper()
        item = {
            "slug": t.get("slug"),
            "name": t.get("display_name") or t.get("name") or t.get("full_name"),
            "country": t.get("country"),
            "city": t.get("city"),
            "category": category,
            "emoji": CATEGORY_EMOJI.get(category, "🎾"),
            "flag_url": t.get("flag_url"),
            "start_date": t.get("start_date_utc"),
            "end_date": t.get("end_date_utc"),
            "prize_money": t.get("prize_money"),
            "status": status,
            "ticket_url": t.get("ticket_url"),
            "where_to_watch_url": t.get("where_to_watch_url"),
        }
        tournaments.append(item)
        if status == "live" and live_slug is None:
            live_slug = t.get("slug")

    tournaments.sort(key=lambda x: x.get("start_date") or "")

    return {
        "year": year,
        "live_slug": live_slug,
        "tournaments": tournaments,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _player_name(player: dict) -> str:
    first = (player.get("first_name") or "").strip()
    last = (player.get("last_name") or "").strip()
    return (f"{first} {last}").strip() or "TBD"


def _team_label(team: dict) -> str:
    players = team.get("players") or []
    names = [_player_name(p) for p in players]
    return " / ".join(n for n in names if n) or "TBD"


def _score_label(team: dict) -> str:
    score = team.get("score") or {}
    sets = []
    for i in range(1, 6):
        s = score.get(f"set{i}")
        if s is None:
            continue
        tie = score.get(f"tie{i}")
        if tie is not None and tie >= 0:
            sets.append(f"{s}({tie})")
        else:
            sets.append(str(s))
    return " ".join(sets)


def get_schedule(slug: str) -> dict:
    """Return the match schedule for a tournament, grouped by day then court."""
    raw = _post("/tournament/getTournamentMatches", {"slug": slug})
    courts = raw.get("courts") or []

    # Flatten all matches, then regroup by day so the UI can show a day at a time.
    days: dict[str, list] = {}
    for court in courts:
        court_name = court.get("court_name", "").strip()
        for m in court.get("matches") or []:
            teams = m.get("teams") or []
            team_a = teams[0] if len(teams) > 0 else {}
            team_b = teams[1] if len(teams) > 1 else {}
            # winner_id holds the winning team_no (1 or 2); "0" means undecided.
            winner_no = str(m.get("winner_id") or "0")

            match = {
                "match_id": m.get("match_id"),
                "court": court_name,
                "date": m.get("date"),
                "time": m.get("start_time"),
                "round": m.get("round_name") or m.get("current_round"),
                "draw_type": m.get("draw_type"),  # MD = main draw
                "status": m.get("status"),
                "status_title": m.get("status_title"),
                "team_a": _team_label(team_a),
                "team_b": _team_label(team_b),
                "score_a": _score_label(team_a),
                "score_b": _score_label(team_b),
                "winner": "a" if winner_no == "1"
                          else "b" if winner_no == "2"
                          else None,
                "broadcast_url": m.get("broadcast_url"),
            }
            day = match["date"] or "?"
            days.setdefault(day, []).append(match)

    # Within each day, order by time then court for a readable order of play.
    for day_matches in days.values():
        day_matches.sort(key=lambda x: (x.get("time") or "99:99", x.get("court") or ""))

    ordered_days = [
        {"date": d, "matches": days[d]}
        for d in sorted(days.keys())
    ]

    return {
        "slug": slug,
        "type": raw.get("type"),
        "status": raw.get("status"),
        "days": ordered_days,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    import json

    cal = get_tournaments()
    print(f"Year {cal['year']} — {len(cal['tournaments'])} tournaments, live={cal['live_slug']}")
    if cal["live_slug"]:
        sched = get_schedule(cal["live_slug"])
        total = sum(len(d["matches"]) for d in sched["days"])
        print(f"Schedule for {cal['live_slug']}: {len(sched['days'])} days, {total} matches")
        print(json.dumps(sched["days"][0]["matches"][:2], indent=2, ensure_ascii=False))
