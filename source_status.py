# FILE: source_status.py
# Bloomberg Macro Dashboard — per-source health for the header status dots.
# Passive: records the outcome of requests the app already makes (no extra
# calls). /api/status reads snapshot(), so it never blocks on a slow source.

import threading
import time
from datetime import datetime, timezone

import requests

SOURCES = {
    "fred":       "FRED",
    "twelvedata": "Twelve Data",
    "fmp":        "FMP",
    "rss":        "News RSS",
    "anthropic":  "Anthropic",
    "fed_web":    "Fed / Conference Board pages",
    "cftc":       "CFTC (COT)",
}

_lock = threading.Lock()
_state: dict[str, dict] = {k: {"last_success": None, "last_error": None, "error": None, "latency_ms": None}
                           for k in SOURCES}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record(source: str, ok: bool, latency_ms: float | None = None, error: str | None = None) -> None:
    """Record one request outcome for a source."""
    with _lock:
        s = _state.setdefault(source, {"last_success": None, "last_error": None, "error": None, "latency_ms": None})
        if ok:
            s["last_success"] = _now()
            s["latency_ms"] = round(latency_ms) if latency_ms is not None else s["latency_ms"]
        else:
            s["last_error"] = _now()
            s["error"] = (error or "request failed")[:120]


def timed_get(source: str, *args, **kwargs) -> requests.Response:
    """requests.get() that also records the outcome for `source`. Same return/raise behavior."""
    t0 = time.perf_counter()
    try:
        resp = requests.get(*args, **kwargs)
    except requests.RequestException as e:
        record(source, False, error=type(e).__name__)
        raise
    ms = (time.perf_counter() - t0) * 1000
    if resp.status_code < 400:
        record(source, True, ms)
    else:
        record(source, False, error=f"HTTP {resp.status_code}")
    return resp


def snapshot() -> dict:
    """
    {sources: {id: {label, state, last_success, last_error, error, latency_ms}}}
    state: ok (latest outcome succeeded), error (latest outcome failed), idle (no requests yet).
    """
    with _lock:
        out = {}
        for key, label in SOURCES.items():
            s = dict(_state.get(key, {}))
            ls, le = s.get("last_success"), s.get("last_error")
            if not ls and not le:
                st = "idle"
            elif le and (not ls or le > ls):
                st = "error"
            else:
                st = "ok"
            out[key] = {"label": label, "state": st, **s}
    return {"sources": out, "timestamp": _now()}
