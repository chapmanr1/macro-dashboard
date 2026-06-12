# FILE: market_data.py
# Bloomberg Macro Dashboard — Market Data
# Indices + VIX from FRED; futures, sectors, commodities, currencies from Twelve Data.

import time
import logging
from datetime import datetime, timezone
from typing import Optional
from twelve_data import get_quotes
from fred_data import get_index_data

log = logging.getLogger(__name__)

CACHE_TTL = 1200  # 20 minutes — conserves Twelve Data free-tier credits (800/day)

_cache = {"data": None, "ts": 0}

def _cache_valid():
    return _cache["data"] is not None and (time.time() - _cache["ts"]) < CACHE_TTL

# ── SYMBOL DEFINITIONS ────────────────────────────────────────
# S&P 500, Dow, Nasdaq, VIX come from FRED (see fred_data.get_index_data).
# Russell 2000 has no daily FRED series — IWM proxy via Twelve Data.
RUT_INDEX = {"symbol": "^RUT", "label": "RUSSELL 2K", "abbr": "RUT"}

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
    {"symbol": "DX-Y.NYB", "label": "UUP",     "description": "US Dollar Index (UUP ETF proxy)", "decimals": 2},
    {"symbol": "EURUSD=X", "label": "EUR/USD",  "description": "Euro / US Dollar","decimals": 4},
]
VIX_DISPLAY_MAX = 50

BREADTH_SYMBOLS = [
    {"symbol": "SPY", "label": "S&P CAP-WT", "description": "Cap-weight S&P 500 proxy"},
    {"symbol": "RSP", "label": "S&P EQL-WT", "description": "Equal-weight S&P 500"},
]

# VIX term structure ETFs (VIXY = front-month, already in VIX_SYMBOL mapping)
VIX_TERM_SYMBOLS = [
    {"symbol": "VIXM", "label": "VIX MID",  "months": "5M",  "description": "~5-month VIX futures"},
    {"symbol": "VXZ",  "label": "VIX LONG", "months": "7M+", "description": "~7-month VIX futures"},
]

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


def _breadth_signal(breadth: dict) -> tuple[str, str]:
    diff = breadth.get("differential")
    up   = breadth.get("sectors_up", 0)
    down = breadth.get("sectors_down", 0)
    if diff is None:
        return "UNKNOWN", "Market breadth data unavailable."
    if diff > 0.3 and up >= 8:
        return "BROAD ADVANCE", f"Equal-weight outpacing cap-weight by {diff:+.2f}% — rally has wide participation."
    elif diff > 0.15:
        return "IMPROVING BREADTH", f"Equal-weight outperforming — breadth expanding. {up} of 11 sectors advancing."
    elif diff < -0.3 and down >= 8:
        return "NARROW RALLY", f"Cap-weight outpacing equal-weight by {abs(diff):.2f}% — mega-cap concentration risk."
    elif diff < -0.15:
        return "DETERIORATING BREADTH", f"Cap-weight leading — breadth narrowing. {down} of 11 sectors declining."
    else:
        return "NEUTRAL BREADTH", f"Cap- and equal-weight S&P roughly in line. {up} sectors up, {down} down."


def _vix_term_signal(vix_term: list[dict]) -> tuple[str, str]:
    front = next((v for v in vix_term if v.get("months") == "1M"), None)
    mid   = next((v for v in vix_term if v.get("months") == "5M"), None)
    if not front or not mid or front.get("price") is None or mid.get("price") is None:
        return "UNKNOWN", "VIX term structure data unavailable."
    fp, mp = front["price"], mid["price"]
    ratio  = mp / fp if fp > 0 else 1.0
    if ratio > 1.12:
        return "CONTANGO — STEEP", f"Front-month VIX well below mid-term ({fp:.2f} vs {mp:.2f}) — market calm; complacency risk building."
    elif ratio > 1.03:
        return "CONTANGO", f"Normal term structure — mid-term VIX ({mp:.2f}) above front ({fp:.2f}). Low systemic stress."
    elif ratio > 0.97:
        return "FLAT", f"VIX term structure flattening ({fp:.2f} vs {mp:.2f}) — subtle shift in risk perception."
    elif ratio > 0.90:
        return "BACKWARDATION", f"Near-term VIX ({fp:.2f}) above mid-term ({mp:.2f}) — elevated current fear vs future expectations."
    else:
        return "STEEP BACKWARDATION", f"VIX strongly inverted ({fp:.2f} vs {mp:.2f}) — acute near-term fear spike. Monitor for capitulation."


def _cu_au_signal(ratio: Optional[float]) -> tuple[str, str]:
    """
    Copper/gold ratio: copper in $/lb (HG), gold in $/oz (XAU).
    Typical range ~0.0015–0.0030. Rising ratio = growth positive.
    """
    if ratio is None:
        return "UNKNOWN", "Copper/gold ratio data unavailable."
    if ratio > 0.0026:
        return "GROWTH POSITIVE", f"Copper/gold ratio {ratio:.4f} — industrial demand strong, growth outlook constructive."
    elif ratio > 0.0020:
        return "NEUTRAL — MODEST GROWTH", f"Copper/gold ratio {ratio:.4f} — balanced cyclical vs safe-haven demand."
    elif ratio > 0.0015:
        return "RISK AVERSE", f"Copper/gold ratio {ratio:.4f} — gold outperforming copper, growth concerns elevated."
    else:
        return "GROWTH CONCERN", f"Copper/gold ratio {ratio:.4f} depressed — gold bid vs copper signals macro risk-off."


def _dollar_signal(dxy_value):
    from config import DOLLAR_THRESHOLDS as D
    if dxy_value is None:
        return "UNKNOWN", "USD index data unavailable."
    if dxy_value >= D["very_strong"]:
        return "USD VERY STRONG", "US dollar is significantly elevated — meaningful headwind for commodities, EM assets, and multinational earnings."
    elif dxy_value >= D["strong"]:
        return "USD STRONG", "US dollar reflects strong demand — watch for drag on commodity prices and export-sensitive earnings."
    elif dxy_value >= D["neutral_hi"]:
        return "USD FIRM", "US dollar is near-neutral but tilted toward strength — balanced currency conditions."
    elif dxy_value >= D["neutral_lo"]:
        return "USD NEUTRAL", "US dollar is in neutral territory — no outsized currency headwinds or tailwinds."
    elif dxy_value >= D["weak"]:
        return "USD SOFT", "US dollar is mildly weak — modest tailwind for commodities and international risk assets."
    else:
        return "USD WEAK", "US dollar is meaningfully depressed — significant tailwind for commodities, EM, and international equities."


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

    # ── FRED: indices + VIX (10s hard timeout — never blocks market data) ────
    import concurrent.futures as _cf
    try:
        with _cf.ThreadPoolExecutor(max_workers=1) as _pool:
            fred_idx = _pool.submit(get_index_data).result(timeout=10)
    except Exception as e:
        log.warning(f"Market: FRED index fetch skipped ({e}) — continuing without index data.")
        fred_idx = {"indices": [], "vix": None}
    fred_indices = fred_idx.get("indices", [])   # SP500, DJIA, NASDAQCOM
    fred_vix     = fred_idx.get("vix")           # VIXCLS

    # ── Twelve Data batches ───────────────────────────────────
    futures_syms   = [f["symbol"] for f in FUTURES]
    sector_syms    = [s["symbol"] for s in SECTORS]
    commodity_syms = [c["symbol"] for c in COMMODITIES]
    currency_syms  = [c["symbol"] for c in CURRENCIES]
    breadth_syms   = [b["symbol"] for b in BREADTH_SYMBOLS]
    vix_term_syms  = [v["symbol"] for v in VIX_TERM_SYMBOLS]
    rut_syms       = [RUT_INDEX["symbol"]]

    # Batch 1: Russell 2000 (IWM proxy) + futures + breadth + VIX term structure
    batch1 = get_quotes(rut_syms + futures_syms + breadth_syms + vix_term_syms)
    # Batch 2: sectors
    batch2 = get_quotes(sector_syms)
    # Batch 3: commodities + currencies
    batch3 = get_quotes(commodity_syms + currency_syms)

    all_quotes = {**batch1, **batch2, **batch3}

    # ── INDICES ───────────────────────────────────────────────
    # FRED indices first (SPX, DJIA, NDX), then RUT from TD
    indices_out = list(fred_indices)
    rut_q = all_quotes.get(RUT_INDEX["symbol"])
    rut_stats = _parse_quote(rut_q) if rut_q else None
    if rut_stats:
        indices_out.append({**RUT_INDEX, **rut_stats})
    else:
        indices_out.append({**RUT_INDEX, **_null_instrument(RUT_INDEX["label"], RUT_INDEX["symbol"])})

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
    # Spot VIX from FRED (VIXCLS) — actual index, not a futures ETF proxy
    vix_value = fred_vix["price"]     if fred_vix else None
    vix_change = fred_vix["change"]   if fred_vix else None
    vix_pct    = fred_vix["pct_change"] if fred_vix else None
    vix_dir    = fred_vix["direction"]  if fred_vix else "FLAT"
    vix_info   = _vix_signal(vix_value)
    vix_out    = {
        "symbol":     "^VIX",
        "label":      "VIX",
        "value":      vix_value,
        "change":     vix_change,
        "pct_change": vix_pct,
        "direction":  vix_dir,
        **vix_info,
    }

    # ── VIX TERM STRUCTURE ────────────────────────────────────
    # Front month = VIXCLS spot (FRED). Mid/long use Twelve Data ETF proxies.
    vix_term_out = []
    vixy_entry = {
        "symbol":      "VIX",
        "label":       "VIX SPOT",
        "months":      "1M",
        "description": "CBOE VIX spot (FRED VIXCLS)",
        "price":       vix_value,
        "pct_change":  vix_pct,
        "direction":   vix_dir,
    }
    vix_term_out.append(vixy_entry)
    for vt in VIX_TERM_SYMBOLS:
        q     = all_quotes.get(vt["symbol"])
        stats = _parse_quote(q) if q else None
        entry = {**vt}
        if stats:
            entry.update({
                "price":      stats["price"],
                "pct_change": stats["pct_change"],
                "direction":  stats["direction"],
            })
        else:
            entry.update({"price": None, "pct_change": None, "direction": "FLAT"})
        vix_term_out.append(entry)

    vix_term_signal, vix_term_detail = _vix_term_signal(vix_term_out)

    # ── SECTORS ───────────────────────────────────────────────
    sectors_out = []
    for sec in SECTORS:
        q = all_quotes.get(sec["symbol"])
        stats = _parse_quote(q) if q else None
        if stats:
            sectors_out.append({**sec, **stats})
        else:
            sectors_out.append({**sec, **_null_instrument(sec["label"], sec["symbol"])})

    sector_signal, sector_detail = _sector_signal(sectors_out)

    # ── MARKET BREADTH ────────────────────────────────────────
    # RSP (equal-weight S&P) vs SPY (cap-weight proxy, both from Twelve Data)
    spy_q     = all_quotes.get("SPY")
    rsp_q     = all_quotes.get("RSP")
    spy_stats = _parse_quote(spy_q) if spy_q else None
    rsp_stats = _parse_quote(rsp_q) if rsp_q else None

    sectors_up   = sum(1 for s in sectors_out if (s.get("pct_change") or 0) >  0.2)
    sectors_down = sum(1 for s in sectors_out if (s.get("pct_change") or 0) < -0.2)

    breadth_out = {
        "spy_pct":      spy_stats["pct_change"] if spy_stats else None,
        "rsp_pct":      rsp_stats["pct_change"] if rsp_stats else None,
        "rsp_price":    rsp_stats["price"]      if rsp_stats else None,
        "differential": None,
        "sectors_up":   sectors_up,
        "sectors_down": sectors_down,
    }
    if spy_stats and rsp_stats:
        breadth_out["differential"] = round(rsp_stats["pct_change"] - spy_stats["pct_change"], 3)

    breadth_signal, breadth_detail = _breadth_signal(breadth_out)
    breadth_out["signal"] = breadth_signal
    breadth_out["detail"] = breadth_detail

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

    # ── COPPER / GOLD RATIO ───────────────────────────────────
    copper_entry = next((c for c in commodities_out if c["symbol"] == "HG=F"), None)
    gold_entry   = next((c for c in commodities_out if c["symbol"] == "GC=F"), None)
    cu_au_ratio  = None
    if copper_entry and gold_entry and copper_entry.get("price") and gold_entry.get("price"):
        cu_au_ratio = round(copper_entry["price"] / gold_entry["price"], 5)
    cu_au_signal, cu_au_detail = _cu_au_signal(cu_au_ratio)

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
            if cur["label"] == "UUP":
                dxy_value = stats["price"]
        else:
            entry.update({"price": None, "change": None, "pct_change": None, "direction": "FLAT"})
        currencies_out.append(entry)

    dollar_signal, dollar_detail = _dollar_signal(dxy_value)

    result = {
        "indices":          indices_out,
        "futures":          futures_out,
        "futures_signal":   futures_signal,
        "futures_detail":   futures_detail,
        "vix":              vix_out,
        "vix_term":         vix_term_out,
        "vix_term_signal":  vix_term_signal,
        "vix_term_detail":  vix_term_detail,
        "sectors":          sectors_out,
        "sector_signal":    sector_signal,
        "sector_detail":    sector_detail,
        "breadth":          breadth_out,
        "commodities":      commodities_out,
        "cu_au_ratio":      cu_au_ratio,
        "cu_au_signal":     cu_au_signal,
        "cu_au_detail":     cu_au_detail,
        "currencies":       currencies_out,
        "dollar_signal":    dollar_signal,
        "dollar_detail":    dollar_detail,
        "timestamp":        ts,
    }

    _cache["data"] = result
    _cache["ts"]   = time.time()
    log.info(f"Market: fetched — futures={futures_signal}, VIX={vix_value}, sector={sector_signal}")
    return result


# ── STANDALONE TEST ───────────────────────────────────────────
if __name__ == "__main__":
    import json
    print(json.dumps(get_market(), indent=2, default=str))
