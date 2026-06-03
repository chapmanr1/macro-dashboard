# FILE: fed_watch.py
# Bloomberg Macro Dashboard — Implied Fed Rate Path Tracker
# Derives expected Fed Funds path from Treasury yields vs current Fed Funds target.
# Reuses fred_data.get_yields() cache to avoid extra FRED API calls on the hot path.
# Only fetches DFEDTARL/DFEDTARU (2 calls) for the target bounds.

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


def _fetch_series(series_id: str) -> Optional[float]:
    """Fetch single latest observation. Returns None on any failure — never raises."""
    if not FRED_API_KEY:
        return None
    params = {
        "series_id":         series_id,
        "api_key":           FRED_API_KEY,
        "file_type":         "json",
        "sort_order":        "desc",
        "limit":             3,
        "observation_start": (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%d"),
    }
    try:
        resp = requests.get(FRED_BASE, params=params, timeout=6)
        resp.raise_for_status()
        obs = resp.json().get("observations", [])
        valid = [o for o in obs if o.get("value") not in (".", "", None)]
        return float(valid[0]["value"]) if valid else None
    except Exception as e:
        log.debug(f"FedWatch _fetch_series {series_id}: {e}")
        return None


def _yield_from_cache(yields_data: list, label: str) -> Optional[float]:
    """Extract a yield value from the cached yields list by label."""
    for y in yields_data:
        if y.get("label") == label and y.get("value") is not None:
            return float(y["value"])
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
    """
    Build implied rate path. Reuses fred_data yields cache for 3M/6M/1Y yields
    (avoids extra FRED calls). Only fetches DFEDTARL/DFEDTARU for the target bounds.
    """
    ts = datetime.utcnow().isoformat()

    # Get Fed Funds target bounds (2 FRED calls, fast series)
    lower = _fetch_series("DFEDTARL")
    upper = _fetch_series("DFEDTARU")

    if lower is not None and upper is not None:
        current_ff = (lower + upper) / 2
    elif lower is not None:
        current_ff = lower + 0.125
    elif upper is not None:
        current_ff = upper - 0.125
    else:
        current_ff = None

    # Pull 3M/6M/1Y yields from fred_data cache (no extra FRED calls)
    tb3 = tb6 = dgs1 = None
    try:
        from fred_data import get_yields
        yields_result = get_yields()
        yields_list = yields_result.get("yields", [])
        # Labels match fred_data.py YIELD_SERIES definitions
        tb3  = _yield_from_cache(yields_list, "3MO")
        tb6  = _yield_from_cache(yields_list, "6MO")
        dgs1 = _yield_from_cache(yields_list, "1YR")
    except Exception as e:
        log.warning(f"FedWatch: could not pull yields from fred_data cache: {e}")

    horizons = [
        ("3M",  "3-month", tb3),
        ("6M",  "6-month", tb6),
        ("12M", "1-year",  dgs1),
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
    """Return implied Fed rate path. Cached 1 hour. Never raises — returns error dict on failure."""
    if _cache_valid():
        log.info("FedWatch: returning cached data.")
        return _cache["data"]

    log.info("FedWatch: building rate path...")
    try:
        result = _fetch_fed_watch()
        _cache["data"] = result
        _cache["ts"]   = time.time()
        log.info(f"FedWatch: done — signal={result.get('signal')}, FF={result.get('current_ff')}")
        return result
    except Exception as e:
        log.error(f"FedWatch: failed — {e}")
        if _cache["data"] is not None:
            return _cache["data"]
        return {"error": str(e), "timestamp": datetime.utcnow().isoformat()}


if __name__ == "__main__":
    import json
    print(json.dumps(get_fed_watch(), indent=2))
