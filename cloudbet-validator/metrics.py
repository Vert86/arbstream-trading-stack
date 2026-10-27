"""Metrics: honest performance report on settled paper bets."""
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan import load_jsonl, STATE


def report(min_n=30):
    bets = [b for b in load_jsonl(os.path.join(STATE, "bets.jsonl")) if b.get("status") == "settled"]
    if not bets:
        print("No settled bets yet.")
        return
    n = len(bets)
    wins = sum(1 for b in bets if b["result"] == "win")
    staked = sum(b["stake"] for b in bets)
    profit = sum(b["profit"] for b in bets)
    roi = profit / staked if staked else 0
    clvs = [b["clv"] for b in bets if b.get("clv") is not None]
    avg_clv = sum(clvs) / len(clvs) if clvs else None
    pos_clv = sum(1 for c in clvs if c > 0) if clvs else 0
    by_league = defaultdict(lambda: [0, 0, 0.0])
    for b in bets:
        d = by_league[b["league"]]
        d[0] += 1
        if b["result"] == "win":
            d[1] += 1
        d[2] += b["profit"]
    print(f"=== PAPER BETTING SCORECARD ===")
    print(f"settled: {n} | win rate: {wins/n*100:.1f}% | avg odds: "
          f"{sum(b['price_taken'] for b in bets)/n:.2f}")
    print(f"staked: {staked:.1f}u | profit: {profit:+.2f}u | ROI: {roi*100:+.2f}%")
    if avg_clv is not None:
        print(f"CLV: avg {avg_clv*100:+.2f}% | positive on {pos_clv}/{len(clvs)} "
              f"(positive CLV is the leading indicator of a real edge)")
    for lg, (cnt, w, p) in sorted(by_league.items()):
        print(f"  {lg}: {cnt} bets, {w} wins, {p:+.2f}u")
    print(f"(validation gate: n>={min_n} settled AND positive ROI AND positive avg CLV)")


if __name__ == "__main__":
    report()
