# Macro Dashboard — Claude Code Context

## What this project is
A personal Bloomberg-style macro intelligence terminal built by Ryan Chapman,
a 25-year-old financial advisor at Stifel Financial. This is a private,
single-user tool used daily for client meeting preparation, market analysis,
and macro research. It is NOT a commercial product.

Live URL: https://macro-dashboard-wxj5.onrender.com
GitHub: github.com/chapmanr1/macro-dashboard (private)
Local: ~/Desktop/macro-dashboard

## Running the app

```bash
# Local development
python3 main.py

# Production (Render uses this — 1 worker, 120s timeout)
gunicorn main.py:app --workers 1 --timeout 120 --bind 0.0.0.0:$PORT
```

Port defaults to 5000 (`PORT` env var overrides). No tests or linters configured.

## Stack
- Python 3.11, Flask, gunicorn
- Frontend: vanilla HTML/CSS/JavaScript (no React, no build step)
- Hosting: Render Starter ($7/mo), auto-deploys on push to main
- No Docker, no virtual environments beyond .venv

## Data sources (current architecture — do not change without asking)
- FRED API → indices (SP500, DJIA, NASDAQCOM, RU2000PR, VIXCLS),
  yields, macro indicators, credit spreads
- Twelve Data free tier → individual stock quotes, watchlist,
  sector ETF rotation (XLK, XLF, XLE, XLV, XLI, XLB, XLP, XLY,
  XLU, XLRE, XLC), charts
- Anthropic API (claude-sonnet-4-6) → AI Morning Briefing
- RSS feeds → news aggregation (multiple sources)
- SEC EDGAR → company filings (research panel)
- NO proxy, NO yfinance, NO Webshare — these were removed

## Environment variables (all set in Render)
- FRED_API_KEY
- TWELVE_DATA_API_KEY
- ANTHROPIC_API_KEY
- PYTHON_VERSION=3.11

## Dashboard features
Six tabs: Overview, Markets, Economy, Credit, News, Portfolio (inactive)

Overview tab:
- AI Morning Briefing (Anthropic API, cached 6 hours)
- Regime status with confidence score
- Macro indicators from FRED
- Suggested positioning drill-down

Markets tab:
- Index levels from FRED (SP500, DJIA, NASDAQCOM, RU2000PR, VIX)
- Sector rotation from Twelve Data (11 SPDR ETFs)
- Watchlist with live prices
- Company analysis cards with Graham/Buffett scorecards
- Price charts with period selector and MA overlays

Economy tab: FRED economic indicators
Credit tab: FRED credit spreads and stress indicators
News tab: RSS aggregation from multiple sources
Portfolio tab: feature-flagged OFF (do not activate)

## Regime engine
Auto-calibrating classification with 6 regimes:
STAGFLATION_RISK, STAGFLATION, REFLATION, STRONG_GROWTH,
CONTRACTION, OVERHEATING

Key rules:
- Hysteresis bands (different enter vs exit thresholds)
- Temporal smoothing (3 consecutive readings to change)
- Minimum 24-hour duration between regime changes
- Cached 60 minutes

Ryan's current thesis: stagflation persists 2-3 years (50% base case),
private credit cascade risk (30% bear), Fed cuts + AI productivity
goldilocks (20% bull).

Falsification triggers (if these occur thesis weakens):
- Core PCE below 2.5% for 3 consecutive months
- GDP above 2.5% for 2 consecutive quarters
- HY spreads sustained below 300bp
- Nonfarm productivity above 2% sustained

## Key data flow patterns

**FRED index pre-warming**: `market_data.py` spawns a background thread at import time to pre-fetch `get_index_data()`. If FRED isn't warm when the first `/api/market` request arrives, the market response caches for only 30s so the next request retries rather than serving stale empty data for the full TTL.

**Twelve Data rate limit**: Free tier is 8 calls/min, 800/day. `twelve_data.py` has a per-process rate limiter. Current market fetch uses 3 batches (breadth+EUR/USD, commodities, sectors = 3 TD calls per refresh). Adding new TD calls must account for this budget.

**All data functions return plain dicts** — Flask routes wrap them with `jsonify()`. When one module needs data from another, import and call the function directly. Never make HTTP calls to `localhost` or `127.0.0.1`.

**AI briefing cache**: `ai_briefing.py` writes a 6-hour JSON cache to `briefing_cache.json`. Force-regenerate via `/api/briefing/regenerate`. It imports directly from `regime_engine`, `fred_data`, `market_data`, and `news_feed` — check all four before modifying.

## Architecture rules — NON-NEGOTIABLE
- NEVER make HTTP calls to localhost or 127.0.0.1 from backend code
  Use direct Python function imports instead
- NEVER use yfinance — it was removed due to Yahoo IP blocking
- NEVER use proxy_config.py — it was deleted (commit 05eed96)
- ALL environment variable access goes through config.py,
  not scattered os.getenv() calls
- ALL external API calls must have try/except with specific exception
  types and structured logging — never silent failures
- NEVER use bare except clauses
- Index data (S&P, Dow, Nasdaq, Russell, VIX) comes from FRED only —
  Twelve Data does not support indices on any tier (verified by API test)
- Sector ETFs (XLK, XLF, etc.) come from Twelve Data free tier

## Code style
- Type hints on every new or modified function
- Use Python's logging module — never print()
- Docstring on any function over 50 lines
- Modules over 500 lines should be split
- Cache external API calls:
  - Stock quotes: 60 seconds during market hours, 5 min after-hours
  - Regime calculation: 60 minutes
  - FRED data: 1 hour
  - Fundamentals: 24 hours
  - Historical chart data: 1 hour intraday, 24 hours daily+
  - AI Briefing: 6 hours

## Workflow rules
- For any change touching 2+ files: use Plan Mode (Shift+Tab twice),
  show me the plan BEFORE writing any code
- Show the full diff for every file changed before committing
- One logical change per commit
- Commit messages: imperative mood, concise
  ("Fix X" not "Fixed X", "Add Y" not "Added Y")
- Always push to origin main when work is complete
- Render auto-deploys within 2-3 minutes of every push

## Things to NEVER do
- Never commit API keys, .env files, or credentials of any kind
- Never use bare except clauses
- Never disable error handling to "make it work"
- Never modify ai_briefing.py without checking how regime engine calls it
- Never add new external API dependencies without asking first
- Never activate the Portfolio tab (compliance reasons — needs attorney
  review before use)
- Never hardcode ticker symbols — they should be in config or constants
- Never remove caching to "simplify" code — caching is load management

## Sensitive context (compliance)
Ryan works at Stifel Financial. This dashboard uses only public,
free data sources. No client data, no Stifel systems, no licensed
firm data is involved. The Portfolio tab is intentionally disabled
pending employment attorney review of IP agreement.
Do not suggest integrating Salesforce, Stifel systems, or any
client-identifiable data.

## Teaching mode
When making non-trivial changes, briefly explain:
- What you changed and why
- What concept or pattern this is an example of
- What Ryan should understand about his own codebase from this

Keep it to 1-2 sentences per change. Goal: Ryan should understand
his dashboard more deeply after every session, not just have
more working code.

## Known bugs and open work (as of June 2026)
- Regime engine `name 'label' is not defined` — NameError introduced
  by dead code cleanup commit, needs fix in regime_engine.py
- Sector rotation may need batching fix for 8/min rate limit
- Temporary PROXY DEBUG logging may still exist in some files
  (should have been removed but verify)
- Graham/Buffett scorecards currently show limited data due to
  Twelve Data free tier not including fundamentals
  Future plan: source fundamentals from SEC EDGAR (free)

## What's working well (don't break these)
- AI Morning Briefing (Anthropic API + FRED data feed)
- FRED macro, yields, credit data
- News RSS aggregation
- Regime classification (once label bug is fixed)
- Watchlist quotes via Twelve Data
- Company analysis cards (basic version)
- Charts via Twelve Data

## Ryan's context (helps you calibrate)
- Financial advisor, 2 years in, building book at Stifel
- Pursuing CFP certification (October 20 exam deadline)
- Not a software engineer — explain reasoning in plain English
- Dashboard is used in client meetings and daily morning prep
- Reliability matters more than features
- Monthly budget for infrastructure: ~$30/mo total
- Prefers to review diffs and plans before Claude commits anything
