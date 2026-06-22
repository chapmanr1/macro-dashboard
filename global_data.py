# FILE: global_data.py
# OECD Composite Leading Indicators from FRED.
# CLIs are normalized to a long-run average of 100.
# Rate of change vs. level determines economic phase signal.

import os
import time
import logging
import requests
from typing import Optional
from datetime import datetime, timezone

log = logging.getLogger(__name__)

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
FRED_BASE    = "https://api.stlouisfed.org/fred/series/observations"
CACHE_TTL    = 14400  # 4 hours — monthly data rarely changes intraday

_cache: dict = {"data": None, "ts": 0}

GLOBAL_CLI_SERIES = [
    {"id": "china",    "fred_id": "CHNLOLITONOSTSAM",  "label": "CHINA",    "description": "OECD CLI — China"},
    {"id": "eurozone", "fred_id": "EUALOLITONOSTSAM",   "label": "EUROZONE", "description": "OECD CLI — Euro Area"},
    {"id": "oecd",     "fred_id": "OECDLOLITONOSTSAM",  "label": "OECD",     "description": "OECD CLI — Total"},
    {"id": "japan",    "fred_id": "JPNLOLITONOSTSAM",   "label": "JAPAN",    "description": "OECD CLI — Japan"},
]


def _fetch_obs(series_id: str, limit: int = 14) -> list:
    if not FRED_API_KEY:
        raise ValueError("FRED_API_KEY not configured.")
    resp = requests.get(FRED_BASE, params={
        "series_id": series_id,
        "api_key":   FRED_API_KEY,
        "file_type": "json",
        "sort_order": "desc",
        "limit":     limit,
    }, timeout=10)
    resp.raise_for_status()
    return resp.json().get("observations", [])


def _valid_float(obs: list, skip: int = 0) -> Optional[float]:
    """Return the (skip+1)-th valid observation value, newest-first."""
    count = 0
    for o in obs:
        if o.get("value") not in (".", "", None):
            try:
                v = float(o["value"])
                if count == skip:
                    return v
                count += 1
            except ValueError:
                pass
    return None


def _cli_signal(current: Optional[float], prior: Optional[float]) -> tuple:
    """Return (signal_label, color) based on CLI level vs 100 and direction."""
    if current is None:
        return "N/A", "muted"
    above   = current > 100.05
    below   = current < 99.95
    rising  = prior is not None and current > prior
    falling = prior is not None and current < prior
    if above and rising:   return "EXPANSION",   "green"
    if above and falling:  return "SLOWING",      "amber"
    if below and rising:   return "RECOVERING",   "amber"
    if below and falling:  return "CONTRACTION",  "red"
    if above:              return "ABOVE TREND",  "amber"
    if below:              return "BELOW TREND",  "red"
    return "AT TREND", "muted"


def _sparkline(obs: list, n: int = 12) -> list[float]:
    valid = [float(o["value"]) for o in obs
             if o.get("value") not in (".", "", None)]
    return list(reversed(valid[:n]))


def _sparkline_dated(obs: list, n: int = 12) -> list[dict]:
    valid = [{"date": o["date"], "value": float(o["value"])}
             for o in obs if o.get("value") not in (".", "", None)]
    return list(reversed(valid[:n]))


def get_global_indicators() -> dict:
    """Fetch OECD CLIs for 4 economies from FRED. Cached 4 hours."""
    if _cache["data"] is not None and (time.time() - _cache["ts"]) < CACHE_TTL:
        log.info("Global: returning cached data.")
        return _cache["data"]

    log.info("Global: fetching OECD CLI data from FRED...")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    economies = []

    for s in GLOBAL_CLI_SERIES:
        entry: dict = {
            "id": s["id"], "label": s["label"], "description": s["description"],
            "current": None, "prior": None, "change": None,
            "direction": "FLAT", "signal": "N/A", "signal_color": "muted",
            "as_of": None, "sparkline": [], "sparkline_dated": [],
        }
        try:
            obs     = _fetch_obs(s["fred_id"], limit=14)
            current = _valid_float(obs, skip=0)
            prior   = _valid_float(obs, skip=1)
            change  = round(current - prior, 3) if current is not None and prior is not None else None
            signal, color = _cli_signal(current, prior)

            as_of = next((o["date"] for o in obs
                          if o.get("value") not in (".", "", None)), None)
            entry.update({
                "current":         round(current, 2) if current is not None else None,
                "prior":           round(prior, 2) if prior is not None else None,
                "change":          change,
                "direction":       "UP" if (change or 0) > 0 else "DOWN" if (change or 0) < 0 else "FLAT",
                "signal":          signal,
                "signal_color":    color,
                "as_of":           as_of,
                "sparkline":       _sparkline(obs),
                "sparkline_dated": _sparkline_dated(obs),
            })
            log.info(f"Global CLI [{s['id']}]: {current:.2f} → {signal}")
        except Exception as e:
            log.warning(f"Global CLI fetch failed [{s['fred_id']}]: {e}")

        economies.append(entry)

    result = {"economies": economies, "timestamp": ts}
    _cache["data"]  = result
    _cache["ts"]    = time.time()
    return result
