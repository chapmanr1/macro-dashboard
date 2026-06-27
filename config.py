# FILE: config.py
# Bloomberg Macro Dashboard — Central Configuration
# Thresholds auto-calibrate monthly from live FRED data.
# Edit floors/ceilings or positioning here; never touch core logic.

import os
import json
import time
import logging
from datetime import datetime

log = logging.getLogger(__name__)

# ── API KEYS ──────────────────────────────────────────────────
TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY", "")

# ── CALIBRATION SETTINGS ──────────────────────────────────────
_CALIB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".thresholds.json")
_CALIB_TTL  = 30 * 24 * 3600   # 30 days in seconds
_mem_cache  = {"data": None, "ts": 0}

# ── DEFAULT THRESHOLDS (fallback if FRED unreachable) ─────────
DEFAULT_THRESHOLDS = {
    "inflation_high":       3.5,
    "inflation_low":        2.0,
    "inflation_very_high":  5.0,
    "growth_strong":        2.5,
    "growth_weak":          1.0,
    "growth_negative":      0.0,
    "unemployment_low":     4.0,
    "unemployment_high":    5.5,
    "fed_funds_high":       4.0,
    "spread_inverted":      0.0,
    "spread_steep":         1.0,
}

# ── PERCENTILE HELPER ─────────────────────────────────────────
def _pct(values, p):
    """Linear-interpolation percentile — no numpy required."""
    s = sorted(v for v in (values or []) if v is not None)
    if not s:
        return None
    idx = (len(s) - 1) * p / 100
    lo, hi = int(idx), min(int(idx) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)

# ── CALIBRATION FUNCTION ──────────────────────────────────────
def calibrate(fred_api_key):
    """
    Recalibrate regime thresholds using trailing FRED data.
    Uses rolling windows:
      - Inflation / labor / policy: last 36 months
      - GDP growth: last 16 quarters (4 years)
      - Yield curve: last 36 monthly averages

    Thresholds are set at percentile boundaries with absolute
    floors/ceilings so they can't drift into nonsensical territory.
    Returns a dict in the same shape as DEFAULT_THRESHOLDS.
    """
    import requests

    FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

    def _fetch(series_id, limit, frequency=None):
        params = {
            "series_id":  series_id,
            "api_key":    fred_api_key,
            "file_type":  "json",
            "sort_order": "desc",
            "limit":      limit,
            "observation_start": (
                datetime.utcnow().replace(year=datetime.utcnow().year - 6)
            ).strftime("%Y-%m-%d"),
        }
        if frequency:
            params["frequency"] = frequency
        resp = requests.get(FRED_BASE, params=params, timeout=15)
        resp.raise_for_status()
        obs = resp.json().get("observations", [])
        return [float(o["value"]) for o in obs if o.get("value") not in (".", "", None)]

    def _yoy_pct_series(raw_monthly, n=36):
        """Compute n YoY % change values from descending monthly raw index."""
        result = []
        for i in range(min(n, len(raw_monthly) - 12)):
            ya = raw_monthly[i + 12]
            if ya != 0:
                result.append((raw_monthly[i] - ya) / abs(ya) * 100)
        return result

    def _qoq_ann_series(raw_quarterly, n=16):
        """Compute n QoQ annualised growth values from descending quarterly raw."""
        result = []
        for i in range(min(n, len(raw_quarterly) - 1)):
            p = raw_quarterly[i + 1]
            if p != 0:
                result.append(((raw_quarterly[i] / p) ** 4 - 1) * 100)
        return result

    # Fetch raw series
    cpi_raw  = _fetch("CPIAUCSL", 50)           # monthly CPI index level
    gdp_raw  = _fetch("GDPC1",    20)           # quarterly real GDP level
    unemp    = _fetch("UNRATE",   40)           # monthly unemployment %
    ff       = _fetch("FEDFUNDS", 40)           # monthly fed funds %
    spread   = _fetch("T10Y2Y",   40, "m")      # monthly 10Y-2Y %

    # Derived series
    cpi_yoy  = _yoy_pct_series(cpi_raw, 36)
    gdp_qoq  = _qoq_ann_series(gdp_raw, 16)

    def clamp(val, floor, ceiling=None):
        if val is None:
            return floor
        v = max(floor, round(val, 2))
        return min(v, ceiling) if ceiling is not None else v

    t = {
        # Inflation thresholds — shift with the current cycle's CPI distribution
        "inflation_low":       clamp(_pct(cpi_yoy, 25), 1.5, 3.0),
        "inflation_high":      clamp(_pct(cpi_yoy, 70), 2.8, 6.0),
        "inflation_very_high": clamp(_pct(cpi_yoy, 88), 4.0, 9.0),

        # Growth thresholds — shift with current cycle's GDP distribution
        "growth_strong":       clamp(_pct(gdp_qoq, 65), 2.0, 4.0),
        "growth_weak":         clamp(_pct(gdp_qoq, 30), 0.5, 2.0),
        "growth_negative":     0.0,   # contraction boundary is always zero

        # Labor thresholds — shift with current cycle's employment conditions
        "unemployment_low":    clamp(_pct(unemp, 25),  3.5, 5.0),
        "unemployment_high":   clamp(_pct(unemp, 75),  5.0, 8.0),

        # Policy threshold — what counts as "restrictive" in this cycle
        "fed_funds_high":      clamp(_pct(ff, 60),     2.5, 7.0),

        # Yield curve — inverted is always < 0; steep is cycle-relative
        "spread_inverted":     0.0,
        "spread_steep":        clamp(_pct(spread, 65), 0.5, 2.0),
    }

    log.info(f"Thresholds calibrated: {t}")
    return t


# ── GET THRESHOLDS (cached, auto-recalibrates monthly) ────────
def get_thresholds():
    """
    Returns calibrated regime thresholds.
    Priority: in-memory cache → disk cache → FRED recalibration → defaults.
    Recalibration happens at most once every 30 days.
    """
    now = time.time()

    # 1. Fast in-memory path
    if _mem_cache["data"] and (now - _mem_cache["ts"]) < _CALIB_TTL:
        return _mem_cache["data"]

    # 2. Disk cache
    if os.path.exists(_CALIB_FILE):
        try:
            with open(_CALIB_FILE) as f:
                saved = json.load(f)
            if (now - saved.get("ts", 0)) < _CALIB_TTL:
                _mem_cache["data"] = saved["thresholds"]
                _mem_cache["ts"]   = saved["ts"]
                log.info("Thresholds: loaded from disk cache.")
                return _mem_cache["data"]
        except Exception as e:
            log.warning(f"Threshold disk cache unreadable: {e}")

    # 3. Recalibrate from FRED
    fred_key = os.environ.get("FRED_API_KEY", "")
    if fred_key:
        try:
            new_t = calibrate(fred_key)
            ts    = now
            _mem_cache["data"] = new_t
            _mem_cache["ts"]   = ts
            try:
                with open(_CALIB_FILE, "w") as f:
                    json.dump({
                        "thresholds":    new_t,
                        "ts":            ts,
                        "calibrated_at": datetime.utcnow().isoformat(),
                    }, f, indent=2)
            except Exception as e:
                log.warning(f"Could not save threshold cache to disk: {e}")
            return new_t
        except Exception as e:
            log.warning(f"Threshold calibration failed, using defaults: {e}")

    # 4. Hardcoded defaults
    return dict(DEFAULT_THRESHOLDS)


def get_calibration_meta():
    """Return metadata about the last calibration (for /api/health)."""
    if os.path.exists(_CALIB_FILE):
        try:
            with open(_CALIB_FILE) as f:
                saved = json.load(f)
            age_days = (time.time() - saved.get("ts", 0)) / 86400
            return {
                "calibrated_at": saved.get("calibrated_at"),
                "age_days":      round(age_days, 1),
                "stale":         age_days > 30,
                "thresholds":    saved.get("thresholds"),
            }
        except Exception:
            pass
    return {"calibrated_at": None, "age_days": None, "stale": True, "thresholds": None}


# ── REGIME LABELS (only GOLDILOCKS gets a plain-English rename) ─
REGIME_LABELS = {
    "GOLDILOCKS": "STRONG GROWTH",
}

REGIME_DESCRIPTIONS = {
    "STRONG GROWTH":    "Economic expansion is broad-based with inflation trending toward target. Risk assets typically outperform as earnings growth outpaces discount rate headwinds.",
    "REFLATION":        "Growth is recovering from a trough with commodity prices leading the upturn. This is the regime where cyclicals and inflation-linked assets historically outperform most aggressively.",
    "OVERHEATING":      "Above-trend growth is generating inflationary pressure that will eventually force a policy tightening response. Duration and rate-sensitive assets are most vulnerable.",
    "STAGFLATION_RISK": "Growth momentum is decelerating while inflation remains above Fed target. The policy constraint is binding — the Fed cannot cut without risking re-acceleration, and cannot tighten further without pushing growth negative.",
    "STAGFLATION":      "Growth is stagnant or contracting while inflation remains elevated. This is the most difficult regime for traditional 60/40 portfolios as both stocks and bonds face simultaneous headwinds.",
    "RECESSION":        "Economic output is contracting as demand destruction leads the cycle. Defensive positioning, quality credit, and duration typically outperform risk assets.",
}

# ── ASSET CLASS POSITIONING BY REGIME ─────────────────────────
POSITIONING = {
    "STRONG GROWTH": [
        {"asset_class": "US EQUITIES",      "stance": "OW"},
        {"asset_class": "CORP CREDIT",      "stance": "OW"},
        {"asset_class": "INT'L EQUITIES",   "stance": "OW"},
        {"asset_class": "COMMODITIES",      "stance": "N"},
        {"asset_class": "GOLD",             "stance": "N"},
        {"asset_class": "LONG TREASURIES",  "stance": "N"},
        {"asset_class": "CASH",             "stance": "UW"},
        {"asset_class": "TIPS",             "stance": "UW"},
    ],
    "REFLATION": [
        {"asset_class": "COMMODITIES",      "stance": "OW"},
        {"asset_class": "ENERGY",           "stance": "OW"},
        {"asset_class": "TIPS",             "stance": "OW"},
        {"asset_class": "INT'L EQUITIES",   "stance": "OW"},
        {"asset_class": "US EQUITIES",      "stance": "N"},
        {"asset_class": "LONG TREASURIES",  "stance": "UW"},
        {"asset_class": "CORP CREDIT",      "stance": "N"},
        {"asset_class": "CASH",             "stance": "N"},
    ],
    "OVERHEATING": [
        {"asset_class": "COMMODITIES",      "stance": "OW"},
        {"asset_class": "TIPS",             "stance": "OW"},
        {"asset_class": "SHORT TREASURIES", "stance": "OW"},
        {"asset_class": "ENERGY",           "stance": "OW"},
        {"asset_class": "US EQUITIES",      "stance": "UW"},
        {"asset_class": "LONG TREASURIES",  "stance": "UW"},
        {"asset_class": "CORP CREDIT",      "stance": "UW"},
        {"asset_class": "CASH",             "stance": "N"},
    ],
    "STAGFLATION": [
        {"asset_class": "GOLD",             "stance": "OW"},
        {"asset_class": "COMMODITIES",      "stance": "OW"},
        {"asset_class": "TIPS",             "stance": "OW"},
        {"asset_class": "CASH",             "stance": "OW"},
        {"asset_class": "ENERGY",           "stance": "OW"},
        {"asset_class": "US EQUITIES",      "stance": "UW"},
        {"asset_class": "LONG TREASURIES",  "stance": "UW"},
        {"asset_class": "CORP CREDIT",      "stance": "UW"},
    ],
    "STAGFLATION_RISK": [
        {"asset_class": "GOLD",             "stance": "OW"},
        {"asset_class": "TIPS",             "stance": "OW"},
        {"asset_class": "COMMODITIES",      "stance": "OW"},
        {"asset_class": "CASH",             "stance": "OW"},
        {"asset_class": "US EQUITIES",      "stance": "N"},
        {"asset_class": "LONG TREASURIES",  "stance": "UW"},
        {"asset_class": "CORP CREDIT",      "stance": "N"},
        {"asset_class": "ENERGY",           "stance": "OW"},
    ],
    "RECESSION": [
        {"asset_class": "LONG TREASURIES",  "stance": "OW"},
        {"asset_class": "GOLD",             "stance": "OW"},
        {"asset_class": "CASH",             "stance": "OW"},
        {"asset_class": "US EQUITIES",      "stance": "UW"},
        {"asset_class": "CORP CREDIT",      "stance": "UW"},
        {"asset_class": "COMMODITIES",      "stance": "UW"},
        {"asset_class": "ENERGY",           "stance": "UW"},
        {"asset_class": "TIPS",             "stance": "N"},
    ],
}

# ── VIX REGIME LEVELS ─────────────────────────────────────────
VIX_LEVELS = [
    # Thresholds calibrated to VIXY (VIX short-term futures ETF, ~0.75× spot VIX)
    {"max": 9,    "label": "COMPLACENT",  "color": "green",
     "description": "Markets pricing near-zero fear — historically precedes volatility spikes."},
    {"max": 13,   "label": "CALM",        "color": "green",
     "description": "Low volatility with bullish sentiment — risk assets broadly well-bid."},
    {"max": 17,   "label": "CAUTIOUS",    "color": "amber",
     "description": "Elevated uncertainty — investors hedging against potential downside."},
    {"max": 21,   "label": "FEARFUL",     "color": "amber",
     "description": "Significant market stress — de-risking underway across portfolios."},
    {"max": 26,   "label": "PANIC",       "color": "red",
     "description": "Acute fear selling — forced liquidations and margin calls likely."},
    {"max": 9999, "label": "EXTREME FEAR","color": "red",
     "description": "Crisis-level volatility — generational buying opportunities historically emerge here."},
]
VIX_DISPLAY_MAX = 38  # ~50 VIX equivalent via VIXY 0.75× ratio

# ── CREDIT SPREAD THRESHOLDS ──────────────────────────────────
CREDIT_THRESHOLDS = {
    "hy_tight":  300, "hy_normal": 450, "hy_wide":  600, "hy_crisis":  900,
    "ig_tight":   80, "ig_normal": 130, "ig_wide":  200, "ig_crisis":  300,
}

# ── DOLLAR INDEX THRESHOLDS ───────────────────────────────────
DOLLAR_THRESHOLDS = {
    # Calibrated to FRED DTWEXBGS (Trade Weighted US Dollar Index, Jan 2006 = 100)
    # Typical range: ~108 (weak) to ~130 (very strong)
    # ~ICE DXY 108+ → DTWEXBGS 127+, DXY 104 → 122, DXY 100 → 117, DXY 96 → 113, DXY 92 → 108
    "very_strong": 127, "strong": 122, "neutral_hi": 117,
    "neutral_lo":  113, "weak":   108,
}

# ── RECESSION PROBABILITY SCORING ────────────────────────────
RECESSION_WEIGHTS = {
    "yield_curve": 25, "gdp": 20, "credit_spreads": 20,
    "unemployment": 20, "ism": 15,
}

RECESSION_SIGNALS = [
    {"max": 20,  "label": "LOW",      "color": "green"},
    {"max": 40,  "label": "MODERATE", "color": "green"},
    {"max": 60,  "label": "ELEVATED", "color": "amber"},
    {"max": 80,  "label": "HIGH",     "color": "red"},
    {"max": 100, "label": "CRITICAL", "color": "red"},
]

# ── FALSIFICATION TRIGGERS ────────────────────────────────────
FALSIFICATION_TRIGGERS = [
    {
        "id": "core_pce", "label": "CORE PCE < 2.5%",
        "full_label": "CORE PCE BELOW 2.5% FOR 3 CONSECUTIVE MONTHS",
        "description": "Fed inflation target sustainably met — eliminates the supply-side constraint and restores full policy flexibility.",
        "threshold": 2.5, "direction": "below", "unit": "%",
        "fred_series": "PCEPILFE", "calc": "yoy", "sustained": 3,
    },
    {
        "id": "gdp_growth", "label": "GDP > 2.5% FOR 2Q",
        "full_label": "REAL GDP ABOVE 2.5% ANNUALIZED FOR 2 QUARTERS",
        "description": "Sustained above-trend growth invalidates the demand destruction thesis and supports durable risk asset performance.",
        "threshold": 2.5, "direction": "above", "unit": "%",
        "fred_series": "GDPC1", "calc": "qoq", "sustained": 2,
    },
    {
        "id": "hy_spreads", "label": "HY SPREADS < 300bp",
        "full_label": "HIGH YIELD OAS BELOW 300 BASIS POINTS",
        "description": "Tight credit spreads signal strong corporate balance sheets and low default risk — inconsistent with WARNING/CAUTION stress.",
        "threshold": 300, "direction": "below", "unit": "bp",
        "fred_series": "BAMLH0A0HYM2", "calc": "latest_bp", "sustained": 1,
    },
    {
        "id": "productivity", "label": "PRODUCTIVITY > 2%",
        "full_label": "NONFARM BUSINESS PRODUCTIVITY ABOVE 2% YOY",
        "description": "Above-trend productivity growth resolves the WARNING tension by expanding supply-side capacity without adding inflation.",
        "threshold": 2.0, "direction": "above", "unit": "%",
        "fred_series": "OPHNFB", "calc": "yoy", "sustained": 1,
    },
    {
        "id": "bdc_nonaccruals", "label": "BDC NON-ACCRUALS < 2%",
        "full_label": "BDC PORTFOLIO NON-ACCRUAL RATE BELOW 2%",
        "description": "Low non-accrual rates across major BDC portfolios (ARCC, BXSL, FSK) signal private credit health and absence of systemic stress.",
        "threshold": 2.0, "direction": "below", "unit": "%",
        "fred_series": None, "calc": "manual", "sustained": 1,
    },
]

# ── DETAILED POSITIONING BY REGIME ───────────────────────────
# Keys must match display labels used in POSITIONING above.
# Asset class keys must match asset_class values in POSITIONING exactly.
def _sp(sector, stance, etf, rationale, **kw):
    d = {"sector": sector, "stance": stance, "etf": etf, "rationale": rationale}
    d.update(kw)
    return d

DETAILED_POSITIONING = {

    # ── STAGFLATION RISK ──────────────────────────────────────
    "STAGFLATION_RISK": {
        "US EQUITIES": {
            "stance": "N",
            "rationale": "Mixed signals — favour value and inflation-linked cash flows over growth",
            "sub_positions": [
                _sp("Energy",                "OW", "XLE",  "Inflation hedge with strong free cash flow"),
                _sp("Materials",             "OW", "XLB",  "Commodity-linked revenues benefit from inflation"),
                _sp("Utilities",             "OW", "XLU",  "Defensive with regulated, inflation-linked revenues"),
                _sp("Consumer Staples",      "OW", "XLP",  "Pricing power insulates margins in inflationary env"),
                _sp("Healthcare",            "N",  "XLV",  "Defensive but rate-sensitive; neutral weighting"),
                _sp("Financials",            "N",  "XLF",  "Benefits from steeper curve, exposed to credit risk"),
                _sp("Industrials",           "UW", "XLI",  "Cyclical earnings face stagflationary headwinds"),
                _sp("Technology",            "UW", "XLK",  "Long-duration growth multiples vulnerable to rates"),
                _sp("Consumer Discretionary","UW", "XLY",  "Squeezed between high prices and weakening demand"),
                _sp("Real Estate",           "UW", "XLRE", "Rate sensitivity dominates; avoid long duration"),
                _sp("Communications",        "UW", "XLC",  "Mix of growth and cyclical headwinds"),
            ],
        },
        "LONG TREASURIES": {
            "stance": "UW",
            "rationale": "Duration risk significant with persistent inflation; favour front end",
            "sub_positions": [
                _sp("30-Year Treasury",    "UW", "TLT",  "Highest duration; avoid in stagflation",    duration="17.4 yr"),
                _sp("10-Year Treasury",    "UW", "IEF",  "Significant rate risk; underweight",         duration="7.5 yr"),
                _sp("5-Year Treasury",     "N",  "IEI",  "Moderate duration; neutral acceptable",      duration="4.4 yr"),
                _sp("2-Year Treasury",     "OW", "SHY",  "Short duration; limits rate risk materially",duration="1.8 yr"),
                _sp("T-Bills (1-3 mo)",    "OW", "BIL",  "Cash equivalent; captures current yield",   duration="0.1 yr"),
            ],
        },
        "TIPS": {
            "stance": "OW",
            "rationale": "Direct inflation linkage critical in stagflation; favour short duration",
            "sub_positions": [
                _sp("Short TIPS (0-5 yr)",  "OW", "VTIP", "Inflation protection with low rate risk",     duration="2.5 yr"),
                _sp("Intermediate TIPS",    "OW", "TIP",  "Core TIPS exposure; balanced duration",       duration="7.0 yr"),
                _sp("Long TIPS (15+ yr)",   "N",  "LTPZ", "Inflation hedge but rate risk elevated",      duration="20.0 yr"),
            ],
        },
        "GOLD": {
            "stance": "OW",
            "rationale": "Store of value and inflation hedge; performs well in stagflation",
            "sub_positions": [
                _sp("Physical Gold ETF",  "OW", "GLD",  "Pure gold exposure; most liquid instrument"),
                _sp("Gold (low cost)",    "OW", "IAU",  "Same exposure as GLD; lower expense ratio"),
                _sp("Gold Miners",        "OW", "GDX",  "Leveraged exposure to gold price; higher beta"),
                _sp("Junior Gold Miners", "N",  "GDXJ", "Higher beta still; size down for risk mgmt"),
                _sp("Silver",             "N",  "SLV",  "Industrial demand adds unwanted cyclicality"),
            ],
        },
        "COMMODITIES": {
            "stance": "OW",
            "rationale": "Real assets benefit directly from inflation; diversify broadly",
            "sub_positions": [
                _sp("Broad Commodities",  "OW", "GSG",  "Diversified commodity basket; core holding"),
                _sp("Crude Oil",          "OW", "USO",  "Supply constraint and energy demand support"),
                _sp("Agriculture",        "OW", "DBA",  "Food inflation persistent and supply-constrained"),
                _sp("Copper",             "OW", "CPER", "Electrification thesis + industrial inflation"),
                _sp("Natural Gas",        "N",  "UNG",  "Volatile; weather and storage dependent"),
                _sp("Industrial Metals",  "N",  "DBB",  "China demand uncertainty limits conviction"),
            ],
        },
        "ENERGY": {
            "stance": "OW",
            "rationale": "Inflation hedge with strong cash flows; structural supply constraint",
            "sub_positions": [
                _sp("Energy Sector",        "OW", "XLE",  "Diversified energy; core OW position"),
                _sp("Oil & Gas E&P",        "OW", "XOP",  "Direct crude price exposure; higher beta"),
                _sp("Pipelines / MLPs",     "OW", "AMLP", "High income stream; inflation-linked tariffs"),
                _sp("Uranium / Nuclear",    "OW", "URA",  "Long-term clean energy + supply constraint"),
                _sp("Energy Equipment",     "N",  "XES",  "Cyclical; dependent on E&P capex decisions"),
            ],
        },
        "CASH": {
            "stance": "OW",
            "rationale": "Optionality high; real yield positive at front end; wait for clarity",
            "sub_positions": [
                _sp("T-Bills (1-3 mo)", "OW", "BIL",   "Highest quality; captures front-end yield",  yld="5.3%"),
                _sp("Money Market",     "OW", "VMFXX", "Daily liquidity; near-identical yield",       yld="5.3%"),
                _sp("Ultra Short Bond", "N",  "GSY",   "Marginal yield pickup; slight duration risk", yld="5.4%"),
                _sp("Short Treasury",   "N",  "SHV",   "Treasury credit quality; very short dur",     yld="5.2%"),
            ],
        },
        "CORP CREDIT": {
            "stance": "N",
            "rationale": "Spreads barely compensate; favour short IG, avoid HY and leveraged loans",
            "sub_positions": [
                _sp("IG Corporate",      "N",  "LQD",  "Spreads tight but income supports; neutral", duration="8.4 yr"),
                _sp("Short IG Corp",     "OW", "VCSH", "Income with limited duration exposure",       duration="2.7 yr"),
                _sp("High Yield",        "UW", "HYG",  "Spreads not compensating for default risk",   duration="3.5 yr"),
                _sp("Bank Loans",        "UW", "BKLN", "Floating rate but underlying credit declining",duration="0.1 yr"),
                _sp("EM Debt (USD)",     "UW", "EMB",  "Dollar strength + EM stress headwinds",       duration="7.5 yr"),
            ],
        },
        "INT'L EQUITIES": {
            "stance": "N",
            "rationale": "Region-dependent; commodity exporters favoured; dollar headwinds for EM",
            "sub_positions": [
                _sp("Developed Markets", "N",  "VEA",  "Europe/Japan face similar stagflationary pressures"),
                _sp("India",             "OW", "INDA", "Structural growth; domestic demand resilient"),
                _sp("Brazil / LatAm",    "OW", "ILF",  "Commodity exporter benefits from inflation"),
                _sp("Emerging Markets",  "UW", "VWO",  "Dollar strength is material headwind for EM"),
                _sp("China",             "UW", "MCHI", "Property and credit overhang unresolved"),
                _sp("Japan",             "N",  "EWJ",  "BoJ policy uncertainty; carry unwind risk"),
            ],
        },
    },

    # ── STAGFLATION (FULL) ────────────────────────────────────
    "STAGFLATION": {
        "US EQUITIES": {
            "stance": "UW",
            "rationale": "Avoid equities broadly; only inflation-linked cash flows defensible",
            "sub_positions": [
                _sp("Energy",                "OW", "XLE",  "Real asset with pricing power; best equity refuge"),
                _sp("Utilities",             "OW", "XLU",  "Regulated revenues; defensive income"),
                _sp("Consumer Staples",      "OW", "XLP",  "Non-discretionary demand; pricing power"),
                _sp("Materials",             "N",  "XLB",  "Commodity link helps but demand risk rising"),
                _sp("Healthcare",            "N",  "XLV",  "Defensive; rate sensitivity limits upside"),
                _sp("Financials",            "UW", "XLF",  "Credit losses accelerating in stagflation"),
                _sp("Industrials",           "UW", "XLI",  "Cyclical earnings under severe pressure"),
                _sp("Technology",            "UW", "XLK",  "Long-duration multiples collapse in stagflation"),
                _sp("Consumer Discretionary","UW", "XLY",  "Consumer demand destruction underway"),
                _sp("Real Estate",           "UW", "XLRE", "Rate and credit risk combine negatively"),
                _sp("Communications",        "UW", "XLC",  "Ad revenue and growth multiples both hit"),
            ],
        },
        "LONG TREASURIES": {
            "stance": "UW",
            "rationale": "Duration risk at maximum with stagflationary inflation unanchored",
            "sub_positions": [
                _sp("30-Year Treasury",  "UW", "TLT",  "Avoid; maximum duration in worst rate env",  duration="17.4 yr"),
                _sp("10-Year Treasury",  "UW", "IEF",  "Significant rate risk; reduce to zero",       duration="7.5 yr"),
                _sp("5-Year Treasury",   "UW", "IEI",  "Even moderate duration risky here",           duration="4.4 yr"),
                _sp("2-Year Treasury",   "OW", "SHY",  "Front end only; captures high policy rate",   duration="1.8 yr"),
                _sp("T-Bills (1-3 mo)",  "OW", "BIL",  "Safest instrument; pure cash equivalent",    duration="0.1 yr"),
            ],
        },
        "TIPS": {
            "stance": "OW",
            "rationale": "Critical core holding; inflation breakevens still underpricing risk",
            "sub_positions": [
                _sp("Short TIPS (0-5 yr)", "OW", "VTIP", "Best risk-adjusted TIPS; low duration",  duration="2.5 yr"),
                _sp("Intermediate TIPS",   "OW", "TIP",  "Core inflation hedge; accept duration",   duration="7.0 yr"),
                _sp("Long TIPS (15+ yr)",  "N",  "LTPZ", "Inflation hedge offset by duration risk", duration="20.0 yr"),
            ],
        },
        "GOLD": {
            "stance": "OW",
            "rationale": "Primary safe-haven and inflation hedge in true stagflation; maximum allocation",
            "sub_positions": [
                _sp("Physical Gold ETF",  "OW", "GLD",  "Core position; maximum quality and liquidity"),
                _sp("Gold (low cost)",    "OW", "IAU",  "Identical exposure; prefer over GLD on cost"),
                _sp("Gold Miners",        "OW", "GDX",  "Leveraged gold with operational risk"),
                _sp("Junior Gold Miners", "N",  "GDXJ", "Too volatile for crisis positioning"),
                _sp("Silver",             "UW", "SLV",  "Industrial exposure hurts in stagflation"),
            ],
        },
        "COMMODITIES": {
            "stance": "OW",
            "rationale": "Real assets are one of few genuine stores of value in stagflation",
            "sub_positions": [
                _sp("Broad Commodities", "OW", "GSG",  "Diversified real asset exposure; core holding"),
                _sp("Agriculture",       "OW", "DBA",  "Food inflation structural; most critical sub-asset"),
                _sp("Crude Oil",         "OW", "USO",  "Supply-constrained; stagflation amplifies pricing"),
                _sp("Copper",            "N",  "CPER", "Electrification structural but cyclical risk elevated"),
                _sp("Natural Gas",       "N",  "UNG",  "Volatility too high; weather risk"),
                _sp("Industrial Metals", "UW", "DBB",  "Demand destruction offsets inflation benefit"),
            ],
        },
        "ENERGY": {
            "stance": "OW",
            "rationale": "Best performing sector historically in stagflation; real asset with yield",
            "sub_positions": [
                _sp("Energy Sector",     "OW", "XLE",  "Maximum overweight; top performing sector"),
                _sp("Pipelines / MLPs",  "OW", "AMLP", "Income + inflation linkage; lower volatility"),
                _sp("Oil & Gas E&P",     "OW", "XOP",  "Leveraged oil exposure; higher conviction"),
                _sp("Uranium / Nuclear", "OW", "URA",  "Supply constraint + long-term energy demand"),
                _sp("Energy Equipment",  "N",  "XES",  "Capex cycle dependent; neutral"),
            ],
        },
        "CASH": {
            "stance": "OW",
            "rationale": "Preserve capital; positive real yield at front end; maximum optionality",
            "sub_positions": [
                _sp("T-Bills (1-3 mo)", "OW", "BIL",   "Best quality; maximum preservation",    yld="5.3%"),
                _sp("Money Market",     "OW", "VMFXX", "Liquidity and yield combination",        yld="5.3%"),
                _sp("Ultra Short Bond", "N",  "GSY",   "Marginal yield; accept very slight risk",yld="5.4%"),
                _sp("Short Treasury",   "N",  "SHV",   "Treasury quality; minimal duration",     yld="5.2%"),
            ],
        },
        "CORP CREDIT": {
            "stance": "UW",
            "rationale": "Default cycle accelerating; spreads not compensating; avoid HY entirely",
            "sub_positions": [
                _sp("Short IG Corp",   "N",  "VCSH", "Short duration limits damage; minimal exposure", duration="2.7 yr"),
                _sp("IG Corporate",    "UW", "LQD",  "Duration + spread widening double negative",     duration="8.4 yr"),
                _sp("High Yield",      "UW", "HYG",  "Default risk rising; spreads must widen further", duration="3.5 yr"),
                _sp("Bank Loans",      "UW", "BKLN", "Floating rate but credit quality deteriorating",  duration="0.1 yr"),
                _sp("EM Debt (USD)",   "UW", "EMB",  "Dollar strength + credit stress: double hit",    duration="7.5 yr"),
            ],
        },
        "INT'L EQUITIES": {
            "stance": "UW",
            "rationale": "Global contagion likely; only commodity exporters merit consideration",
            "sub_positions": [
                _sp("Brazil / LatAm",    "N",  "ILF",  "Commodity exports partially offset pressure"),
                _sp("India",             "N",  "INDA", "Domestic-driven; more insulated but not immune"),
                _sp("Developed Markets", "UW", "VEA",  "Europe facing same or worse stagflation"),
                _sp("Japan",             "UW", "EWJ",  "Import inflation + BoJ policy dilemma"),
                _sp("Emerging Markets",  "UW", "VWO",  "Dollar strength destroys EM returns"),
                _sp("China",             "UW", "MCHI", "Property crisis + stagflation is toxic mix"),
            ],
        },
    },

    # ── REFLATION ─────────────────────────────────────────────
    "REFLATION": {
        "US EQUITIES": {
            "stance": "N",
            "rationale": "Early-cycle cyclicals OW; rising rates start to compress growth multiples",
            "sub_positions": [
                _sp("Energy",                "OW", "XLE",  "Commodity demand recovery + inflation linkage"),
                _sp("Materials",             "OW", "XLB",  "Early-cycle demand pickup; commodity leverage"),
                _sp("Financials",            "OW", "XLF",  "Steeper yield curve boosts net interest margins"),
                _sp("Industrials",           "OW", "XLI",  "Early-cycle demand recovery; capex spending"),
                _sp("Consumer Discretionary","N",  "XLY",  "Recovery offset by rising cost of living"),
                _sp("Technology",            "N",  "XLK",  "Rates rising; growth multiples under pressure"),
                _sp("Healthcare",            "N",  "XLV",  "Defensive; underperforms in risk-on recovery"),
                _sp("Consumer Staples",      "UW", "XLP",  "Defensive positioning unnecessary in recovery"),
                _sp("Utilities",             "UW", "XLU",  "Rate-sensitive; dividend yield less attractive"),
                _sp("Real Estate",           "UW", "XLRE", "Rate headwind dominates over cap rate expansion"),
                _sp("Communications",        "N",  "XLC",  "Mixed; value names ok, growth names pressured"),
            ],
        },
        "LONG TREASURIES": {
            "stance": "UW",
            "rationale": "Inflation and growth both rising; yields moving higher; reduce duration",
            "sub_positions": [
                _sp("30-Year Treasury",  "UW", "TLT",  "Maximum duration exposure; avoid",              duration="17.4 yr"),
                _sp("10-Year Treasury",  "UW", "IEF",  "Yield rising; capital loss risk high",           duration="7.5 yr"),
                _sp("5-Year Treasury",   "UW", "IEI",  "Still vulnerable to rate move; underweight",     duration="4.4 yr"),
                _sp("2-Year Treasury",   "N",  "SHY",  "More rate-rise resistant; neutral",              duration="1.8 yr"),
                _sp("T-Bills (1-3 mo)",  "OW", "BIL",  "Wait for better entry on longer bonds",         duration="0.1 yr"),
            ],
        },
        "TIPS": {
            "stance": "OW",
            "rationale": "Inflation picking up from below; real yields attractive; core allocation",
            "sub_positions": [
                _sp("Short TIPS (0-5 yr)", "OW", "VTIP", "Inflation protection; low rate risk",   duration="2.5 yr"),
                _sp("Intermediate TIPS",   "OW", "TIP",  "Core TIPS; inflation rising supports",  duration="7.0 yr"),
                _sp("Long TIPS (15+ yr)",  "N",  "LTPZ", "Inflation tailwind but rate risk real", duration="20.0 yr"),
            ],
        },
        "GOLD": {
            "stance": "N",
            "rationale": "Dollar firming and real yields rising headwind; not the best reflationary trade",
            "sub_positions": [
                _sp("Physical Gold ETF",  "N",  "GLD",  "Neutral; real yield rise is headwind"),
                _sp("Gold (low cost)",    "N",  "IAU",  "Same as GLD; neutral weight"),
                _sp("Gold Miners",        "N",  "GDX",  "Operational leverage doesn't help if gold flat"),
                _sp("Junior Gold Miners", "UW", "GDXJ", "Too speculative without gold directional catalyst"),
                _sp("Silver",             "N",  "SLV",  "Industrial demand recovery partially supportive"),
            ],
        },
        "COMMODITIES": {
            "stance": "OW",
            "rationale": "Demand recovery and supply constraints drive commodity cycle higher",
            "sub_positions": [
                _sp("Broad Commodities", "OW", "GSG",  "Core reflation trade; diversified exposure"),
                _sp("Crude Oil",         "OW", "USO",  "Demand recovery + supply discipline supports oil"),
                _sp("Industrial Metals", "OW", "DBB",  "Manufacturing recovery drives copper/aluminum"),
                _sp("Agriculture",       "N",  "DBA",  "Supply-driven; less tied to demand cycle"),
                _sp("Copper",            "OW", "CPER", "Best single commodity for reflation trade"),
                _sp("Natural Gas",       "N",  "UNG",  "Volatile and weather-driven; selective"),
            ],
        },
        "ENERGY": {
            "stance": "OW",
            "rationale": "Top reflation sector; demand recovery with supply-side discipline",
            "sub_positions": [
                _sp("Energy Sector",     "OW", "XLE",  "Core reflationary sector; top overweight"),
                _sp("Oil & Gas E&P",     "OW", "XOP",  "Leveraged to oil price recovery"),
                _sp("Pipelines / MLPs",  "OW", "AMLP", "Yield + volume recovery combination"),
                _sp("Energy Equipment",  "OW", "XES",  "Capex cycle recovers as oil rises"),
                _sp("Uranium / Nuclear", "N",  "URA",  "Structural but less tied to reflation cycle"),
            ],
        },
        "CASH": {
            "stance": "N",
            "rationale": "Some cash useful as yields rise; opportunity cost moderate",
            "sub_positions": [
                _sp("T-Bills (1-3 mo)", "N",  "BIL",   "Hold for redeployment into rising yield assets",yld="5.3%"),
                _sp("Money Market",     "N",  "VMFXX", "Liquidity buffer; deploy into commodities/equities",yld="5.3%"),
                _sp("Ultra Short Bond", "OW", "GSY",   "Carry while waiting; marginal yield pickup",     yld="5.4%"),
                _sp("Short Treasury",   "N",  "SHV",   "Treasury quality; cash management",              yld="5.2%"),
            ],
        },
        "CORP CREDIT": {
            "stance": "N",
            "rationale": "Spreads compressing in recovery but rising rates offset gains; stay short",
            "sub_positions": [
                _sp("Short IG Corp",   "OW", "VCSH", "Income without duration penalty",                   duration="2.7 yr"),
                _sp("IG Corporate",    "N",  "LQD",  "Spread tightening offset by duration risk",          duration="8.4 yr"),
                _sp("High Yield",      "N",  "HYG",  "Spreads tighten in recovery; short duration only",   duration="3.5 yr"),
                _sp("Bank Loans",      "OW", "BKLN", "Floating rate is valuable as rates rise",            duration="0.1 yr"),
                _sp("EM Debt (USD)",   "N",  "EMB",  "EM recovery; dollar strength limits upside",         duration="7.5 yr"),
            ],
        },
        "INT'L EQUITIES": {
            "stance": "OW",
            "rationale": "Weak dollar and commodity recovery favour non-US markets; EM leads",
            "sub_positions": [
                _sp("Emerging Markets",  "OW", "VWO",  "Commodity link + weaker dollar = strong EM"),
                _sp("Brazil / LatAm",    "OW", "ILF",  "Commodity exporter leads EM recovery"),
                _sp("India",             "OW", "INDA", "Domestic demand recovery; growth leader"),
                _sp("Developed Markets", "N",  "VEA",  "Europe benefits from recovery but structurally weak"),
                _sp("Japan",             "N",  "EWJ",  "Yen weakness partially offsets equity gains"),
                _sp("China",             "UW", "MCHI", "Property overhang limits China recovery speed"),
            ],
        },
    },

    # ── STRONG GROWTH (GOLDILOCKS) ────────────────────────────
    "STRONG GROWTH": {
        "US EQUITIES": {
            "stance": "OW",
            "rationale": "Risk-on regime; favour growth and cyclicals; broad equity participation",
            "sub_positions": [
                _sp("Technology",            "OW", "XLK",  "Low rates support growth multiples; leadership"),
                _sp("Consumer Discretionary","OW", "XLY",  "Strong consumer demand; spending cycle intact"),
                _sp("Industrials",           "OW", "XLI",  "Capex cycle robust; manufacturing recovery"),
                _sp("Financials",            "OW", "XLF",  "Credit demand high; loan growth strong"),
                _sp("Communications",        "OW", "XLC",  "Ad spend and streaming growth both strong"),
                _sp("Materials",             "N",  "XLB",  "Demand solid but inflation not a catalyst"),
                _sp("Energy",                "N",  "XLE",  "Demand good; supply growth limits upside"),
                _sp("Healthcare",            "N",  "XLV",  "Defensive; adequate but not leadership"),
                _sp("Real Estate",           "N",  "XLRE", "Economic growth positive but rate sensitivity"),
                _sp("Consumer Staples",      "UW", "XLP",  "Defensives lag in strong growth environment"),
                _sp("Utilities",             "UW", "XLU",  "Rate sensitivity; underperforms risk-on"),
            ],
        },
        "LONG TREASURIES": {
            "stance": "N",
            "rationale": "Yields stable in Goldilocks; hold for diversification but not for return",
            "sub_positions": [
                _sp("30-Year Treasury",  "UW", "TLT",  "Duration adds risk without expected return",  duration="17.4 yr"),
                _sp("10-Year Treasury",  "N",  "IEF",  "Benchmark rate; hold for diversification",    duration="7.5 yr"),
                _sp("5-Year Treasury",   "N",  "IEI",  "Belly of curve; moderate exposure",           duration="4.4 yr"),
                _sp("2-Year Treasury",   "OW", "SHY",  "Solid yield; low risk in benign environment", duration="1.8 yr"),
                _sp("T-Bills (1-3 mo)",  "N",  "BIL",  "Opportunity cost vs equities is high",       duration="0.1 yr"),
            ],
        },
        "TIPS": {
            "stance": "UW",
            "rationale": "Inflation controlled; TIPS inflation premium not needed; opportunity cost high",
            "sub_positions": [
                _sp("Short TIPS (0-5 yr)", "N",  "VTIP", "Low cost if inflation surprises; small hedge",  duration="2.5 yr"),
                _sp("Intermediate TIPS",   "UW", "TIP",  "Inflation premium not warranted; underweight",  duration="7.0 yr"),
                _sp("Long TIPS (15+ yr)",  "UW", "LTPZ", "Duration + low inflation = poor risk/reward",   duration="20.0 yr"),
            ],
        },
        "GOLD": {
            "stance": "N",
            "rationale": "Real yields rising and dollar stable; gold opportunity cost high vs equities",
            "sub_positions": [
                _sp("Physical Gold ETF",  "N",  "GLD",  "Small strategic position only"),
                _sp("Gold (low cost)",    "N",  "IAU",  "Prefer IAU over GLD on cost if holding"),
                _sp("Gold Miners",        "UW", "GDX",  "Miners underperform when gold is flat to down"),
                _sp("Junior Gold Miners", "UW", "GDXJ", "Avoid; high risk, poor expected return here"),
                _sp("Silver",             "N",  "SLV",  "Industrial demand ok; small position"),
            ],
        },
        "COMMODITIES": {
            "stance": "N",
            "rationale": "Demand solid but inflation controlled limits upside; selective exposure",
            "sub_positions": [
                _sp("Broad Commodities", "N",  "GSG",  "Demand driven; hold selectively"),
                _sp("Copper",            "OW", "CPER", "Growth proxy; industrial demand strongest"),
                _sp("Industrial Metals", "N",  "DBB",  "Manufacturing demand supports; not a top trade"),
                _sp("Crude Oil",         "N",  "USO",  "Demand fine; supply growth limits rally"),
                _sp("Agriculture",       "UW", "DBA",  "No inflation catalyst; food prices contained"),
                _sp("Natural Gas",       "UW", "UNG",  "Storage adequate; volatility not worth it"),
            ],
        },
        "ENERGY": {
            "stance": "N",
            "rationale": "Demand healthy but supply response limits upside; neutral not OW",
            "sub_positions": [
                _sp("Energy Sector",     "N",  "XLE",  "Demand ok; supply growth caps price upside"),
                _sp("Pipelines / MLPs",  "OW", "AMLP", "Stable cash flows + volume growth; income"),
                _sp("Oil & Gas E&P",     "N",  "XOP",  "Oil stable; E&P returns depend on discipline"),
                _sp("Energy Equipment",  "N",  "XES",  "Capex cycle modest; neutral"),
                _sp("Uranium / Nuclear", "OW", "URA",  "Long-term structural regardless of cycle"),
            ],
        },
        "CASH": {
            "stance": "UW",
            "rationale": "Opportunity cost very high vs equities in Goldilocks; deploy capital",
            "sub_positions": [
                _sp("T-Bills (1-3 mo)", "UW", "BIL",   "Yields attractive but equity returns dominate",yld="5.3%"),
                _sp("Money Market",     "UW", "VMFXX", "Keep minimal for tactical redeployment",       yld="5.3%"),
                _sp("Ultra Short Bond", "N",  "GSY",   "Only if equity allocation maxed",              yld="5.4%"),
                _sp("Short Treasury",   "UW", "SHV",   "Underperforms equities significantly",         yld="5.2%"),
            ],
        },
        "CORP CREDIT": {
            "stance": "OW",
            "rationale": "Strong growth drives spread compression; corporate balance sheets healthy",
            "sub_positions": [
                _sp("IG Corporate",    "OW", "LQD",  "Low default risk; carry attractive",              duration="8.4 yr"),
                _sp("Short IG Corp",   "OW", "VCSH", "Best risk-adjusted credit position",              duration="2.7 yr"),
                _sp("High Yield",      "OW", "HYG",  "Spreads compress as defaults decline",            duration="3.5 yr"),
                _sp("Bank Loans",      "OW", "BKLN", "Floating rate + tight spreads = good carry",      duration="0.1 yr"),
                _sp("EM Debt (USD)",   "OW", "EMB",  "EM growth supports credit; dollar stable",        duration="7.5 yr"),
            ],
        },
        "INT'L EQUITIES": {
            "stance": "OW",
            "rationale": "Global growth synchronised; EM growth accelerates; diversify beyond US",
            "sub_positions": [
                _sp("Emerging Markets",  "OW", "VWO",  "Growth premium; dollar stable is supportive"),
                _sp("India",             "OW", "INDA", "Structural growth leader; top EM conviction"),
                _sp("Developed Markets", "OW", "VEA",  "Synchronised global growth lifts all"),
                _sp("Japan",             "OW", "EWJ",  "Earnings recovery; corporate reform catalyst"),
                _sp("China",             "N",  "MCHI", "Recovery potential but structural risks remain"),
                _sp("Brazil / LatAm",    "N",  "ILF",  "Commodity exposure less critical in Goldilocks"),
            ],
        },
    },

    # ── OVERHEATING ───────────────────────────────────────────
    "OVERHEATING": {
        "US EQUITIES": {
            "stance": "UW",
            "rationale": "Margins being squeezed; Fed tightening aggressively; late-cycle positioning",
            "sub_positions": [
                _sp("Energy",                "OW", "XLE",  "Commodity inflation + demand still running hot"),
                _sp("Materials",             "OW", "XLB",  "Pricing power at peak; last innings OW"),
                _sp("Financials",            "N",  "XLF",  "Rate lift helps margins; credit risk building"),
                _sp("Consumer Staples",      "N",  "XLP",  "Pricing power fading; margins at peak"),
                _sp("Utilities",             "N",  "XLU",  "Defensive dividend but rate headwind"),
                _sp("Healthcare",            "N",  "XLV",  "Defensive; neutral as rates rise"),
                _sp("Industrials",           "UW", "XLI",  "Peak capex cycle; margin compression ahead"),
                _sp("Technology",            "UW", "XLK",  "High rates compress growth multiples hard"),
                _sp("Consumer Discretionary","UW", "XLY",  "Consumer being squeezed by rates and prices"),
                _sp("Real Estate",           "UW", "XLRE", "Rate sensitivity is a direct negative"),
                _sp("Communications",        "UW", "XLC",  "Growth multiple compression; ad spend slowing"),
            ],
        },
        "LONG TREASURIES": {
            "stance": "UW",
            "rationale": "Fed still hiking or holding high; duration is the enemy; stay very short",
            "sub_positions": [
                _sp("30-Year Treasury",  "UW", "TLT",  "Worst duration in overheating; avoid entirely", duration="17.4 yr"),
                _sp("10-Year Treasury",  "UW", "IEF",  "Yield still rising; capital loss risk",          duration="7.5 yr"),
                _sp("5-Year Treasury",   "UW", "IEI",  "Moderate duration still hurts",                  duration="4.4 yr"),
                _sp("2-Year Treasury",   "OW", "SHY",  "Front end captures high yield; minimal risk",    duration="1.8 yr"),
                _sp("T-Bills (1-3 mo)",  "OW", "BIL",  "Best risk-adjusted fixed income position",       duration="0.1 yr"),
            ],
        },
        "TIPS": {
            "stance": "OW",
            "rationale": "Inflation above target; TIPS real yield now positive and inflation protection critical",
            "sub_positions": [
                _sp("Short TIPS (0-5 yr)", "OW", "VTIP", "Best trade: high inflation + low duration risk",duration="2.5 yr"),
                _sp("Intermediate TIPS",   "OW", "TIP",  "Accept intermediate duration for inflation hedge",duration="7.0 yr"),
                _sp("Long TIPS (15+ yr)",  "UW", "LTPZ", "Duration risk too high even with inflation hedge",duration="20.0 yr"),
            ],
        },
        "GOLD": {
            "stance": "N",
            "rationale": "Real yields rising (bad for gold) but inflation high (good); net neutral",
            "sub_positions": [
                _sp("Physical Gold ETF",  "N",  "GLD",  "Rising real yields offset inflation tailwind"),
                _sp("Gold (low cost)",    "N",  "IAU",  "Neutral; use as hedge only"),
                _sp("Gold Miners",        "UW", "GDX",  "Operating costs inflating; margins squeezed"),
                _sp("Junior Gold Miners", "UW", "GDXJ", "Avoid; cost inflation kills junior margins"),
                _sp("Silver",             "N",  "SLV",  "Industrial demand still ok at peak cycle"),
            ],
        },
        "COMMODITIES": {
            "stance": "OW",
            "rationale": "Price momentum and demand still running; last innings of commodity supercycle",
            "sub_positions": [
                _sp("Crude Oil",         "OW", "USO",  "Demand still robust; supply remains disciplined"),
                _sp("Broad Commodities", "OW", "GSG",  "Late cycle but still trending; hold core"),
                _sp("Agriculture",       "OW", "DBA",  "Food inflation persistent; supply constrained"),
                _sp("Industrial Metals", "N",  "DBB",  "Peak demand; watch for slowdown signal"),
                _sp("Copper",            "N",  "CPER", "Good structural but cyclical peak risk"),
                _sp("Natural Gas",       "N",  "UNG",  "Volatile; hold with tight stop"),
            ],
        },
        "ENERGY": {
            "stance": "OW",
            "rationale": "Peak demand with supply constraints; energy is the last cyclical standing",
            "sub_positions": [
                _sp("Energy Sector",     "OW", "XLE",  "Top overweight; late cycle leadership"),
                _sp("Oil & Gas E&P",     "OW", "XOP",  "Maximum exposure to oil price"),
                _sp("Pipelines / MLPs",  "OW", "AMLP", "Income + volume peak combination"),
                _sp("Energy Equipment",  "N",  "XES",  "Capex at peak; new orders may stall"),
                _sp("Uranium / Nuclear", "N",  "URA",  "Structural; less cycle-dependent"),
            ],
        },
        "CASH": {
            "stance": "N",
            "rationale": "Yield attractive; build reserves for future opportunities",
            "sub_positions": [
                _sp("T-Bills (1-3 mo)", "OW", "BIL",   "5%+ yield with zero risk; accumulate",yld="5.3%"),
                _sp("Money Market",     "OW", "VMFXX", "Parking gains from late-cycle winners", yld="5.3%"),
                _sp("Ultra Short Bond", "N",  "GSY",   "Marginal yield; slight risk",           yld="5.4%"),
                _sp("Short Treasury",   "N",  "SHV",   "Adequate; treasury quality",            yld="5.2%"),
            ],
        },
        "CORP CREDIT": {
            "stance": "UW",
            "rationale": "Late cycle = cracks forming; spreads will widen; reduce exposure now",
            "sub_positions": [
                _sp("Short IG Corp",  "N",  "VCSH", "Short duration provides some protection",         duration="2.7 yr"),
                _sp("IG Corporate",   "UW", "LQD",  "Spread widening + duration = double negative",    duration="8.4 yr"),
                _sp("High Yield",     "UW", "HYG",  "Spreads at tights; asymmetric downside",          duration="3.5 yr"),
                _sp("Bank Loans",     "N",  "BKLN", "Floating rate helps but credit quality declining", duration="0.1 yr"),
                _sp("EM Debt (USD)",  "UW", "EMB",  "Dollar strength + late cycle = avoid",            duration="7.5 yr"),
            ],
        },
        "INT'L EQUITIES": {
            "stance": "N",
            "rationale": "Dollar strong hurts EM; Europe facing its own overheating; selective",
            "sub_positions": [
                _sp("Brazil / LatAm",    "OW", "ILF",  "Commodity exporter benefits from late-cycle prices"),
                _sp("India",             "N",  "INDA", "Domestic demand; less rate-sensitive"),
                _sp("Developed Markets", "N",  "VEA",  "Europe facing rate pressure; neutral"),
                _sp("Japan",             "UW", "EWJ",  "Yen weakness and BoJ policy risk"),
                _sp("Emerging Markets",  "UW", "VWO",  "Dollar strength headwind at late cycle"),
                _sp("China",             "UW", "MCHI", "Structural property risk; avoid late cycle"),
            ],
        },
    },

    # ── RECESSION ─────────────────────────────────────────────
    "RECESSION": {
        "US EQUITIES": {
            "stance": "UW",
            "rationale": "Earnings recession underway; preserve capital; only pure defensives",
            "sub_positions": [
                _sp("Utilities",             "OW", "XLU",  "Most defensive sector; regulated revenue"),
                _sp("Consumer Staples",      "OW", "XLP",  "Non-discretionary spending; recession-proof"),
                _sp("Healthcare",            "OW", "XLV",  "Counter-cyclical demand; defensive core"),
                _sp("Energy",                "N",  "XLE",  "Supply discipline may support; watch demand"),
                _sp("Financials",            "UW", "XLF",  "Credit losses accelerating; NIM compressing"),
                _sp("Materials",             "UW", "XLB",  "Demand destruction hits commodity producers"),
                _sp("Industrials",           "UW", "XLI",  "Capex contraction; earnings freefall"),
                _sp("Technology",            "UW", "XLK",  "Multiple contraction + earnings miss"),
                _sp("Consumer Discretionary","UW", "XLY",  "Consumer spending collapse"),
                _sp("Real Estate",           "UW", "XLRE", "Defaults rising; vacancy rising"),
                _sp("Communications",        "UW", "XLC",  "Ad revenue collapses in recession"),
            ],
        },
        "LONG TREASURIES": {
            "stance": "OW",
            "rationale": "Flight to safety drives yields lower; long bonds appreciate significantly",
            "sub_positions": [
                _sp("30-Year Treasury",  "OW", "TLT",  "Maximum duration = maximum flight-to-safety gain",duration="17.4 yr"),
                _sp("10-Year Treasury",  "OW", "IEF",  "Core position; benchmark safety asset",           duration="7.5 yr"),
                _sp("5-Year Treasury",   "OW", "IEI",  "Solid risk-adjusted return in recession",         duration="4.4 yr"),
                _sp("2-Year Treasury",   "N",  "SHY",  "Lower duration limits recession upside",          duration="1.8 yr"),
                _sp("T-Bills (1-3 mo)",  "OW", "BIL",  "Capital preservation while deploying",           duration="0.1 yr"),
            ],
        },
        "TIPS": {
            "stance": "N",
            "rationale": "Inflation falling in recession; real yields rise; limited TIPS tailwind",
            "sub_positions": [
                _sp("Short TIPS (0-5 yr)", "N",  "VTIP", "Low duration acceptable; inflation may still bite",duration="2.5 yr"),
                _sp("Intermediate TIPS",   "N",  "TIP",  "Neutral; inflation falling offsets duration",       duration="7.0 yr"),
                _sp("Long TIPS (15+ yr)",  "UW", "LTPZ", "Duration risk without clear inflation benefit",     duration="20.0 yr"),
            ],
        },
        "GOLD": {
            "stance": "OW",
            "rationale": "Safe-haven demand surges; negative real rates supportive; dollar may weaken",
            "sub_positions": [
                _sp("Physical Gold ETF",  "OW", "GLD",  "Primary safe-haven; core OW position"),
                _sp("Gold (low cost)",    "OW", "IAU",  "Same exposure; lower cost structure"),
                _sp("Gold Miners",        "N",  "GDX",  "Operating leverage risky in recession"),
                _sp("Junior Gold Miners", "UW", "GDXJ", "Financing risk in recession; avoid"),
                _sp("Silver",             "UW", "SLV",  "Industrial demand collapse offsets safe-haven"),
            ],
        },
        "COMMODITIES": {
            "stance": "UW",
            "rationale": "Demand destruction overwhelms supply; commodity prices fall across the board",
            "sub_positions": [
                _sp("Broad Commodities", "UW", "GSG",  "Demand collapse; reduce entire allocation"),
                _sp("Crude Oil",         "UW", "USO",  "Travel and industrial demand falls sharply"),
                _sp("Industrial Metals", "UW", "DBB",  "Manufacturing contraction; copper falls"),
                _sp("Agriculture",       "N",  "DBA",  "Food demand relatively stable; neutral"),
                _sp("Natural Gas",       "N",  "UNG",  "Heating demand stable; utility usage persists"),
                _sp("Copper",            "UW", "CPER", "Best leading indicator of recession; falls hard"),
            ],
        },
        "ENERGY": {
            "stance": "UW",
            "rationale": "Demand destruction overrides supply discipline in deep recession",
            "sub_positions": [
                _sp("Pipelines / MLPs",  "N",  "AMLP", "Contracted volumes provide floor; defensive"),
                _sp("Energy Sector",     "UW", "XLE",  "Demand destruction drives sector lower"),
                _sp("Oil & Gas E&P",     "UW", "XOP",  "Oil price falls hurt E&P severely"),
                _sp("Energy Equipment",  "UW", "XES",  "Capex cancelled immediately"),
                _sp("Uranium / Nuclear", "N",  "URA",  "Structural demand; less cyclical"),
            ],
        },
        "CASH": {
            "stance": "OW",
            "rationale": "Capital preservation paramount; optionality to deploy at distressed prices",
            "sub_positions": [
                _sp("T-Bills (1-3 mo)", "OW", "BIL",   "Maximum capital preservation",           yld="5.3%"),
                _sp("Money Market",     "OW", "VMFXX", "Liquidity + safety; await buying oppty",  yld="5.3%"),
                _sp("Ultra Short Bond", "N",  "GSY",   "Marginal yield for patient capital",      yld="5.4%"),
                _sp("Short Treasury",   "OW", "SHV",   "Treasury quality; recession-proof",       yld="5.2%"),
            ],
        },
        "CORP CREDIT": {
            "stance": "UW",
            "rationale": "Default cycle in full swing; avoid all but highest quality; spreads blow out",
            "sub_positions": [
                _sp("Short IG Corp",   "N",  "VCSH", "Short duration limits mark-to-market damage",   duration="2.7 yr"),
                _sp("IG Corporate",    "UW", "LQD",  "Spreads widening + defaults rising",             duration="8.4 yr"),
                _sp("High Yield",      "UW", "HYG",  "Default wave; spreads to 700-900bp in recession",duration="3.5 yr"),
                _sp("Bank Loans",      "UW", "BKLN", "Floating rate means highest coupon as rates peak",duration="0.1 yr"),
                _sp("EM Debt (USD)",   "UW", "EMB",  "EM defaults spike; avoid entirely",              duration="7.5 yr"),
            ],
        },
        "INT'L EQUITIES": {
            "stance": "UW",
            "rationale": "Global recession contagion; no safe international haven except quality EM",
            "sub_positions": [
                _sp("India",             "N",  "INDA", "Domestic-driven; more insulated from global shock"),
                _sp("Developed Markets", "UW", "VEA",  "Europe often worse in global recessions"),
                _sp("Brazil / LatAm",    "UW", "ILF",  "Commodity demand collapse hits LatAm hard"),
                _sp("Japan",             "UW", "EWJ",  "Export economy; global recession devastates"),
                _sp("Emerging Markets",  "UW", "VWO",  "Capital flight from EM in crisis"),
                _sp("China",             "UW", "MCHI", "Property crisis + global recession = avoid"),
            ],
        },
    },
}


# ── K-SHAPE DIVERGENCE THRESHOLDS ────────────────────────────
K_SHAPE = {
    "cc_delinquency_stress": 3.0, "cc_delinquency_crisis": 4.5,
    "savings_rate_low": 4.0,      "savings_rate_very_low": 2.5,
    "umich_weak": 70.0,           "umich_strong": 85.0,
    "cs_hpi_strong": 5.0,
}
