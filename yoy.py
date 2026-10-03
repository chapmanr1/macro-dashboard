# FILE: yoy.py
# Bloomberg Macro Dashboard — Year-over-year calculation
# FRED dates every observation by the start of its period (monthly "2026-08-01",
# quarterly "2026-07-01"), so the year-ago value is the observation dated one
# calendar year earlier. Matching by date instead of counting 12 rows back keeps
# YoY correct when FRED is missing a month and for quarterly or annual series.
# If the year-ago observation is missing, the result is None, never a wrong number.


def year_ago(date_str: str) -> str:
    """Return the same period one year earlier: "2026-08-01" -> "2025-08-01"."""
    return f"{int(date_str[:4]) - 1:04d}{date_str[4:]}"


def _valid(obs: list[dict]) -> list[dict]:
    """Drop FRED's missing-value markers ("." or empty)."""
    return [o for o in obs if o.get("value") not in (".", "", None)]


def _by_date(obs: list[dict]) -> dict[str, float]:
    return {o["date"]: float(o["value"]) for o in _valid(obs)}


def _pct(current: float, base: float) -> float | None:
    if base == 0:
        return None
    return (current - base) / abs(base) * 100


def yoy_at(obs: list[dict], i: int = 0) -> float | None:
    """
    YoY % change (unrounded) for the i-th valid observation of a newest-first
    FRED list. Returns None if that observation or its year-ago match is missing.
    """
    valid = _valid(obs)
    if i >= len(valid):
        return None
    lookup = _by_date(valid)
    base = lookup.get(year_ago(valid[i]["date"]))
    if base is None:
        return None
    return _pct(float(valid[i]["value"]), base)


def yoy_series(obs: list[dict], n: int) -> list[dict]:
    """
    Up to n most recent YoY points as [{"date", "value"}], oldest-first,
    rounded to 2 decimals. Accepts observations in either date order;
    dates with no year-ago match are skipped.
    """
    lookup = _by_date(obs)
    points = []
    for d in sorted(lookup, reverse=True):
        base = lookup.get(year_ago(d))
        if base is None:
            continue
        v = _pct(lookup[d], base)
        if v is not None:
            points.append({"date": d, "value": round(v, 2)})
        if len(points) == n:
            break
    return list(reversed(points))
