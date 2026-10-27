#!/usr/bin/env python3
"""Wallet edge study: leaderboard pull + per-wallet luck testing.

Binomial luck test: for each wallet's settled binary positions, the
no-information null says bet i wins with prob = its entry price p_i.
z = (W - sum(p)) / sqrt(sum(p*(1-p))).  |z| > 2 = unlikely to be luck.
"""
import json
import time
import requests
import numpy as np

DATA = "https://data-api.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
OUT = "/app/conversations/6ab927997f5a926229b9b171/polymarket/data"

SPORT = ("nba","nfl","serie","la-liga","epl","mlb","nhl","ufc","tennis","f1","soccer","lol","csgo","dota","cfl","ncaa","premier","champ","boxing","cs2","valorant")
POLIT = ("pres","senate","house","gov","mayor","uk-","fr-","ge-","minister","election","balance-of-power","cabinet","nominee","primary","party")

def get(url, params=None):
    for i in range(3):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=30)
            if r.status_code == 429:
                time.sleep(15); continue
            r.raise_for_status()
            return r.json() if r.text.strip() else None
        except Exception:
            time.sleep(4)
    return None

def board(window, max_rows=1000):
    rows, cursor, guard = [], None, 0
    while len(rows) < max_rows and guard < 25:
        p = {"window": window, "limit": 100}
        if cursor:
            p["cursor"] = cursor
        d = get(DATA + "/v2/leaderboard", p)
        if not d or not d.get("data"):
            break
        rows.extend(d["data"])
        cursor = (d.get("pagination") or {}).get("next_cursor")
        guard += 1
        if not cursor:
            break
        time.sleep(0.4)
    return rows

def cat(slug):
    s = (slug or "").lower()
    if any(k in s for k in SPORT): return "sports"
    if any(k in s for k in POLIT): return "politics"
    if any(k in s for k in ("btc","eth","sol","crypto","token","coin")): return "crypto"
    return "other"

def activity(addr, max_pages=10):
    acts, offset = [], 0
    while offset < 500 * max_pages:
        d = get(DATA + "/activity", {"user": addr, "limit": 500, "offset": offset})
        if not d:
            break
        acts.extend(d)
        if len(d) < 500:
            break
        offset += 500
        time.sleep(0.3)
    return acts


def wallet_stats(addr):
    acts = activity(addr)
    # entry map: weighted avg buy price per asset from TRADEs
    buys = {}
    wins_redeem, p_for_wins = [], []
    for a in acts:
        if a.get("type") == "TRADE" and a.get("price") is not None:
            key = a.get("asset")
            sz = float(a.get("size") or 0) * float(a.get("price") or 0)
            b = buys.setdefault(key, [0.0, 0.0])
            b[0] += sz; b[1] += float(a.get("size") or 0)
        elif a.get("type") == "REDEEM" and float(a.get("usdcSize") or 0) > 0:
            # each redeemed share paid $1 -> winning bet
            n = float(a.get("size") or 0)
            if n <= 0:
                continue
            bb = buys.get(a.get("asset"))
            p = min(0.98, max(0.02, bb[0] / bb[1])) if bb and bb[1] > 0 else 0.5
            wins_redeem.append(n)
            p_for_wins.append(p)          # ONE bet per redeem event
    pos, offset = [], 0
    while offset < 2000:
        d = get(DATA + "/positions", {"user": addr, "limit": 500, "offset": offset})
        if not d:
            break
        pos.extend(d)
        if len(d) < 500:
            break
        offset += 500
        time.sleep(0.3)
    settled = [p for p in pos if p.get("curPrice") in (0.0, 0.0) or
               (p.get("curPrice") is not None and float(p.get("curPrice")) in (0.0, 1.0))]
    # robust: settle = curPrice 0 or 1 AND initialValue > 0
    # losses: settled-to-zero positions; winners held: settled-to-one
    losers = [p for p in pos if p.get("initialValue")
              and p.get("curPrice") is not None
              and float(p["curPrice"]) == 0.0
              and float(p.get("realizedPnl") or 0) < 0]
    held_wins = [p for p in pos if p.get("initialValue")
                 and p.get("curPrice") is not None
                 and float(p["curPrice"]) == 1.0
                 and float(p.get("realizedPnl") or 0) > 0]
    n_bet = len(losers) + len(held_wins) + len(wins_redeem)
    if n_bet < 5:
        return None
    wins = len(held_wins) + len(wins_redeem)
    ps = ([min(0.98, max(0.02, float(p.get("avgPrice") or 0.5))) for p in losers]
          + [min(0.98, max(0.02, float(p.get("avgPrice") or 0.5))) for p in held_wins]
          + p_for_wins)
    exp_w = sum(ps); var = sum(p * (1 - p) for p in ps)
    z = (wins - exp_w) / (max(var, 1e-9) ** 0.5) if var > 0 else 0.0
    pnl = sum(float(p.get("realizedPnl") or 0) for p in losers + held_wins)
    abs_pnl = sum(abs(float(p.get("realizedPnl") or 0)) for p in losers + held_wins)
    top_share = 0.0
    if losers + held_wins:
        best = max(abs(float(p.get("realizedPnl") or 0)) for p in losers + held_wins)
        top_share = best / abs_pnl if abs_pnl > 0 else 0.0
    cats = {}
    for p in pos:
        c = cat(p.get("slug"))
        cats[c] = cats.get(c, 0) + abs(float(p.get("initialValue") or 0))
    return {"n_settled": n_bet, "wins": wins,
            "win_rate": round(wins / n_bet, 3),
            "z_luck": round(z, 2), "settled_pnl": round(pnl, 0),
            "top_pnl_share": round(top_share, 2),
            "cat_mix": {k: round(v / max(sum(cats.values()), 1), 2) for k, v in cats.items()},
            "n_positions": len(pos), "n_redeem_wins": len(wins_redeem)}

def main():
    import os
    os.makedirs(OUT, exist_ok=True)
    b90, b30, b7 = board("90d"), board("30d"), board("7d")
    for name, b in (("90d", b90), ("30d", b30), ("7d", b7)):
        with open(f"{OUT}/wallets_{name}.json", "w") as f:
            json.dump(b, f)
    print(f"leaderboards: 90d={len(b90)} 30d={len(b30)} 7d={len(b7)}")

    top90 = {w["user_id"]: w for w in b90}
    t30 = {w["user_id"] for w in b30[:100]}
    t7 = {w["user_id"] for w in b7[:100]}
    t90_top100 = {w["user_id"] for w in b90[:100]}
    persist_30 = len(t90_top100 & t30) / max(len(t30), 1)
    persist_7 = len(t30 & t7) / max(len(t7), 1)
    print(f"persistence: {persist_30:.0%} of top-30d in top-90d; "
          f"{persist_7:.0%} of top-7d in top-30d")

    # deep dive: top 25 by 90d pnl + top 10 by volume
    by_pnl = sorted(b90, key=lambda w: -w.get("pnl", 0))[:25]
    by_vol = sorted(b90, key=lambda w: -w.get("volume", 0))[:10]
    seen, results = set(), []
    for w in by_pnl + by_vol:
        if w["user_id"] in seen:
            continue
        seen.add(w["user_id"])
        st = wallet_stats(w["user_id"])
        if st:
            st.update({"name": w.get("user_name") or w["user_id"][:10],
                       "addr": w["user_id"], "lb_pnl": round(w.get("pnl", 0), 0),
                       "lb_vol": round(w.get("volume", 0), 0)})
            results.append(st)
        print(f"  done {len(seen)}: {st['name'] if st else 'no data'}", flush=True)
    results.sort(key=lambda r: -r["z_luck"])
    with open(f"{OUT}/wallet_stats.json", "w") as f:
        json.dump(results, f, indent=1)
    print(f"\n=== DEEP STATS for {len(results)} wallets (sorted by luck z) ===")
    hdr = f"{'name':<16}{'bets':>5}{'winR':>6}{'z':>6}{'pnl90d':>10}{'top%':>6}  cats"
    print(hdr)
    for r in results[:30]:
        cats = ",".join(f"{k}{int(v*100)}%" for k, v in sorted(r["cat_mix"].items(), key=lambda x: -x[1])[:2])
        print(f"{r['name'][:15]:<16}{r['n_settled']:>5}{r['win_rate']:>6}"
              f"{r['z_luck']:>6}{r['lb_pnl']:>10}{r['top_pnl_share']:>6}  {cats}")

if __name__ == "__main__":
    main()
