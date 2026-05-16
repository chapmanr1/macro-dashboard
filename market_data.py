# FILE: market_data.py
# Bloomberg Macro Dashboard — Market Data via Twelve Data
# Fetches equities, futures, VIX, sectors, commodities, currencies.

import time
import logging
from datetime import datetime, timezone
from typing import Optional
from twelve_data import get_quotes, get_time_series, to_td_symbol

log = logging.getLogger(__name__)

CACHE_TTL = 300  # 5 minutes for market data

_cache = {"data": None, "ts": 0}

def _cache_valid():
    return _cache["data"] is not None and (time.time() - _cache["ts"]) < CACHE_TTL

# ── SYMBOL DEFINITIONS ────────────────────────────────────────
INDICES = [
    {"symbol": "^GSPC", "label": "S&P 500",    "abbr": "SPX"},
    {"symbol": "^DJI",  "label": "DOW JONES",  "abbr": "DJIA"},
    {"symbol": "^IXIC", "label": "NASDAQ",      "abbr": "NDX"},
    {"symbol": "^RUT",  "label": "RUSSELL 2K",  "abbr": "RUT"},
]

FUTURES = [
    {"symbol": "ES=F",  "label": "S&P FUT",    "group": "equity"},
    {"symbol": "NQ=F",  "label": "NQ FUT",     "group": "equity"},
    {"symbol": "YM=F",  "label": "DOW FUT",    "group": "equity"},
]

SECTORS = [
    {"symbol": "XLK",  "label": "TECHNOLOGY",       "short": "TECH"},
    {"symbol": "XLF",  "label": "FINANCIALS",        "short": "FINS"},
    {"symbol": "XLE",  "label": "ENERGY",            "short": "ENGY"},
    {"symbol": "XLV",  "label": "HEALTH CARE",       "short": "HLTH"},
    {"symbol": "XLI",  "label": "INDUSTRIALS",       "short": "INDU"},
    {"symbol": "XLB",  "label": "MATERIALS",         "short": "MATL"},
    {"symbol": "XLY",  "label": "CONS DISC",         "short": "DISC"},
    {"symbol": "XLP",  "label": "CONS STAPLES",      "short": "STPL"},
    {"symbol": "XLRE", "label": "REAL ESTATE",       "short": "REIT"},
    {"symbol": "XLU",  "label": "UTILITIES",         "short": "UTIL"},
    {"symbol": "XLC",  "label": "COMM SERVICES",     "short": "COMM"},
]

COMMODITIES = [
    {"symbol": "CL=F",  "label": "CRUDE OIL",   "suffix": "$/bbl", "decimals": 2},
    {"symbol": "GC=F",  "label": "GOLD",        "suffix": "$/oz",  "decimals": 2},
    {"symbol": "SI=F",  "label": "SILVER",      "suffix": "$/oz",  "decimals": 3},
    {"symbol": "HG=F",  "label": "COPPER",      "suffix": "$/lb",  "decimals": 3},
    {"symbol": "NG=F",  "label": "NAT GAS",     "suffix": "$/mmBtu","decimals": 3},
]

CURRENCIES = [
    {"symbol": "DX-Y.NYB", "label": "DXY",     "description": "US Dollar Index", "decimals": 2},
    {"symbol": "EURUSD=X", "label": "EUR/USD",  "description": "Euro / US Dollar","decimals": 4},
]
VIX_SYMBOL = "^VIX"
VIX_DISPLAY_MAX = 50

# YTD cache: {td_symbol: {"jan_close": float, "ts": float}}
_ytd_cache: dict = {}
_YTD_TTL = 24 * 3600  # refresh Jan close once per day


# ── QUOTE PARSING ─────────────────────────────────────────────

def _parse_quote(quote: dict) -> Optional[dict]:
    """Extract price, change, pct_change, direction from a TD /quote response."""
    try:
        price  = float(quote["close"])
        change = float(quote["change"])
        pct    = float(quote["percent_change"])
        return {
            "price":      round(price,  4),
            "change":     round(change, 4),
            "pct_change": round(pct,    3),
            "direction":  "UP" if change > 0.0005 else "DOWN" if change < -0.0005 else "FLAT",
            "ytd_pct":    None,
        }
    except (KeyError, TypeError, ValueError):
        return None


def _ytd_pct(symbol: str, current_price: float) -> Optional[float]:
    """
    Return year-to-date % change for a symbol. Uses a 24h cache of the
    Jan 1 close so we don't burn API calls on every market refresh.
    Fetches via /time_series on cache miss.
    """
    td_sym = to_td_symbol(symbol)
    now = time.time()
    cached = _ytd_cache.get(td_sym)
    if cached and (now - cached["ts"]) < _YTD_TTL:
        jan_close = cached["jan_close"]
    else:
        try:
            # 1 bar from the start of the year — ask for enough history to cover Jan 1
            bars = get_time_series(td_sym, "1month", 14)  # ~14 months of monthly bars
            if not bars:
                return None
            # Find the bar whose datetime starts with the current year
            year = str(datetime.now().year)
            jan_bar = next((b for b in bars if b.get("datetime", "").startswith(year)), None)
            if jan_bar is None:
                # Fall back: oldest available bar
                jan_bar = bars[0]
            jan_close = float(jan_bar["open"])  # open of first bar ≈ Jan 1 level
            _ytd_cache[td_sym] = {"jan_close": jan_close, "ts": now}
        except Exception as e:
            log.debug(f"YTD fetch failed for {td_sym}: {e}")
            return None
    if jan_close and jan_close != 0:
        return round((current_price - jan_close) / jan_close * 100, 2)
    return None


def _null_instrument(label, symbol=""):
    return {
        "symbol":     symbol,
        "label":      label,
        "price":      None,
        "change":     None,
        "pct_change": None,
        "ytd_pct":    None,
        "direction":  "FLAT",
        "error":      True,
    }


# ── SIGNAL GENERATORS ─────────────────────────────────────────
def _futures_signal(futures_data):
    equity = [f for f in futures_data if f.get("group") == "equity" and f.get("pct_change") is not None]
    if not equity:
        return "NEUTRAL", "Futures data unavailable — cannot determine pre-market bias."
    avg = sum(f["pct_change"] for f in equity) / len(equity)
    all_pos = all(f["pct_change"] > 0 for f in equity)
    all_neg = all(f["pct_change"] < 0 for f in equity)

    if all_pos and avg > 0.4:
        return "RISK ON", "All three major equity futures are pointing higher — pre-market bias is constructive."
    elif all_pos and avg > 0.1:
        return "MILDLY POSITIVE", "Equity futures tilted higher but gains are modest — cautiously constructive tone."
    elif all_neg and avg < -0.4:
        return "RISK OFF", "All three major equity futures under pressure — pre-market de-risking underway."
    elif all_neg:
        return "MILDLY NEGATIVE", "Equity futures slightly lower — modest pre-market headwinds."
    else:
        return "MIXED", "Divergence across equity futures — no clear directional bias pre-market."


def _vix_signal(vix_value):
    from config import VIX_LEVELS, VIX_DISPLAY_MAX
    if vix_value is None:
        return {"label": "UNKNOWN", "color": "muted", "gauge_pct": 0,
                "description": "VIX data unavailable."}
    for level in VIX_LEVELS:
        if vix_value <= level["max"]:
            gauge_pct = min(100, round(vix_value / VIX_DISPLAY_MAX * 100, 1))
            return {
                "label":       level["label"],
                "color":       level["color"],
                "gauge_pct":   gauge_pct,
                "description": level["description"],
            }
    return {"label": "EXTREME FEAR", "color": "red", "gauge_pct": 100,
            "description": VIX_LEVELS[-1]["description"]}


def _sector_signal(sectors_data):
    valid = [s for s in sectors_data if s.get("pct_change") is not None]
    if not valid:
        return "NO DATA", "Sector data unavailable."

    positive = sum(1 for s in valid if s["pct_change"] > 0)
    negative = sum(1 for s in valid if s["pct_change"] < 0)
    total    = len(valid)

    defensive_sym = {"XLU", "XLP", "XLV", "XLRE"}
    cyclical_sym  = {"XLK", "XLF", "XLE", "XLI", "XLB", "XLY", "XLC"}

    def avg_pct(syms):
        vals = [s["pct_change"] for s in valid if s["symbol"] in syms]
        return sum(vals) / len(vals) if vals else 0

    def_avg  = avg_pct(defensive_sym)
    cyc_avg  = avg_pct(cyclical_sym)

    if positive >= 9:
        if cyc_avg > def_avg + 0.3:
            return "RISK ON — CYCLICAL LEADERSHIP", "Wide breadth led by cyclicals signals investor confidence in durable economic expansion."
        return "BROAD ADVANCE", "Near-unanimous sector participation suggests genuine macro momentum rather than narrow speculation."
    elif negative >= 9:
        if def_avg > cyc_avg + 0.3:
            return "RISK OFF — DEFENSIVE ROTATION", "Defensive sectors outperforming as money rotates away from growth-sensitive cyclicals."
        return "BROAD SELL-OFF", "Wide sector decline points to macro-driven forced de-risking, not isolated sector weakness."
    elif cyc_avg > def_avg + 0.5:
        return "CYCLICAL ROTATION — GROWTH POSITIVE", "Cyclicals outperforming defensives by a meaningful margin — markets are pricing in growth resilience."
    elif def_avg > cyc_avg + 0.5:
        return "DEFENSIVE ROTATION — RISK AVERSE", "Defensives outperforming cyclicals signals elevated macro uncertainty and growth concern."
    elif positive > negative:
        return "SLIGHT POSITIVE BIAS", f"{positive} of {total} sectors advancing — modest constructive tone without clear leadership."
    elif negative > positive:
        return "SLIGHT NEGATIVE BIAS", f"{negative} of {total} sectors declining — modest risk-off tone without clear defensive rotation."
    else:
        return "MIXED — NO CLEAR SIGNAL", "Sector performance is balanced with no dominant rotation signal — wait for confirmation."


def _dollar_signal(dxy_value):
    from config import DOLLAR_THRESHOLDS as D
    if dxy_value is None:
        return "UNKNOWN", "DXY data unavailable."
    if dxy_value >= D["very_strong"]:
        return "USD VERY STRONG", f"DXY at {dxy_value:.1f} is significantly elevated — meaningful headwind for commodities, EM assets, and multinational earnings."
    elif dxy_value >= D["strong"]:
        return "USD STRONG", f"DXY at {dxy_value:.1f} reflects dollar demand — watch for drag on commodity prices and export-sensitive earnings."
    elif dxy_value >= D["neutral_hi"]:
        return "USD FIRM", f"DXY at {dxy_value:.1f} is near-neutral but tilted toward dollar strength — balanced currency conditions."
    elif dxy_value >= D["neutral_lo"]:
        return "USD NEUTRAL", f"DXY at {dxy_value:.1f} is in neutral territory — no outsized currency headwinds or tailwinds."
    elif dxy_value >= D["weak"]:
        return "USD SOFT", f"DXY at {dxy_value:.1f} is mildly weak — modest tailwind for commodities and international risk assets."
    else:
        return "USD WEAK", f"DXY at {dxy_value:.1f} is meaningfully depressed — significant tailwind for commodities, EM, and international equities."


# ── MAIN ENTRY POINT ──────────────────────────────────────────
def get_market() -> dict:
    if _cache_valid():
        log.info("Market: returning cached data.")
        return _cache["data"]

    log.info("Market: fetching fresh data from Twelve Data...")
    ts = datetime.now(timezone.utc).isoformat()
    try:
        return _fetch_market_data()
    except Exception as e:
        log.error(f"Market: fetch failed — {e}")
        if _cache["data"] is not None:
            log.info("Market: returning stale cache after error.")
            return _cache["data"]
        return {"indices": [], "futures": [], "sectors": [], "commodities": [], "currencies": [],
                "timestamp": ts, "error": str(e)}


def _fetch_market_data() -> dict:
    ts = datetime.now(timezone.utc).isoformat()

    # Collect all symbols into three batches (one API call each)
    index_syms     = [i["symbol"] for i in INDICES]
    futures_syms   = [f["symbol"] for f in FUTURES]
    vix_syms       = [VIX_SYMBOL]
    sector_syms    = [s["symbol"] for s in SECTORS]
    commodity_syms = [c["symbol"] for c in COMMODITIES]
    currency_syms  = [c["symbol"] for c in CURRENCIES]

    # Batch 1: indices + futures + VIX
    batch1 = get_quotes(index_syms + futures_syms + vix_syms)
    # Batch 2: sectors
    batch2 = get_quotes(sector_syms)
    # Batch 3: commodities + currencies
    batch3 = get_quotes(commodity_syms + currency_syms)

    all_quotes = {**batch1, **batch2, **batch3}

    # ── INDICES ───────────────────────────────────────────────
    indices_out = []
    for idx in INDICES:
        q = all_quotes.get(idx["symbol"])
        stats = _parse_quote(q) if q else None
        if stats:
            stats["ytd_pct"] = _ytd_pct(idx["symbol"], stats["price"])
            indices_out.append({**idx, **stats})
        else:
            indices_out.append({**idx, **_null_instrument(idx["label"], idx["symbol"])})

    # ── FUTURES ───────────────────────────────────────────────
    futures_out = []
    for fut in FUTURES:
        q = all_quotes.get(fut["symbol"])
        stats = _parse_quote(q) if q else None
        if stats:
            futures_out.append({**fut, **stats})
        else:
            futures_out.append({**fut, **_null_instrument(fut["label"], fut["symbol"])})

    futures_signal, futures_detail = _futures_signal(futures_out)

    # ── VIX ───────────────────────────────────────────────────
    vix_q     = all_quotes.get(VIX_SYMBOL)
    vix_stats = _parse_quote(vix_q) if vix_q else None
    vix_value = vix_stats["price"] if vix_stats else None
    vix_info  = _vix_signal(vix_value)
    vix_out   = {
        "symbol":     VIX_SYMBOL,
        "label":      "VIX",
        "value":      vix_value,
        "change":     vix_stats["change"]     if vix_stats else None,
        "pct_change": vix_stats["pct_change"] if vix_stats else None,
        "direction":  vix_stats["direction"]  if vix_stats else "FLAT",
        **vix_info,
    }

    # ── SECTORS ───────────────────────────────────────────────
    sectors_out = []
    for sec in SECTORS:
        q = all_quotes.get(sec["symbol"])
        stats = _parse_quote(q) if q else None
        if stats:
            stats["ytd_pct"] = _ytd_pct(sec["symbol"], stats["price"])
            sectors_out.append({**sec, **stats})
        else:
            sectors_out.append({**sec, **_null_instrument(sec["label"], sec["symbol"])})

    sector_signal, sector_detail = _sector_signal(sectors_out)

    # ── COMMODITIES ───────────────────────────────────────────
    commodities_out = []
    for com in COMMODITIES:
        q = all_quotes.get(com["symbol"])
        stats = _parse_quote(q) if q else None
        entry = {**com}
        if stats:
            entry.update({
                "price":      round(stats["price"],      com["decimals"]),
                "change":     round(stats["change"],     com["decimals"]),
                "pct_change": stats["pct_change"],
                "direction":  stats["direction"],
            })
        else:
            entry.update({"price": None, "change": None, "pct_change": None, "direction": "FLAT"})
        commodities_out.append(entry)

    # ── CURRENCIES ────────────────────────────────────────────
    currencies_out = []
    dxy_value = None
    for cur in CURRENCIES:
        q = all_quotes.get(cur["symbol"])
        stats = _parse_quote(q) if q else None
        entry = {**cur}
        if stats:
            entry.update({
                "price":      round(stats["price"],      cur["decimals"]),
                "change":     round(stats["change"],     cur["decimals"]),
                "pct_change": stats["pct_change"],
                "direction":  stats["direction"],
            })
            if cur["label"] == "DXY":
                dxy_value = stats["price"]
        else:
            entry.update({"price": None, "change": None, "pct_change": None, "direction": "FLAT"})
        currencies_out.append(entry)

    dollar_signal, dollar_detail = _dollar_signal(dxy_value)

    result = {
        "indices":        indices_out,
        "futures":        futures_out,
        "futures_signal": futures_signal,
        "futures_detail": futures_detail,
        "vix":            vix_out,
        "sectors":        sectors_out,
        "sector_signal":  sector_signal,
        "sector_detail":  sector_detail,
        "commodities":    commodities_out,
        "currencies":     currencies_out,
        "dollar_signal":  dollar_signal,
        "dollar_detail":  dollar_detail,
        "timestamp":      ts,
    }

    _cache["data"] = result
    _cache["ts"]   = time.time()
    log.info(f"Market: fetched — futures={futures_signal}, VIX={vix_value}, sector={sector_signal}")
    return result


# ── STANDALONE TEST ───────────────────────────────────────────
if __name__ == "__main__":
    import json
    print(json.dumps(get_market(), indent=2, default=str))
