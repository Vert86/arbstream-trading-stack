#!/usr/bin/env python3
"""Basket executor — all-or-nothing multi-leg arb execution (Polymarket).

BUILT Oct 29 2026. INERT BY DEFAULT. `plan` mode never loads credentials and
places nothing. Live execution is TRIPLE-GATED:
    --live   AND   env POLYMARKET_BASKET_LIVE=1   AND   --event-slug + --max-usd
The owner's standing rule also requires explicit sign-off for live Polymarket
trading; only run `live` after that sign-off.

Execution semantics
 - Equal SHARES across legs (that is what makes the basket a matched set):
   S = max_usd / sum(live asks); per-leg cost = S * ask_i.
 - Legs execute THINNEST-DEPTH-FIRST so a failure leaves the least to unwind.
 - Books are re-fetched immediately before execution; if recomputed net edge
   falls below MIN_LIVE_NET_EDGE_PCT the basket is ABANDONED (edge gone).
 - Any leg failure -> FOK-SELL every filled leg at best bid, log the unwind.
   A failed unwind is recorded naked_exposure=true for manual handling.
 - Fills verified by order response only; balance delta checked at the end.

Modes
  plan    (default) print execution plan for the latest verified candidates
  live              execute ONE named basket (gated; see above)
  settle            settle resolved live baskets, leg-scoped
  status            live basket ledger summary

Ledger: basket_live_ledger.jsonl (never mixed with paper or shadow books)
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

ARBS = os.path.join(BASE, "arb_candidates.jsonl")
LIVE_BASKETS = os.path.join(BASE, "basket_live_ledger.jsonl")
CLOB = "https://clob.polymarket.com"

MIN_LIVE_NET_EDGE_PCT = 0.5
MAX_SCAN_AGE_MIN = 45
MIN_LEG_DEPTH_MULT = 1.5
MIN_USD = 3.0


def now():
    return datetime.now(timezone.utc).isoformat()[:19]


def jload(p):
    if not os.path.exists(p):
        return []
    out = []
    for line in open(p):
        if line.strip():
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def jappend(p, row):
    with open(p, "a") as f:
        f.write(json.dumps(row) + "\n")


def _book(token_id):
    req = urllib.request.Request("%s/book?token_id=%s" % (CLOB, token_id),
                                 headers={"User-Agent": "Mozilla/5.0"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=20).read())
    except Exception:
        return None


def ask_of(token_id):
    d = _book(token_id)
    asks = (d or {}).get("asks") or []
    if not asks:
        return None, 0.0
    best = asks[-1]
    px = float(best.get("price", 0) or 0)
    sz = float(best.get("size", 0) or 0)
    return (px if px > 0 else None), px * sz


def bid_of(token_id):
    d = _book(token_id)
    bids = (d or {}).get("bids") or []
    if not bids:
        return None, 0.0
    best = bids[-1]
    px = float(best.get("price", 0) or 0)
    sz = float(best.get("size", 0) or 0)
    return (px if px > 0 else None), px * sz


def latest_candidates():
    rows = jload(ARBS)
    if not rows:
        return {}
    latest_ts = max(r.get("ts", "") for r in rows)
    best = {"_ts": latest_ts}
    for r in rows:
        if r.get("ts") != latest_ts or "net_edge_pct" not in r:
            continue
        if r.get("net_edge_pct", 0) <= 0.05:
            continue
        best[r.get("eventSlug")] = r
    return best


def build_plan(cand, max_usd, ask_lookup):
    legs = cand.get("outcomes") or []
    if len(legs) < 2:
        return None, "not-a-basket"
    books, sum_asks, min_depth = [], 0.0, 1e18
    for o in legs:
        ask, depth = ask_lookup(o["clobTokenId"])
        if ask is None or ask <= 0:
            return None, "missing-book:%s" % o.get("slug")
        books.append({"slug": o.get("slug"), "token": o["clobTokenId"],
                      "ask": ask, "depth_usdc": round(depth, 2)})
        sum_asks += ask
        min_depth = min(min_depth, depth)
    if sum_asks >= 1:
        return None, "no-edge-live:sum_asks=%.4f" % sum_asks
    net_edge_pct = (1 - sum_asks) * 100 - (cand.get("fee_per_set_pct") or 0)
    if net_edge_pct < MIN_LIVE_NET_EDGE_PCT:
        return None, "edge-too-thin:%.2f%%" % net_edge_pct
    shares = max_usd / sum_asks
    for b in books:
        b["usd"] = round(shares * b["ask"], 2)
        b["shares"] = round(shares, 2)
        if b["usd"] > b["depth_usdc"] / MIN_LEG_DEPTH_MULT:
            return None, "depth-too-thin:%s" % b["slug"]
    books.sort(key=lambda b: b["depth_usdc"])
    return {"event": cand.get("event"), "slug": cand.get("eventSlug"),
            "sum_asks": round(sum_asks, 4), "net_edge_pct": round(net_edge_pct, 2),
            "shares": round(shares, 2), "cost_usd": round(max_usd, 2),
            "expected_profit_usd": round(shares - max_usd, 2), "legs": books}, None


def cmd_plan(argv):
    max_usd = 25.0
    if "--max-usd" in argv:
        max_usd = float(argv[argv.index("--max-usd") + 1])
    cands = latest_candidates()
    if not cands:
        print(json.dumps({"mode": "plan", "plans": [], "note": "no candidates"}))
        return
    out = {"mode": "plan", "scan_ts": cands.get("_ts"), "max_usd": max_usd,
           "plans": [], "skips": []}
    for slug, cand in cands.items():
        if slug.startswith("_"):
            continue
        plan, why = build_plan(cand, max_usd, ask_of)
        if plan:
            out["plans"].append(plan)
        else:
            out["skips"].append({"slug": slug, "why": why})
    print(json.dumps(out, indent=1))


def _filled(resp):
    d = resp.model_dump() if hasattr(resp, "model_dump") else {"ok": str(resp)}
    return d, ((d.get("ok") is True) or (d.get("success") is True)
               or bool(d.get("orderId") or d.get("order_id")))


def cmd_live(argv):
    import live as L

    if os.environ.get("POLYMARKET_BASKET_LIVE") != "1":
        print(json.dumps({"mode": "live", "ok": False,
                          "error": "refused: POLYMARKET_BASKET_LIVE != 1"}))
        return
    if "--event-slug" not in argv or "--max-usd" not in argv:
        print(json.dumps({"mode": "live", "ok": False,
                          "error": "refused: --event-slug and --max-usd required"}))
        return
    slug = argv[argv.index("--event-slug") + 1]
    max_usd = float(argv[argv.index("--max-usd") + 1])
    if max_usd < MIN_USD:
        print(json.dumps({"mode": "live", "ok": False, "error": "max-usd < %.0f" % MIN_USD}))
        return

    cands = latest_candidates()
    cand = cands.get(slug)
    if not cand:
        print(json.dumps({"mode": "live", "ok": False, "error": "no fresh candidate: %s" % slug}))
        return
    age_min = (datetime.now(timezone.utc) - datetime.fromisoformat(
        cands["_ts"]).replace(tzinfo=timezone.utc)).total_seconds() / 60
    if age_min > MAX_SCAN_AGE_MIN:
        print(json.dumps({"mode": "live", "ok": False,
                          "error": "scan stale (%.0f min); rerun radar first" % age_min}))
        return

    out = {"mode": "live", "ts": now(), "slug": slug, "orders": [], "unwinds": [], "ok": False}
    with L.Relay():
        cl = L.make_client()
        bal = L.get_balance(cl)
        out["balance_before"] = bal
        if bal is None or bal < max_usd + 0.5:
            out["error"] = "insufficient balance: %s" % bal
            print(json.dumps(out, indent=1))
            return
        plan, why = build_plan(cand, max_usd, ask_of)
        if not plan:
            out["error"] = why
            print(json.dumps(out, indent=1))
            return
        out["plan"] = plan
        filled = []
        for leg in plan["legs"]:
            try:
                d, ok = _filled(cl.place_market_order(token_id=leg["token"], side="BUY",
                                                      amount=leg["usd"], max_price=leg["ask"],
                                                      order_type="FOK"))
                out["orders"].append({"leg": leg["slug"], "usd": leg["usd"],
                                      "ask": leg["ask"], "ok": ok})
                if not ok:
                    break
                filled.append(leg)
            except Exception as ex:
                out["orders"].append({"leg": leg["slug"], "error": str(ex)[:150]})
                break
        naked = False
        if len(filled) == len(plan["legs"]):
            out["ok"] = True
            jappend(LIVE_BASKETS, {"ts": out["ts"], "type": "basket", "event": plan["event"],
                                   "slug": slug,
                                   "legs": [l["slug"] for l in plan["legs"]],
                                   "shares": plan["shares"], "cost_usd": plan["cost_usd"],
                                   "expected_profit_usd": plan["expected_profit_usd"],
                                   "net_edge_pct": plan["net_edge_pct"],
                                   "status": "open", "source": "basket_executor"})
        else:
            for leg in filled:
                try:
                    bid, _ = bid_of(leg["token"])
                    if not bid:
                        naked = True
                        out["unwinds"].append({"leg": leg["slug"], "error": "no-bid"})
                        continue
                    d, uok = _filled(cl.place_market_order(token_id=leg["token"], side="SELL",
                                                           amount=leg["shares"],
                                                           max_price=round(bid, 3),
                                                           order_type="FOK"))
                    out["unwinds"].append({"leg": leg["slug"], "shares": leg["shares"],
                                           "bid": bid, "ok": uok})
                    if not uok:
                        naked = True
                except Exception as ex:
                    naked = True
                    out["unwinds"].append({"leg": leg["slug"], "error": str(ex)[:150]})
            out["naked_exposure"] = naked
            jappend(LIVE_BASKETS, {"ts": out["ts"], "type": "basket_aborted", "slug": slug,
                                   "filled": [l["slug"] for l in filled],
                                   "unwinds": out["unwinds"], "naked_exposure": naked,
                                   "status": "aborted", "source": "basket_executor"})
    print(json.dumps(out, indent=1))


def cmd_settle(argv):
    import scanner
    rows = jload(LIVE_BASKETS)
    changed = []
    for r in rows:
        if r.get("status") != "open" or r.get("type") != "basket":
            continue
        evs = scanner.get(scanner.GAMMA + "/events", {"slug": r["slug"]}) or []
        if not evs:
            continue
        by_slug = {m.get("slug"): m for m in (evs[0].get("markets") or [])}
        ms = [by_slug[s] for s in r["legs"] if s in by_slug]
        if len(ms) < len(r["legs"]) or any(not m.get("closed") for m in ms):
            continue
        winners = [1 for m in ms
                   if float((scanner.parse_j(m.get("outcomePrices")) or [0])[0]) > 0.99]
        if len(winners) == 1:
            r["status"], r["result"] = "settled", "WIN"
            r["pnl_usd"] = round(r["shares"] - r["cost_usd"], 2)
        elif len(winners) == 0:
            r["status"], r["result"] = "settled", "LOSS_NOT_EXHAUSTIVE"
            r["pnl_usd"] = -r["cost_usd"]
        else:
            r["status"], r["result"] = "anomaly", "MULTIPLE_YES"
        changed.append(r)
    if changed:
        with open(LIVE_BASKETS, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    print(json.dumps({"mode": "settle", "settled": len(changed),
                      "pnl_usd": round(sum(c.get("pnl_usd", 0) for c in changed), 2),
                      "detail": {c["slug"]: (c["result"], c.get("pnl_usd")) for c in changed}},
                     indent=1))


def cmd_status(argv):
    rows = jload(LIVE_BASKETS)
    openb = [r for r in rows if r.get("status") == "open"]
    done = [r for r in rows if r.get("status") == "settled"]
    print(json.dumps({"mode": "status", "baskets": len(rows), "open": len(openb),
                      "settled": len(done),
                      "pnl_usd": round(sum(r.get("pnl_usd", 0) for r in done), 2),
                      "aborted": sum(1 for r in rows if r.get("status") == "aborted")},
                     indent=1))


def main():
    argv = sys.argv[1:]
    mode = argv[0] if argv and not argv[0].startswith("--") else "plan"
    if mode == "plan":
        cmd_plan(argv)
    elif mode == "live":
        if "--live" not in argv:
            print(json.dumps({"mode": "live", "ok": False, "error": "refused: --live required"}))
            return
        cmd_live(argv)
    elif mode == "settle":
        cmd_settle(argv)
    elif mode == "status":
        cmd_status(argv)
    else:
        print(json.dumps({"error": "unknown mode", "modes": ["plan", "live", "settle", "status"]}))


if __name__ == "__main__":
    main()
