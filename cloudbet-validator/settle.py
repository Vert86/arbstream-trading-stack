"""Settle open paper bets from ESPN results; compute CLV vs last snapshot."""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import espn_odds
from scan import load_jsonl, STATE


def _et_date(iso_utc):
    t = dt.datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    offset = -4 if t.month in range(4, 11) else -5
    return t.astimezone(dt.timezone(dt.timedelta(hours=offset))).strftime("%Y%m%d")


def settle(verbose=True):
    bets_path = os.path.join(STATE, "bets.jsonl")
    all_bets = load_jsonl(bets_path)
    snaps = load_jsonl(os.path.join(STATE, "snapshots.jsonl"))
    now = dt.datetime.now(dt.timezone.utc)
    settled, pending = [], []
    results_cache = {}

    for b in [x for x in all_bets if x.get("status") == "open"]:
        kickoff = dt.datetime.fromisoformat(b["kickoff"].replace("Z", "+00:00"))
        if kickoff > now:
            pending.append(b)
            continue
        if (now - kickoff).total_seconds() < 3 * 3600:
            pending.append(b)
            continue
        league, mk = b["league"], b["match_key"]
        et_date = _et_date(b["kickoff"])
        key = (league, et_date)
        if key not in results_cache:
            results_cache[key] = espn_odds.results(league, et_date)
        res = results_cache[key].get(mk)
        if res is None:
            if (now - kickoff).total_seconds() > 30 * 3600:
                b["status"] = "void"
                b["result"] = "void"
                settled.append(b)
            else:
                pending.append(b)
            continue
        win = res == b["selection"]
        b["status"] = "settled"
        b["result"] = "win" if win else "loss"
        b["payout"] = round(b["stake"] * b["price_taken"], 4) if win else 0.0
        b["profit"] = round(b["payout"] - b["stake"], 4)
        ev_snaps = [s for s in snaps if str(s.get("espn_id")) == str(b["event_id"])
                    and dt.datetime.fromisoformat(s["ts"].replace("Z", "+00:00")) < kickoff]
        if ev_snaps:
            last = max(ev_snaps, key=lambda s: s["ts"])
            closing = last.get("home_ml" if b["selection"] == "home" else "away_ml")
            if closing and closing > 1.01:
                b["price_closing"] = closing
                b["clv"] = round(b["price_taken"] / closing - 1, 4)
        settled.append(b)

    by_key = {(b["ts"], b.get("event_id"), b.get("selection")): dict(b) for b in settled}
    for b in all_bets:
        k = (b["ts"], b.get("event_id"), b.get("selection"))
        if k in by_key:
            b.clear()
            b.update(by_key[k])
    with open(bets_path, "w") as f:
        for b in all_bets:
            f.write(json.dumps(b) + "\n")

    if verbose:
        for b in settled:
            clv = f" | CLV {b['clv']*100:+.1f}%" if b.get("clv") is not None else ""
            print(f"settled: {b['event']} {b['selection'].upper()} {b['team']} @ {b['price_taken']}"
                  f" = {b['result'].upper()} ({b['profit']:+.2f}u){clv}")
        print(f"open bets remaining: {len(pending)}")
    return settled, pending


if __name__ == "__main__":
    settle()
