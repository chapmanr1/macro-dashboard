# FILE: research.py
# Bloomberg Macro Terminal — Research Panel Backend

import math
import time
import logging
import urllib.request
import urllib.parse
import json as _json
from datetime import datetime, timezone

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


def search_tickers(query):
    q = query.strip().upper()
    if not q:
        return []
    now = time.time()
    if q in _search_cache and (now - _search_cache[q]["ts"]) < SEARCH_TTL:
        return _search_cache[q]["data"]
    try:
        import yfinance as yf
        results = []
        try:
            t = yf.Ticker(q)
            fi = t.fast_info
            price = getattr(fi, "last_price", None)
            if price:
                info = t.info
                results.append({
                    "symbol":   q,
                    "name":     info.get("shortName") or info.get("longName", q),
                    "exchange": info.get("exchange", ""),
                    "type":     info.get("quoteType", ""),
                    "price":    round(price, 2),
                })
        except Exception:
            pass
        try:
            search = yf.Search(q, max_results=8)
            for item in (search.quotes or []):
                sym = item.get("symbol", "")
                if sym and sym not in [r["symbol"] for r in results]:
                    results.append({
                        "symbol":   sym,
                        "name":     item.get("shortname") or item.get("longname", ""),
                        "exchange": item.get("exchange", ""),
                        "type":     item.get("quoteType", ""),
                        "price":    None,
                    })
                if len(results) >= 8:
                    break
        except Exception:
            pass
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

def get_company_analysis(symbol):
    sym = symbol.strip().upper()
    now = time.time()
    if sym in _company_cache and (now - _company_cache[sym]["ts"]) < COMPANY_TTL:
        return _company_cache[sym]["data"]
    try:
        import yfinance as yf
        t    = yf.Ticker(sym)
        info = t.info

        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if not price:
            fi    = t.fast_info
            price = getattr(fi, "last_price", None)

        prev      = info.get("previousClose") or info.get("regularMarketPreviousClose")
        day_chg   = round(float(price) - float(prev), 2) if price and prev else None
        day_pct   = round(day_chg / float(prev) * 100, 2) if day_chg and prev else None

        pre_mkt   = info.get("preMarketPrice")
        post_mkt  = info.get("postMarketPrice")

        w52h = info.get("fiftyTwoWeekHigh")
        w52l = info.get("fiftyTwoWeekLow")
        w52pos = None
        if w52h and w52l and price and (w52h - w52l) > 0:
            w52pos = round((float(price) - float(w52l)) / (float(w52h) - float(w52l)) * 100, 1)

        next_earn_ts = info.get("earningsTimestamp") or info.get("earningsTimestampStart")
        next_earn_dt = None
        earn_days    = None
        if next_earn_ts:
            try:
                ndt       = datetime.fromtimestamp(int(next_earn_ts), tz=timezone.utc)
                earn_days = (ndt - datetime.now(timezone.utc)).days
                next_earn_dt = ndt.strftime("%Y-%m-%d")
            except Exception:
                pass

        eps   = info.get("trailingEps")
        bvps  = info.get("bookValue")
        gnum  = calculate_graham_number(eps, bvps)
        gmgn  = round((gnum - float(price)) / float(price) * 100, 1) if gnum and price else None

        dcf   = calculate_dcf(info)
        dcfmgn = round((dcf - float(price)) / float(price) * 100, 1) if dcf and price else None

        nav   = calculate_nav(info)
        navmgn = round((nav - float(price)) / float(price) * 100, 1) if nav and price else None

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

        if vscore >= 80:    verdict, vcolor = "STRONG BUY",    "green"
        elif vscore >= 60:  verdict, vcolor = "BUY",           "green"
        elif vscore >= 40:  verdict, vcolor = "HOLD",          "amber"
        elif vscore >= 20:  verdict, vcolor = "AVOID",         "red"
        else:               verdict, vcolor = "STRONG AVOID",  "red"

        hq = ", ".join(filter(None, [info.get("city", ""), info.get("country", "")]))

        open_price = info.get("open") or info.get("regularMarketOpen")
        day_high   = info.get("dayHigh") or info.get("regularMarketDayHigh")
        day_low    = info.get("dayLow") or info.get("regularMarketDayLow")
        volume_val = info.get("volume") or info.get("regularMarketVolume")

        result = {
            "symbol":          sym,
            "name":            info.get("shortName") or info.get("longName", sym),
            "sector":          info.get("sector", "--"),
            "industry":        info.get("industry", "--"),
            "exchange":        info.get("exchange", ""),
            "employees":       info.get("fullTimeEmployees"),
            "hq":              hq,
            "website":         info.get("website", ""),
            "description":     info.get("longBusinessSummary", ""),
            "market_cap":      info.get("marketCap"),
            "price":           round(float(price), 2) if price else None,
            "prev_close":      round(float(prev), 2) if prev else None,
            "open":            round(float(open_price), 2) if open_price else None,
            "day_high":        round(float(day_high), 2) if day_high else None,
            "day_low":         round(float(day_low), 2) if day_low else None,
            "volume":          int(volume_val) if volume_val else None,
            "day_change":      day_chg,
            "day_pct":         day_pct,
            "pre_market":      round(float(pre_mkt), 2) if pre_mkt else None,
            "post_market":     round(float(post_mkt), 2) if post_mkt else None,
            "52w_high":        w52h,
            "52w_low":         w52l,
            "52w_pos":         w52pos,
            "next_earnings":   next_earn_dt,
            "earnings_days":   earn_days,
            "eps_est":         info.get("forwardEps"),
            "eps_actual":      eps,
            "pe":              info.get("trailingPE"),
            "forward_pe":      info.get("forwardPE"),
            "pb":              info.get("priceToBook"),
            "ps":              info.get("priceToSalesTrailing12Months"),
            "ev_ebitda":       info.get("enterpriseToEbitda"),
            "peg":             info.get("pegRatio"),
            "gross_margins":   info.get("grossMargins"),
            "op_margins":      info.get("operatingMargins"),
            "profit_margins":  info.get("profitMargins"),
            "roe":             info.get("returnOnEquity"),
            "roa":             info.get("returnOnAssets"),
            "current_ratio":   info.get("currentRatio"),
            "quick_ratio":     info.get("quickRatio"),
            "debt_equity":     info.get("debtToEquity"),
            "total_cash":      info.get("totalCash"),
            "free_cashflow":   info.get("freeCashflow"),
            "revenue_growth":  info.get("revenueGrowth"),
            "earnings_growth": info.get("earningsGrowth"),
            "eps":             eps,
            "forward_eps":     info.get("forwardEps"),
            "bvps":            bvps,
            "div_yield":       info.get("dividendYield"),
            "beta":            info.get("beta"),
            "analyst_target":  info.get("targetMeanPrice"),
            "recommendation":  info.get("recommendationKey", "").upper(),
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
            "timestamp": datetime.now(timezone.utc).isoformat(),
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

def get_chart_data(symbol, period="1y"):
    sym = symbol.strip().upper()
    cache_key = f"{sym}_{period}"
    now = time.time()
    if cache_key in _chart_cache and (now - _chart_cache[cache_key]["ts"]) < CHART_TTL:
        return _chart_cache[cache_key]["data"]
    try:
        import yfinance as yf
        interval_map = {
            "1d":  ("1d",  "5m"),
            "5d":  ("5d",  "15m"),
            "1mo": ("1mo", "1d"),
            "3mo": ("3mo", "1d"),
            "6mo": ("6mo", "1d"),
            "1y":  ("1y",  "1d"),
            "2y":  ("2y",  "1d"),
            "5y":  ("5y",  "1wk"),
            "max": ("max", "1mo"),
        }
        yf_period, interval = interval_map.get(period, ("1y", "1d"))
        t = yf.Ticker(sym)
        hist = t.history(period=yf_period, interval=interval)
        if hist.empty:
            return {"error": "No data available", "symbol": sym}

        closes = hist["Close"].tolist()
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
        for i, (ts, row) in enumerate(hist.iterrows()):
            data_points.append({
                "timestamp": ts.isoformat(),
                "open":   round(float(row["Open"]),  2),
                "high":   round(float(row["High"]),  2),
                "low":    round(float(row["Low"]),   2),
                "close":  round(float(row["Close"]), 2),
                "volume": int(row["Volume"]),
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


def get_watchlist_prices(tickers):
    results = []
    for sym in tickers[:20]:
        try:
            import yfinance as yf
            t  = yf.Ticker(sym)
            fi = t.fast_info
            price = getattr(fi, "last_price", None)
            prev  = getattr(fi, "previous_close", None)
            chg   = round(price - prev, 2) if price and prev else None
            pct   = round(chg / prev * 100, 2) if chg and prev else None
            results.append({
                "symbol":     sym,
                "price":      round(price, 2) if price else None,
                "change":     chg,
                "pct_change": pct,
                "direction":  "UP" if chg and chg > 0 else "DOWN" if chg and chg < 0 else "FLAT",
            })
        except Exception as e:
            results.append({"symbol": sym, "error": str(e)[:60]})
    return results
