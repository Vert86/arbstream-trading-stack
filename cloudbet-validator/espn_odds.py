"""ESPN scoreboard odds (DraftKings): free moneylines/spreads for NBA/NFL/NHL.
Also doubles as the results source for settlement."""
import datetime as dt
import json
import urllib.request

SITE = "https://site.api.espn.com/apis/site/v2/sports"

LEAGUE_PATHS = {"nba": "basketball/nba", "nfl": "football/nfl", "nhl": "hockey/nhl",
                 "ncaab": "basketball/mens-college-basketball"}


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def amer_to_dec(s):
    """'+150'/'-142' -> 2.50/1.70"""
    if s is None:
        return None
    try:
        v = int(str(s).replace("+", ""))
    except (ValueError, TypeError):
        return None
    if v > 0:
        return round(1 + v / 100.0, 3)
    if v < 0:
        return round(1 + 100.0 / (-v), 3)
    return None


def scoreboard(league, yyyymmdd=None):
    """All events for a league (today if no date). Returns raw list."""
    path = f"{SITE}/{LEAGUE_PATHS[league]}/scoreboard"
    if yyyymmdd:
        path += f"?dates={yyyymmdd}"
    return _get(path).get("events", [])


def game_odds(ev):
    """Extract moneyline decimal odds + metadata from an ESPN event.
    Returns None if no priced odds available."""
    try:
        comp = ev.get("competitions", [{}])[0]
        o = (comp.get("odds") or [None])[0]
        if not o:
            return None
        ml = o.get("moneyline") or {}
        h = (ml.get("home") or {}).get("close") or {}
        a = (ml.get("away") or {}).get("close") or {}
        d = (ml.get("draw") or {}).get("close") or {}
        dh, da, dd = amer_to_dec(h.get("odds")), amer_to_dec(a.get("odds")), amer_to_dec(d.get("odds"))
        if not dh or not da or dh <= 1.01 or da <= 1.01:
            return None
        return {
            "espn_id": ev.get("id"),
            "home_abbr": (next((c for c in comp.get("competitors", []) if c.get("homeAway") == "home" or c.get("home")), {}).get("team") or {}).get("abbreviation"),
            "away_abbr": (next((c for c in comp.get("competitors", []) if not (c.get("homeAway") == "home" or c.get("home"))), {}).get("team") or {}).get("abbreviation"),
            "state": comp.get("status", {}).get("type", {}).get("state"),
            "home_ml": dh, "away_ml": da, "draw_ml": dd,
            "spread": o.get("details"), "total": o.get("overUnder"),
            "provider": (o.get("provider") or {}).get("name"),
        }
    except (IndexError, KeyError, ValueError):
        return None


def upcoming_games(league, days_ahead=3):
    """Priced, not-started games within days_ahead."""
    out = []
    for off in range(days_ahead):
        day = (dt.datetime.now(dt.timezone(dt.timedelta(hours=-4))) + dt.timedelta(days=off)).strftime("%Y%m%d")
        try:
            evs = scoreboard(league, day)
        except Exception:
            continue
        for ev in evs:
            g = game_odds(ev)
            if g and g["state"] == "pre":
                g["kickoff"] = ev.get("date")
                g["league"] = league
                out.append(g)
    return out


def results(league, yyyymmdd):
    """Completed games: {away_abbr@home_abbr: winner_side}."""
    out = {}
    try:
        evs = scoreboard(league, yyyymmdd)
    except Exception:
        return out
    for ev in evs:
        try:
            comp = ev.get("competitions", [{}])[0]
            if not comp.get("status", {}).get("type", {}).get("completed"):
                continue
            h = [c for c in comp.get("competitors", []) if c.get("homeAway") == "home" or c.get("home")][0]
            a = [c for c in comp.get("competitors", []) if not (c.get("homeAway") == "home" or c.get("home"))][0]
            hs, as_ = int(h.get("score", 0) or 0), int(a.get("score", 0) or 0)
            if hs == as_:
                continue  # push/tie - skip for ML settle (rare; NFL ties excluded)
            out[f"{a['team']['abbreviation']}@{h['team']['abbreviation']}"] = "home" if hs > as_ else "away"
        except (IndexError, KeyError, ValueError):
            continue
    return out
