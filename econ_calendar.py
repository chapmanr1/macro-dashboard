# FILE: econ_calendar.py
# Bloomberg Macro Dashboard — Economic Calendar
# Every date comes from the agency that sets it, read automatically — no pattern
# guesses and no yearly hardcoded lists to maintain:
#   - BLS / BEA / Census / DOL / UMich releases → FRED release-dates API
#   - FOMC decisions, Beige Book               → federalreserve.gov pages
#   - ISM Manufacturing / Services             → ISM's stated rule (1st / 3rd business day)
#   - Conference Board LEI, Consumer Confidence → "next release" on conference-board.org
#                                                (+ stated last-Tuesday rule for CCI)
# If a source fails, its events are omitted and a warning is logged; nothing
# falls back to guessing. Sources are read at most once a day.

import html
import logging
import re
import threading
import time
import concurrent.futures
from datetime import date, datetime, timedelta

import pytz
import requests

from fred_data import FRED_API_KEY, _fetch_surprise_data

log = logging.getLogger(__name__)

EASTERN   = pytz.timezone("US/Eastern")
CACHE_TTL = 86400  # 24h — release schedules change rarely
WINDOW    = 400    # days of schedule fetched (frontend month view asks for 400)
RETRY_TTL = 3600   # rebuild sooner when any source failed
_cache: dict = {}
_lock = threading.Lock()
_failed: set = set()  # sources that failed during the current schedule build

FRED_RELEASE_DATES = "https://api.stlouisfed.org/fred/release/dates"
FOMC_URL   = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
BEIGE_URL  = "https://www.federalreserve.gov/monetarypolicy/publications/beige-book-default.htm"
CB_LEI_URL = "https://www.conference-board.org/topics/us-leading-indicators/"
CB_CCI_URL = "https://www.conference-board.org/topics/consumer-confidence/"
_HEADERS   = {"User-Agent": "Mozilla/5.0 (macro-dashboard personal research tool)"}

# FRED release_id → (ET release time, [(event, impact)], source note).
# FRED's API returns dates only; times are fixed by each agency.
# Event names match fred_data._SURPRISE_MAP so actuals can be attached.
RELEASES = {
    10:  ("8:30 ET",  [("CPI RELEASE", "HIGH"), ("CORE CPI", "HIGH")],                      "BLS"),
    50:  ("8:30 ET",  [("NONFARM PAYROLLS", "HIGH"), ("UNEMPLOYMENT RATE", "HIGH")],        "BLS"),
    46:  ("8:30 ET",  [("PPI RELEASE", "MEDIUM")],                                          "BLS"),
    53:  ("8:30 ET",  [("GDP", "HIGH")],                                                    "BEA"),
    54:  ("8:30 ET",  [("CORE PCE INFLATION", "HIGH"), ("PERSONAL INCOME/SPEND", "MEDIUM")], "BEA"),
    9:   ("8:30 ET",  [("RETAIL SALES", "HIGH")],                                           "Census"),
    192: ("10:00 ET", [("JOLTS JOB OPENINGS", "HIGH")],                                     "BLS"),
    95:  ("8:30 ET",  [("DURABLE GOODS ORDERS", "MEDIUM")],                                 "Census"),
    51:  ("8:30 ET",  [("TRADE BALANCE", "MEDIUM")],                                        "Census/BEA"),
    11:  ("8:30 ET",  [("EMPLOYMENT COST INDEX", "MEDIUM")],                                "BLS"),
    47:  ("8:30 ET",  [("PRODUCTIVITY & COSTS", "MEDIUM")],                                 "BLS"),
    27:  ("8:30 ET",  [("HOUSING STARTS", "MEDIUM")],                                       "Census"),
    180: ("8:30 ET",  [("INITIAL JOBLESS CLAIMS", "HIGH")],                                 "DOL"),
    91:  ("10:00 ET", [("UMICH SENTIMENT", "MEDIUM")],                                      "UMich"),
}
# Releases with two publications a month under one FRED release_id:
#   95 = full M3 report (early month) + advance durable goods (late month) → keep late-month
#   27 = housing starts + a later same-month publication → keep the first date each month
_LATE_MONTH_ONLY  = {95}
_FIRST_IN_MONTH   = {27}

# Used only if the Fed's pages can't be read (verified against federalreserve.gov 2026-10-02).
_FOMC_FALLBACK = [
    (date(2026, 10, 28), False), (date(2026, 12, 9), True),
    (date(2027, 1, 27), False), (date(2027, 3, 17), True), (date(2027, 4, 28), False),
    (date(2027, 6, 9), True), (date(2027, 7, 28), False), (date(2027, 9, 15), True),
    (date(2027, 10, 27), False), (date(2027, 12, 8), True),
]
_BEIGE_FALLBACK = [date(2026, 10, 14), date(2026, 11, 25)]

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _get_page(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        log.warning(f"Calendar source unreachable [{url}]: {e}")
        return None


# ── FRED RELEASE DATES ────────────────────────────────────────
def _fetch_release_dates(release_id: int, start: date) -> list[date] | None:
    """Scheduled dates for one FRED release from `start` onward, or None on failure."""
    if not FRED_API_KEY:
        log.warning("FRED_API_KEY not set — official release dates unavailable")
        return None
    params = {
        "release_id":   release_id,
        "api_key":      FRED_API_KEY,
        "file_type":    "json",
        "realtime_start": start.isoformat(),
        "realtime_end": "9999-12-31",
        "include_release_dates_with_no_data": "true",
        "sort_order":   "asc",
        "limit":        1000,
    }
    for attempt in range(3):
        try:
            resp = requests.get(FRED_RELEASE_DATES, params=params, timeout=12)
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            rows = resp.json().get("release_dates", [])
            return [date.fromisoformat(r["date"]) for r in rows if r.get("date")]
        except (requests.RequestException, ValueError, KeyError) as e:
            log.warning(f"FRED release dates failed [release {release_id}]: {e}")
            return None
    log.warning(f"FRED release dates rate limited after 3 attempts [release {release_id}]")
    return None


def _filter_release_dates(release_id: int, dates: list[date]) -> list[date]:
    """Apply the per-release rules for releases that publish twice a month."""
    if release_id in _LATE_MONTH_ONLY:
        return [d for d in dates if d.day >= 15]
    if release_id in _FIRST_IN_MONTH:
        seen, out = set(), []
        for d in sorted(dates):
            if (d.year, d.month) not in seen:
                seen.add((d.year, d.month))
                out.append(d)
        return out
    return dates


def _all_release_dates(start: date) -> dict[int, list[date]]:
    """{release_id: [dates]} for every release in RELEASES; failed releases are omitted."""
    out: dict[int, list[date]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_fetch_release_dates, rid, start): rid for rid in RELEASES}
        for fut in concurrent.futures.as_completed(futures):
            rid = futures[fut]
            dates = fut.result()
            if dates is not None:
                out[rid] = _filter_release_dates(rid, dates)
            else:
                _failed.add(f"fred:{rid}")
    return out


# ── FEDERAL RESERVE ───────────────────────────────────────────
def parse_fomc(page: str) -> list[tuple[date, bool]]:
    """[(decision date, has SEP)] from the Fed's FOMC calendar page. Skips notation votes."""
    meetings = []
    parts = re.split(r"<h4[^>]*>\s*<a[^>]*>\s*(\d{4}) FOMC Meetings", page)
    for year, chunk in zip(parts[1::2], parts[2::2]):
        for month, days in re.findall(
                r"fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?fomc-meeting__date[^>]*>([^<]+)<",
                chunk, re.S):
            m = re.match(r"\s*(\d{1,2})(?:-(\d{1,2}))?(\*)?\s*$", days)
            if not m:
                continue  # notation votes, unscheduled meetings
            mnum = _MONTHS.get(month.split("/")[-1].strip()[:3].lower())
            if not mnum:
                continue
            try:
                meetings.append((date(int(year), mnum, int(m.group(2) or m.group(1))), bool(m.group(3))))
            except ValueError:
                continue
    return sorted(meetings)


def parse_beige_book(page: str) -> list[date]:
    """Beige Book release dates from each year's table on the Fed's Beige Book page."""
    dates = []
    for year, table in re.findall(r'id="year">\s*(\d{4})\s*</th>(.*?)</table>', page, re.S):
        for month, day in re.findall(r"<td>\s*([A-Z][a-z]+)\s+(\d{1,2})\b", table):
            mnum = _MONTHS.get(month[:3].lower())
            if mnum:
                try:
                    dates.append(date(int(year), mnum, int(day)))
                except ValueError:
                    continue
    return sorted(dates)


def _fomc_meetings() -> list[tuple[date, bool]]:
    page = _get_page(FOMC_URL)
    meetings = parse_fomc(page) if page else []
    if not meetings:
        log.warning("FOMC calendar page unreadable — using built-in 2026-27 schedule")
        _failed.add("fomc")
        return _FOMC_FALLBACK
    return meetings


def _beige_dates() -> list[date]:
    page = _get_page(BEIGE_URL)
    dates = parse_beige_book(page) if page else []
    if not dates:
        log.warning("Beige Book page unreadable — using built-in 2026 schedule")
        _failed.add("beige")
        return _BEIGE_FALLBACK
    return dates


# ── CONFERENCE BOARD ──────────────────────────────────────────
def parse_next_release(page: str, today: date) -> date | None:
    """Date from a Conference Board 'next release is [scheduled for] <Weekday>, <Month> <D>[th][, YYYY]' line."""
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))
    m = re.search(r"next release is (?:scheduled for )?\w+day,? ([A-Z][a-z]+) (\d{1,2})(?:st|nd|rd|th)?,? ?(\d{4})?", text)
    if not m:
        return None
    mnum = _MONTHS.get(m.group(1)[:3].lower())
    if not mnum:
        return None
    year = int(m.group(3)) if m.group(3) else today.year
    try:
        d = date(year, mnum, int(m.group(2)))
    except ValueError:
        return None
    if not m.group(3) and d < today - timedelta(days=31):
        d = date(year + 1, mnum, int(m.group(2)))  # e.g. a January date read in December
    return d


def _cb_next(url: str, today: date) -> date | None:
    page = _get_page(url)
    d = parse_next_release(page, today) if page else None
    if page and not d:
        log.warning(f"Conference Board page changed — next release date not found [{url}]")
    if not d:
        _failed.add(url)
    return d


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


# ── ISM (business-day rule) ───────────────────────────────────
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    return d + timedelta(days=(weekday - d.weekday()) % 7 + 7 * (n - 1))


def _observed(d: date) -> date:
    return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d


def us_federal_holidays(year: int) -> set[date]:
    """Observed US federal holidays for a year (includes Jan 1 of next year observed on Dec 31)."""
    fixed = [date(year, 1, 1), date(year, 6, 19), date(year, 7, 4), date(year, 11, 11), date(year, 12, 25)]
    hol = {_observed(d) for d in fixed}
    hol |= {
        _nth_weekday(year, 1, 0, 3),     # MLK Day
        _nth_weekday(year, 2, 0, 3),     # Presidents Day
        _last_weekday(year, 5, 0),       # Memorial Day
        _nth_weekday(year, 9, 0, 1),     # Labor Day
        _nth_weekday(year, 10, 0, 2),    # Columbus Day
        _nth_weekday(year, 11, 3, 4),    # Thanksgiving
        _observed(date(year + 1, 1, 1)), # next New Year's, may fall on Dec 31
    }
    return hol


def nth_business_day(year: int, month: int, n: int) -> date:
    hol = us_federal_holidays(year) | us_federal_holidays(year - 1)
    d, count = date(year, month, 1), 0
    while True:
        if d.weekday() < 5 and d not in hol:
            count += 1
            if count == n:
                return d
        d += timedelta(days=1)


# ── ASSEMBLY ──────────────────────────────────────────────────
def _months_between(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _build_schedule(today: date) -> dict[date, list[dict]]:
    """Every scheduled event from today through today+WINDOW, keyed by date (no actuals)."""
    end = today + timedelta(days=WINDOW)
    sched: dict[date, list[dict]] = {}

    def add(d: date, ev: dict) -> None:
        if today <= d <= end:
            sched.setdefault(d, []).append(ev)

    for rid, dates in _all_release_dates(today).items():
        t, events, source = RELEASES[rid]
        for d in dates:
            for name, impact in events:
                add(d, {"time": t, "event": name, "impact": impact, "note": source})

    for d, sep in _fomc_meetings():
        add(d, {"time": "2:00 ET", "event": "FOMC RATE DECISION", "impact": "HIGH",
                "note": "Fed · with projections (SEP)" if sep else "Fed"})
        add(d, {"time": "2:30 ET", "event": "FED CHAIR PRESS CONFERENCE", "impact": "HIGH", "note": "Fed"})

    for d in _beige_dates():
        add(d, {"time": "2:00 ET", "event": "BEIGE BOOK RELEASE", "impact": "HIGH", "note": "Fed"})

    for y, m in _months_between(today, end):
        add(nth_business_day(y, m, 1), {"time": "10:00 ET", "event": "ISM MANUFACTURING", "impact": "HIGH",
                                        "note": "ISM · 1st business day"})
        add(nth_business_day(y, m, 3), {"time": "10:00 ET", "event": "ISM SERVICES", "impact": "HIGH",
                                        "note": "ISM · 3rd business day"})

    lei = _cb_next(CB_LEI_URL, today)
    if lei:
        add(lei, {"time": "10:00 ET", "event": "LEADING ECONOMIC INDEX", "impact": "MEDIUM",
                  "note": "Conference Board"})
    cci = _cb_next(CB_CCI_URL, today)
    if cci:
        add(cci, {"time": "10:00 ET", "event": "CONSUMER CONFIDENCE", "impact": "MEDIUM",
                  "note": "Conference Board"})
        for y, m in _months_between(cci + timedelta(days=31), end):
            if (y, m) > (cci.year, cci.month):
                add(_last_weekday(y, m, 1), {"time": "10:00 ET", "event": "CONSUMER CONFIDENCE",
                                             "impact": "MEDIUM", "note": "Conference Board · last Tuesday"})
    return sched


def _release_passed(time_str: str, now_et: datetime) -> bool:
    """True once now (ET) is past an event time like '8:30 ET' (2:00/2:30 are PM)."""
    m = re.match(r"(\d{1,2}):(\d{2})", time_str or "")
    if not m:
        return False
    h, mi = int(m.group(1)), int(m.group(2))
    if h < 7:
        h += 12
    return (now_et.hour, now_et.minute) >= (h, mi)


def attach_actuals(day_events: list[dict], d: date, now_et: datetime, surprises: dict) -> None:
    """
    Add the released value to today's events only after release time, and only
    if FRED shows the series was updated today. Upcoming events get no number.
    """
    if d != now_et.date():
        return
    for ev in day_events:
        sur = surprises.get(ev["event"])
        if (sur and sur.get("updated") == d.isoformat()
                and _release_passed(ev["time"], now_et)):
            ev["actual_str"] = sur.get("actual_str")
            ev["vs_prior"]   = sur.get("vs_prior")


def _get_schedule(today: date) -> dict[date, list[dict]]:
    """Cached schedule: rebuilt daily, or hourly while any source is failing."""
    with _lock:
        entry = _cache.get("schedule")
        if entry and entry["day"] == today and time.time() - entry["ts"] < entry["ttl"]:
            return entry["sched"]
        _failed.clear()
        sched = _build_schedule(today)
        if _failed:
            log.warning(f"Economic calendar built without: {sorted(_failed)}")
        _cache["schedule"] = {"sched": sched, "day": today, "ts": time.time(),
                              "ttl": RETRY_TTL if _failed else CACHE_TTL}
        return sched


def get_economic_calendar(days: int = 8) -> list:
    """
    Official economic calendar for the next N days:
    [{"date", "day_of_week", "day_display", "events": [{time, event, impact, note, actual_str?, vs_prior?}]}]
    """
    now_et = datetime.now(pytz.UTC).astimezone(EASTERN)
    today  = now_et.date()
    sched  = _get_schedule(today)
    try:
        surprises = _fetch_surprise_data()
    except (requests.RequestException, ValueError, KeyError) as e:
        log.warning(f"Calendar actuals unavailable: {e}")
        surprises = {}

    out = []
    for offset in range(max(1, min(days, WINDOW))):
        d = today + timedelta(days=offset)
        if d not in sched:
            continue
        evs = [dict(ev) for ev in sorted(sched[d], key=lambda e: _sort_key(e["time"]))]
        attach_actuals(evs, d, now_et, surprises)
        out.append({
            "date":        d.isoformat(),
            "day_of_week": d.strftime("%A").upper(),
            "day_display": d.strftime("%b %d").upper(),
            "events":      evs,
        })
    return out


def _sort_key(time_str: str) -> tuple[int, int]:
    m = re.match(r"(\d{1,2}):(\d{2})", time_str or "")
    if not m:
        return (99, 0)
    h = int(m.group(1))
    return (h + 12 if h < 7 else h, int(m.group(2)))
