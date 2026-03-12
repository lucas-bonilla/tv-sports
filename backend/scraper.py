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


def _parse_events(day_block) -> list:
    events = []
    for li in day_block.select("li.dailyevent"):
        sport_icon_el = li.select_one("i[class*='icon-']")
        icon_class = sport_icon_el["class"][0] if sport_icon_el else ""

        event = {
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
    today_pattern = f"{now.day} de"  # e.g. "12 de" — matches day number regardless of locale

    events = []
    date_str = None

    for day_block in soup.select("li.content-item"):
        label = day_block.select_one("span.title-section-widget")
        if not label:
            continue
        label_text = label.get_text(strip=True)
        if today_pattern in label_text:
            if date_str is None:
                strong = label.find("strong")
                rest = label.get_text(strip=True).replace(strong.get_text(strip=True), "").strip() if strong else label_text
                day_name = strong.get_text(strip=True) if strong else ""
                date_str = f"{day_name} {rest}".strip()

            events.extend(_parse_events(day_block))

    if date_str is None:
        # Fallback: use the first day block
        first = soup.select_one("li.content-item span.title-section-widget")
        date_str = first.get_text(strip=True) if first else datetime.now().strftime("%A %d de %B de %Y")
        first_block = soup.select_one("li.content-item")
        if first_block:
            events = _parse_events(first_block)

    return {
        "date": date_str,
        "events": events,
        "scraped_at": datetime.now().isoformat(),
    }


if __name__ == "__main__":
    import json
    data = scrape_events()
    print(f"Date: {data['date']}")
    print(f"Total events: {len(data['events'])}")
    print(json.dumps(data["events"][:3], indent=2, ensure_ascii=False))
