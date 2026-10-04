# FILE: tools/preview_ui.py
# Local UI preview — renders the real templates/static files and serves /api/*
# from JSON fixtures, so the front end can be checked without API keys,
# Python 3.11, or any network calls. Development only; not used by Render.
#
#   python3 tools/preview_ui.py            → http://127.0.0.1:5050
#
# Simulate states (comma-separated endpoint names, e.g. "macro,credit"):
#   PREVIEW_FAIL=macro,market   → those endpoints return HTTP 500
#   PREVIEW_EMPTY=news          → those endpoints return empty payloads
#   PREVIEW_DELAY=3             → every API response waits N seconds (skeletons)

from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request

ROOT     = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))

FAIL  = {s.strip() for s in os.environ.get("PREVIEW_FAIL", "").split(",") if s.strip()}
EMPTY = {s.strip() for s in os.environ.get("PREVIEW_EMPTY", "").split(",") if s.strip()}
DELAY = float(os.environ.get("PREVIEW_DELAY", "0") or 0)


def _fixture(name: str) -> dict | None:
    path = FIXTURES / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else None


def _status_payload() -> dict:
    now = datetime.now(timezone.utc)
    def src(label: str, ok: bool, mins: int, ms: int) -> dict:
        t = (now - timedelta(minutes=mins)).isoformat()
        return {"label": label, "state": "ok" if ok else "error",
                "last_success": t if ok else (now - timedelta(hours=3)).isoformat(),
                "last_error": None if ok else t, "latency_ms": ms}
    return {"sources": {
        "fred":        src("FRED", "fred" not in FAIL, 4, 240),
        "twelvedata":  src("Twelve Data", "twelvedata" not in FAIL, 2, 610),
        "fmp":         src("FMP", True, 55, 380),
        "rss":         src("News RSS", "news" not in FAIL, 9, 450),
        "anthropic":   src("Anthropic", True, 120, 41000),
        "fed_web":     src("Fed / Conference Board pages", True, 300, 320),
        "cftc":        src("CFTC (COT)", True, 600, 900),
    }, "timestamp": now.isoformat()}


def _history_payload() -> dict:
    ids = [s.strip().upper() for s in request.args.get("series", "").split(",") if s.strip()][:4]
    n = int(request.args.get("obs", "60") or 60)
    start = datetime(2021, 10, 1)
    out = []
    for k, sid in enumerate(ids):
        data = [{"date": (start + timedelta(days=30.4 * i)).strftime("%Y-%m-01"),
                 "value": round(3 + math.sin(i / 7 + k) * 1.2 + i * 0.01, 2)} for i in range(n)]
        out.append({"id": sid, "label": sid, "unit": "", "data": data})
    return {"series": out}


@app.route("/")
def index():
    return render_template("index.html", build_id="preview")


@app.route("/api/<path:endpoint>", methods=["GET", "POST"])
def api(endpoint: str):
    if DELAY:
        time.sleep(DELAY)
    key = endpoint.split("/")[0]
    if key in FAIL or endpoint in FAIL:
        return jsonify({"error": f"PREVIEW_FAIL simulated failure for {endpoint}"}), 500
    if key in EMPTY or endpoint in EMPTY:
        return jsonify({"series": [], "articles": [], "events": [], "economies": [], "timestamp": datetime.utcnow().isoformat()})

    if endpoint == "status":
        return jsonify(_status_payload())
    if endpoint == "macro/history":
        return jsonify(_history_payload())
    if endpoint == "calendar":
        days = int(request.args.get("days", "8") or 8)
        return jsonify(_fixture("calendar_days_400" if days > 8 else "calendar_days_8"))
    if endpoint == "watchlist":
        tickers = [t for t in request.args.get("tickers", "").split(",") if t]
        return jsonify({"quotes": [{"symbol": t, "price": 100 + i * 37.5, "change": 1.25 - i, "pct_change": 0.8 - i * 0.6}
                                   for i, t in enumerate(tickers)], "timestamp": datetime.utcnow().isoformat()})
    if endpoint in ("watchlist/save", "briefing/refresh"):
        return jsonify({"status": "ok"})

    data = _fixture(endpoint.replace("/", "_"))
    if data is None:
        return jsonify({"error": f"no preview fixture for /api/{endpoint}"}), 404
    return jsonify(data)


if __name__ == "__main__":
    print("UI preview → http://127.0.0.1:5050  (fixtures, no API keys, no network)")
    app.run(host="127.0.0.1", port=int(os.environ.get("PREVIEW_PORT", "5050")), debug=False)
