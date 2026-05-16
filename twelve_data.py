# FILE: twelve_data.py
# Bloomberg Macro Dashboard — Twelve Data API wrapper
# Replaces yfinance. Rate-limited to 8 calls/min (free tier).

import time
import logging
import requests
from collections import deque
from config import TWELVE_DATA_API_KEY

log = logging.getLogger(__name__)

BASE_URL = "https://api.twelvedata.com"

# ── SYMBOL MAP (yfinance → Twelve Data) ───────────────────────
# Equity ETFs and common stocks are identical; only special symbols differ.
SYMBOL_MAP: dict[str, str] = {
    # Indices: direct TD index symbols require Pro tier; use ETF proxies instead.
    # QQQ tracks Nasdaq-100 (not composite), which diverges slightly from IXIC.
    # VIXY tracks front-month VIX futures (~15-25% below spot VIX in contango).
    "^GSPC":    "SPY",
    "^DJI":     "DIA",
    "^IXIC":    "QQQ",
    "^RUT":     "IWM",
    "^VIX":     "VIXY",
    # Futures: CME futures unavailable on free tier; ETF proxies trade extended
    # hours and reflect pre-market direction, which is the purpose of this data.
    "ES=F":     "SPY",
    "NQ=F":     "QQQ",
    "YM=F":     "DIA",
    # Commodities and currencies (unchanged — confirmed working)
    "CL=F":     "WTI/USD",
    "GC=F":     "XAU/USD",
    "SI=F":     "XAG/USD",
    "HG=F":     "HG1",
    "NG=F":     "NG/USD",
    "DX-Y.NYB": "DXY",
    "DX=F":     "DXY",
    "EURUSD=X": "EUR/USD",
}

def to_td_symbol(symbol: str) -> str:
    """Translate a yfinance symbol to its Twelve Data equivalent."""
    return SYMBOL_MAP.get(symbol.upper(), symbol.upper())


# ── RATE LIMITER ──────────────────────────────────────────────
# Free tier: 8 API calls per minute.
_call_times: deque = deque()
_RATE_LIMIT = 8
_RATE_WINDOW = 60.0


def _rate_limit() -> None:
    """Block until we are under the 8-calls/minute ceiling."""
    now = time.time()
    # Evict timestamps older than the window
    while _call_times and now - _call_times[0] >= _RATE_WINDOW:
        _call_times.popleft()
    if len(_call_times) >= _RATE_LIMIT:
        sleep_for = _RATE_WINDOW - (now - _call_times[0]) + 0.05
        if sleep_for > 0:
            log.debug(f"Rate limit: sleeping {sleep_for:.2f}s")
            time.sleep(sleep_for)
    _call_times.append(time.time())


# ── BASE HTTP ─────────────────────────────────────────────────

def _get(endpoint: str, params: dict) -> dict:
    """Make a rate-limited GET to the Twelve Data API."""
    if not TWELVE_DATA_API_KEY:
        raise RuntimeError("TWELVE_DATA_API_KEY not set")
    _rate_limit()
    params = {**params, "apikey": TWELVE_DATA_API_KEY}
    resp = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("status") == "error":
        raise RuntimeError(f"Twelve Data error: {data.get('message', data)}")
    return data


# ── PUBLIC API ────────────────────────────────────────────────

def get_quotes(symbols: list[str]) -> dict[str, dict]:
    """
    Batch /quote for a list of symbols. Symbols may be in yfinance format
    (e.g. '^GSPC') or Twelve Data format — both are normalised via SYMBOL_MAP.

    Returns a dict keyed by the ORIGINAL symbol as passed in, so callers
    don't need to know about the translation.
    Returns an empty dict on failure; individual failed symbols are omitted.
    """
    if not symbols:
        return {}
    td_syms = [to_td_symbol(s) for s in symbols]
    orig_by_td = {to_td_symbol(s): s for s in symbols}
    try:
        data = _get("quote", {"symbol": ",".join(td_syms)})
        # Single-symbol response is a dict; multi-symbol is {symbol: dict}
        if td_syms and len(td_syms) == 1:
            data = {td_syms[0]: data}
        result: dict[str, dict] = {}
        for td_sym, quote in data.items():
            if not isinstance(quote, dict):
                continue
            if quote.get("status") == "error":
                log.warning(f"TD quote error for {td_sym}: {quote.get('message', '')}")
                continue
            orig = orig_by_td.get(td_sym, td_sym)
            result[orig] = quote
        return result
    except Exception as e:
        log.warning(f"get_quotes failed: {e}")
        return {}


def get_time_series(symbol: str, interval: str, outputsize: int) -> list[dict]:
    """
    /time_series for a symbol. interval is a TD interval string (e.g. '1day',
    '5min', '1week'). Returns a list of OHLCV dicts sorted OLDEST FIRST
    (TD returns newest-first; we reverse). Returns [] on failure.
    """
    td_sym = to_td_symbol(symbol)
    try:
        data = _get("time_series", {
            "symbol":     td_sym,
            "interval":   interval,
            "outputsize": outputsize,
        })
        values = data.get("values", [])
        if not values:
            log.warning(f"get_time_series: no values for {td_sym} interval={interval}")
            return []
        # TD returns newest-first — reverse to oldest-first for MA computation and charting
        return list(reversed(values))
    except Exception as e:
        log.warning(f"get_time_series failed [{td_sym} {interval}]: {e}")
        return []


def search_symbols(query: str, max_results: int = 8) -> list[dict]:
    """
    /symbol_search. Returns a list of dicts with keys:
      symbol, name, exchange, type
    Returns [] on failure.
    """
    try:
        data = _get("symbol_search", {"symbol": query, "outputsize": max_results})
        out = []
        for item in data.get("data", []):
            out.append({
                "symbol":   item.get("symbol", ""),
                "name":     item.get("instrument_name", ""),
                "exchange": item.get("exchange", ""),
                "type":     item.get("instrument_type", ""),
            })
            if len(out) >= max_results:
                break
        return out
    except Exception as e:
        log.warning(f"search_symbols failed for '{query}': {e}")
        return []


def get_profile(symbol: str) -> dict:
    """
    /profile for a company. Returns a dict with company metadata.
    Returns {} on any error — endpoint availability on free tier is uncertain.
    """
    td_sym = to_td_symbol(symbol)
    try:
        return _get("profile", {"symbol": td_sym})
    except Exception as e:
        log.debug(f"get_profile failed [{td_sym}]: {e}")
        return {}


def get_statistics(symbol: str) -> dict:
    """
    /statistics for fundamental data. Returns a dict.
    Returns {} on any error — endpoint availability on free tier is uncertain.
    """
    td_sym = to_td_symbol(symbol)
    try:
        return _get("statistics", {"symbol": td_sym})
    except Exception as e:
        log.debug(f"get_statistics failed [{td_sym}]: {e}")
        return {}


# ── STANDALONE TEST ───────────────────────────────────────────
if __name__ == "__main__":
    import json
    print("Testing get_quotes(['AAPL', '^GSPC', 'DX-Y.NYB'])...")
    q = get_quotes(["AAPL", "^GSPC", "DX-Y.NYB"])
    print(json.dumps({k: {kk: v for kk, v in vv.items() if kk in ("close", "change", "percent_change", "fifty_two_week")} for k, vv in q.items()}, indent=2))
    print("\nTesting get_time_series('SPX', '1day', 5)...")
    ts = get_time_series("SPX", "1day", 5)
    print(json.dumps(ts, indent=2))
    print("\nTesting search_symbols('AAPL')...")
    s = search_symbols("AAPL", max_results=3)
    print(json.dumps(s, indent=2))
