"""Local dev server: serves the static frontend and proxies /api/* to the backend.

In production, vercel.json rewrites /api/(.*) to the FastAPI function and serves
the frontend statically. Locally we reproduce that with one process so the
frontend's relative /api fetches resolve.

Usage:
    # 1. start the API (separate terminal):
    cd api && ../.venv/bin/python -m uvicorn index:app --port 8077 --reload
    # 2. start this proxy:
    .venv/bin/python backend/dev_server.py
    # then open http://127.0.0.1:8078
"""

import http.server
import os
import urllib.request

ROOT = os.path.join(os.path.dirname(__file__), "..", "frontend")
API_BACKEND = os.environ.get("API_BACKEND", "http://127.0.0.1:8077")
PORT = int(os.environ.get("PORT", "8078"))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.abspath(ROOT), **kwargs)

    def do_GET(self):
        if self.path.startswith("/api/"):
            try:
                with urllib.request.urlopen(API_BACKEND + self.path, timeout=15) as r:
                    body, code = r.read(), r.status
            except urllib.error.HTTPError as e:
                body, code = e.read(), e.code
            except Exception as e:
                body, code = str(e).encode(), 502
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()


if __name__ == "__main__":
    print(f"Serving {os.path.abspath(ROOT)} on http://127.0.0.1:{PORT}  (proxying /api → {API_BACKEND})")
    http.server.HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
