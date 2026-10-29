#!/usr/bin/env python3
"""Polymarket Radar — paper-mode scanner.

Modes:
  scan    : pull live markets, detect multi-outcome arb + pair arb, log candidates
  news    : pull headlines, match to markets, flag resolution-implied mispricings
  spreads: log bid-ask spreads + depth on top-volume markets (MM opportunity data)
  status  : read-only summary of paper ledger

PAPER ONLY. Never places orders. Live execution requires explicit owner
authorization (standing rule) and is structurally not implemented here.
"""
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

BASE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(BASE, "paper_ledger.jsonl")
ARBS = os.path.join(BASE, "arb_candidates.jsonl")
SPREADS = os.path.join(BASE, "spread_ledger.jsonl")

ARB_THRESH = 0.005      # 0.5% gross deviation before book verification
NEWS_MATCH_MIN = 3      # token overlap for a headline-market match
BANKROLL = 500.0        # paper bankroll, USDC
MAX_KELLY_FRAC = 0.25   # owner's standing concentration cap
MAX_DEPTH_USDC = 200.0  # cap paper size at book depth


def get(url, params=None, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=30)
            if r.status_code == 429:
                time.sleep(20 * (i + 1)); continue
            r.raise_for_status()
            return r.json() if r.text.strip() else {}
        except Exception:
            if i == tries - 1:
                return None
            time.sleep(5 * (i + 1))
    return None


def pull_events(max_events=1200, quiet=True):
    """All open events with their markets (multi-outcome groups)."""
    events, offset, budget = [], 0, time.time() + 240
    while len(events) < max_events and time.time() < budget:
        batch = get(GAMMA + "/events", {"closed": "false", "active": "true",
                                        "limit": 100, "offset": offset})
        if not batch:
            break
        events.extend(batch)
        if len(batch) < 100:
            break
        offset += 100
    return events


def parse_j(x):
    try:
        return json.loads(x) if isinstance(x, str) else x
    except Exception:
        return []


def detect_arb(events):
    """Multi-outcome basket arb on mid prices, then CLOB book verification."""
    candidates = []
    for ev in events:
        markets = ev.get("markets") or []
        if len(markets) < 2:
            continue
        # Only mutually-exclusive AND collectively-exhaustive groups qualify:
        # negRisk enforces at-most-one YES; "at least one" needs a guaranteed
        # winner (contest/election/match). Date ladders ("out by March") and
        # multi-select markets can all resolve NO -> NOT risk-free.
        title = (ev.get("title") or "")
        if not ev.get("negRisk"):
            continue
        exhaustive = any(p in title.lower() for p in (
            "champion", "winner", "mvp", "wins", "award", "vs", "vote",
            "election", "senate", "house", "president", "party", "nominee",
            "gold medal", "title", "finish"))
        if not exhaustive:
            continue
        yes_mids, meta = [], []
        open_markets = [m for m in markets
                        if not m.get("closed") and m.get("active", True)]
        for m in open_markets:
            prices = parse_j(m.get("outcomePrices"))
            tokens = parse_j(m.get("clobTokenIds"))
            if not prices or not tokens or len(tokens) < 2:
                continue          # unpriceable member -> basket incomplete
            yes_mids.append(float(prices[0]))   # token[0] = YES side
            meta.append({"question": m.get("question"), "slug": m.get("slug"),
                         "clobTokenId": tokens[0], "mid": float(prices[0])})
        if len(yes_mids) < 2 or len(yes_mids) != len(open_markets):
            continue              # skip events with unpriced/unlisted outcomes
        s = sum(yes_mids)
        n = len(yes_mids)
        if s < 0.90 or s > 1.10:
            continue              # deep deviation = structural, not arb
        if s < 1 - ARB_THRESH:
            direction, edge = "BUY_ALL_YES", 1 - s
        elif s > 1 + ARB_THRESH:
            direction, edge = "BUY_ALL_NO", s - 1
        else:
            continue
        ends = [m.get("endDate") for m in markets if m.get("endDate")]
        candidates.append({"event": ev.get("title"), "eventSlug": ev.get("slug"),
                           "negRisk": ev.get("negRisk"), "n_outcomes": n,
                           "mid_sum": round(s, 4), "direction": direction,
                           "gross_edge_pct": round(edge * 100, 2),
                           "end_date": max(ends) if ends else None,
                           "outcomes": meta})
    return candidates


MAX_OUTCOMES_CHECK = 16


def verify_with_books(cand, max_check=None):
    """Executable edge: verify ALL outcome books; best bid/ask = last element."""
    if len(cand["outcomes"]) > MAX_OUTCOMES_CHECK:
        return None
    asks, bids, depth_ok, depth_usdc = [], [], True, 0.0
    for o in cand["outcomes"]:
        book = get(CLOB + "/book", {"token_id": o["clobTokenId"]})
        if not book or not book.get("asks") or not book.get("bids"):
            depth_ok = False
            break
        best_ask = float(book["asks"][-1].get("price", 1))
        best_bid = float(book["bids"][-1].get("price", 0))
        asks.append(best_ask)
        bids.append(best_bid)
        depth_usdc = min(depth_usdc + float(book["asks"][-1].get("size", 0)) * best_ask,
                         1e9)
    if not depth_ok:
        return None
    sum_ask, sum_bid = sum(asks), sum(bids)
    n = len(asks)
    if cand["direction"] == "BUY_ALL_YES":
        exec_edge = 1 - sum_ask
        side = f"buy all YES at asks (sum {sum_ask:.4f})"
    else:
        exec_edge = sum_bid - 1   # buy NO basket = n - sum(bid) cost for n-1 payout
        side = f"buy all NO at asks (YES bids sum {sum_bid:.4f})"
    fee_per_set = sum(fee_rate_for(cand.get("slug")) * p * (1 - p) for p in asks)
    net_edge = exec_edge - fee_per_set
    # annualize over lockup vs idle-USDC baseline (owner, Oct 27 2026):
    # 0.05% net over months loses to doing nothing
    end_date = cand.get("end_date")
    lockup_days = 90.0
    if end_date:
        try:
            from datetime import datetime as _dt
            lockup_days = (_dt.fromisoformat(end_date.replace("Z", "")) - _dt.utcnow()).total_seconds() / 86400
        except Exception:
            pass
    lockup_days = max(lockup_days, 1.0)
    ann = net_edge * 365.0 / lockup_days
    return {"exec_edge_pct": round(exec_edge * 100, 2),
            "fee_per_set_pct": round(fee_per_set * 100, 2),
            "net_edge_pct": round(net_edge * 100, 2), "side": side,
            "lockup_days_est": round(lockup_days),
            "net_edge_ann_pct": round(ann * 100, 2),
            "checked_outcomes": n,
            "best_ask_depth_usdc": round(depth_usdc, 2)}


def kelly_binary(p_true, price, bankroll=BANKROLL, frac_cap=MAX_KELLY_FRAC):
    """Full Kelly for a binary buy at `price` with true prob `p_true`."""
    if p_true is None or price <= 0 or price >= 1:
        return 0.0, 0.0
    b = (1 - price) / price          # net odds
    q = 1 - p_true
    f = (b * p_true - q) / b if b > 0 else 0.0
    f = max(0.0, f)
    frac = min(f, frac_cap)          # quarter-ish cap per standing rule
    return frac, round(frac * bankroll, 2)


def scan():
    out = {"ts": datetime.now(timezone.utc).isoformat()[:19], "mode": "scan",
           "events_scanned": 0, "candidates": [], "verified": []}
    events = pull_events()
    out["events_scanned"] = len(events)
    cands = detect_arb(events)
    out["candidates"] = [
        {k: c[k] for k in ("event", "n_outcomes", "mid_sum", "direction",
                           "gross_edge_pct")} for c in cands]
    for c in cands:
        v = verify_with_books(c)
        row = dict(c)
        if v:
            row.update(v)
            if v.get("net_edge_ann_pct", 0) > IDLE_BASELINE_APR_PCT and v.get("net_edge_pct", 0) > 0.05:  # must beat idle USDC annualized over lockup
                out["verified"].append({
                    "event": c["event"], "direction": c["direction"],
                    "mid_sum": c["mid_sum"], **v})
                paper_size = min(v.get("best_ask_depth_usdc", 0), MAX_DEPTH_USDC,
                                 BANKROLL * MAX_KELLY_FRAC)
                row["paper_size_usdc"] = round(paper_size, 2)
                if paper_size >= 5:
                    with open(LEDGER, "a") as f:
                        f.write(json.dumps({
                            "ts": out["ts"], "type": "arb", "event": c["event"],
                            "slug": c.get("eventSlug"), "direction": c["direction"],
                            "legs": [o.get("slug") for o in c.get("outcomes", [])],
                            "n_outcomes": c.get("n_outcomes"),
                            "size_usdc": round(paper_size, 2),
                            "expected_profit_usdc": round(
                                paper_size * v["exec_edge_pct"] / 100 / max(0.01, 1 - v["exec_edge_pct"] / 100), 2),
                            "status": "open"}) + "\n")
                with open(ARBS, "a") as f:
                    f.write(json.dumps({"ts": out["ts"], **row}) + "\n")
    out["verified_count"] = len(out["verified"])
    print(json.dumps(out, indent=1))
    return out


NEWS_STOP = set("the a an of in on for to and or at as by with from after over under his her its their this that what who".split())


def pull_headlines(max_headlines=60):
    feeds = [
        "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=election+OR+senate+OR+president+OR+fed+OR+bitcoin+when:1d&hl=en-US&gl=US&ceid=US:en",
    ]
    items = []
    for url in feeds:
        try:
            r = requests.get(url, headers=UA, timeout=30)
            root = ET.fromstring(r.content)
            for item in root.iter("item"):
                t = (item.findtext("title") or "").strip()
                if t:
                    items.append(t)
        except Exception:
            continue
        if len(items) >= max_headlines:
            break
    return items[:max_headlines]


def tokenize(s):
    return set(w for w in re.findall(r"[a-z0-9]{3,}", s.lower()) if w not in NEWS_STOP)


def news_scan(events):
    heads = pull_headlines()
    out = {"ts": datetime.now(timezone.utc).isoformat()[:19], "mode": "news",
           "n_headlines": len(heads), "flags": []}
    mk = []
    for ev in events:
        for m in (ev.get("markets") or []):
            prices = parse_j(m.get("outcomePrices"))
            if prices and 0.12 < float(prices[0]) < 0.88:  # only undecided-looking
                mk.append({"q": m.get("question"), "slug": m.get("slug"),
                           "yes_mid": float(prices[0]),
                           "end": m.get("endDate"), "tokens": tokenize(m.get("question") or "")})
    for h in heads:
        ht = tokenize(h)
        if len(ht) < 3:
            continue
        best, score = None, 0
        for m in mk:
            ov = len(ht & m["tokens"])
            if ov > score:
                best, score = m, ov
        if best and score >= NEWS_MATCH_MIN:
            out["flags"].append({"headline": h[:140], "market": best["q"],
                                 "yes_mid": best["yes_mid"],
                                 "match_tokens": score, "endDate": best["end"]})
    out["n_flags"] = len(out["flags"])
    print(json.dumps(out, indent=1))
    return out



def spreads():
    """Log bid-ask spreads + inside depth on top-volume markets (MM opportunity data).
    PAPER-ONLY measurement: quantifies the market-making opportunity surface
    (how tight liquid books are, how often wide/liquid gaps appear)."""
    ts = datetime.now(timezone.utc).isoformat()[:19]
    out = {"ts": ts, "mode": "spreads", "logged": 0, "errors": 0}
    rows = []
    markets = get(GAMMA + "/markets", {"closed": "false", "active": "true",
                                       "order": "volume24hr", "ascending": "false",
                                       "limit": 20}) or []
    for m in markets:
        toks = parse_j(m.get("clobTokenIds"))
        if not toks:
            continue
        book = get(CLOB + "/book", {"token_id": toks[0]})
        if not book or not book.get("bids") or not book.get("asks"):
            out["errors"] += 1
            continue
        bids, asks = book["bids"], book["asks"]
        bb, ba = float(bids[-1]["price"]), float(asks[-1]["price"])  # bids ASC, asks DESC
        if ba <= bb:  # crossed/closed book, skip
            continue
        mid = (bb + ba) / 2
        spread = ba - bb
        bd = sum(float(x["size"]) for x in bids[-3:])
        ad = sum(float(x["size"]) for x in asks[-3:])
        row = {"ts": ts,
               "q": (m.get("question") or "")[:70],
               "vol24h": float(m.get("volume24hr") or 0),
               "bb": bb, "ba": ba,
               "spread": round(spread, 4),
               "spread_pct": round(spread / mid * 100, 2),
               "depth_usd": round(min(bd, ad) * mid, 0)}
        rows.append(row)
    with open(SPREADS, "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    rows.sort(key=lambda r: -r["vol24h"])
    top10 = rows[:10]
    out["logged"] = len(rows)
    if top10:
        s = sorted(r["spread_pct"] for r in top10)
        out["median_top10_spread_pct"] = s[len(s)//2]
    wide_liquid = [r for r in rows if r["vol24h"] > 250_000 and r["spread_pct"] >= 8]
    out["wide_liquid"] = [{"q": r["q"][:40], "spread_pct": r["spread_pct"],
                           "vol24h": round(r["vol24h"])} for r in wide_liquid]
    print(json.dumps(out))

def status():
    rows = []
    if os.path.exists(LEDGER):
        rows = [json.loads(l) for l in open(LEDGER) if l.strip()]
    print(json.dumps({"n_paper_bets": len(rows),
                      "ledger": LEDGER}, indent=1))


# ---- Polymarket taker fees ("Fee Structure V2", Mar 30 2026; sports raised Jul 10 2026) ----
# fee = shares x rate x price x (1-price); takers pay, makers free; geopolitics/world free
import re as _re
FEE_RATES = {"crypto": 0.07, "sports": 0.05, "economics": 0.05, "culture": 0.05,
             "weather": 0.05, "other": 0.05, "finance": 0.04, "politics": 0.04,
             "mentions": 0.04, "tech": 0.04, "geopolitics": 0.0}

def fee_rate_for(slug):
    s = (slug or "").lower()
    if any(k in s for k in ("attack", "war", "ceasefire", "nuclear", "invade",
                            "conflict", "hostage", "military", "coup", "airstrike")):
        return FEE_RATES["geopolitics"]
    if any(k in s for k in ("bitcoin", "ethereum", "solana", "-up-or-down",
                            "dogecoin", "xrp-up", "crypto")):
        return FEE_RATES["crypto"]
    dated = _re.search(r"-\d{4}-\d{1,2}-\d{1,2}(-|$)", s)
    political = any(k in s for k in ("election", "governor", "president", "senate",
                                     "mayor", "party", "minister", "nominee",
                                     "primary", "ballot", "prop-", "congress"))
    if dated and not political:
        return FEE_RATES["sports"]
    if any(k in s for k in ("fed-", "interest-rate", "cpi", "gdp", "recession",
                            "inflation", "s-p", "earnings", "moody", "rate-cut")):
        return FEE_RATES["finance"]
    if any(k in s for k in ("elon", "tweet", "spacex", "openai", "apple-",
                            "tesla", "nvidia", "amazon", "google", "meta-",
                            "microsoft", "mention", "say-", "announce")):
        return FEE_RATES["mentions"]
    if political:
        return FEE_RATES["politics"]
    if any(k in s for k in ("oscar", "grammy", "emmy", "nobel", "temperature",
                            "snow", "rain", "hurricane", "weather", "box-office")):
        return FEE_RATES["culture"]
    return FEE_RATES["other"]

def entry_fee(shares, price, slug):
    return round(shares * fee_rate_for(slug) * price * (1 - price), 4)

def book_mid(token_id):
    """Mid AND spread of the current book; thin/stale books show wide spread."""
    try:
        b = get(CLOB + "/book", {"token_id": token_id})
        if b and b.get("bids") and b.get("asks"):
            a = float(b["asks"][-1]["price"])
            bb = float(b["bids"][-1]["price"])
            mid = (a + bb) / 2
            spread = (a - bb) / mid if mid > 0 else 1.0
            return {"mid": round(mid, 4), "spread": round(spread, 4)}
    except Exception:
        pass
    return None

CLV_MAX_SPREAD = 0.05   # drop CLV observations where the book is wider than 5%
IDLE_BASELINE_APR_PCT = 4.0   # what idle USDT earns (Simple Earn ~3.1% + risk buffer)

def _legs_from_candidates(row):
    """Recover a basket's leg slugs for legacy ledger rows written before legs were stored."""
    try:
        cands = [json.loads(l) for l in open(ARBS) if l.strip()]
    except Exception:
        return None
    best = None
    for c in cands:
        if c.get("eventSlug") == row.get("slug") and c.get("ts", "") <= row.get("ts", "9"):
            if best is None or c["ts"] > best["ts"]:
                best = c
    if best and best.get("outcomes"):
        return [o.get("slug") for o in best["outcomes"]]
    return None


def settle():
    """Settle open paper arbs: refetch event, compute basket payout."""
    if not os.path.exists(LEDGER):
        print(json.dumps({"open": 0})); return
    rows = [json.loads(l) for l in open(LEDGER) if l.strip()]
    changed = {}
    for r in rows:
        if r.get("status") != "open":
            continue
        evs = get(GAMMA + "/events", {"slug": r["slug"]}) or []
        if not evs:
            continue
        ms = (evs[0].get("markets") or [])
        # Oct 29 2026 FIX: events carry extra sub-markets (e.g. an 8-market House event
        # where only 2 markets form the basket). Settle ONLY the basket's legs, else
        # valid baskets get flagged MULTIPLE_YES / mis-attributed on resolution.
        legs = r.get("legs") or _legs_from_candidates(r)
        if legs:
            by_slug = {m.get("slug"): m for m in ms}
            ms = [by_slug[s] for s in legs if s in by_slug]
            if len(ms) < len(legs):
                continue                  # leg markets not all present yet
        open_ms = [m for m in ms if not m.get("closed")]
        if open_ms:
            continue                      # basket still live
        yes_finals = [parse_j(m.get("outcomePrices")) for m in ms]
        winners = [1 for p in yes_finals if p and float(p[0]) > 0.99]
        if len(winners) == 1:
            r["status"], r["result"] = "settled", "WIN"
            r["pnl_usdc"] = round(r["expected_profit_usdc"], 2)
        elif len(winners) == 0:
            r["status"], r["result"] = "settled", "ALL_NO_LOSS"
            r["pnl_usdc"] = -r["size_usdc"]
            r["note"] = "no YES winner — basket NOT exhaustive (false positive)"
        else:
            r["status"], r["result"] = "anomaly", "MULTIPLE_YES"
        changed[r["slug"]] = r
    if changed:
        with open(LEDGER, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    settled = [c for c in changed.values() if c.get("status") == "settled"]
    out = {"settled": len(settled),
           "pnl": round(sum(c.get("pnl_usdc", 0) for c in settled), 2),
           "detail": {c["slug"]: (c["result"], c.get("pnl_usdc"))
                      for c in changed.values()}}
    try:
        out.update(settle_extras())
    except Exception as ex:
        out["settle_extras_error"] = str(ex)[:150]
    print(json.dumps(out, indent=1))



# ---------------- settlement of shadow + live ledgers ----------------
def _gamma_lookup(cid):
    ms = get(GAMMA + "/markets", {"condition_ids": cid}) or []
    for m in ms:
        if m.get("conditionId", "").lower() == cid.lower():
            toks = parse_j(m.get("clobTokenIds")) or []
            prices = parse_j(m.get("outcomePrices")) or []
            if not m.get("closed") or len(toks) != len(prices):
                return None
            for p, t in zip(prices, toks):
                try:
                    if float(p) >= 0.95:
                        return str(t)
                except Exception:
                    pass
            return None
    return "MISSING"


def _event_lookup(row):
    evs = get(GAMMA + "/events", {"slug": row.get("slug", "")}) or []
    for ev in evs:
        for m in (ev.get("markets") or []):
            if m.get("conditionId", "").lower() == row.get("conditionId", "").lower():
                toks = parse_j(m.get("clobTokenIds")) or []
                prices = parse_j(m.get("outcomePrices")) or []
                if not m.get("closed") or len(toks) != len(prices):
                    return None
                for p, t in zip(prices, toks):
                    try:
                        if float(p) >= 0.95:
                            return str(t)
                    except Exception:
                        pass
                return None
    return "MISSING"


def _clob_lookup(cids):
    """Resolution of archived markets via CLOB through the Bangalore SOCKS relay."""
    res = {c: None for c in cids}
    try:
        import requests as rq
        from live import Relay
        with Relay():
            for c in cids:
                try:
                    r = rq.get("https://clob.polymarket.com/markets/" + c, timeout=20)
                    m = r.json() if r.ok else {}
                    w = None
                    if m.get("closed"):
                        for t in (m.get("tokens") or []):
                            if t.get("winner") is True:
                                w = str(t.get("token_id"))
                    res[c] = w
                except Exception:
                    res[c] = None
                time.sleep(0.25)
    except Exception:
        pass
    return res


def settle_extras():
    """Settle open rows in the SHADOW paper ledger and the LIVE mirror ledger."""
    import datetime
    now = datetime.datetime.utcnow()
    out = {"shadow_settled": 0, "shadow_pnl": 0.0, "shadow_open_remaining": 0,
           "leaders": {}, "live_settled": 0, "live_pnl": 0.0, "live_open_remaining": 0}

    def process(path, key_asset, key_fill, key_size, is_live):
        if not os.path.exists(path):
            return
        rows = [json.loads(l) for l in open(path) if l.strip()]
        pending, changed = [], False
        for r in rows:
            if r.get("status") != "open":
                continue
            try:
                age_h = (now - datetime.datetime.fromisoformat(r["ts"])).total_seconds() / 3600
            except Exception:
                age_h = 99
            if age_h < 4:
                continue
            cid = r.get("conditionId")
            if not cid:
                continue
            w = _gamma_lookup(cid)
            if w == "MISSING":
                w = _event_lookup(r)
            if w == "MISSING":
                pending.append(r)
                continue
            if w is None:
                continue
            asset = str(r[key_asset])
            fill = float(r[key_fill])
            if asset == w:
                r["status"], r["result"] = "settled", "WIN"
                r["fee_usdc"] = entry_fee(float(r["shares"]), fill, r.get("slug")); r["pnl_usdc"] = round(float(r["shares"]) * (1 - fill) - r["fee_usdc"], 2)
            else:
                r["status"], r["result"] = "settled", "LOSS"
                r["fee_usdc"] = entry_fee(float(r["shares"]), fill, r.get("slug")); r["pnl_usdc"] = round(-float(r[key_size]) - r["fee_usdc"], 2)
            changed = True
            if is_live:
                out["live_settled"] += 1
                out["live_pnl"] = round(out["live_pnl"] + r["pnl_usdc"], 2)
            else:
                out["shadow_settled"] += 1
                out["shadow_pnl"] = round(out["shadow_pnl"] + r["pnl_usdc"], 2)
                ld = out["leaders"].setdefault(r.get("leader", "?"), {"n": 0, "wins": 0, "pnl": 0.0})
                ld["n"] += 1
                if r["result"] == "WIN":
                    ld["wins"] += 1
                ld["pnl"] = round(ld["pnl"] + r["pnl_usdc"], 2)
        if pending:
            wl = _clob_lookup(sorted({r["conditionId"] for r in pending}))
            for r in pending:
                w = wl.get(r["conditionId"])
                if w is None:
                    continue
                asset, fill = str(r[key_asset]), float(r[key_fill])
                if asset == w:
                    r["status"], r["result"] = "settled", "WIN"
                    r["fee_usdc"] = entry_fee(float(r["shares"]), fill, r.get("slug")); r["pnl_usdc"] = round(float(r["shares"]) * (1 - fill) - r["fee_usdc"], 2)
                else:
                    r["status"], r["result"] = "settled", "LOSS"
                    r["fee_usdc"] = entry_fee(float(r["shares"]), fill, r.get("slug")); r["pnl_usdc"] = round(-float(r[key_size]) - r["fee_usdc"], 2)
                changed = True
                if is_live:
                    out["live_settled"] += 1
                    out["live_pnl"] = round(out["live_pnl"] + r["pnl_usdc"], 2)
                else:
                    out["shadow_settled"] += 1
                    out["shadow_pnl"] = round(out["shadow_pnl"] + r["pnl_usdc"], 2)
                    ld = out["leaders"].setdefault(r.get("leader", "?"), {"n": 0, "wins": 0, "pnl": 0.0})
                    ld["n"] += 1
                    if r["result"] == "WIN":
                        ld["wins"] += 1
                    ld["pnl"] = round(ld["pnl"] + r["pnl_usdc"], 2)
        if changed:
            with open(path, "w") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
        out[("live" if is_live else "shadow") + "_open_remaining"] = \
            sum(1 for r in rows if r.get("status") == "open")

    process(SHADOW_BOOK, "asset", "our_fill", "size_usdc", False)
    process(os.path.join(BASE, "live_ledger.jsonl"), "token_id", "price", "cost_usdc", True)
    return out


# ---------------- copy-shadow monitor ----------------
SHADOW_WALLETS = os.path.join(BASE, "shadow_wallets.json")
SHADOW_STATE = os.path.join(BASE, "shadow_state.json")
SHADOW_BOOK = os.path.join(BASE, "shadow_ledger.jsonl")
SHADOW_SLIP = 0.01       # 1% adverse slippage assumed on our paper copy
SHADOW_SIZE = 25.0       # fixed paper size per copied signal, USDC


def shadow():
    """Track shortlisted wallets; paper-copy their new trades with slippage."""
    if not os.path.exists(SHADOW_WALLETS):
        print(json.dumps({"shadow": "no wallet list"})); return
    wallets = json.load(open(SHADOW_WALLETS))
    state = json.load(open(SHADOW_STATE)) if os.path.exists(SHADOW_STATE) else {}
    out = {"ts": datetime.now(timezone.utc).isoformat()[:19], "mode": "shadow",
           "new_signals": []}
    for name, addr in wallets.items():
        acts = get(DATA + "/activity", {"user": addr, "limit": 100}) or []
        trades = [a for a in acts if a.get("type") == "TRADE"]
        last_ts = state.get(name, 0)
        fresh = [a for a in trades if a.get("timestamp", 0) > last_ts]
        if not trades:
            continue
        state[name] = max(a.get("timestamp", 0) for a in trades)
        seen_keys = set()
        for a in fresh:
            px = float(a.get("price") or 0)
            if not (0.02 < px < 0.98):
                continue          # skip near-resolved certainties
            key = (a.get("conditionId"), (a.get("side") or "").lower(),
                   datetime.fromtimestamp(a.get("timestamp", 0), timezone.utc).strftime("%Y-%m-%d"))
            if key in seen_keys:
                continue          # dedupe leader scalps on same market/day
            seen_keys.add(key)
            side = (a.get("side") or "").lower()
            slip_px = px * (1 + SHADOW_SLIP) if side == "buy" else px * (1 - SHADOW_SLIP)
            row = {"ts": out["ts"], "leader": name, "market": a.get("title"),
                   "conditionId": a.get("conditionId"), "slug": a.get("slug"),
                   "asset": a.get("asset"), "side": side, "leader_price": px,
                   "our_fill": round(slip_px, 4), "size_usdc": SHADOW_SIZE,
                   "shares": round(SHADOW_SIZE / slip_px, 2), "status": "open"}
            with open(SHADOW_BOOK, "a") as f:
                f.write(json.dumps(row) + "\n")
            out["new_signals"].append({k: row[k] for k in
                ("leader", "market", "side", "leader_price", "our_fill")})
    json.dump(state, open(SHADOW_STATE, "w"))
    # ---- CLV capture: record mid price at +1h and +24h after each buy signal ----
    clv_marks = 0
    if os.path.exists(SHADOW_BOOK):
        rows = [json.loads(l) for l in open(SHADOW_BOOK) if l.strip()]
        t_now = datetime.utcnow()
        changed = False
        for r in rows:
            if r.get("status") != "open" or r.get("side") != "buy":
                continue
            try:
                age = (t_now - datetime.fromisoformat(r["ts"])).total_seconds()
            except Exception:
                continue
            if age < 3600 or age > 48 * 3600:
                continue
            if r.get("mid_1h") is None and age >= 3600:
                m = book_mid(r.get("asset"))
                time.sleep(0.12)
                if m is not None:
                    r["mid_1h"], r["spread_1h"] = m["mid"], m["spread"]
                    changed = True
                    clv_marks += 1
            if r.get("mid_24h") is None and age >= 86400:
                m = book_mid(r.get("asset"))
                time.sleep(0.12)
                if m is not None:
                    r["mid_24h"], r["spread_24h"] = m["mid"], m["spread"]
                    changed = True
                    clv_marks += 1
        if changed:
            with open(SHADOW_BOOK, "w") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
        m1 = [r for r in rows if r.get("mid_1h") is not None
              and r.get("spread_1h", 1) <= CLV_MAX_SPREAD]
        drop1 = sum(1 for r in rows if r.get("mid_1h") is not None
                    and r.get("spread_1h", 1) > CLV_MAX_SPREAD)
        if m1:
            lead = [(r["mid_1h"] - r["leader_price"]) / r["leader_price"] for r in m1]
            ours = [(r["mid_1h"] - r["our_fill"]) / r["our_fill"] for r in m1]
            out["clv_1h"] = {"n": len(m1), "n_dropped_wide": drop1,
                             "leader_fill_vs_1h_mid_pct": round(sum(lead) / len(lead) * 100, 2),
                             "our_fill_vs_1h_mid_pct": round(sum(ours) / len(ours) * 100, 2)}
        m24 = [r for r in rows if r.get("mid_24h") is not None
               and r.get("spread_24h", 1) <= CLV_MAX_SPREAD]
        drop24 = sum(1 for r in rows if r.get("mid_24h") is not None
                     and r.get("spread_24h", 1) > CLV_MAX_SPREAD)
        if m24:
            lead = [(r["mid_24h"] - r["leader_price"]) / r["leader_price"] for r in m24]
            out["clv_24h"] = {"n": len(m24), "n_dropped_wide": drop24,
                              "leader_fill_vs_24h_mid_pct": round(sum(lead) / len(lead) * 100, 2)}
    out["clv_marks"] = clv_marks
    out["n_new"] = len(out["new_signals"])
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if mode == "scan":
        scan()
    elif mode == "news":
        news_scan(pull_events(max_events=800))
    elif mode == "settle":
        settle()
    elif mode == "shadow":
        shadow()
    elif mode == "spreads":
        spreads()
    elif mode == "status":
        status()
    else:
        print(__doc__)


