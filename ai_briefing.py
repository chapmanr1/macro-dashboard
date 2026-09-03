# FILE: ai_briefing.py
# Bloomberg Macro Dashboard — AI Morning Briefing via Anthropic

import os
import json
import logging
import pytz
import threading
from datetime import datetime, timedelta
from twelve_data import get_quotes
from fred_data import get_series_history

log = logging.getLogger(__name__)

CACHE_FILE  = "briefing_cache.json"
CACHE_HOURS = 6
EASTERN     = pytz.timezone("America/New_York")

# Prevents concurrent requests from each triggering a separate Anthropic API call
_generation_lock = threading.Lock()


def _now_et():
    """Current datetime in Eastern Time."""
    return datetime.now(pytz.UTC).astimezone(EASTERN)


# ── TECHNICAL INDICATORS ──────────────────────────────────────
def _calculate_technicals():
    """Calculate S&P, VIX, 10Y, and sector technicals via Twelve Data and FRED."""
    tech = {}

    # ── S&P 500 (actual SPX levels from FRED SP500 series) ───────
    try:
        bars = get_series_history("SP500", 365)
        if len(bars) >= 50:
            closes = [b["value"] for b in bars]
            n = len(closes)
            c     = closes[-1]
            ma50  = sum(closes[n-50:n]) / 50
            ma200 = sum(closes[n-200:n]) / 200 if n >= 200 else None
            high_10 = closes[-10] if n >= 10 else closes[0]
            high_30 = closes[-30] if n >= 30 else closes[0]
            hi52 = max(closes)
            lo52 = min(closes)
            tech["spx_current"]       = round(c, 2)
            tech["spx_vs_50dma"]      = round((c - ma50)  / ma50  * 100, 2)
            tech["spx_vs_200dma"]     = round((c - ma200) / ma200 * 100, 2) if ma200 else None
            tech["spx_50dma_level"]   = round(ma50, 2)
            tech["spx_200dma_level"]  = round(ma200, 2) if ma200 else None
            tech["spx_10d_momentum"]  = round((c - high_10) / high_10 * 100, 2)
            tech["spx_30d_momentum"]  = round((c - high_30) / high_30 * 100, 2)
            tech["spx_52w_high"]      = round(hi52, 2)
            tech["spx_52w_low"]       = round(lo52, 2)
            tech["spx_pct_from_high"] = round((c - hi52) / hi52 * 100, 2)
            tech["spx_pct_from_low"]  = round((c - lo52) / lo52 * 100, 2)
    except Exception as e:
        tech["spx_error"] = str(e)[:120]

    # ── VIX (actual VIXCLS from FRED) ─────────────────────────
    try:
        vbars = get_series_history("VIXCLS", 60)
        if len(vbars) >= 2:
            vc_list = [b["value"] for b in vbars]
            vc  = vc_list[-1]
            v30 = sum(vc_list) / len(vc_list)
            tech["vix_current"]  = round(vc, 2)
            tech["vix_30d_avg"]  = round(v30, 2)
            tech["vix_vs_avg"]   = round(vc - v30, 2)
            if vc < 11:
                tech["vix_signal"] = "COMPLACENT — elevated complacency, watch for reversal"
            elif vc < 14:
                tech["vix_signal"] = "CALM — normal conditions"
            elif vc < 19:
                tech["vix_signal"] = "CAUTIOUS — elevated awareness"
            elif vc < 23:
                tech["vix_signal"] = "FEARFUL — significant uncertainty"
            else:
                tech["vix_signal"] = "PANIC — crisis territory"
    except Exception as e:
        tech["vix_error"] = str(e)[:120]

    # ── 10Y Treasury — sourced from FRED (already cached) ─────
    try:
        from fred_data import get_yields
        yields_data = get_yields()
        ten_yr = next(
            (y for y in yields_data.get("yields", []) if y.get("id") == "dgs10"),
            None,
        )
        if ten_yr and ten_yr.get("value") is not None:
            ty = float(ten_yr["value"])
            tech["ten_year_yield"] = round(ty, 3)
            # 30d range not available from cached FRED snapshot; AI will work without it
    except Exception as e:
        tech["ten_year_error"] = str(e)[:120]

    # ── SECTOR PERFORMANCE ────────────────────────────────────
    try:
        sector_map = {
            "XLK": "Technology", "XLF": "Financials", "XLE": "Energy",
            "XLV": "Healthcare", "XLI": "Industrials", "XLY": "Cons Discretionary",
            "XLP": "Cons Staples", "XLU": "Utilities", "XLRE": "Real Estate",
            "XLB": "Materials", "XLC": "Communications",
        }
        quotes = get_quotes(list(sector_map.keys()))
        perf = {}
        for sym, name in sector_map.items():
            q = quotes.get(sym)
            if q and q.get("percent_change") is not None:
                perf[name] = round(float(q["percent_change"]), 2)
        if perf:
            sorted_p = sorted(perf.items(), key=lambda x: x[1], reverse=True)
            tech["sector_performance"] = dict(sorted_p)
            tech["sector_leaders"]  = [s[0] for s in sorted_p[:3]]
            tech["sector_laggards"] = [s[0] for s in sorted_p[-3:]]
    except Exception as e:
        tech["sector_error"] = str(e)[:120]

    return tech


def _calculate_key_levels(tech):
    """Derive support/resistance levels from technical data."""
    levels = {}
    try:
        c   = tech.get("spx_current")
        hi  = tech.get("spx_52w_high")
        lo  = tech.get("spx_52w_low")
        ma50  = tech.get("spx_50dma_level")
        ma200 = tech.get("spx_200dma_level")
        if c is None:
            return levels

        spx_lvls = {"current": c}
        if hi:
            spx_lvls["52w_high"] = hi
            spx_lvls["pct_below_high"] = round((c - hi) / hi * 100, 2)
        if lo:
            spx_lvls["52w_low"]   = lo
        if ma50:
            spx_lvls["50dma"]     = ma50
            spx_lvls["50dma_gap"] = f"{tech.get('spx_vs_50dma', 0):+.2f}%"
        if ma200:
            spx_lvls["200dma"]     = ma200
            spx_lvls["200dma_gap"] = f"{tech.get('spx_vs_200dma', 0):+.2f}%"

        # Round-number levels within ±5%
        base   = int(c / 100) * 100
        rounds = []
        for lvl in range(base - 200, base + 300, 50):
            if lvl != int(c) and abs(lvl - c) / c < 0.05:
                rounds.append(lvl)
        spx_lvls["nearby_round_levels"] = rounds

        levels["spx"] = spx_lvls

        # 10Y levels
        ty = tech.get("ten_year_yield")
        if ty:
            levels["ten_year"] = {
                "current":     ty,
                "30d_range":   tech.get("ten_year_range", "N/A"),
                "watch_above": round(ty + 0.10, 2),
                "watch_below": round(ty - 0.10, 2),
            }

        # VIX levels
        vc = tech.get("vix_current")
        if vc:
            levels["vix"] = {
                "current":    vc,
                "30d_avg":    tech.get("vix_30d_avg"),
                "complacent": 15,
                "cautious":   20,
                "stressed":   25,
                "panic":      35,
            }

    except Exception as e:
        levels["error"] = str(e)[:120]

    return levels


def _build_economic_calendar():
    """Return today's and tomorrow's known recurring economic events."""
    now      = _now_et()
    tomorrow = now + timedelta(days=1)
    dow      = now.strftime("%A")
    dom      = now.day
    dow_tom  = tomorrow.strftime("%A")

    def _events_for(d, weekday, mday):
        events = []
        if weekday == "Thursday":
            events.append("8:30 AM ET — Initial Jobless Claims")
        if weekday == "Friday" and mday <= 7:
            events.append("8:30 AM ET — Nonfarm Payrolls (first Friday of month)")
        if mday in (12, 13, 14, 15):
            events.append("8:30 AM ET — CPI release (mid-month)")
        if mday in (27, 28, 29, 30):
            events.append("8:30 AM ET — Core PCE release (month-end)")
        if weekday == "Wednesday" and 15 <= mday <= 21:
            events.append("2:00 PM ET — Possible FOMC meeting (mid-month Wed)")
        return events if events else ["No major scheduled releases"]

    return {
        "today":     _events_for(now, dow, dom),
        "tomorrow":  _events_for(tomorrow, dow_tom, tomorrow.day),
        "this_week": "Thursday: Jobless Claims. Check Fed speakers calendar and earnings.",
    }


def _load_watchlist_tickers() -> list:
    """Read watchlist tickers from server-side watchlist.json."""
    try:
        with open("watchlist.json") as f:
            data = json.load(f)
        return [t.upper() for t in data.get("tickers", []) if str(t).strip()]
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _get_watchlist_context(tickers: list, news_articles: list) -> str:
    """Fetch live quotes + news mentions for watchlist tickers. Returns formatted context block."""
    if not tickers:
        return ""
    try:
        quotes = get_quotes(tickers)
    except Exception as e:
        log.warning(f"Watchlist quotes failed: {e}")
        quotes = {}

    def _mentions(article: dict, ticker: str) -> bool:
        text = f"{article.get('title', '')} {article.get('description', '')}".upper()
        return ticker.upper() in text

    lines = []
    for ticker in tickers:
        q = quotes.get(ticker.upper(), {})
        try:
            price = round(float(q["close"]), 2)
            pct   = round(float(q["percent_change"]), 2)
            arrow = "▲" if pct > 0 else "▼" if pct < 0 else "→"
            price_str = f"${price} {arrow}{pct:+.2f}%"
        except (KeyError, TypeError, ValueError):
            price_str = "no data"
        relevant = [a for a in news_articles if _mentions(a, ticker)]
        news_str = f" | NEWS: {relevant[0].get('title', '')[:120]}" if relevant else ""
        lines.append(f"  {ticker}: {price_str}{news_str}")
    return "\n".join(lines)


# ── MAIN ENTRY POINT ──────────────────────────────────────────
def get_briefing():
    """Generate AI morning briefing from current dashboard data."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {
            "status": "no_api_key",
            "message": "Anthropic API key not configured",
            "setup_instructions": [
                "1. Go to console.anthropic.com",
                "2. Create account separate from Claude Pro",
                "3. Add credit card (you'll be billed pennies per month)",
                "4. Set spending limit to $5/month for safety",
                "5. Create API key (starts with sk-ant-)",
                "6. In Replit: Tools > Secrets > add ANTHROPIC_API_KEY",
                "7. Refresh this page",
            ],
        }

    cached = _load_cache()
    if cached and _cache_valid(cached):
        return {
            "status":       "success",
            "briefing":     cached["briefing"],
            "generated_at": cached["generated_at"],
            "from_cache":   True,
        }

    with _generation_lock:
        # Re-check cache after acquiring lock — a concurrent request may have
        # already generated and saved it while we were waiting.
        cached = _load_cache()
        if cached and _cache_valid(cached):
            return {
                "status":       "success",
                "briefing":     cached["briefing"],
                "generated_at": cached["generated_at"],
                "from_cache":   True,
            }

        return _generate_briefing(api_key)


def _generate_briefing(api_key: str) -> dict:
    """Call Anthropic and generate a new briefing. Must be called under _generation_lock."""
    # ── FETCH DASHBOARD DATA ──────────────────────────────────
    try:
        from regime_engine import get_regime
        from fred_data import get_macro, get_yields, get_credit
        from market_data import get_market
        from news_feed import get_news
        from global_data import get_cot_positioning
        from fed_watch import get_fed_watch
        regime_data = get_regime()
        macro_data  = get_macro()
        yields_data = get_yields()
        credit_data = get_credit()
        market_data = get_market()
        news_data   = get_news()
        cot_data    = get_cot_positioning()
        fedwatch_data = get_fed_watch()
        top_news    = (news_data.get("articles") or [])[:20]
    except Exception as e:
        return {"status": "data_error", "message": f"Could not fetch dashboard data: {e}"}

    # ── NEWS RECENCY SPLIT ────────────────────────────────────
    # Separate news into "this morning / last 12h" vs "prior session"
    # so Claude can distinguish fresh signals from already-priced news.
    cutoff_12h = _now_et() - timedelta(hours=12)
    fresh_news, stale_news = [], []
    for a in top_news:
        ts_str = a.get("publishedAt") or a.get("timestamp", "")
        try:
            from datetime import timezone as _tz
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.astimezone(EASTERN) >= cutoff_12h:
                fresh_news.append(a)
            else:
                stale_news.append(a)
        except (ValueError, AttributeError):
            stale_news.append(a)

    # ── SUPPLEMENTAL CALCULATIONS ─────────────────────────────
    tech_data    = _calculate_technicals()
    key_levels   = _calculate_key_levels(tech_data)
    calendar     = _build_economic_calendar()
    watchlist_tickers   = _load_watchlist_tickers()
    watchlist_context   = _get_watchlist_context(watchlist_tickers, top_news)

    # ── BUILD CONTEXT ─────────────────────────────────────────
    regime_label      = regime_data.get("label") or regime_data.get("regime", "UNKNOWN")
    regime_confidence = regime_data.get("confidence_score") or regime_data.get("confidence", 0)
    regime_breakdown  = regime_data.get("indicator_breakdown", [])
    regime_risks      = regime_data.get("key_risks", [])
    regime_internal   = regime_data.get("internal_label", "")

    # Extract top 3 regime drivers
    top_drivers = [
        f"{r['name']}: {r['value']} ({r['signal']})"
        for r in regime_breakdown[:3]
        if r.get("name") and r.get("value")
    ]

    context = {
        "current_date":      _now_et().strftime("%A, %B %d %Y"),
        "current_time":      _now_et().strftime("%I:%M %p ET"),
        "day_of_week":       _now_et().strftime("%A"),
        "current_regime":    regime_label,
        "internal_regime":   regime_internal,
        "regime_confidence": regime_confidence,
        "regime_drivers":    top_drivers,
        "regime_risks":      regime_risks,
        "macro_indicators":  macro_data,
        "yield_curve":       yields_data,
        "credit_spreads":    credit_data,
        "market_data":       market_data,
        "technical":         tech_data,
        "key_levels":        key_levels,
        "calendar":          calendar,
        "institutional": {
            "note":            "Positioning derived from public ETF/futures data",
            "sector_rotation": tech_data.get("sector_performance", {}),
            "sector_leaders":  tech_data.get("sector_leaders", []),
            "sector_laggards": tech_data.get("sector_laggards", []),
        },
        "cot_positioning":  cot_data,
        "fed_watch":        fedwatch_data,
        "fresh_news": [
            {
                "title":     a.get("title", ""),
                "source":    a.get("source", ""),
                "summary":   (a.get("description") or "")[:300],
                "published": a.get("publishedAt") or a.get("timestamp", ""),
            }
            for a in fresh_news
        ],
        "prior_news": [
            {
                "title":     a.get("title", ""),
                "source":    a.get("source", ""),
                "summary":   (a.get("description") or "")[:200],
                "published": a.get("publishedAt") or a.get("timestamp", ""),
            }
            for a in stale_news
        ],
    }

    # ── CALL ANTHROPIC ────────────────────────────────────────
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)

        system_prompt = """You are a senior macro strategist writing the morning briefing for Ryan Chapman, a financial advisor at Stifel Financial.

ALL TIMES ARE EASTERN TIME (ET). NYSE hours: 9:30 AM–4:00 PM ET.

RYAN'S STANDING CONTEXT:
- Stagflation thesis held since 2021 (50% base case): inflation stays sticky, growth stays weak
- Bear case (30%): private credit cascade → wealth effect reversal → GDP contraction
- Bull case fear (20%): Fed cuts aggressively, goldilocks resumes
- Falsification triggers: Core PCE below 2.5% for 3 months, GDP above 2.5% for 2 quarters, HY spreads sustained below 300bp, productivity above 2% sustained
- Cares about: regime changes, credit stress, cross-asset divergences, institutional positioning, what markets are pricing vs what the Fed is saying

THE CURRENT REGIME from his terminal is the SOURCE OF TRUTH. Always address it by name with specific numbers.

WHAT MAKES A GREAT BRIEFING:
The goal is not to summarize the news — Ryan can read headlines. The goal is to tell him what the data implies that the news hasn't said yet, and whether this morning's action confirms or threatens his thesis. Every section must earn its place with specific numbers. No filler, no hedging, no generic statements.

YOUR BRIEFING MUST USE THESE SECTIONS IN THIS ORDER:

═══ MORNING CONTEXT ═══
What happened in the last 12 hours that actually matters. Lead with any economic data releases or earnings that printed this morning — state the number, the expectation if known, and critically: how the market is reacting vs what you'd expect. A "good" number the market sells is more important than the number itself. If there's a Fed speaker or policy statement in the fresh news, flag it immediately. If nothing significant happened overnight, say so in one sentence and move on — don't pad this section. 2-4 sentences max.

═══ WHAT THE DATA SAYS ═══
This is the section the financial media won't write. Identify 2-3 cross-asset signals that headlines are missing. For each signal: state the observation with a specific number, then say what it implies. Draw from the full data set — all of the following are valid sources:
- Credit (HY spreads) moving against equities — which market is right?
- VIX rising while the S&P holds — hedging beneath the surface
- VIX term structure in backwardation — near-term fear elevated even if headline VIX looks moderate
- Defensive sectors (XLU, XLP, XLRE) rotating in without a headline reason — quiet risk-off
- Equal-weight (RSP) underperforming cap-weight (SPY) by a meaningful margin — index concentration building
- Copper/Gold ratio direction vs 10Y yield direction — if they're diverging, one market is mispriced
- COT shows large speculators EXTREME LONG equities or EXTREME SHORT bonds — crowded trade, reversal risk (note: ~1 week lag, structural context not real-time)
- Rate market (FedWatch) pricing cuts while the Fed is talking hikes (or vice versa) — someone is wrong, that's a trade
- DXY rising while equities also rise — unusual, signals dollar demand from risk-off flows underneath
Every signal must cite the actual number from the data. Do not include a signal if you don't have a specific number to support it.

═══ REGIME STATUS ═══
Current regime: [USE EXACT LABEL FROM DATA]
Confidence: [score]% — [strengthening / stable / at risk of shifting — pick one and explain why in one sentence]
Top drivers: list the top 3 indicators with their actual values.
Falsification check: Which of Ryan's triggers is currently closest to being hit? State the current reading, the threshold, and the gap. (Triggers: Core PCE below 2.5%, GDP above 2.5%, HY spreads below 300bp, productivity above 2%)
Regime implication: One sentence on what this regime means for positioning right now.

═══ MARKET SNAPSHOT & KEY LEVELS ═══
State the current condition across six indicators — specific numbers only, no vague commentary:
1. S&P 500 — where it sits vs its 50DMA and 200DMA (state exact level of each moving average)
2. VIX — current level vs its 30-day average; state what VIX÷16 implies about today's expected daily move and whether the actual market move is normal or unusual
3. 10Y Treasury yield — current level and whether it's rising or falling recently
4. Crude Oil — current price and whether it is above/below the key $80 and $100 thresholds; what that means for the inflation picture
5. US Dollar (DXY) — current level and direction; one sentence on what that implies for global risk appetite and commodities
6. Market Breadth (RSP vs SPY) — the RSP−SPY differential and signal; whether the average stock is keeping up with the index or falling behind

Then list 3-5 specific levels that would change the picture if broken today. Include levels across equities, yields, commodities, or spreads — not just SPX. For each: the exact level, what a breach means, and the catalyst or time if known. Format each as: "Instrument at Level — consequence — catalyst (if any)"

═══ COUNTER-THESIS RISK ═══
One data point or market signal most inconsistent with the stagflation thesis right now. State the exact number. Then one sentence: noise or genuine threat?

═══ POSITIONING IMPLICATIONS ═══
1-2 specific, actionable considerations based on the current regime and this morning's action. Not generic asset allocation — specific to what is actually happening today. If COT or FedWatch signals something actionable, include it here.

═══ WATCHLIST ═══
For each ticker in the watchlist data: current price, day's move, one sentence of macro context for that move. Skip tickers with no data. Omit section entirely if watchlist is empty.

═══ CFP CONNECTION ═══
One sentence connecting today's most notable macro condition to a CFP curriculum topic (retirement income, portfolio management, risk management, economic analysis, tax planning, or estate planning).

HARD REQUIREMENTS:
- 900 words maximum total
- Every number you cite must come from the data provided — no fabrication
- Bold key levels: **5,400** not "around 5400"
- If a data section is missing or empty, skip that reference entirely — do not write "data unavailable"
- Use ═══ SECTION NAME ═══ headers exactly as shown
- Be direct and specific — Ryan is a financial advisor preparing for client meetings, not a casual reader"""

        # Strip sparkline arrays from credit data — they're for frontend charts only
        # and waste ~800-1200 tokens that Claude can't use in a text briefing.
        _SPARKLINE_KEYS = {"sparkline", "sparkline_dated"}
        def _strip_sparklines(items: list) -> list:
            return [{k: v for k, v in item.items() if k not in _SPARKLINE_KEYS} for item in items]

        clean_credit = {
            k: (_strip_sparklines(v) if isinstance(v, list) else v)
            for k, v in credit_data.items()
        }

        # Build comprehensive user message
        spy_block = ""
        if "spx_current" in tech_data:
            spy_block = f"""S&P 500 (actual SPX):
  Current: {tech_data.get('spx_current', 'N/A')}
  vs 50DMA ({tech_data.get('spx_50dma_level', 'N/A')}): {tech_data.get('spx_vs_50dma', 'N/A'):+.2f}%
  vs 200DMA ({tech_data.get('spx_200dma_level', 'N/A')}): {tech_data.get('spx_vs_200dma', 'N/A'):+.2f}%
  10d momentum: {tech_data.get('spx_10d_momentum', 'N/A'):+.2f}%
  30d momentum: {tech_data.get('spx_30d_momentum', 'N/A'):+.2f}%
  52w high: {tech_data.get('spx_52w_high', 'N/A')} ({tech_data.get('spx_pct_from_high', 'N/A'):+.2f}% from high)
  52w low:  {tech_data.get('spx_52w_low', 'N/A')}"""
        else:
            spy_block = f"S&P 500 technical data unavailable: {tech_data.get('spx_error', 'unknown error')}"

        vix_block = ""
        if "vix_current" in tech_data:
            vix_block = f"""VIX:
  Current: {tech_data.get('vix_current', 'N/A')}
  30d avg:  {tech_data.get('vix_30d_avg', 'N/A')}
  vs avg:   {tech_data.get('vix_vs_avg', 'N/A'):+.2f}
  Signal:   {tech_data.get('vix_signal', 'N/A')}"""
        else:
            vix_block = f"VIX data unavailable: {tech_data.get('vix_error', 'unknown error')}"

        ten_yr_block = ""
        if "ten_year_yield" in tech_data:
            ten_yr_block = f"""10Y Treasury:
  Current yield: {tech_data.get('ten_year_yield', 'N/A')}%
  60d range:     {tech_data.get('ten_year_range', 'N/A')}"""
        else:
            ten_yr_block = f"10Y data unavailable: {tech_data.get('ten_year_error', 'unknown error')}"

        sector_block = ""
        if tech_data.get("sector_performance"):
            sector_lines = "\n".join(
                f"  {name}: {pct:+.2f}%"
                for name, pct in tech_data["sector_performance"].items()
            )
            sector_block = f"Sector Performance Today (ranked best to worst):\n{sector_lines}"
        else:
            sector_block = "Sector performance data unavailable."

        kl_spy = json.dumps(key_levels.get("spy", {}), indent=2)
        kl_ty  = json.dumps(key_levels.get("ten_year", {}), indent=2)
        kl_vix = json.dumps(key_levels.get("vix", {}), indent=2)

        # ── BUILD COMMODITY BLOCK ─────────────────────────────
        commod_block = ""
        commodities = market_data.get("commodities", [])
        if commodities:
            lines = []
            for c in commodities:
                lbl  = c.get("label", "")
                px   = c.get("price")
                pct  = c.get("pct_change")
                sfx  = c.get("suffix", "")
                px_str  = f"${px:.2f}" if px is not None else "N/A"
                pct_str = f"{pct:+.2f}%" if pct is not None else ""
                lines.append(f"  {lbl}: {px_str} {pct_str} {sfx}".rstrip())
            commod_block = "Commodities:\n" + "\n".join(lines) + (
                "\nReference: Crude >$80 = elevated inflation pressure | >$100 = significant stagflation driver"
                " | Gold rising + real yields falling = inflation hedge demand | Copper falling = growth concern"
            )
        else:
            commod_block = "Commodity data unavailable."

        # ── BUILD FX BLOCK ────────────────────────────────────
        fx_block = ""
        currencies = market_data.get("currencies", [])
        if currencies:
            lines = []
            for c in currencies:
                lbl  = c.get("label", "")
                px   = c.get("price")
                pct  = c.get("pct_change")
                px_str  = f"{px:.4f}" if px is not None else "N/A"
                pct_str = f"{pct:+.2f}%" if pct is not None else ""
                lines.append(f"  {lbl}: {px_str} {pct_str}")
            fx_block = "Currencies:\n" + "\n".join(lines) + (
                "\nReference: DXY rising = global risk-off, commodity headwind, EM stress"
                " | DXY falling = risk-on, commodity tailwind | EUR/USD below 1.05 = extreme USD strength"
            )
        else:
            fx_block = "FX data unavailable."

        # ── BUILD CU/AU RATIO BLOCK ───────────────────────────
        cu_au_block = ""
        cu_au_ratio = market_data.get("cu_au_ratio")
        cu_au_signal = market_data.get("cu_au_signal", "")
        cu_au_detail = market_data.get("cu_au_detail", "")
        if cu_au_ratio is not None:
            cu_au_block = (
                f"Copper/Gold Ratio: {cu_au_ratio:.4f}\n"
                f"  Signal: {cu_au_signal}\n"
                f"  {cu_au_detail}\n"
                f"Reference: Rising ratio = growth/risk-on (copper outperforming gold)."
                f" Falling ratio = growth concern/stagflation (gold outperforming copper)."
                f" Ratio tracks 10Y Treasury yields historically."
            )
        else:
            cu_au_block = "Cu/Au ratio data unavailable."

        # ── BUILD VIX TERM STRUCTURE BLOCK ────────────────────
        vix_term_block = ""
        vix_terms = market_data.get("vix_term", [])
        vix_term_signal = market_data.get("vix_term_signal", "")
        vix_term_detail = market_data.get("vix_term_detail", "")
        if vix_terms:
            term_lines = []
            for t in vix_terms:
                lbl = t.get("label", "")
                px  = t.get("price")
                px_str = f"{px:.2f}" if px is not None else "N/A"
                term_lines.append(f"  {lbl}: {px_str}")
            vix_term_block = (
                "VIX Term Structure:\n"
                + "\n".join(term_lines) + "\n"
                + f"  Signal: {vix_term_signal} — {vix_term_detail}\n"
                + "Reference: Contango (near < long) = calm, normal structure."
                " Backwardation (near > long) = near-term fear elevated, event risk priced."
                " Deep backwardation historically marks market bottoms or crisis peaks."
            )
        else:
            vix_term_block = "VIX term structure data unavailable."

        # ── BUILD BREADTH SNAPSHOT BLOCK ──────────────────────
        breadth_snapshot_block = ""
        b = market_data.get("breadth", {})
        if b:
            spy_b  = b.get("spy_pct")
            rsp_b  = b.get("rsp_pct")
            diff_b = b.get("differential")
            sig_b  = b.get("signal", "")
            det_b  = b.get("detail", "")
            spy_str  = f"{spy_b:+.2f}%" if spy_b is not None else "N/A"
            rsp_str  = f"{rsp_b:+.2f}%" if rsp_b is not None else "N/A"
            diff_str = f"{diff_b:+.2f}%" if diff_b is not None else "N/A"
            breadth_snapshot_block = (
                f"Market Breadth (RSP vs SPY):\n"
                f"  SPY (cap-weight S&P 500): {spy_str}\n"
                f"  RSP (equal-weight S&P 500): {rsp_str}\n"
                f"  RSP − SPY differential: {diff_str} — Signal: {sig_b}\n"
                f"  {det_b}\n"
                f"Reference: RSP > SPY = broad participation (healthy) | SPY > RSP = mega-cap concentration (fragility risk)"
            )
        else:
            breadth_snapshot_block = "Breadth data unavailable."

        wl_block = ""
        if watchlist_context:
            wl_block = f"""
═══ WATCHLIST DATA ═══
{watchlist_context}
"""

        # ── BUILD COT BLOCK ───────────────────────────────────
        cot_block = ""
        cot_positions = context.get("cot_positioning", {}).get("positions", [])
        cot_as_of     = context.get("cot_positioning", {}).get("as_of", "N/A")
        if cot_positions:
            cot_lines = []
            for p in cot_positions:
                signal = p.get("signal", "N/A")
                score  = p.get("score")
                score_str = f"{score:+.0f}" if score is not None else "N/A"
                net    = p.get("net_long")
                net_str = f"{net:,}" if net is not None else "N/A"
                cot_lines.append(
                    f"  {p.get('label','?'):12s} | Score: {score_str:>6} | Net Long: {net_str:>12} | Signal: {signal}"
                )
            cot_block = (
                f"CFTC COT (Large Speculator Positioning — as of {cot_as_of}, ~1 week lag):\n"
                + "\n".join(cot_lines)
                + "\nInterpretation: Score -100 to +100 normalized vs 52-week range. "
                "EXTREME readings = crowded trade = reversal risk."
            )
        else:
            cot_block = "COT data unavailable."

        # ── BUILD FEDWATCH BLOCK ──────────────────────────────
        fw = context.get("fed_watch", {})
        fw_block = ""
        if fw and not fw.get("error"):
            fw_signal = fw.get("signal", "N/A")
            fw_detail = fw.get("signal_detail", "")
            fw_ff     = fw.get("current_ff", "N/A")
            path_lines = []
            for h in fw.get("implied_path", []):
                lbl    = h.get("label", "?")
                cuts   = h.get("cuts_priced")
                dirn   = h.get("direction", "")
                cuts_str = f"{cuts:+.1f} cuts" if cuts is not None else "N/A"
                path_lines.append(f"  {lbl}: {cuts_str} ({dirn})")
            fw_block = (
                f"Rate Market Expectations (CME-implied via FRED yields):\n"
                f"  Current Fed Funds: {fw_ff}%\n"
                f"  Market Signal: {fw_signal} — {fw_detail}\n"
                + "\n".join(path_lines)
            )
        else:
            fw_block = "FedWatch data unavailable."

        # ── BUILD NEWS BLOCKS ─────────────────────────────────
        def _fmt_news(articles: list, max_summary: int = 300) -> str:
            if not articles:
                return "  (none)"
            lines = []
            for i, a in enumerate(articles, 1):
                pub = (a.get("published") or "")[:16]
                lines.append(
                    f"  {i}. [{a.get('source','')}] {a.get('title','')}\n"
                    f"     Published: {pub}\n"
                    f"     {(a.get('summary') or '')[:max_summary]}"
                )
            return "\n".join(lines)

        fresh_block = _fmt_news(context.get("fresh_news", []))
        prior_block = _fmt_news(context.get("prior_news", []), max_summary=150)

        user_message = f"""Generate today's morning briefing.

═══ DATE & TIME ═══
Date: {context['current_date']}
Time: {context['current_time']} ({context['day_of_week']})

═══ CURRENT REGIME (SOURCE OF TRUTH) ═══
Label: {context['current_regime']}
Internal: {context['internal_regime']}
Confidence: {context['regime_confidence']}%
Top Drivers: {json.dumps(context['regime_drivers'], indent=2)}
Key Risks: {json.dumps(context['regime_risks'], indent=2)}

═══ NEWS — LAST 12 HOURS (this morning / overnight) ═══
{fresh_block}

═══ NEWS — PRIOR SESSION (already priced, context only) ═══
{prior_block}

═══ ECONOMIC CALENDAR ═══
Today ({context['day_of_week']}): {json.dumps(context['calendar']['today'])}
Tomorrow: {json.dumps(context['calendar']['tomorrow'])}

═══ INSTITUTIONAL POSITIONING (CFTC COT) ═══
{cot_block}

═══ RATE MARKET EXPECTATIONS ═══
{fw_block}

═══ CREDIT MARKETS ═══
{json.dumps(clean_credit, indent=2)}
HY stress levels: 400bp = stress, 500bp = crisis

═══ MACRO INDICATORS ═══
{json.dumps(context['macro_indicators'], indent=2)}

═══ YIELD CURVE ═══
{json.dumps(context['yield_curve'], indent=2)}

═══ TECHNICALS ═══
{spy_block}

{vix_block}

{ten_yr_block}

{sector_block}

═══ COMMODITIES ═══
{commod_block}

═══ CURRENCIES & DOLLAR ═══
{fx_block}

═══ MARKET BREADTH ═══
{breadth_snapshot_block}

═══ COPPER/GOLD RATIO (CROSS-ASSET GROWTH SIGNAL) ═══
{cu_au_block}

═══ VIX TERM STRUCTURE ═══
{vix_term_block}

═══ KEY LEVELS ═══
S&P 500:
{kl_spy}

10Y Treasury:
{kl_ty}

VIX:
{kl_vix}
{wl_block}
Generate the briefing now. Use specific numbers from the data above. Do not fabricate any figures."""

        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=3500,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
        )

        briefing_text = next(block.text for block in message.content if block.type == "text")
        _save_cache(briefing_text)

        return {
            "status":       "success",
            "briefing":     briefing_text,
            "generated_at": _now_et().isoformat(),
            "from_cache":   False,
        }

    except Exception as e:
        log.error(f"AI briefing error: {e}")
        return {
            "status":   "api_error",
            "message":  f"AI briefing failed: {e}",
            "fallback": "Briefing temporarily unavailable. Check API key and try again.",
        }


def force_regenerate() -> dict:
    """Force regeneration ignoring cache, with 15-minute rate limit."""
    cached = _load_cache()
    if cached and "generated_at" in cached:
        cached_time = datetime.fromisoformat(cached["generated_at"])
        if cached_time.tzinfo is None:
            cached_time = EASTERN.localize(cached_time)
        age = _now_et() - cached_time
        cooldown = timedelta(minutes=15)
        if age < cooldown:
            remaining = int((cooldown - age).total_seconds() / 60) + 1
            return {
                "status":       "rate_limited",
                "message":      f"Briefing refreshed recently — next manual refresh available in {remaining} minutes.",
                "generated_at": cached["generated_at"],
            }
    try:
        os.remove(CACHE_FILE)
    except FileNotFoundError:
        pass
    return get_briefing()


def _prewarm_briefing() -> None:
    """Background startup task: generate briefing cache so it's ready on first page load."""
    import time as _time
    _time.sleep(20)  # Let other startup tasks (FRED, market data) initialize first
    try:
        cached = _load_cache()
        if cached and _cache_valid(cached):
            log.info("Briefing pre-warm skipped — valid cache already exists.")
            return
        log.info("Briefing pre-warm: generating fresh briefing in background...")
        get_briefing()
        log.info("Briefing pre-warm complete.")
    except Exception as e:
        log.warning(f"Briefing pre-warm failed (non-fatal): {e}")


def _load_cache():
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_cache(briefing_text):
    with open(CACHE_FILE, "w") as f:
        json.dump({"briefing": briefing_text, "generated_at": _now_et().isoformat()}, f)


def _cache_valid(cached):
    if not cached or "generated_at" not in cached:
        return False
    cached_time = datetime.fromisoformat(cached["generated_at"])
    if cached_time.tzinfo is None:
        cached_time = EASTERN.localize(cached_time)
    age = _now_et() - cached_time
    return age < timedelta(hours=CACHE_HOURS)
