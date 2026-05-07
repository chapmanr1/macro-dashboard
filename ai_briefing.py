# FILE: ai_briefing.py
# Bloomberg Macro Terminal — AI Morning Briefing via Anthropic

import os
import json
import logging
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

CACHE_FILE  = "briefing_cache.json"
CACHE_HOURS = 6


# ── TECHNICAL INDICATORS ──────────────────────────────────────
def _calculate_technicals():
    """Calculate S&P, VIX, 10Y, and sector technicals via yfinance."""
    tech = {}

    try:
        import yfinance as yf

        # ── S&P 500 ───────────────────────────────────────────
        try:
            spy = yf.Ticker("^GSPC")
            h = spy.history(period="1y")
            h = h.dropna(subset=["Close"])
            if len(h) >= 50:
                c = float(h["Close"].iloc[-1])
                ma50  = float(h["Close"].rolling(50).mean().iloc[-1])
                ma200 = float(h["Close"].rolling(200).mean().iloc[-1]) if len(h) >= 200 else None
                high_10 = float(h["Close"].iloc[-10]) if len(h) >= 10 else c
                high_30 = float(h["Close"].iloc[-30]) if len(h) >= 30 else c
                hi52 = float(h["High"].max())
                lo52 = float(h["Low"].min())
                tech["spy_current"]       = round(c, 2)
                tech["spy_vs_50dma"]      = round((c - ma50)  / ma50  * 100, 2)
                tech["spy_vs_200dma"]     = round((c - ma200) / ma200 * 100, 2) if ma200 else None
                tech["spy_50dma_level"]   = round(ma50, 2)
                tech["spy_200dma_level"]  = round(ma200, 2) if ma200 else None
                tech["spy_10d_momentum"]  = round((c - high_10) / high_10 * 100, 2)
                tech["spy_30d_momentum"]  = round((c - high_30) / high_30 * 100, 2)
                tech["spy_52w_high"]      = round(hi52, 2)
                tech["spy_52w_low"]       = round(lo52, 2)
                tech["spy_pct_from_high"] = round((c - hi52) / hi52 * 100, 2)
                tech["spy_pct_from_low"]  = round((c - lo52) / lo52 * 100, 2)
        except Exception as e:
            tech["spy_error"] = str(e)[:120]

        # ── VIX ───────────────────────────────────────────────
        try:
            vix = yf.Ticker("^VIX")
            vh = vix.history(period="60d").dropna(subset=["Close"])
            if len(vh) >= 2:
                vc = float(vh["Close"].iloc[-1])
                v30 = float(vh["Close"].mean())
                tech["vix_current"]  = round(vc, 2)
                tech["vix_30d_avg"]  = round(v30, 2)
                tech["vix_vs_avg"]   = round(vc - v30, 2)
                if vc < 15:
                    tech["vix_signal"] = "COMPLACENT — elevated complacency, watch for reversal"
                elif vc < 18:
                    tech["vix_signal"] = "CALM — normal conditions"
                elif vc < 25:
                    tech["vix_signal"] = "CAUTIOUS — elevated awareness"
                elif vc < 30:
                    tech["vix_signal"] = "FEARFUL — significant uncertainty"
                else:
                    tech["vix_signal"] = "PANIC — crisis territory"
        except Exception as e:
            tech["vix_error"] = str(e)[:120]

        # ── 10Y Treasury ──────────────────────────────────────
        try:
            tnx = yf.Ticker("^TNX")
            th = tnx.history(period="60d").dropna(subset=["Close"])
            if len(th) >= 2:
                tc = float(th["Close"].iloc[-1])
                tech["ten_year_yield"]     = round(tc / 10, 3)  # ^TNX is quoted ×10 in yfinance history
                tech["ten_year_30d_high"]  = round(float(th["High"].max()) / 10, 3)
                tech["ten_year_30d_low"]   = round(float(th["Low"].min()) / 10, 3)
                tech["ten_year_range"]     = (
                    f"{round(float(th['Low'].min())/10, 2)}% — "
                    f"{round(float(th['High'].max())/10, 2)}%"
                )
        except Exception as e:
            tech["ten_year_error"] = str(e)[:120]

        # ── SECTOR PERFORMANCE ────────────────────────────────
        sectors = {
            "XLK": "Technology", "XLF": "Financials", "XLE": "Energy",
            "XLV": "Healthcare", "XLI": "Industrials", "XLY": "Cons Discretionary",
            "XLP": "Cons Staples", "XLU": "Utilities", "XLRE": "Real Estate",
            "XLB": "Materials", "XLC": "Communications",
        }
        perf = {}
        for sym, name in sectors.items():
            try:
                sh = yf.Ticker(sym).history(period="5d").dropna(subset=["Close"])
                if len(sh) >= 2:
                    today_c = float(sh["Close"].iloc[-1])
                    prev_c  = float(sh["Close"].iloc[-2])
                    perf[name] = round((today_c - prev_c) / prev_c * 100, 2)
            except Exception:
                pass
        if perf:
            sorted_p = sorted(perf.items(), key=lambda x: x[1], reverse=True)
            tech["sector_performance"] = dict(sorted_p)
            tech["sector_leaders"]  = [s[0] for s in sorted_p[:3]]
            tech["sector_laggards"] = [s[0] for s in sorted_p[-3:]]

    except Exception as e:
        tech["global_error"] = str(e)[:120]

    return tech


def _calculate_key_levels(tech):
    """Derive support/resistance levels from technical data."""
    levels = {}
    try:
        c   = tech.get("spy_current")
        hi  = tech.get("spy_52w_high")
        lo  = tech.get("spy_52w_low")
        ma50  = tech.get("spy_50dma_level")
        ma200 = tech.get("spy_200dma_level")
        if c is None:
            return levels

        spy_lvls = {"current": c}
        if hi:
            spy_lvls["52w_high"] = hi
            spy_lvls["pct_below_high"] = round((c - hi) / hi * 100, 2)
        if lo:
            spy_lvls["52w_low"]   = lo
        if ma50:
            spy_lvls["50dma"]     = ma50
            spy_lvls["50dma_gap"] = f"{tech.get('spy_vs_50dma', 0):+.2f}%"
        if ma200:
            spy_lvls["200dma"]     = ma200
            spy_lvls["200dma_gap"] = f"{tech.get('spy_vs_200dma', 0):+.2f}%"

        # Round-number levels within ±5%
        base   = int(c / 100) * 100
        rounds = []
        for lvl in range(base - 200, base + 300, 50):
            if lvl != int(c) and abs(lvl - c) / c < 0.05:
                rounds.append(lvl)
        spy_lvls["nearby_round_levels"] = rounds

        levels["spy"] = spy_lvls

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
    now      = datetime.now()
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

    # ── FETCH DASHBOARD DATA ──────────────────────────────────
    try:
        import requests as req
        base = "http://localhost:5000"
        regime_data = req.get(f"{base}/api/regime", timeout=10).json()
        macro_data  = req.get(f"{base}/api/macro",  timeout=10).json()
        yields_data = req.get(f"{base}/api/yields", timeout=10).json()
        credit_data = req.get(f"{base}/api/credit", timeout=10).json()
        market_data = req.get(f"{base}/api/market", timeout=10).json()
        news_data   = req.get(f"{base}/api/news",   timeout=10).json()
        top_news    = (news_data.get("articles") or [])[:10]
    except Exception as e:
        return {"status": "data_error", "message": f"Could not fetch dashboard data: {e}"}

    # ── SUPPLEMENTAL CALCULATIONS ─────────────────────────────
    tech_data    = _calculate_technicals()
    key_levels   = _calculate_key_levels(tech_data)
    calendar     = _build_economic_calendar()

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
        "current_date":      datetime.now().strftime("%A, %B %d %Y"),
        "current_time":      datetime.now().strftime("%I:%M %p ET"),
        "day_of_week":       datetime.now().strftime("%A"),
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
        "top_headlines": [
            {
                "title":     a.get("title", ""),
                "source":    a.get("source", ""),
                "summary":   (a.get("description") or "")[:300],
                "published": a.get("publishedAt") or a.get("timestamp", ""),
                "impact":    a.get("score", ""),
            }
            for a in top_news
        ],
    }

    # ── CALL ANTHROPIC ────────────────────────────────────────
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)

        system_prompt = """You are a senior macro and technical analyst briefing financial advisor Ryan Chapman.

CRITICAL CONTEXT:
- Ryan has held a stagflation thesis since 2021
- His bear case is private credit cascade leading to wealth effect reversal and GDP contraction
- His bull case fear is Fed cuts aggressively and goldilocks resumes
- He cares about: macro regime changes, credit market stress, technical levels, institutional positioning, news flow
- Time horizon: 2-3 years primarily, with awareness of near-term positioning

THE CURRENT REGIME from his terminal is the SOURCE OF TRUTH for the macro environment. Address both: what the data is showing now AND how it relates to his thesis.

YOUR BRIEFING MUST INCLUDE THESE SECTIONS:

═══ MARKET PULSE ═══
2-3 sentences on what's happening in markets RIGHT NOW. Reference specific levels, percentages, moves. Note any technical breakouts or breakdowns. Mention sector rotation if notable.

═══ NEWS THAT MATTERS ═══
Synthesize the 3 most important news stories from the headlines provided. Don't just list them — explain why each matters and what it implies. Connect them to broader themes when relevant.

═══ TECHNICAL LANDSCAPE ═══
Specific technical observations:
- S&P 500 position vs key moving averages (use exact levels)
- Yield curve action — what specific levels mean
- VIX level interpretation
- Sector leadership/rotation
- Any unusual cross-asset moves
Be specific with numbers, not vague.

═══ REGIME STATUS ═══
The terminal currently shows: [USE ACTUAL REGIME LABEL FROM DATA]
- Address this current regime directly with specific indicator values from the data
- What this regime status means for positioning
- How this relates to Ryan's stagflation thesis: strengthening, stable, or weakening?
- Which falsification triggers are approaching?

═══ SPECIFIC LEVELS TO WATCH TODAY ═══
Provide ACTIONABLE, SPECIFIC items — never generic:
- Exact price/yield/spread levels with what a break above or below would signal
- Specific events with times if known from the calendar data
- At least 3-4 concrete watch points with numbers

═══ COUNTER-THESIS RISK ═══
What is Ryan potentially missing? What would challenge his current view? Be specific.

═══ POSITIONING IMPLICATIONS ═══
Based on the current regime AND today's action: 1-2 specific actionable considerations.

REQUIREMENTS:
- Maximum 650 words total
- Use specific numbers and levels throughout
- Reference actual data points from the context provided
- Address the ACTUAL CURRENT REGIME shown in the data
- No filler, no hedging language, no generic advice
- If data is missing for a section just skip it
- Bold key levels using **number** markdown

FORMAT: Use the ═══ SECTION NAME ═══ headers exactly as shown above."""

        # Build comprehensive user message
        spy_block = ""
        if "spy_current" in tech_data:
            spy_block = f"""S&P 500:
  Current: {tech_data.get('spy_current', 'N/A')}
  vs 50DMA ({tech_data.get('spy_50dma_level', 'N/A')}): {tech_data.get('spy_vs_50dma', 'N/A'):+.2f}%
  vs 200DMA ({tech_data.get('spy_200dma_level', 'N/A')}): {tech_data.get('spy_vs_200dma', 'N/A'):+.2f}%
  10d momentum: {tech_data.get('spy_10d_momentum', 'N/A'):+.2f}%
  30d momentum: {tech_data.get('spy_30d_momentum', 'N/A'):+.2f}%
  52w high: {tech_data.get('spy_52w_high', 'N/A')} ({tech_data.get('spy_pct_from_high', 'N/A'):+.2f}% from high)
  52w low:  {tech_data.get('spy_52w_low', 'N/A')}"""
        else:
            spy_block = f"S&P 500 technical data unavailable: {tech_data.get('spy_error', 'unknown error')}"

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

        user_message = f"""Generate today's macro briefing.

═══ CONTEXT ═══
Date: {context['current_date']}
Time: {context['current_time']}
Day: {context['day_of_week']}

═══ CURRENT REGIME (SOURCE OF TRUTH) ═══
Regime Label: {context['current_regime']}
Internal Classification: {context['internal_regime']}
Confidence: {context['regime_confidence']}%
Key Drivers: {json.dumps(context['regime_drivers'], indent=2)}
Key Risks from Engine: {json.dumps(context['regime_risks'], indent=2)}

═══ MACRO INDICATORS ═══
{json.dumps(context['macro_indicators'], indent=2)}

═══ YIELD CURVE ═══
{json.dumps(context['yield_curve'], indent=2)}

═══ CREDIT MARKETS ═══
{json.dumps(context['credit_spreads'], indent=2)}

═══ MARKET DATA ═══
{json.dumps(context['market_data'], indent=2)}

═══ TECHNICAL INDICATORS ═══
{spy_block}

{vix_block}

{ten_yr_block}

{sector_block}

═══ KEY LEVELS ═══
S&P 500:
{kl_spy}

10Y Treasury:
{kl_ty}

VIX:
{kl_vix}

HY Spread stress levels: 400bp = stress, 500bp = crisis

═══ INSTITUTIONAL FLOWS ═══
Sector rotation (ETF price action): {json.dumps(context['institutional']['sector_rotation'], indent=2)}
Leaders today: {context['institutional']['sector_leaders']}
Laggards today: {context['institutional']['sector_laggards']}

═══ TOP 10 HEADLINES ═══
{json.dumps(context['top_headlines'], indent=2)}

═══ ECONOMIC CALENDAR ═══
Today ({context['day_of_week']}): {json.dumps(context['calendar']['today'])}
Tomorrow: {json.dumps(context['calendar']['tomorrow'])}
This week: {context['calendar']['this_week']}

Generate the briefing now. Be specific, reference actual numbers, address the actual current regime."""

        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        briefing_text = message.content[0].text
        _save_cache(briefing_text)

        return {
            "status":       "success",
            "briefing":     briefing_text,
            "generated_at": datetime.now().isoformat(),
            "from_cache":   False,
        }

    except Exception as e:
        log.error(f"AI briefing error: {e}")
        return {
            "status":   "api_error",
            "message":  f"AI briefing failed: {e}",
            "fallback": "Briefing temporarily unavailable. Check API key and try again.",
        }


def force_regenerate():
    """Force regeneration ignoring cache."""
    try:
        os.remove(CACHE_FILE)
    except FileNotFoundError:
        pass
    return get_briefing()


def _load_cache():
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_cache(briefing_text):
    with open(CACHE_FILE, "w") as f:
        json.dump({"briefing": briefing_text, "generated_at": datetime.now().isoformat()}, f)


def _cache_valid(cached):
    if not cached or "generated_at" not in cached:
        return False
    age = datetime.now() - datetime.fromisoformat(cached["generated_at"])
    return age < timedelta(hours=CACHE_HOURS)
