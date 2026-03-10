# 📺 TV Sports PWA

A Progressive Web App that scrapes sports TV listings from marca.com and displays them in a clean mobile-friendly interface.

## Stack

- **Backend**: Python + FastAPI + BeautifulSoup
- **Frontend**: Vanilla JS + CSS PWA

## Project structure

```
tv-sports/
├── backend/
│   ├── scraper.py        # Scrapes marca.com
│   ├── main.py           # FastAPI server (GET /events)
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── app.js
│   ├── style.css
│   ├── manifest.json     # PWA manifest
│   └── sw.js             # Service worker (offline support)
└── README.md
```

## Running locally

### Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload
# API available at http://localhost:8000/events
```

### Frontend

Serve with any static server — the simplest option:

```bash
cd frontend
python3 -m http.server 3000
# Open http://localhost:3000
```

Or use the VS Code Live Server extension.

## Test the scraper standalone

```bash
cd backend
python scraper.py
```
