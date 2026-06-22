# FILE: global_data.py
# OECD Composite Leading Indicators from FRED + CFTC COT positioning.

import csv
import io
import os
import time
import logging
import zipfile
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


# ── COT POSITIONING ────────────────────────────────────────────────────────────
# CFTC Commitment of Traders — Legacy Futures-Only, Non-Commercial Net Long.
# Normalized to −100/+100 vs 52-week range. Cached 24 hours.

COT_INSTRUMENTS = [
    {"id": "sp500",  "name_key": "E-MINI S&P 500",      "label": "S&P 500",    "description": "Large spec equity sentiment"},
    {"id": "gold",   "name_key": "GOLD - COMMODITY",     "label": "Gold",       "description": "Inflation hedge demand"},
    {"id": "tnote",  "name_key": "UST 10Y NOTE",         "label": "10Y T-Note", "description": "Duration / rate view"},
    {"id": "crude",  "name_key": "CRUDE OIL, LIGHT SWEET","label": "Crude Oil",  "description": "Commodity cycle"},
    {"id": "dxy",    "name_key": "USD INDEX",             "label": "USD Index",  "description": "Dollar positioning"},
]

_COT_CACHE: dict = {"data": None, "ts": 0}
COT_TTL = 86400  # 24 hours


def _fetch_cot_year(year: int) -> list:
    """Download and parse CFTC legacy futures-only COT zip for one calendar year."""
    url = f"https://www.cftc.gov/files/dea/history/deacot{year}.zip"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        fname = z.namelist()[0]
        with z.open(fname) as f:
            text = f.read().decode("latin-1", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def _parse_cot_date(row: dict) -> Optional[datetime]:
    """Parse YYMMDD or ISO 8601 date from a COT CSV row."""
    iso = (row.get("Report Date in ISO 8601 Format") or "").strip()
    ymd = (row.get("As of Date in Form YYMMDD") or "").strip()
    try:
        if iso:
            return datetime.strptime(iso[:10], "%Y-%m-%d")
    except ValueError:
        pass
    try:
        if ymd and len(ymd) == 6:
            return datetime.strptime(ymd, "%y%m%d")
    except ValueError:
        pass
    return None


def _extract_net_longs(rows: list, name_key: str) -> list:
    """Return [{date, iso, net}] sorted newest-first for one COT instrument."""
    matched = []
    for r in rows:
        mkt = r.get("Market and Exchange Names", "")
        if name_key.upper() not in mkt.upper():
            continue
        d = _parse_cot_date(r)
        if d is None:
            continue
        try:
            longs  = int(r.get("Noncommercial Positions-Long (All)",  "0").replace(",", ""))
            shorts = int(r.get("Noncommercial Positions-Short (All)", "0").replace(",", ""))
            matched.append({"date": d, "iso": d.strftime("%Y-%m-%d"), "net": longs - shorts})
        except (ValueError, AttributeError):
            pass
    matched.sort(key=lambda x: x["date"], reverse=True)
    seen: set = set()
    deduped = []
    for m in matched:
        if m["iso"] not in seen:
            seen.add(m["iso"])
            deduped.append(m)
    return deduped


def _cot_signal(score: float) -> tuple:
    if score > 75:    return "EXTREME LONG",  "amber"
    if score > 50:    return "NET LONG",       "green"
    if score > 25:    return "SLIGHT LONG",    "green"
    if score >= -25:  return "NEUTRAL",        "muted"
    if score >= -50:  return "SLIGHT SHORT",   "red"
    if score >= -75:  return "NET SHORT",      "red"
    return "EXTREME SHORT", "amber"


def get_cot_positioning() -> dict:
    """Fetch CFTC COT data for 5 instruments. 52-week normalized score. Cached 24 hours."""
    if _COT_CACHE["data"] is not None and (time.time() - _COT_CACHE["ts"]) < COT_TTL:
        log.info("COT: returning cached data.")
        return _COT_CACHE["data"]

    log.info("COT: downloading CFTC legacy futures data...")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    year = datetime.now().year
    all_rows: list = []
    for y in [year, year - 1]:
        try:
            rows = _fetch_cot_year(y)
            all_rows.extend(rows)
            log.info(f"COT: fetched {len(rows)} rows for {y}")
        except Exception as e:
            log.warning(f"COT: fetch failed for {y}: {e}")

    if not all_rows:
        result: dict = {"positions": [], "as_of": None, "timestamp": ts,
                        "error": "COT data unavailable"}
        _COT_CACHE["data"] = result
        _COT_CACHE["ts"]   = time.time()
        return result

    positions = []
    global_as_of: Optional[str] = None

    for inst in COT_INSTRUMENTS:
        entry: dict = {
            "id": inst["id"], "label": inst["label"], "description": inst["description"],
            "net_long": None, "score": None, "signal": "N/A", "signal_color": "muted",
            "week_of": None, "history": [],
        }
        try:
            history = _extract_net_longs(all_rows, inst["name_key"])
            if not history:
                log.warning(f"COT: no rows found for '{inst['name_key']}'")
                positions.append(entry)
                continue

            nets = [h["net"] for h in history[:52]]
            current_net = nets[0]
            lo, hi = min(nets), max(nets)
            rng = hi - lo
            score = round(((current_net - lo) / rng) * 200 - 100, 1) if rng > 0 else 0.0
            signal, color = _cot_signal(score)
            week_of = history[0]["iso"]
            if global_as_of is None:
                global_as_of = week_of

            entry.update({
                "net_long":     current_net,
                "score":        score,
                "signal":       signal,
                "signal_color": color,
                "week_of":      week_of,
                "history":      [{"date": h["iso"], "net": h["net"]} for h in history[:52]],
            })
            log.info(f"COT [{inst['id']}]: net={current_net:,}  score={score}  → {signal}")
        except Exception as e:
            log.warning(f"COT: parse failed [{inst['id']}]: {e}")

        positions.append(entry)

    result = {"positions": positions, "as_of": global_as_of, "timestamp": ts}
    _COT_CACHE["data"] = result
    _COT_CACHE["ts"]   = time.time()
    return result
