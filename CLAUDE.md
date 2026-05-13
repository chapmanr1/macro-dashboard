# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Personal Bloomberg-style macro intelligence terminal for a financial advisor at Stifel. Used daily for client preparation and market analysis. Single-user production system — deployed on Render, auto-deploys on push to `main`.

## Running the app

```bash
# Local development
python main.py

# Production (Render uses this)
gunicorn main.py:app
```

Port defaults to 5000 (`PORT` env var overrides). No tests or linters configured.

## Environment variables

All env var access goes through `config.py` — do not use `os.getenv` directly in other modules.

| Variable | Required | Purpose |
|---|---|---|
| `FRED_API_KEY` | Yes | All macro/yield/credit data via FRED API |
| `ANTHROPIC_API_KEY` | Yes | AI morning briefing via Claude |
| `PROXY_HOST`, `PROXY_PORT`, `PROXY_USER`, `PROXY_PASS` | No | Webshare proxy for yfinance (required on Render — Yahoo blocks cloud IPs) |

Never commit API keys, `.env` files, or anything under `/secrets`.

## Architecture

Single-file Flask app (`main.py`) that thin-wraps standalone data modules. Each module owns its own in-memory cache (TTL-based, no Redis). Frontend is vanilla HTML/CSS/JS in `static/app.js` + `templates/index.html` — no React, no build step.

```
main.py                  Flask routes + startup
├── regime_engine.py     Classifies macro regime from FRED data
├── fred_data.py         FRED API → macro indicators, yields, credit, economy
├── market_data.py       yfinance → equities, futures, sectors, commodities
├── news_feed.py         RSS feeds → parsed headlines
├── research.py          yfinance + FRED + SEC EDGAR → ticker/company analysis
├── ai_briefing.py       Anthropic API → morning briefing (6h file cache)
├── config.py            Regime thresholds, positioning, all env var access
└── proxy_config.py      Webshare proxy session injected into all yfinance calls
```

## Key data flow patterns

**Regime classification** (`regime_engine.py → config.py`): `get_regime()` pulls FRED series, scores them against thresholds from `config.get_thresholds()`, and emits one of 6 regimes: `GOLDILOCKS` (displayed as `STRONG GROWTH`), `REFLATION`, `OVERHEATING`, `STAGFLATION_RISK`, `STAGFLATION`, `RECESSION`. Thresholds auto-recalibrate from FRED percentiles every 30 days and persist to `.thresholds.json`.

**All data functions return plain dicts** — Flask routes wrap them with `jsonify()`. When one module needs data from another, import and call the function directly. Never make HTTP calls to `localhost` or `127.0.0.1`.

**yfinance proxy**: `proxy_config.py` builds a `requests.Session` at import time. Every `yf.Ticker()` call across the codebase must pass `session=proxy_session`. Never bypass this — Yahoo Finance blocks Render's cloud IPs without it.

**AI briefing cache**: `ai_briefing.py` writes a 6-hour JSON cache to `briefing_cache.json`. Force-regenerate via `/api/briefing/regenerate`. Before modifying `ai_briefing.py`, check how it is called — it imports directly from `regime_engine`, `fred_data`, `market_data`, and `news_feed`.

## Cache TTLs

| Data type | TTL |
|---|---|
| Regime (FRED-based) | 60 minutes |
| Market quotes (yfinance) | 60 seconds during market hours |
| FRED macro data | 1 hour |
| AI briefing | 6 hours (file cache) |
| News feed | 2 minutes |

## Architecture rules (non-negotiable)

- **No localhost HTTP calls.** Use direct Python function imports for all internal data access.
- **All env vars through `config.py`.** No `os.getenv` scattered in other modules.
- **All yfinance calls use `session=proxy_session`** from `proxy_config.py`.
- **All external API calls need `try/except`** with specific exception types (never bare `except:`) and `log.*` calls.
- **Never disable error handling to make something work.**

## Code style

- Type hints on every new or modified function.
- Use `logging` module — no `print` statements.
- Functions over 50 lines need a docstring.
- Modules over 500 lines should be split into separate files.

## Workflow rules

- **Before changing 2+ files: show a plan first. Do not write code until approved.**
- **Show the full diff for every file before committing.**
- One logical change per commit.
- Commit messages use imperative mood: "Add X" not "Added X".
- Push to `origin/main` when work is complete.

## Positioning and regime config

`POSITIONING` and `DETAILED_POSITIONING` in `config.py` define asset class stances (OW/N/UW) and sector sub-positions for each regime. Edit thresholds and positioning in `config.py` only — not in `regime_engine.py`.

## Compliance note

Never suggest scraping copyrighted or licensed financial data sources. All data in this dashboard comes from FRED (public), yfinance (public market data), SEC EDGAR (public filings), and RSS feeds.
