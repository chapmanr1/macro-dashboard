# FILE: twelve_data.py
# Bloomberg Macro Dashboard — Twelve Data API wrapper
# Replaces yfinance. Rate-limited to 8 calls/min (free tier).

import time
import logging
import threading
import requests
from source_status import timed_get
from collections import deque
from config import TWELVE_DATA_API_KEY

log = logging.getLogger(__name__)

BASE_URL = "https://api.twelvedata.com"

# ── SYMBOL MAP (yfinance → Twelve Data) ───────────────────────
# Equity ETFs and common stocks are identical; only special symbols differ.
SYMBOL_MAP: dict[str, str] = {
    # Commodities
    "CL=F":     "WTI/USD",
    "GC=F":     "XAU/USD",
    "SI=F":     "XAG/USD",
    "HG=F":     "HG1",
    "NG=F":     "NG/USD",
    # Currencies
    "EURUSD=X": "EUR/USD",
}

def to_td_symbol(symbol: str) -> str:
    """Translate a yfinance symbol to its Twelve Data equivalent."""
    return SYMBOL_MAP.get(symbol.upper(), symbol.upper())


# ── RATE LIMITER ──────────────────────────────────────────────
# Free tier: 8 credits per minute. TD bills per SYMBOL, not per request —
# a batch /quote for 11 symbols costs 11 credits and is rejected outright
# if it would exceed the per-minute budget.
_credit_times: deque = deque()  # one timestamp per credit spent
_credit_lock = threading.Lock()
_RATE_LIMIT = 8
_RATE_WINDOW = 60.0


def _rate_limit(credits: int = 1) -> None:
    """Block until `credits` can be spent without exceeding 8 credits/minute."""
    if credits > _RATE_LIMIT:
        raise ValueError(f"Request needs {credits} credits; max per minute is {_RATE_LIMIT}")
    # Lock is held while sleeping on purpose: callers queue up rather than
    # two threads both seeing free budget and overspending it together.
    with _credit_lock:
        while True:
            now = time.time()
            while _credit_times and now - _credit_times[0] >= _RATE_WINDOW:
                _credit_times.popleft()
            if len(_credit_times) + credits <= _RATE_LIMIT:
                break
            # Wait until enough of the oldest credits age out of the window
            release_at = _credit_times[len(_credit_times) + credits - _RATE_LIMIT - 1]
            sleep_for = _RATE_WINDOW - (now - release_at) + 0.05
            log.info(f"Twelve Data rate limit: waiting {sleep_for:.1f}s for {credits} credit(s)")
            time.sleep(max(sleep_for, 0.05))
        stamp = time.time()
        _credit_times.extend([stamp] * credits)


# ── BASE HTTP ─────────────────────────────────────────────────

def _get(endpoint: str, params: dict, credits: int = 1) -> dict:
    """Make a rate-limited GET to the Twelve Data API."""
    if not TWELVE_DATA_API_KEY:
        raise RuntimeError("TWELVE_DATA_API_KEY not set")
    _rate_limit(credits)
    params = {**params, "apikey": TWELVE_DATA_API_KEY}
    resp = timed_get("twelvedata", f"{BASE_URL}/{endpoint}", params=params, timeout=15)
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
    Lists longer than 8 symbols are split into chunks that each fit the
    per-minute credit budget; later chunks may block up to ~60s waiting.
    Returns an empty dict on failure; failed symbols or chunks are omitted.
    """
    if not symbols:
        return {}
    # Build TD→[originals] map before deduplicating, so multiple original
    # symbols that share a TD proxy (e.g. ^GSPC and ES=F both → SPY) all
    # receive the quote in the result dict.
    td_to_origs: dict[str, list] = {}
    for s in symbols:
        td_to_origs.setdefault(to_td_symbol(s), []).append(s)
    td_syms_deduped = list(td_to_origs.keys())
    result: dict[str, dict] = {}
    for i in range(0, len(td_syms_deduped), _RATE_LIMIT):
        chunk = td_syms_deduped[i:i + _RATE_LIMIT]
        try:
            data = _get("quote", {"symbol": ",".join(chunk)}, credits=len(chunk))
        except (requests.RequestException, RuntimeError, ValueError) as e:
            log.warning(f"get_quotes failed for {chunk}: {e}")
            continue
        # Single-symbol response is a bare dict; multi-symbol is {symbol: dict}
        if len(chunk) == 1:
            data = {chunk[0]: data}
        for td_sym, quote in data.items():
            if not isinstance(quote, dict):
                continue
            if quote.get("status") == "error":
                log.warning(f"TD quote error for {td_sym}: {quote.get('message', '')}")
                continue
            for orig in td_to_origs.get(td_sym, [td_sym]):
                result[orig] = quote
    return result


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
