# FILE: research.py
# Bloomberg Macro Dashboard — Research Panel Backend

import math
import time
import logging
import urllib.request
import urllib.parse
import json as _json
from datetime import datetime, timezone
from twelve_data import get_quotes, get_time_series, get_profile, get_statistics, search_symbols, to_td_symbol

log = logging.getLogger(__name__)

_search_cache = {}
_edgar_cache  = {}
_company_cache = {}
SEARCH_TTL  = 300
EDGAR_TTL   = 3600
COMPANY_TTL = 3600

# ── FRED SERIES DICTIONARY ────────────────────────────────────
FRED_SERIES_DICT = {
    "cpi":                    ("CPIAUCSL",          "Consumer Price Index"),
    "inflation":              ("CPIAUCSL",          "Consumer Price Index"),
    "core cpi":               ("CPILFESL",          "Core CPI Ex-Food & Energy"),
    "pce":                    ("PCEPI",             "PCE Price Index"),
    "core pce":               ("PCEPILFE",          "Core PCE Price Index"),
    "ppi":                    ("PPIACO",            "Producer Price Index"),
    "gdp":                    ("GDPC1",             "Real GDP"),
    "real gdp":               ("GDPC1",             "Real GDP"),
    "nominal gdp":            ("GDP",               "Nominal GDP"),
    "gdp growth":             ("A191RL1Q225SBEA",   "Real GDP Growth Rate"),
    "unemployment":           ("UNRATE",            "Unemployment Rate"),
    "jobless claims":         ("ICSA",              "Initial Jobless Claims"),
    "initial claims":         ("ICSA",              "Initial Jobless Claims"),
    "nonfarm payrolls":       ("PAYEMS",            "Nonfarm Payrolls"),
    "jobs":                   ("PAYEMS",            "Nonfarm Payrolls"),
    "jolts":                  ("JTSJOL",            "Job Openings JOLTS"),
    "job openings":           ("JTSJOL",            "Job Openings JOLTS"),
    "wages":                  ("CES0500000003",     "Average Hourly Earnings"),
    "fed funds":              ("FEDFUNDS",          "Federal Funds Rate"),
    "fed funds rate":         ("FEDFUNDS",          "Federal Funds Rate"),
    "ffr":                    ("FEDFUNDS",          "Federal Funds Rate"),
    "10 year":                ("DGS10",             "10-Year Treasury Yield"),
    "10yr":                   ("DGS10",             "10-Year Treasury Yield"),
    "2 year":                 ("DGS2",              "2-Year Treasury Yield"),
    "2yr":                    ("DGS2",              "2-Year Treasury Yield"),
    "30 year":                ("DGS30",             "30-Year Treasury Yield"),
    "3 month":                ("DTB3",              "3-Month T-Bill"),
    "t-bill":                 ("DTB3",              "3-Month T-Bill"),
    "yield curve":            ("T10Y2Y",            "10Y-2Y Treasury Spread"),
    "10y2y":                  ("T10Y2Y",            "10Y-2Y Treasury Spread"),
    "inversion":              ("T10Y2Y",            "10Y-2Y Treasury Spread"),
    "hy spread":              ("BAMLH0A0HYM2",      "High Yield OAS Spread"),
    "high yield":             ("BAMLH0A0HYM2",      "High Yield OAS Spread"),
    "credit spread":          ("BAMLH0A0HYM2",      "High Yield OAS Spread"),
    "ig spread":              ("BAMLC0A0CM",        "Investment Grade OAS Spread"),
    "investment grade":       ("BAMLC0A0CM",        "Investment Grade OAS Spread"),
    "breakeven":              ("T5YIE",             "5-Year Breakeven Inflation"),
    "breakeven inflation":    ("T5YIE",             "5-Year Breakeven Inflation"),
    "tips":                   ("DFII10",            "10-Year TIPS Real Yield"),
    "real yield":             ("DFII10",            "10-Year TIPS Real Yield"),
    "inflation expectations": ("T10YIE",            "10-Year Breakeven Inflation"),
    "5y5y":                   ("T5YIFR",            "5Y5Y Forward Inflation"),
    "m2":                     ("M2SL",              "M2 Money Supply"),
    "money supply":           ("M2SL",              "M2 Money Supply"),
    "m1":                     ("M1SL",              "M1 Money Supply"),
    "housing":                ("HOUST",             "Housing Starts"),
    "housing starts":         ("HOUST",             "Housing Starts"),
    "case shiller":           ("CSUSHPINSA",        "Case-Shiller Home Price Index"),
    "home prices":            ("CSUSHPINSA",        "Case-Shiller Home Price Index"),
    "mortgage rate":          ("MORTGAGE30US",      "30-Year Fixed Mortgage Rate"),
    "retail sales":           ("RSAFS",             "Advance Retail Sales"),
    "retail":                 ("RSAFS",             "Advance Retail Sales"),
    "consumer confidence":    ("UMCSENT",           "U. Michigan Consumer Sentiment"),
    "michigan sentiment":     ("UMCSENT",           "U. Michigan Consumer Sentiment"),
    "consumer spending":      ("PCE",               "Personal Consumption Expenditures"),
    "personal income":        ("PI",                "Personal Income"),
    "savings rate":           ("PSAVERT",           "Personal Saving Rate"),
    "ism":                    ("NAPM",              "ISM Manufacturing PMI"),
    "pmi":                    ("NAPM",              "ISM Manufacturing PMI"),
    "manufacturing":          ("NAPM",              "ISM Manufacturing PMI"),
    "industrial production":  ("INDPRO",            "Industrial Production Index"),
    "capacity utilization":   ("TCU",               "Capacity Utilization"),
    "vix":                    ("VIXCLS",            "CBOE Volatility Index"),
    "financial conditions":   ("NFCI",              "Chicago Fed Financial Conditions"),
    "nfci":                   ("NFCI",              "Chicago Fed Financial Conditions"),
    "dollar":                 ("DTWEXBGS",          "Trade-Weighted US Dollar Index"),
    "dxy":                    ("DTWEXBGS",          "Trade-Weighted US Dollar Index"),
    "oil":                    ("DCOILWTICO",        "WTI Crude Oil Price"),
    "crude oil":              ("DCOILWTICO",        "WTI Crude Oil Price"),
    "gold":                   ("GOLDAMGBD228NLBM",  "Gold Fixing Price"),
    "national debt":          ("GFDEBTN",           "Federal Debt Total Public"),
    "debt to gdp":            ("GFDEGDQ188S",       "Federal Debt as % of GDP"),
    "trade deficit":          ("BOPGSTB",           "U.S. Trade Balance"),
}
FRED_BASE = "https://fred.stlouisfed.org/series/"


def search_fred(query):
    q = query.strip().lower()
    if not q:
        return []
    results = []
    seen = set()
    for key, (series_id, name) in FRED_SERIES_DICT.items():
        if q in key or q in name.lower() or q in series_id.lower():
            if series_id not in seen:
                seen.add(series_id)
                results.append({"series_id": series_id, "name": name, "url": FRED_BASE + series_id})
            if len(results) >= 10:
                break
    return results


def search_edgar(query):
    q = query.strip()
    if not q:
        return []
    cache_key = q.upper()
    now = time.time()
    if cache_key in _edgar_cache and (now - _edgar_cache[cache_key]["ts"]) < EDGAR_TTL:
        return _edgar_cache[cache_key]["data"]
    try:
        url = ("https://efts.sec.gov/LATEST/search-index?q="
               + urllib.parse.quote(q) + "&forms=10-K,10-Q,8-K")
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "MacroTerminal/1.0 ryanmichaelchapman@yahoo.com"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = _json.loads(resp.read().decode())
        hits = (raw.get("hits") or {}).get("hits") or []
        results = []
        for h in hits[:8]:
            src = h.get("_source") or {}
            company = (src.get("entity_name")
                       or (src.get("display_names") or [""])[0])
            file_url = src.get("file_url", "")
            entity_id = src.get("entity_id") or src.get("cik") or ""
            if not file_url and entity_id:
                file_url = ("https://www.sec.gov/cgi-bin/browse-edgar"
                            "?action=getcompany&CIK=" + str(entity_id)
                            + "&type=" + (src.get("form_type") or "") + "&count=5")
            results.append({
                "company":   company,
                "form_type": src.get("form_type", ""),
                "date":      src.get("file_date", ""),
                "url":       file_url,
            })
        _edgar_cache[cache_key] = {"data": results, "ts": now}
        return results
    except Exception as e:
        log.warning(f"EDGAR search failed for {query}: {e}")
        _edgar_cache[cache_key] = {"data": [], "ts": now}
        return []


def search_tickers(query: str) -> list:
    q = query.strip().upper()
    if not q:
        return []
    now = time.time()
    if q in _search_cache and (now - _search_cache[q]["ts"]) < SEARCH_TTL:
        return _search_cache[q]["data"]
    try:
        # Try to get price for an exact symbol match
        price_map: dict = {}
        try:
            quotes = get_quotes([q])
            qdata = quotes.get(q, {})
            if qdata.get("close"):
                price_map[q] = round(float(qdata["close"]), 2)
        except Exception:
            pass

        # Broad symbol search
        found = search_symbols(q, max_results=8)
        seen = set()
        results = []
        for item in found:
            sym = item.get("symbol", "")
            if not sym or sym in seen:
                continue
            seen.add(sym)
            results.append({
                "symbol":   sym,
                "name":     item.get("name", ""),
                "exchange": item.get("exchange", ""),
                "type":     item.get("type", ""),
                "price":    price_map.get(sym),
            })
            if len(results) >= 8:
                break

        _search_cache[q] = {"data": results, "ts": now}
        return results
    except Exception as e:
        log.warning(f"Search failed for {q}: {e}")
        return []


# ── VALUATION HELPERS ─────────────────────────────────────────

def calculate_graham_number(eps, bvps):
    if eps is None or bvps is None or eps <= 0 or bvps <= 0:
        return None
    return round(math.sqrt(22.5 * eps * bvps), 2)


def calculate_dcf(info):
    try:
        fcf    = info.get("freeCashflow")
        shares = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
        if not fcf or not shares or shares <= 0 or fcf <= 0:
            return None
        growth = 0.05
        rg = info.get("revenueGrowth")
        if rg is not None and -0.3 < rg < 0.5:
            growth = min(max(float(rg) * 0.7, 0.01), 0.15)
        discount = 0.10
        tg = 0.03
        pv = 0.0
        cf = float(fcf)
        for yr in range(1, 11):
            cf *= (1 + growth)
            pv += cf / (1 + discount) ** yr
        terminal = (cf * (1 + tg)) / (discount - tg)
        pv += terminal / (1 + discount) ** 10
        val = pv / float(shares)
        return round(val, 2) if val > 0 else None
    except Exception as e:
        log.debug(f"DCF error: {e}")
        return None


def calculate_nav(info):
    try:
        bvps   = info.get("bookValue")
        if bvps is not None:
            return round(float(bvps), 2)
        assets = info.get("totalAssets")
        liab   = info.get("totalLiab") or info.get("totalDebt")
        shares = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
        if assets and liab and shares and shares > 0:
            return round((float(assets) - float(liab)) / float(shares), 2)
        return None
    except Exception as e:
        log.debug(f"NAV error: {e}")
        return None


def build_graham_scorecard(info, price, graham_number):
    pe            = info.get("trailingPE") or info.get("forwardPE")
    pb            = info.get("priceToBook")
    current_ratio = info.get("currentRatio")
    lt_debt       = info.get("longTermDebt")
    cur_assets    = info.get("totalCurrentAssets")
    cur_liab      = info.get("totalCurrentLiabilities")
    eps           = info.get("trailingEps")
    eg            = info.get("earningsGrowth")

    debt_ok = False
    debt_cur = "N/A"
    if lt_debt is not None:
        debt_cur = f"${lt_debt/1e9:.1f}B"
        if cur_assets and cur_liab:
            net_cur = float(cur_assets) - float(cur_liab)
            debt_ok = float(lt_debt) < net_cur

    return [
        {"label": "P/E < 15",                    "current": f"{pe:.1f}"           if pe is not None else "N/A",            "pass": pe is not None and pe < 15},
        {"label": "P/B < 1.5",                   "current": f"{pb:.2f}"           if pb is not None else "N/A",            "pass": pb is not None and pb < 1.5},
        {"label": "Current Ratio > 2",           "current": f"{current_ratio:.2f}" if current_ratio is not None else "N/A", "pass": current_ratio is not None and current_ratio > 2.0},
        {"label": "LT Debt < Net Current Assets","current": debt_cur,                                                        "pass": debt_ok},
        {"label": "EPS Growth Positive",         "current": f"{eg*100:.1f}%"      if eg is not None else (f"${eps:.2f}" if eps else "N/A"), "pass": (eg is not None and eg > 0) or (eps is not None and eps > 0)},
        {"label": "Price < Graham Number",       "current": f"${price:.2f}"       if price else "N/A",                      "pass": graham_number is not None and price is not None and price < graham_number},
    ]


def build_buffett_scorecard(info, price):
    roe        = info.get("returnOnEquity")
    net_margin = info.get("profitMargins")
    fcf        = info.get("freeCashflow")
    de         = info.get("debtToEquity")
    rg         = info.get("revenueGrowth")
    eps        = info.get("trailingEps")

    oy = None
    if eps and price and price > 0:
        oy = float(eps) / float(price)

    return [
        {"label": "ROE > 15%",               "current": f"{roe*100:.1f}%"      if roe is not None else "N/A",        "pass": roe is not None and roe > 0.15},
        {"label": "Net Margin > 10%",        "current": f"{net_margin*100:.1f}%" if net_margin is not None else "N/A","pass": net_margin is not None and net_margin > 0.10},
        {"label": "FCF Positive",            "current": f"${fcf/1e9:.1f}B"     if fcf is not None else "N/A",        "pass": fcf is not None and fcf > 0},
        {"label": "D/E < 0.5",               "current": f"{de/100:.2f}x"       if de is not None else "N/A",         "pass": de is not None and de < 50},
        {"label": "Revenue Growth > 0%",     "current": f"{rg*100:.1f}%"       if rg is not None else "N/A",         "pass": rg is not None and rg > 0},
        {"label": "Owner Earnings Yield > 8%","current": f"{oy*100:.1f}%"      if oy is not None else "N/A",         "pass": oy is not None and oy > 0.08},
    ]


# ── COMPANY ANALYSIS ─────────────────────────────────────────

def _sf(d: dict, *keys):
    """Safely traverse nested dict and return a float, or None."""
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    try:
        return float(d) if d is not None else None
    except (TypeError, ValueError):
        return None


def get_company_analysis(symbol: str) -> dict:
    """
    Fetch company data from Twelve Data (/quote + /profile + /statistics).
    Fields not available on the free tier degrade gracefully to None.
    """
    sym = symbol.strip().upper()
    now = time.time()
    if sym in _company_cache and (now - _company_cache[sym]["ts"]) < COMPANY_TTL:
        return _company_cache[sym]["data"]
    try:
        quotes  = get_quotes([sym])
        q       = quotes.get(sym, {})
        prof    = get_profile(sym)
        stats   = get_statistics(sym)

        # ── Price fields from /quote ──────────────────────────
        price    = _sf(q, "close")
        prev     = _sf(q, "previous_close")
        day_chg  = round(price - prev, 2) if price and prev else None
        day_pct  = round(day_chg / prev * 100, 2) if day_chg and prev else None

        w52 = q.get("fifty_two_week") or {}
        w52h = _sf(w52, "high")
        w52l = _sf(w52, "low")
        w52pos = None
        if w52h and w52l and price and (w52h - w52l) > 0:
            w52pos = round((price - w52l) / (w52h - w52l) * 100, 1)

        # ── Profile fields ────────────────────────────────────
        city    = prof.get("city", "") or ""
        country = prof.get("country", "") or ""
        hq      = ", ".join(filter(None, [city, country]))

        # ── Statistics fields (may all be None on free tier) ──
        vm    = stats.get("valuations_metrics") or {}
        fin   = stats.get("financials") or {}
        sinfo = stats.get("statistics") or {}
        divs  = stats.get("dividends_and_splits") or {}

        eps  = _sf(fin, "diluted_eps_ttm")
        bvps = _sf(fin, "book_value_per_share_mrq")

        # debtToEquity: TD gives ratio (e.g. 1.52), yfinance gives ×100 (152).
        # Helper functions check de < 50, so keep TD's value and adjust the
        # Buffett scorecard threshold comment — value already in same scale.
        de_ratio = _sf(fin, "total_debt_to_equity_mrq")

        # Build a flat info dict for the valuation helpers
        info: dict = {
            "trailingPE":                   _sf(vm, "trailing_pe"),
            "forwardPE":                    _sf(vm, "forward_pe"),
            "priceToBook":                  _sf(vm, "price_to_book_mrq"),
            "priceToSalesTrailing12Months": _sf(vm, "price_to_sales_ttm"),
            "enterpriseToEbitda":           _sf(vm, "enterprise_to_ebitda"),
            "pegRatio":                     _sf(vm, "peg_ratio"),
            "profitMargins":                _sf(fin, "profit_margin"),
            "operatingMargins":             _sf(fin, "operating_margin_ttm"),
            "grossMargins":                 None,  # TD gives $ not ratio; not available
            "returnOnEquity":               _sf(fin, "return_on_equity_ttm"),
            "returnOnAssets":               _sf(fin, "return_on_assets_ttm"),
            "trailingEps":                  eps,
            "forwardEps":                   None,  # not in TD free stats
            "bookValue":                    bvps,
            "totalCash":                    _sf(fin, "total_cash_mrq"),
            "freeCashflow":                 None,  # not in TD free stats
            "debtToEquity":                 de_ratio * 100 if de_ratio is not None else None,
            "currentRatio":                 _sf(fin, "current_ratio_mrq"),
            "quickRatio":                   None,  # not in TD free stats
            "revenueGrowth":                _sf(fin, "quarterly_revenue_growth_yoy"),
            "earningsGrowth":               _sf(fin, "quarterly_earnings_growth_yoy"),
            "marketCap":                    _sf(vm, "market_capitalization"),
            "beta":                         _sf(sinfo, "beta"),
            "dividendYield":                _sf(divs, "forward_annual_dividend_yield"),
            "sharesOutstanding":            None,
            "totalAssets":                  None,
            "totalLiab":                    None,
            "totalCurrentAssets":           None,
            "totalCurrentLiabilities":      None,
            "longTermDebt":                 None,
            "targetMeanPrice":              None,
            "recommendationKey":            "",
        }

        gnum  = calculate_graham_number(eps, bvps)
        gmgn  = round((gnum - price) / price * 100, 1) if gnum and price else None
        dcf   = calculate_dcf(info)
        dcfmgn = round((dcf - price) / price * 100, 1) if dcf and price else None
        nav   = calculate_nav(info)
        navmgn = round((nav - price) / price * 100, 1) if nav and price else None

        gsc   = build_graham_scorecard(info, price, gnum)
        bsc   = build_buffett_scorecard(info, price)
        gpass = sum(1 for c in gsc if c["pass"])
        bpass = sum(1 for c in bsc if c["pass"])

        mos = 0.0
        if gmgn is not None and gmgn > 0:
            mos = min(gmgn / 50, 1.0)
        elif dcfmgn is not None and dcfmgn > 0:
            mos = min(dcfmgn / 50, 1.0)
        vscore = round((gpass / len(gsc)) * 40 + (bpass / len(bsc)) * 40 + mos * 20)

        if vscore >= 80:    verdict, vcolor = "STRONG BUY",   "green"
        elif vscore >= 60:  verdict, vcolor = "BUY",          "green"
        elif vscore >= 40:  verdict, vcolor = "HOLD",         "amber"
        elif vscore >= 20:  verdict, vcolor = "AVOID",        "red"
        else:               verdict, vcolor = "STRONG AVOID", "red"

        result = {
            "symbol":          sym,
            "name":            prof.get("name") or q.get("name") or sym,
            "sector":          prof.get("sector") or "--",
            "industry":        prof.get("industry") or "--",
            "exchange":        prof.get("exchange") or q.get("exchange") or "",
            "employees":       prof.get("employees"),
            "hq":              hq,
            "website":         prof.get("website") or "",
            "description":     prof.get("description") or "",
            "market_cap":      info["marketCap"],
            "price":           round(price, 2) if price else None,
            "prev_close":      round(prev, 2) if prev else None,
            "open":            round(_sf(q, "open"), 2) if _sf(q, "open") else None,
            "day_high":        round(_sf(q, "high"), 2) if _sf(q, "high") else None,
            "day_low":         round(_sf(q, "low"), 2) if _sf(q, "low") else None,
            "volume":          int(float(q["volume"])) if q.get("volume") else None,
            "day_change":      day_chg,
            "day_pct":         day_pct,
            "pre_market":      None,   # not on free tier
            "post_market":     None,   # not on free tier
            "52w_high":        w52h,
            "52w_low":         w52l,
            "52w_pos":         w52pos,
            "next_earnings":   None,   # not in TD free stats
            "earnings_days":   None,
            "eps_est":         info["forwardEps"],
            "eps_actual":      eps,
            "pe":              info["trailingPE"],
            "forward_pe":      info["forwardPE"],
            "pb":              info["priceToBook"],
            "ps":              info["priceToSalesTrailing12Months"],
            "ev_ebitda":       info["enterpriseToEbitda"],
            "peg":             info["pegRatio"],
            "gross_margins":   info["grossMargins"],
            "op_margins":      info["operatingMargins"],
            "profit_margins":  info["profitMargins"],
            "roe":             info["returnOnEquity"],
            "roa":             info["returnOnAssets"],
            "current_ratio":   info["currentRatio"],
            "quick_ratio":     info["quickRatio"],
            "debt_equity":     info["debtToEquity"],
            "total_cash":      info["totalCash"],
            "free_cashflow":   info["freeCashflow"],
            "revenue_growth":  info["revenueGrowth"],
            "earnings_growth": info["earningsGrowth"],
            "eps":             eps,
            "forward_eps":     info["forwardEps"],
            "bvps":            bvps,
            "div_yield":       info["dividendYield"],
            "beta":            info["beta"],
            "analyst_target":  info["targetMeanPrice"],
            "recommendation":  info["recommendationKey"].upper() if info["recommendationKey"] else "",
            "graham_number":   gnum,
            "graham_margin":   gmgn,
            "dcf_value":       dcf,
            "dcf_margin":      dcfmgn,
            "nav":             nav,
            "nav_margin":      navmgn,
            "graham_scorecard":  gsc,
            "buffett_scorecard": bsc,
            "graham_pass":       gpass,
            "buffett_pass":      bpass,
            "value_score":       vscore,
            "verdict":           verdict,
            "verdict_color":     vcolor,
            "timestamp":         datetime.now(timezone.utc).isoformat(),
        }
        _company_cache[sym] = {"data": result, "ts": now}
        return result
    except Exception as e:
        log.warning(f"Company analysis failed for {symbol}: {e}")
        return {"symbol": sym, "error": str(e), "timestamp": datetime.now(timezone.utc).isoformat()}


def get_ticker_analysis(symbol):
    return get_company_analysis(symbol)


_chart_cache = {}
CHART_TTL = 300  # 5 minutes

# TD interval + outputsize per requested period
_CHART_INTERVAL_MAP = {
    "1d":  ("5min",   78),
    "5d":  ("15min", 130),
    "1mo": ("1day",   30),
    "3mo": ("1day",   90),
    "6mo": ("1day",  180),
    "1y":  ("1day",  365),
    "2y":  ("1day",  730),
    "5y":  ("1week", 260),
    "max": ("1month",500),
}

def get_chart_data(symbol: str, period: str = "1y") -> dict:
    sym = symbol.strip().upper()
    cache_key = f"{sym}_{period}"
    now = time.time()
    if cache_key in _chart_cache and (now - _chart_cache[cache_key]["ts"]) < CHART_TTL:
        return _chart_cache[cache_key]["data"]
    try:
        interval, outputsize = _CHART_INTERVAL_MAP.get(period, ("1day", 365))
        bars = get_time_series(sym, interval, outputsize)
        if not bars:
            return {"error": "No data available", "symbol": sym}

        closes = [float(b["close"]) for b in bars]
        n = len(closes)

        # Rolling MA50 and MA200
        ma50_vals  = [None] * n
        ma200_vals = [None] * n
        for i in range(n):
            if i >= 49:
                ma50_vals[i]  = round(sum(closes[i-49:i+1]) / 50, 2)
            if i >= 199:
                ma200_vals[i] = round(sum(closes[i-199:i+1]) / 200, 2)

        data_points = []
        for i, bar in enumerate(bars):
            data_points.append({
                "timestamp": bar["datetime"],
                "open":   round(float(bar["open"]),   2),
                "high":   round(float(bar["high"]),   2),
                "low":    round(float(bar["low"]),    2),
                "close":  round(float(bar["close"]),  2),
                "volume": int(float(bar.get("volume", 0))),
                "ma50":   ma50_vals[i],
                "ma200":  ma200_vals[i],
            })

        if not data_points:
            return {"error": "No data points", "symbol": sym}

        close_prices = [d["close"] for d in data_points]
        first_close  = close_prices[0]
        period_chg   = round((close_prices[-1] - first_close) / first_close * 100, 2) if first_close else 0

        result = {
            "symbol": sym,
            "period": period,
            "data":   data_points,
            "stats": {
                "period_high":   max(close_prices),
                "period_low":    min(close_prices),
                "period_change": period_chg,
            },
        }
        _chart_cache[cache_key] = {"data": result, "ts": now}
        return result
    except Exception as e:
        log.warning(f"Chart data failed for {symbol} ({period}): {e}")
        return {"error": str(e), "symbol": sym}


def get_watchlist_prices(tickers: list) -> list:
    """Batch /quote for up to 20 watchlist tickers."""
    syms = [s.strip().upper() for s in tickers[:20] if s.strip()]
    if not syms:
        return []
    quotes = get_quotes(syms)
    results = []
    for sym in syms:
        q = quotes.get(sym)
        if q and q.get("close"):
            try:
                price = round(float(q["close"]), 2)
                chg   = round(float(q["change"]), 2)
                pct   = round(float(q["percent_change"]), 3)
                results.append({
                    "symbol":     sym,
                    "price":      price,
                    "change":     chg,
                    "pct_change": pct,
                    "direction":  "UP" if chg > 0 else "DOWN" if chg < 0 else "FLAT",
                })
            except (KeyError, TypeError, ValueError) as e:
                results.append({"symbol": sym, "error": str(e)[:60]})
        else:
            results.append({"symbol": sym, "error": "no data"})
    return results
