# FILE: news_feed.py
# Bloomberg Macro Dashboard — RSS News Feed
# Real-time headlines from free RSS feeds.
# Fixed: date parsing and chronological sorting.

import re
import time
import logging
import requests
from collections import Counter
from source_status import timed_get
from datetime import datetime, timezone
from xml.etree import ElementTree as ET
from email.utils import parsedate_to_datetime
from config import (NEWS_TOPICS, NEWS_DEFAULT_TOPIC, NEWS_IMPACT_SCALE,
                    NEWS_WATCHLIST, NEWS_WATCHLIST_CHECKPOINTS)

log = logging.getLogger(__name__)

# ── CONFIG ────────────────────────────────────────────────────
CACHE_TTL       = 900
MAX_ARTICLES    = 20
REQUEST_TIMEOUT = 10

# ── CACHE ─────────────────────────────────────────────────────
_cache = {"data": None, "ts": 0}
_last_good: dict = {"data": None}   # last successful pull, served (marked stale) if every feed fails

def _cache_valid():
    return (
        _cache["data"] is not None and
        (time.time() - _cache["ts"]) < CACHE_TTL
    )

def _set_cache(data):
    _cache["data"] = data
    _cache["ts"]   = time.time()

# ── RSS FEEDS ─────────────────────────────────────────────────
RSS_FEEDS = [
    {
        "url":    "https://www.marketwatch.com/rss/marketpulse",
        "source": "MarketWatch",
    },
    {
        "url":    "https://www.marketwatch.com/rss/realtimeheadlines",
        "source": "MarketWatch",
    },
    {
        "url":    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
        "source": "WSJ",
    },
    {
        "url":    "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml",
        "source": "WSJ",
    },
    {
        "url":    "https://feeds.a.dj.com/rss/RSSOpinion.xml",
        "source": "WSJ",
    },
    {
        "url":    "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
        "source": "WSJ",
    },
    {
        "url":    "https://feeds.a.dj.com/rss/RSSWSJD.xml",
        "source": "WSJ",
    },
    {
        "url":    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
        "source": "CNBC",
    },
    {
        "url":    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
        "source": "CNBC Economy",
    },
    {
        "url":    "https://www.ft.com/rss/home",
        "source": "Financial Times",
    },
    {
        "url":    "https://www.axios.com/feeds/feed.rss",
        "source": "Axios",
    },
    {
        "url":    "https://feeds.bloomberg.com/markets/news.rss",
        "source": "Bloomberg",
    },
    {
        "url":    "https://www.federalreserve.gov/feeds/press_all.xml",
        "source": "Federal Reserve",
    },
    {
        "url":    "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
        "source": "MarketWatch RT",
    },
]

# ── RELEVANCE KEYWORDS ────────────────────────────────────────
KEYWORD_SCORES = {
    "federal reserve":  10,
    "fed rate":         10,
    "fomc":             10,
    "stagflation":      10,
    "yield curve":       9,
    "recession":         9,
    "inflation":         8,
    "cpi":               8,
    "pce":               8,
    "powell":            8,
    "rate hike":         8,
    "rate cut":          8,
    "treasury":          7,
    "10-year":           7,
    "credit":            7,
    "tariff":            7,
    "trade":             6,
    "gdp":               6,
    "unemployment":      6,
    "jobs":              6,
    "nonfarm":           6,
    "payroll":           6,
    "debt":              5,
    "deficit":           5,
    "fiscal":            5,
    "bank":              5,
    "lending":           5,
    "mortgage":          5,
    "housing":           5,
    "economic":          4,
    "market":            3,
    "stocks":            2,
    "s&p":               2,
    "nasdaq":            2,
    "wall street":       2,
    "financial":         2,
}

NOISE_TERMS = [
    "cryptocurrency", "bitcoin", "crypto", "nft",
    "celebrity", "sports", "entertainment", "gaming",
    "cannabis", "obituary", "weather", "horoscope",
    "personal finance", "best credit cards", "mortgage rates today",
    "how to save", "retirement tips", "budget", "coupon",
    "best savings account", "cd rates", "insurance",
    "real estate agent", "home buying tips", "car insurance",
    "student loans", "personal loan", "dating", "relationship",
    "health tips", "diet", "exercise", "celebrity net worth",
    "richest", "wealthiest", "billionaire list",
]

MIN_SCORE = 6

# ── DATE PARSER ───────────────────────────────────────────────
def _parse_date(pub_date):
    """
    Robustly parse any RSS date format.
    Returns (iso_string, datetime_object), or (None, None) when the feed gives no
    usable date — a story is never re-dated to "now" to look fresh.
    """
    if not pub_date or not pub_date.strip():
        return None, None

    pub_date = pub_date.strip()

    # Method 1 — email.utils handles all RFC 2822 formats
    # This covers 99% of RSS feeds correctly
    try:
        dt = parsedate_to_datetime(pub_date)
        dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ"), dt
    except Exception:
        pass

    # Method 2 — ISO 8601 variants
    iso_formats = [
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in iso_formats:
        try:
            dt = datetime.strptime(pub_date, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ"), dt
        except ValueError:
            continue

    log.warning(f"Could not parse date: '{pub_date}' — story kept as undated")
    return None, None


def _fmt_rel_time(dt):
    """
    Format datetime as relative time string.
    Returns e.g. '5MIN AGO', '2HR AGO', 'JUST NOW'
    """
    if dt is None:
        return "--"
    now  = datetime.now(timezone.utc)
    diff = int((now - dt).total_seconds())

    if diff < 0:
        return "JUST NOW"
    if diff < 60:
        return "JUST NOW"
    if diff < 3600:
        mins = diff // 60
        return f"{mins}MIN AGO"
    if diff < 86400:
        hrs = diff // 3600
        return f"{hrs}HR AGO"

    days = diff // 86400
    return f"{days}D AGO"


# ── RSS PARSER ────────────────────────────────────────────────
def _fetch_rss(feed):
    """Fetch and parse a single RSS feed."""
    headers = {
        "User-Agent": "Mozilla/5.0 MacroTerminal/1.0",
        "Accept":     "application/rss+xml, application/xml, text/xml",
    }

    resp = timed_get("rss", feed["url"], headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()

    root    = ET.fromstring(resp.content)
    channel = root.find("channel")
    if channel is None:
        channel = root

    articles = []
    for item in channel.findall("item"):
        title    = item.findtext("title", "").strip()
        url      = item.findtext("link", "").strip()
        pub_date = item.findtext("pubDate", "").strip()
        desc     = item.findtext("description", "").strip()

        # Some feeds use atom:updated or dc:date
        if not pub_date:
            for tag in [
                "{http://purl.org/dc/elements/1.1/}date",
                "{http://www.w3.org/2005/Atom}updated",
                "{http://www.w3.org/2005/Atom}published",
            ]:
                val = item.findtext(tag, "").strip()
                if val:
                    pub_date = val
                    break

        if not title or not url:
            continue

        iso_ts, dt_obj = _parse_date(pub_date)
        rel_time       = _fmt_rel_time(dt_obj)

        articles.append({
            "title":       title,
            "headline":    title,
            "description": desc[:200] if desc else "",
            "source":      feed["source"],
            "url":         url,
            "publishedAt": iso_ts,
            "timestamp":   iso_ts,
            "relTime":     rel_time,
            "dt":          dt_obj,  # keep for sorting, removed before response
        })

    return articles


# ── SCORING ───────────────────────────────────────────────────
def _score_article(article):
    title = (article.get("title") or "").lower()
    desc  = (article.get("description") or "").lower()
    text  = title + " " + desc
    score = 0

    for keyword, weight in KEYWORD_SCORES.items():
        if keyword in title:
            score += weight * 2
        elif keyword in desc:
            score += weight

    for noise in NOISE_TERMS:
        if noise in text:
            score -= 15

    return max(0, score)

def _matches(term: str, text: str) -> bool:
    """Whole-word (prefix) match, so short terms like 'srt' don't hit inside other words."""
    return re.search(r"\b" + re.escape(term), text) is not None


def _topic(article: dict) -> str:
    """One topic per story: highest keyword score (title 2, description 1); ties → earlier topic."""
    title = " " + (article.get("title") or "").lower() + " "
    desc  = " " + (article.get("description") or "").lower() + " "
    best, best_score = NEWS_DEFAULT_TOPIC, 0
    for name, words in NEWS_TOPICS:
        sc = sum(2 if _matches(w, title) else 1 if _matches(w, desc) else 0 for w in words)
        if sc > best_score:
            best, best_score = name, sc
    return best


def _watch_tags(article: dict) -> list[str]:
    text = ((article.get("title") or "") + " " + (article.get("description") or "")).lower()
    return [tag for tag, terms in NEWS_WATCHLIST.items() if any(_matches(t, text) for t in terms)]


def _is_valid(article):
    return (
        article.get("title") and
        article.get("url") and
        article.get("title") != "[Removed]"
    )

def _deduplicate(articles):
    seen   = set()
    unique = []
    for a in articles:
        key = a["title"][:60].lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(a)
    return unique


# ── FALLBACK ──────────────────────────────────────────────────
def _fallback(error: str = "") -> dict:
    """Every feed failed. Serve the last good pull (marked stale) rather than padding the feed."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    prev = _last_good["data"]
    if prev:
        return {**prev, "cached": True, "status": "stale", "error": error, "timestamp": ts}
    return {"articles": [], "count": 0, "timestamp": ts, "cached": False, "error": error,
            "status": "failed", "last_success_at": None, "source_counts": {},
            "watchlist": list(NEWS_WATCHLIST.keys()), "checkpoint_events": NEWS_WATCHLIST_CHECKPOINTS}


# ── MAIN ENTRY POINT ──────────────────────────────────────────
def get_news():
    """
    Fetch real-time macro headlines from free RSS feeds.
    Sorted by recency. Relative timestamps accurate.
    """
    if _cache_valid():
        log.info("News: returning cached RSS data.")
        cached = dict(_cache["data"])
        cached["cached"] = True
        return cached

    log.info("News: fetching fresh RSS feeds...")
    ts     = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw    = []
    errors = []

    # Fetch all feeds in parallel, not one at a time: sequentially, a handful
    # of dead/slow feeds (each up to REQUEST_TIMEOUT=10s) can sum to minutes
    # of blocking on this single-worker server — enough to exceed gunicorn's
    # request timeout and take the whole app down with it. Parallel fetch
    # bounds total wait to roughly the slowest single feed instead.
    import concurrent.futures as _cf
    with _cf.ThreadPoolExecutor(max_workers=len(RSS_FEEDS)) as pool:
        futures = {pool.submit(_fetch_rss, feed): feed for feed in RSS_FEEDS}
        try:
            done_iter = _cf.as_completed(futures, timeout=REQUEST_TIMEOUT + 5)
            while True:
                try:
                    fut = next(done_iter)
                except StopIteration:
                    break
                feed = futures[fut]
                try:
                    articles = fut.result()
                    raw.extend(articles)
                    log.info(f"News: {len(articles)} from {feed['source']}")
                except Exception as e:
                    log.warning(f"RSS failed [{feed['source']}]: {e}")
                    errors.append(f"{feed['source']}: {str(e)[:60]}")
        except _cf.TimeoutError:
            log.warning("News: some RSS feeds did not finish in time; using whatever completed.")

    if not raw:
        error_msg = "; ".join(errors) if errors else "All RSS feeds failed."
        log.error(f"News: complete failure — {error_msg}")
        result = _fallback(error_msg)
        _set_cache(result)
        return result

    # Filter valid articles
    valid = [a for a in raw if _is_valid(a)]

    # Score for relevance; watchlist stories are always kept
    scored = [(a, _score_article(a), _watch_tags(a)) for a in valid]
    filtered = [(a, s, w) for a, s, w in scored if s >= MIN_SCORE or w]

    # Sort by recency first — most recent at top; undated stories last
    filtered.sort(key=lambda x: x[0].get("dt") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    # Add score/impact/topic/watchlist, remove dt object before sending to frontend
    cleaned = []
    for a, s, w in filtered:
        article = {k: v for k, v in a.items() if k != "dt"}
        article["score"]     = s
        article["impact"]    = min(100, round(s * 100 / NEWS_IMPACT_SCALE))
        article["topic"]     = _topic(a)
        article["watchlist"] = w
        cleaned.append(article)

    unique = _deduplicate(cleaned)
    final  = unique[:MAX_ARTICLES]

    status = "ok" if not errors else "partial"

    result = {
        "articles":          final,
        "count":             len(final),
        "timestamp":         ts,
        "last_success_at":   ts,                      # true time of this successful pull
        "source_counts":     dict(Counter(a["source"] for a in final)),
        "watchlist":         list(NEWS_WATCHLIST.keys()),
        "checkpoint_events": NEWS_WATCHLIST_CHECKPOINTS,
        "cached":            False,
        "error":             "; ".join(errors) if errors else None,
        "status":            status,
    }

    _last_good["data"] = result
    _set_cache(result)
    log.info(f"News: {len(final)} articles, status={status}.")
    return result


# ── STANDALONE TEST ───────────────────────────────────────────
if __name__ == "__main__":
    import json
    result = get_news()
    # Remove dt objects for clean print
    print(json.dumps(
        {k: v for k, v in result.items()},
        indent=2,
        default=str
    ))
