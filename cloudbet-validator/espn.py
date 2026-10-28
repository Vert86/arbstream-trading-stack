"""ESPN free API layer: standings, injuries, scoreboard results."""
import json
import urllib.request

SITE = "https://site.api.espn.com/apis/site/v2/sports"
V2 = "https://site.api.espn.com/apis/v2/sports"

LEAGUES = {
    "nba": {"site": "basketball/nba", "hfa": 2.5, "scale": 11.5, "inj_pts": 0.8, "gp_ref": 20},
    "nfl": {"site": "football/nfl", "hfa": 1.6, "scale": 13.5, "inj_pts": 0.5, "gp_ref": 8},
    "nhl": {"site": "hockey/nhl", "hfa": 0.30, "scale": 1.9, "inj_pts": 0.06, "gp_ref": 15},
    # NCAAB added Oct 28 2026 (owner wants more bet flow; NBA volume unlock happens ~Nov 10).
    # College: bigger home edge (~3.5 pts), no reliable injury feed, gp_ref 15 so the
    # fundamental tilt is shrunk hard in the first weeks of a 350-team season.
    "ncaab": {"site": "basketball/mens-college-basketball", "hfa": 3.5, "scale": 11.5, "inj_pts": 0.0, "gp_ref": 15},
}


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def standings(league):
    """Return {abbr: {name, wins, losses, winPct, net_ppg, streak}} from ESPN v2 standings."""
    cfg = LEAGUES[league]
    d = _get(f"{V2}/{cfg['site']}/standings")
    teams = {}
    for child in d.get("children", []):
        st = child.get("standings") or {}
        for entry in st.get("entries", []):
            team = entry.get("team", {})
            abbr = team.get("abbreviation")
            stats = {s.get("name"): s.get("value") for s in entry.get("stats", [])}
            wins = stats.get("wins") or 0
            losses = stats.get("losses") or 0
            ot = stats.get("otlosses") or stats.get("ties") or 0
            played = wins + losses + ot
            win_pct = (wins + 0.5 * ot) / played if played > 0 else 0.5
            pf = stats.get("pointsFor") or stats.get("avgPointsFor") or 0
            pa = stats.get("pointsAgainst") or stats.get("avgPointsAgainst") or 0
            games = stats.get("gamesPlayed") or played or 1
            # avg-based PF/PA vs totals: ESPN gives avg for nhl/nfl, totals for nba. Normalize to per-game.
            if pf and pa and pf > games * 3:  # totals not averages
                pf, pa = pf / games, pa / games
            teams[abbr] = {
                "name": team.get("displayName"),
                "abbr": abbr,
                "wins": wins, "losses": losses, "gp": int(played),
                "winPct": win_pct,
                "pf": pf, "pa": pa,
                "netPPG": (pf - pa) if (pf and pa) else 0.0,
                "rank": stats.get("playoffSeeding") or stats.get("rank") or 15,
            }
    return teams


def injuries(league, teams_by_name=None):
    """Return {abbr: count_of_significant_injuries}. Counts Out/Doubtful/IR players."""
    cfg = LEAGUES[league]
    try:
        d = _get(f"{SITE}/{cfg['site']}/injuries")
    except Exception:
        return {}
    out = {}
    for team_block in d.get("injuries", []):
        name = team_block.get("displayName", "")
        cnt = 0
        for inj in team_block.get("injuries", []):
            status = (inj.get("status") or "").lower()
            if status in ("out", "doubtful", "ir", "injured_reserve", "out indefinitely",
                          "out for season", "doubtful/questionable") or "out" in status:
                cnt += 1
        abbr = None
        if teams_by_name:
            abbr = teams_by_name.get(name.lower())
        if abbr:
            out[abbr] = cnt
    return out


_ABBR_FIX = {
    # Cloudbet prefix -> ESPN abbreviation
    "NY": None,  # ambiguous (NYG/NYJ/NYK/NYR/NYI), resolved by second token
    "NJE": "NYJ", "NYJ": "NYJ", "NYG": "NYG", "NYK": "NYK", "NYR": "NYR", "NYI": "NYI",
    "BS": "BOS", "SA": "SAS", "GS": "GSW", "PH": None, "PHI": "PHI", "PHX": "PHX",
    "SD": "SD", "LAC": "LAC", "LAL": "LAL", "LAD": "LAD", "SF": "SF", "KC": "KC",
    "NE": "NE", "GB": "GB", "NO": "NO", "TB": "TB", "WAS": "WAS", "IND": "IND",
    "VAN": "VAN", "VGK": "VGK", "SJS": "SJS", "WSH": "WSH",
}
_ESPN_SECOND = {  # "NY <mascot>" disambiguation
    ("NY", "giants"): "NYG", ("NY", "jets"): "NYJ", ("NY", "knicks"): "NYK",
    ("NY", "rangers"): "NYR", ("NY", "islanders"): "NYI",
    ("NY", "yankees"): "NYY", ("NY", "mets"): "NYM",
    ("SA", "spurs"): "SAS", ("GS", "warriors"): "GSW", ("BS", "bruins"): "BOS",
    ("PH", "76ers"): "PHI", ("PH", "suns"): "PHX",
}


def cloudbet_to_abbr(cb_name, league_teams=None):
    """'DET Pistons' -> 'DET'; 'NY Giants' -> 'NYG' using known teams when available."""
    tokens = cb_name.split()
    if not tokens:
        return None
    first = tokens[0].upper()
    if league_teams and first in league_teams:
        return first
    if league_teams and first == "NY":
        for cand in ("NYG", "NYJ", "NYK", "NYR", "NYI"):
            if cand in league_teams:
                # disambiguate by mascot token
                masc = " ".join(tokens[1:]).lower()
                pair = _ESPN_SECOND.get(("NY", masc.split()[0] if masc else ""))
                if pair:
                    return pair
                return None
    if league_teams:
        # try second token as abbr (some leagues prefix city differently)
        for t in tokens[:2]:
            if t.upper() in league_teams:
                return t.upper()
    return _ABBR_FIX.get(first, first if len(first) <= 3 else None)


def name_to_abbr(display_name, league_teams=None):
    """ESPN injuries displayName 'Atlanta Hawks' -> find in standings by full name."""
    return None  # resolved in model layer with team dicts by name


def scoreboard_results(league, yyyymmdd):
    """Return list of {home_abbr, away_abbr, home_score, away_score, completed}."""
    cfg = LEAGUES[league]
    d = _get(f"{SITE}/{cfg['site']}/scoreboard?dates={yyyymmdd}")
    results = []
    for ev in d.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        try:
            h = [c for c in comp.get("competitors", []) if c.get("homeAway") == "home" or c.get("home")][0]
            a = [c for c in comp.get("competitors", []) if not (c.get("homeAway") == "home" or c.get("home"))][0]
            completed = comp.get("status", {}).get("type", {}).get("completed", False)
            results.append({
                "home_abbr": h["team"]["abbreviation"],
                "away_abbr": a["team"]["abbreviation"],
                "home_score": int(h.get("score", 0) or 0),
                "away_score": int(a.get("score", 0) or 0),
                "completed": bool(completed),
            })
        except (IndexError, KeyError, ValueError):
            continue
    return results
