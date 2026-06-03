# FILE: fed_watch.py
# Bloomberg Macro Dashboard — Implied Fed Rate Path Tracker
# Derives expected Fed Funds path from FRED T-bill yields vs current target.
# No options data required — uses 3M/6M/1Y Treasury yields as forward proxies.

import os
import time
import logging
import requests
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
FRED_BASE    = "https://api.stlouisfed.org/fred/series/observations"
CACHE_TTL    = 3600  # 1 hour

_cache = {"data": None, "ts": 0}


def _cache_valid() -> bool:
    return _cache["data"] is not None and (time.time() - _cache["ts"]) < CACHE_TTL


def _fetch_series(series_id: str, limit: int = 3) -> list[dict]:
    if not FRED_API_KEY:
        raise ValueError("FRED_API_KEY not set")
    params = {
        "series_id":         series_id,
        "api_key":           FRED_API_KEY,
        "file_type":         "json",
        "sort_order":        "desc",
        "limit":             limit,
        "observation_start": (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d"),
    }
    for attempt in range(3):
        resp = requests.get(FRED_BASE, params=params, timeout=10)
        if resp.status_code == 429:
            wait = 2 ** attempt
            log.warning(f"FRED rate limit on {series_id} (attempt {attempt+1}), retrying in {wait}s")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        obs = resp.json().get("observations", [])
        return [o for o in obs if o.get("value") not in (".", "", None)]
    raise RuntimeError(f"FRED rate limited after 3 attempts: {series_id}")


def _latest(obs: list[dict]) -> Optional[float]:
    if not obs:
        return None
    try:
        return float(obs[0]["value"])
    except (KeyError, TypeError, ValueError):
        return None


def _cuts_priced(current_ff: float, forward_yield: float) -> float:
    """Positive = cuts priced in; negative = hikes priced in."""
    return round((current_ff - forward_yield) / 0.25, 1)


def _build_signal(path: list[dict]) -> tuple[str, str]:
    twelve_m = next((p for p in path if p["label"] == "12M"), None)
    if not twelve_m or twelve_m.get("cuts_priced") is None:
        return "UNKNOWN", "Insufficient data to derive implied rate path."
    c = twelve_m["cuts_priced"]
    if c >= 3:
        return "DOVISH", f"Market pricing {c:.1f} cuts over 12 months — significant easing expected."
    elif c >= 1.5:
        return "MILDLY DOVISH", f"Market pricing {c:.1f} cuts over 12 months — modest easing expected."
    elif c >= 0.3:
        return "SLIGHT EASING BIAS", f"Market pricing {c:.1f} cut(s) over 12 months — neutral to slightly dovish."
    elif c <= -1.5:
        return "HAWKISH", f"Market pricing {abs(c):.1f} hike(s) over 12 months — tightening bias remains."
    elif c <= -0.3:
        return "SLIGHT TIGHTENING BIAS", f"Market pricing {abs(c):.1f} hike(s) over 12 months — modest hawkish lean."
    else:
        return "ON HOLD", "Market pricing no change in Fed Funds rate over 12 months."


def _fetch_fed_watch() -> dict:
    ts = datetime.utcnow().isoformat()

    lower_obs = _fetch_series("DFEDTARL", limit=3)
    upper_obs = _fetch_series("DFEDTARU", limit=3)
    tb3ms_obs = _fetch_series("TB3MS",    limit=3)
    tb6ms_obs = _fetch_series("TB6MS",    limit=3)
    dgs1_obs  = _fetch_series("DGS1",     limit=3)

    lower = _latest(lower_obs)
    upper = _latest(upper_obs)

    if lower is not None and upper is not None:
        current_ff = (lower + upper) / 2
    elif lower is not None:
        current_ff = lower + 0.125
    elif upper is not None:
        current_ff = upper - 0.125
    else:
        current_ff = None

    horizons = [
        ("3M",  "3-month",  _latest(tb3ms_obs)),
        ("6M",  "6-month",  _latest(tb6ms_obs)),
        ("12M", "1-year",   _latest(dgs1_obs)),
    ]

    path = []
    for label, horizon, yield_val in horizons:
        if current_ff is not None and yield_val is not None:
            diff  = current_ff - yield_val
            cuts  = _cuts_priced(current_ff, yield_val)
            entry = {
                "label":       label,
                "horizon":     horizon,
                "yield":       round(yield_val, 2),
                "current_ff":  round(current_ff, 2),
                "diff":        round(diff, 2),
                "cuts_priced": cuts,
                "direction":   "CUTS" if cuts > 0.3 else "HIKES" if cuts < -0.3 else "HOLD",
            }
        else:
            entry = {
                "label":       label,
                "horizon":     horizon,
                "yield":       round(yield_val, 2) if yield_val is not None else None,
                "current_ff":  round(current_ff, 2) if current_ff is not None else None,
                "diff":        None,
                "cuts_priced": None,
                "direction":   "UNKNOWN",
            }
        path.append(entry)

    signal, signal_detail = _build_signal(path)

    return {
        "current_ff":    round(current_ff, 2) if current_ff is not None else None,
        "ff_lower":      round(lower, 2) if lower is not None else None,
        "ff_upper":      round(upper, 2) if upper is not None else None,
        "implied_path":  path,
        "signal":        signal,
        "signal_detail": signal_detail,
        "timestamp":     ts,
    }


def get_fed_watch() -> dict:
    """Return implied Fed rate path derived from FRED T-bill yields. Cached 1 hour."""
    if _cache_valid():
        log.info("FedWatch: returning cached data.")
        return _cache["data"]

    log.info("FedWatch: fetching fresh FRED data...")
    try:
        result = _fetch_fed_watch()
        _cache["data"] = result
        _cache["ts"]   = time.time()
        log.info(f"FedWatch: fetched — signal={result.get('signal')}, FF={result.get('current_ff')}")
        return result
    except Exception as e:
        log.error(f"FedWatch: fetch failed — {e}")
        if _cache["data"] is not None:
            log.info("FedWatch: returning stale cache after error.")
            return _cache["data"]
        return {"error": str(e), "timestamp": datetime.utcnow().isoformat()}


if __name__ == "__main__":
    import json
    print(json.dumps(get_fed_watch(), indent=2))
