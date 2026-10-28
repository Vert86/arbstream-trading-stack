"""Settle open paper bets from ESPN results; compute CLV vs last snapshot.
v5 Oct 28: settles BOTH ledgers — the real gate cohort (bets.jsonl) and the
near-miss shadow cohort (shadow_bets.jsonl) — in separate files."""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import espn_odds
from scan import load_jsonl, STATE

RESULTS_CACHE = {}


def _et_date(iso_utc):
    t = dt.datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    offset = -4 if t.month in range(4, 11) else -5
    return t.astimezone(dt.timezone(dt.timedelta(hours=offset))).strftime("%Y%m%d")


def _get_results(league, et_date):
    key = (league, et_date)
    if key not in RESULTS_CACHE:
        RESULTS_CACHE[key] = espn_odds.results(league, et_date)
    return RESULTS_CACHE[key]


def settle_file(fname, snaps, now, verbose):
    bets_path = os.path.join(STATE, fname)
    all_bets = load_jsonl(bets_path)
    settled, pending = [], []
    for b in [x for x in all_bets if x.get("status") == "open"]:
        kickoff = dt.datetime.fromisoformat(b["kickoff"].replace("Z", "+00:00"))
        if kickoff > now:
            pending.append(b)
            continue
        if (now - kickoff).total_seconds() < 3 * 3600:
            pending.append(b)
            continue
        league, mk = b["league"], b["match_key"]
        res = _get_results(league, _et_date(b["kickoff"])).get(mk)
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
        tag = " [SHADOW]" if fname == "shadow_bets.jsonl" else ""
        for b in settled:
            clv = f" | CLV {b['clv']*100:+.1f}%" if b.get("clv") is not None else ""
            print(f"settled{tag}: {b['event']} {b['selection'].upper()} {b['team']} @ {b['price_taken']}"
                  f" = {b['result'].upper()} ({b['profit']:+.2f}u){clv}")
        print(f"open in {fname}: {len(pending)}")
    return settled, pending


def settle(verbose=True):
    now = dt.datetime.now(dt.timezone.utc)
    snaps = load_jsonl(os.path.join(STATE, "snapshots.jsonl"))
    a = settle_file("bets.jsonl", snaps, now, verbose)
    b = settle_file("shadow_bets.jsonl", snaps, now, verbose)
    return a, b


if __name__ == "__main__":
    settle()
