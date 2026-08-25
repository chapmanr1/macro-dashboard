# FILE: fmp_data.py
# Financial Modeling Prep — free-tier fundamentals client.
# Called by research.py to populate Graham/Buffett scorecard data.
# Two endpoints per symbol: /stable/ratios and /stable/key-metrics.
# Cache TTL: 24 hours (fundamentals are annual/quarterly, not real-time).

import logging
import time
import requests
from config import FMP_API_KEY

log = logging.getLogger(__name__)

_BASE = "https://financialmodelingprep.com/stable"
_cache: dict = {}
_CACHE_TTL = 86400  # 24 hours


def get_fundamentals(symbol: str) -> dict:
    """
    Fetch FMP free-tier fundamentals for a stock symbol.
    Returns a flat dict with the same keys as the info dict in research.py.
    Degrades gracefully to all-None if the key is missing or the API fails.
    """
    sym = symbol.strip().upper()
    now = time.time()
    if sym in _cache and (now - _cache[sym]["ts"]) < _CACHE_TTL:
        return _cache[sym]["data"]

    result = _empty_info()
    if not FMP_API_KEY:
        log.warning("FMP_API_KEY not set — fundamentals unavailable")
        return result

    try:
        ratios = _fetch("ratios", sym)
        km     = _fetch("key-metrics", sym)

        # ── From /stable/ratios ───────────────────────────────
        result["trailingPE"]                   = _f(ratios, "priceToEarningsRatio")
        result["priceToBook"]                  = _f(ratios, "priceToBookRatio")
        result["priceToSalesTrailing12Months"] = _f(ratios, "priceToSalesRatio")
        result["enterpriseToEbitda"]           = _f(ratios, "enterpriseValueMultiple")
        result["grossMargins"]                 = _f(ratios, "grossProfitMargin")
        result["operatingMargins"]             = _f(ratios, "operatingProfitMargin")
        result["profitMargins"]                = _f(ratios, "netProfitMargin")
        result["currentRatio"]                 = _f(ratios, "currentRatio")
        result["quickRatio"]                   = _f(ratios, "quickRatio")
        result["dividendYield"]                = _f(ratios, "dividendYield")
        result["bookValue"]                    = _f(ratios, "bookValuePerShare")
        result["trailingEps"]                  = _f(ratios, "netIncomePerShare")

        # FMP returns actual ratio (e.g. 1.52); research.py convention is ×100
        # so build_buffett_scorecard's `de < 50` means de < 0.5 actual.
        de_raw = _f(ratios, "debtToEquityRatio")
        result["debtToEquity"] = round(de_raw * 100, 2) if de_raw is not None else None

        # ── From /stable/key-metrics ──────────────────────────
        result["returnOnEquity"] = _f(km, "returnOnEquity")
        result["returnOnAssets"] = _f(km, "returnOnAssets")
        result["marketCap"]      = _f(km, "marketCap")
        result["freeCashflow"]   = _f(km, "freeCashFlowToFirm")

    except requests.RequestException as e:
        log.warning(f"FMP request failed for {sym}: {e}")
    except Exception as e:
        log.warning(f"FMP fundamentals error for {sym}: {e}")

    _cache[sym] = {"data": result, "ts": now}
    return result


def _fetch(endpoint: str, symbol: str) -> dict:
    r = requests.get(
        f"{_BASE}/{endpoint}",
        params={"symbol": symbol, "apikey": FMP_API_KEY},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict) and "message" in data:
        log.warning(f"FMP {endpoint} error for {symbol}: {data['message']}")
    return {}


def _f(d: dict, key: str):
    val = d.get(key)
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _empty_info() -> dict:
    return {
        "trailingPE":                   None,
        "forwardPE":                    None,
        "priceToBook":                  None,
        "priceToSalesTrailing12Months": None,
        "enterpriseToEbitda":           None,
        "pegRatio":                     None,
        "grossMargins":                 None,
        "operatingMargins":             None,
        "profitMargins":                None,
        "returnOnEquity":               None,
        "returnOnAssets":               None,
        "trailingEps":                  None,
        "forwardEps":                   None,
        "bookValue":                    None,
        "totalCash":                    None,
        "freeCashflow":                 None,
        "debtToEquity":                 None,
        "currentRatio":                 None,
        "quickRatio":                   None,
        "revenueGrowth":                None,
        "earningsGrowth":               None,
        "marketCap":                    None,
        "beta":                         None,
        "dividendYield":                None,
        "sharesOutstanding":            None,
        "totalAssets":                  None,
        "totalLiab":                    None,
        "totalCurrentAssets":           None,
        "totalCurrentLiabilities":      None,
        "longTermDebt":                 None,
        "targetMeanPrice":              None,
        "recommendationKey":            "",
    }
