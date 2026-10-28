"""Scan v2: ESPN(DraftKings) odds + fundamentals -> paper bets. Paper-only, no real money.
Odds venue note: validates the MODEL against a sharp US book (harder test than Cloudbet).
Cloudbet API is temporarily degraded (main-market coverage, see state/cloudbet_api_status.md)
and is kept as a dormant snapshot source until coverage returns."""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import espn
import espn_odds
import model

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
os.makedirs(STATE, exist_ok=True)

ESPN_CACHE_SEC = 3600
BETS_PER_SCAN = 3
LOOKAHEAD_H = 72
BANKROLL_U = 100.0
STAKE_U = 1.0


def _jsonl_append(path, rows):
    with open(path, "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def _espn_cached(league, kind):
    path = os.path.join(STATE, f"espn_{league}_{kind}.json")
    if os.path.exists(path) and dt.datetime.now().timestamp() - os.path.getmtime(path) < ESPN_CACHE_SEC:
        with open(path) as f:
            return json.load(f)
    if kind == "standings":
        data = espn.standings(league)
    else:
        st = _espn_cached(league, "standings")
        by_name = {v["name"].lower(): k for k, v in st.items()}
        data = espn.injuries(league, by_name)
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def open_bets():
    return [b for b in load_jsonl(os.path.join(STATE, "bets.jsonl")) if b.get("status") == "open"]


def scan(verbose=True):
    now = dt.datetime.now(dt.timezone.utc)
    bets = open_bets()
    open_ids = {b["event_id"] for b in bets}
    snap_rows, new_bets, log, edge_rows = [], [], [], []
    shadow_bets = []
    shadow_open = load_jsonl(os.path.join(STATE, "shadow_bets.jsonl"))
    shadow_ids = {b["event_id"] for b in shadow_open if b.get("status") == "open"}

    for league in ("nfl", "nba", "nhl"):
        try:
            teams = _espn_cached(league, "standings")
            inj = _espn_cached(league, "injuries")
            games = espn_odds.upcoming_games(league, days_ahead=3)
        except Exception as e:
            log.append(f"[{league}] fetch failed: {e}")
            continue
        for g in games:
            try:
                kickoff = dt.datetime.fromisoformat(g["kickoff"].replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            hours = (kickoff - now).total_seconds() / 3600
            if hours < 0.5 or hours > LOOKAHEAD_H:
                continue
            gid = str(g["espn_id"])
            snap_rows.append({
                "ts": now.isoformat(), "league": league, "espn_id": gid,
                "match": f"{g['away_abbr']}@{g['home_abbr']}",
                "kickoff": g["kickoff"], "home_ml": g["home_ml"], "away_ml": g["away_ml"],
                "spread": g["spread"], "total": g["total"], "provider": g["provider"],
            })
            if gid in open_ids or gid in shadow_ids:
                continue
            h_abbr, a_abbr = g["home_abbr"], g["away_abbr"]
            h = teams.get(h_abbr)
            a = teams.get(a_abbr)
            if not h or not a:
                continue
            inv = {g["home_ml"]: "home", g["away_ml"]: "away"}
            overround = 1 / g["home_ml"] + 1 / g["away_ml"]
            fair = {"home": (1 / g["home_ml"]) / overround, "away": (1 / g["away_ml"]) / overround}
            prices = {"home": g["home_ml"], "away": g["away_ml"]}
            ev = model.evaluate(league, teams, inj, h_abbr, a_abbr, fair, prices)
            if not ev:
                continue
            for side, rec in ev.items():
                edge_rows.append({
                    "ts": now.isoformat(), "league": league, "espn_id": gid,
                    "match": f"{a_abbr}@{h_abbr}", "kickoff": g["kickoff"],
                    "selection": side, "price": rec["price"], "fair": rec["fair"],
                    "p_fund": rec["p_fund"], "p_model": rec["p_model"], "edge": rec["edge"],
                })
            for side, rec in ev.items():
                if (model.MIN_EDGE <= rec["edge"] <= model.MAX_EDGE
                        and model.ODDS_MIN <= rec["price"] <= model.ODDS_MAX):
                    new_bets.append({
                        "ts": now.isoformat(), "league": league, "event_id": gid,
                        "event": f"{a_abbr} @ {h_abbr}", "kickoff": g["kickoff"],
                        "market": "moneyline", "selection": side, "mv": model.MODEL_VERSION,
                        "team": h_abbr if side == "home" else a_abbr,
                        "match_key": f"{a_abbr}@{h_abbr}",
                        "price_taken": rec["price"], "fair_prob": rec["fair"],
                        "p_fund": rec["p_fund"], "p_model": rec["p_model"],
                        "edge": rec["edge"], "stake": STAKE_U,
                        "status": "open", "result": None, "payout": None,
                        "profit": None, "clv": None, "price_closing": None,
                    })
                    break  # one bet per event
            if model.SHADOW_EDGE_MIN <= min(
                    (r["edge"] for r in ev.values()), default=-1) < model.MIN_EDGE:
                # near-miss shadow: best side with edge in [0.5%, 2%), odds in band.
                # Settled like real paper bets but tagged shadow — measures whether
                # the +2% floor is calibrated, NEVER counts toward the gate cohort.
                best = max(ev.values(), key=lambda r: r["edge"])
                if model.ODDS_MIN <= best["price"] <= model.ODDS_MAX:
                    shadow_bets.append({
                        "ts": now.isoformat(), "league": league, "event_id": gid,
                        "event": f"{a_abbr} @ {h_abbr}", "kickoff": g["kickoff"],
                        "market": "moneyline", "selection": None, "shadow": True,
                        "mv": model.MODEL_VERSION,
                        "team": None, "match_key": f"{a_abbr}@{h_abbr}",
                        "selection": max(ev, key=lambda s: ev[s]["edge"]),
                        "team": (h_abbr if max(ev, key=lambda s: ev[s]["edge"]) == "home" else a_abbr),
                        "price_taken": best["price"], "fair_prob": best["fair"],
                        "p_fund": best["p_fund"], "p_model": best["p_model"],
                        "edge": best["edge"], "stake": STAKE_U,
                        "status": "open", "result": None, "payout": None,
                        "profit": None, "clv": None, "price_closing": None,
                    })

    if new_bets:
        new_bets = new_bets[:BETS_PER_SCAN]
        _jsonl_append(os.path.join(STATE, "bets.jsonl"), new_bets)
    if shadow_bets:
        _jsonl_append(os.path.join(STATE, "shadow_bets.jsonl"), shadow_bets)
    if snap_rows:
        _jsonl_append(os.path.join(STATE, "snapshots.jsonl"), snap_rows)
    if edge_rows:
        _jsonl_append(os.path.join(STATE, "edges.jsonl"), edge_rows)

    if verbose:
        print(f"scan {now.isoformat()}: {len(snap_rows)} priced games, {len(new_bets)} new bets, {len(shadow_bets)} shadow near-miss")
        for b in new_bets:
            print(f"  BET: {b['event']} -> {b['selection'].upper()} {b['team']} @ {b['price_taken']}"
                  f" | p_model {b['p_model']} vs implied {round(1/b['price_taken'],3)}"
                  f" | edge {b['edge']*100:+.1f}% | stake {b['stake']}u")
        for line in log:
            print(" ", line)
    return new_bets


if __name__ == "__main__":
    scan()
