import requests
from bs4 import BeautifulSoup
from datetime import datetime

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
    for day_block in soup.select("ol.daylist li.content-item"):
        label = day_block.select_one("span.title-section-widget")
        if not label or not day_block.select("li.dailyevent"):
            continue
        date_str = _format_label(label)
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


if __name__ == "__main__":
    import json
    data = scrape_events()
    print(f"Date: {data['date']}")
    print(f"Total events: {len(data['events'])}")
    print(json.dumps(data["events"][:3], indent=2, ensure_ascii=False))
