# FILE: main.py
# Bloomberg Macro Dashboard — Flask Entry Point

import os
import sys
import signal
import threading
import time
import logging
import traceback
import concurrent.futures
from flask import Flask, jsonify, render_template, request
from datetime import datetime
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

try:
    from ai_briefing import get_briefing, force_regenerate
except ImportError:
    def get_briefing():
        return {"status": "no_api_key", "message": "AI briefing module not loaded"}
    def force_regenerate():
        return {"status": "no_api_key", "message": "AI briefing module not loaded"}

try:
    from regime_engine import get_regime, get_regime_history
except ImportError:
    def get_regime():
        return {"label": "UNAVAILABLE", "confidence_score": 0,
                "indicator_breakdown": [], "key_risks": ["Regime engine not loaded"],
                "asset_class_positioning": [], "timestamp": datetime.utcnow().isoformat()}
    def get_regime_history():
        return []

try:
    from fed_watch import get_fed_watch
except ImportError:
    def get_fed_watch():
        return {"error": "FedWatch module not loaded", "timestamp": datetime.utcnow().isoformat()}

try:
    from fred_data import get_macro, get_yields, get_economy, get_credit, get_economic_calendar, get_macro_history
except ImportError:
    def get_macro():
        return {"series": [], "timestamp": datetime.utcnow().isoformat(), "error": "FRED module not loaded"}
    def get_yields():
        return {"yields": [], "spreads": [], "timestamp": datetime.utcnow().isoformat(), "error": "FRED module not loaded"}
    def get_economy():
        return {"growth": [], "inflation": [], "labor": [], "consumer": [],
                "timestamp": datetime.utcnow().isoformat(), "error": "Economy module not loaded"}
    def get_credit():
        return {"spreads": [], "breakevens": [], "real_yields": [], "falsification_triggers": [],
                "timestamp": datetime.utcnow().isoformat(), "error": "Credit module not loaded"}
    def get_economic_calendar():
        return []
    def get_macro_history(series_id: str, n_obs: int) -> dict:
        return {"label": series_id, "unit": "", "data": [], "error": "FRED module not loaded"}

try:
    from global_data import get_global_indicators
except ImportError:
    def get_global_indicators():
        return {"economies": [], "timestamp": datetime.utcnow().isoformat(), "error": "Global module not loaded"}

try:
    from news_feed import get_news
except ImportError:
    def get_news():
        return {"articles": [], "timestamp": datetime.utcnow().isoformat(), "error": "News module not loaded"}

try:
    from market_data import get_market
except ImportError:
    def get_market():
        return {"indices": [], "futures": [], "sectors": [], "commodities": [], "currencies": [],
                "timestamp": datetime.utcnow().isoformat(), "error": "Market module not loaded"}

try:
    from research import (search_tickers, search_fred, search_edgar,
                          get_ticker_analysis, get_company_analysis,
                          get_watchlist_prices as research_prices,
                          get_chart_data)
except ImportError:
    def search_tickers(q): return []
    def search_fred(q): return []
    def search_edgar(q): return []
    def get_ticker_analysis(s): return {"symbol": s, "error": "Research module not loaded"}
    def get_company_analysis(s): return {"symbol": s, "error": "Research module not loaded"}
    def research_prices(t): return []
    def get_chart_data(s, p="1y"): return {"error": "Research module not loaded", "symbol": s}

app = Flask(__name__)

# ── ROUTES ────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/ping")
def ping():
    return jsonify({"status": "ok", "ts": datetime.utcnow().isoformat()})

@app.route("/api/regime")
def api_regime():
    try:
        return jsonify(get_regime())
    except Exception as e:
        log.error(f"Regime error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e), "label": "ERROR", "timestamp": datetime.utcnow().isoformat()}), 500

@app.route("/api/macro")
def api_macro():
    try:
        return jsonify(get_macro())
    except Exception as e:
        log.error(f"Macro error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e), "series": [], "timestamp": datetime.utcnow().isoformat()}), 500

@app.route("/api/yields")
def api_yields():
    try:
        return jsonify(get_yields())
    except Exception as e:
        log.error(f"Yields error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e), "yields": [], "spreads": [], "timestamp": datetime.utcnow().isoformat()}), 500

@app.route("/api/news")
def api_news():
    try:
        return jsonify(get_news())
    except Exception as e:
        log.error(f"News error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e), "articles": [], "timestamp": datetime.utcnow().isoformat()}), 500

@app.route("/api/market")
def api_market():
    try:
        return jsonify(get_market())
    except Exception as e:
        log.error(f"Market error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e), "indices": [], "timestamp": datetime.utcnow().isoformat()}), 500

@app.route("/api/economy")
def api_economy():
    try:
        return jsonify(get_economy())
    except Exception as e:
        log.error(f"Economy error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e), "growth": [], "timestamp": datetime.utcnow().isoformat()}), 500

@app.route("/api/credit")
def api_credit():
    try:
        return jsonify(get_credit())
    except Exception as e:
        log.error(f"Credit error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e), "spreads": [], "timestamp": datetime.utcnow().isoformat()}), 500

@app.route("/api/calendar")
def api_calendar():
    try:
        return jsonify({"events": get_economic_calendar(), "timestamp": datetime.utcnow().isoformat()})
    except Exception as e:
        log.error(f"Calendar error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e), "events": [], "timestamp": datetime.utcnow().isoformat()}), 500

@app.route("/api/research/search")
def api_research_search():
    try:
        q = request.args.get('q', '').strip()
        if not q:
            return jsonify({"tickers": [], "fred": [], "sec_filings": []})
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            ft = ex.submit(search_tickers, q)
            ff = ex.submit(search_fred, q)
            fe = ex.submit(search_edgar, q)
            tickers = ft.result(timeout=10)
            fred    = ff.result(timeout=5)
            sec     = fe.result(timeout=12)
        return jsonify({"tickers": tickers, "fred": fred, "sec_filings": sec})
    except Exception as e:
        log.error(f"Research search error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e), "tickers": [], "fred": [], "sec_filings": []}), 500

@app.route("/api/research/ticker/<symbol>")
def api_research_ticker(symbol):
    try:
        return jsonify(get_ticker_analysis(symbol))
    except Exception as e:
        log.error(f"Research ticker error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e), "symbol": symbol}), 500

@app.route("/api/research/company/<symbol>")
def api_research_company(symbol):
    try:
        sym = symbol.strip().upper()
        if not sym.replace(".", "").replace("-", "").replace("^", "").isalnum():
            return jsonify({"error": "Invalid symbol"}), 400
        return jsonify(get_company_analysis(sym))
    except Exception as e:
        log.error(f"Research company error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e), "symbol": symbol}), 500

@app.route("/api/research/chart/<symbol>")
def api_research_chart(symbol):
    try:
        sym = symbol.strip().upper()
        if not sym.replace(".", "").replace("-", "").replace("^", "").isalnum():
            return jsonify({"error": "Invalid symbol"}), 400
        period = request.args.get("period", "1y")
        if period not in ("1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max"):
            period = "1y"
        return jsonify(get_chart_data(sym, period))
    except Exception as e:
        log.error(f"Chart error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e), "symbol": symbol}), 500

@app.route("/api/research/prices", methods=["POST"])
def api_research_prices():
    try:
        data = request.get_json() or {}
        tickers = data.get("tickers", [])
        return jsonify({"items": research_prices(tickers)})
    except Exception as e:
        log.error(f"Research prices error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e), "items": []}), 500

@app.route("/api/watchlist")
def api_watchlist():
    try:
        tickers_str = request.args.get('tickers', '')
        if not tickers_str:
            return jsonify({"items": [], "timestamp": datetime.utcnow().isoformat()})
        tickers = [t.strip().upper() for t in tickers_str.split(',') if t.strip()]
        from research import get_watchlist_prices
        items = get_watchlist_prices(tickers)
        return jsonify({"items": items, "timestamp": datetime.utcnow().isoformat()})
    except Exception as e:
        log.error(f"Watchlist error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e), "items": [], "timestamp": datetime.utcnow().isoformat()}), 500

@app.route("/api/briefing")
def api_briefing():
    try:
        return jsonify(get_briefing())
    except Exception as e:
        log.error(f"Briefing error: {e}\n{traceback.format_exc()}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/positioning/detail/<path:asset_class>")
def api_positioning_detail(asset_class):
    try:
        from config import DETAILED_POSITIONING
        regime_data = get_regime()
        regime_label = regime_data.get("label", "")
        regime_detail = DETAILED_POSITIONING.get(regime_label, {})
        asset_detail = regime_detail.get(asset_class)
        if asset_detail is None:
            return jsonify({"error": "Not found", "asset_class": asset_class, "regime": regime_label}), 404
        return jsonify({
            "asset_class": asset_class,
            "regime": regime_label,
            "stance": asset_detail.get("stance"),
            "rationale": asset_detail.get("rationale"),
            "sub_positions": asset_detail.get("sub_positions", []),
        })
    except Exception as e:
        log.error(f"Positioning detail error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/briefing/refresh", methods=["POST"])
def api_briefing_refresh():
    try:
        return jsonify(force_regenerate())
    except Exception as e:
        log.error(f"Briefing refresh error: {e}\n{traceback.format_exc()}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/fedwatch")
def api_fedwatch():
    try:
        return jsonify(get_fed_watch())
    except Exception as e:
        log.error(f"FedWatch error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e), "timestamp": datetime.utcnow().isoformat()}), 500

@app.route("/api/regime/history")
def api_regime_history():
    try:
        return jsonify({"history": get_regime_history(), "timestamp": datetime.utcnow().isoformat()})
    except Exception as e:
        log.error(f"Regime history error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e), "history": [], "timestamp": datetime.utcnow().isoformat()}), 500

@app.route("/api/macro/history")
def api_macro_history():
    series_param = request.args.get("series", "")
    obs_param    = request.args.get("obs", "60")
    try:
        n_obs = max(12, min(300, int(obs_param)))
    except ValueError:
        n_obs = 60
    series_ids = [s.strip().upper() for s in series_param.split(",") if s.strip()][:4]
    if not series_ids:
        return jsonify({"error": "series parameter required"}), 400
    try:
        result = {"series": []}
        for sid in series_ids:
            h = get_macro_history(sid, n_obs)
            result["series"].append({"id": sid, **h})
        return jsonify(result)
    except Exception as e:
        log.error(f"Macro history error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e), "series": []}), 500

@app.route("/api/global")
def api_global():
    try:
        return jsonify(get_global_indicators())
    except Exception as e:
        log.error(f"Global indicators error: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e), "economies": []}), 500

@app.route("/api/health")
def api_health():
    health = {"status": "ok", "timestamp": datetime.utcnow().isoformat(), "modules": {}}
    for name, fn in [("regime", get_regime), ("macro", get_macro), ("yields", get_yields),
                     ("news", get_news), ("market", get_market), ("economy", get_economy),
                     ("credit", get_credit)]:
        try:
            fn()
            health["modules"][name] = "ok"
        except Exception as e:
            health["modules"][name] = str(e)
            health["status"] = "degraded"
    return jsonify(health)

# ── STARTUP CACHE PRE-WARM ────────────────────────────────────
def _prewarm_caches() -> None:
    """
    Fetch all slow FRED caches in parallel at startup so the first user request
    is never cold. Runs as a background daemon thread — does not block startup.
    Regime + macro + yields + credit each make serial FRED calls (~15-40s each);
    running them in parallel cuts total warm time to the slowest single module.
    """
    def _run():
        import concurrent.futures as _cf
        tasks = []
        try:
            from regime_engine import get_regime
            tasks.append(get_regime)
        except ImportError:
            pass
        try:
            from fred_data import get_macro, get_yields, get_credit
            tasks += [get_macro, get_yields, get_credit]
        except ImportError:
            pass
        if not tasks:
            return
        log.info(f"Pre-warming {len(tasks)} FRED caches in parallel...")
        with _cf.ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            futures = {pool.submit(fn): fn.__name__ for fn in tasks}
            for fut in _cf.as_completed(futures, timeout=110):
                name = futures[fut]
                try:
                    fut.result()
                    log.info(f"Pre-warm done: {name}")
                except Exception as e:
                    log.warning(f"Pre-warm failed [{name}]: {e}")
        log.info("Startup cache pre-warm complete.")

    threading.Thread(target=_run, daemon=True).start()

_prewarm_caches()

# ── INTERNAL KEEP-ALIVE ───────────────────────────────────────
def internal_keepalive():
    import urllib.request
    replit_url = os.environ.get("REPLIT_URL", "")
    if not replit_url:
        log.info("REPLIT_URL not set — internal keep-alive disabled.")
        return
    while True:
        try:
            urllib.request.urlopen(f"{replit_url}/ping", timeout=10)
            log.info("Keep-alive ping sent.")
        except Exception as e:
            log.warning(f"Keep-alive failed: {e}")
        time.sleep(240)

# ── INTERNAL HEALTH MONITOR ───────────────────────────────────
def health_monitor():
    while True:
        try:
            time.sleep(60)
            log.info(f"Health check — app running, pid={os.getpid()}")
        except Exception as e:
            log.error(f"Health monitor error: {e}")

# ── SIGNAL HANDLERS ───────────────────────────────────────────
def _shutdown(signum, frame):
    log.info(f"Received signal {signum} — shutting down gracefully.")
    sys.exit(0)

signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT,  _shutdown)

# ── ENTRY POINT (dev only — production uses gunicorn) ─────────
if __name__ == "__main__":
    threading.Thread(target=internal_keepalive, daemon=True).start()
    threading.Thread(target=health_monitor,     daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    log.info(f"Starting Macro Dashboard on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)
