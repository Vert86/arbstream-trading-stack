#!/usr/bin/env python3
"""
Funding Radar (monitor-only, no trading) — Oct 25 2026.
Scans live funding rates across three venues (public APIs, no auth):
  - Binance USDT-M perps (via user relay arbstream.duckdns.org -> fapi)
  - dYdX v4 (indexer.dydx.trade)
  - Hyperliquid (api.hyperliquid.xyz)
Alerts ONLY hedgeable Binance carries (real spot leg) above the APR threshold; other venues/coins go to the watch list only. Dedupes alerts per coin
for 48h (state.json in skill dir). Pure monitor: NEVER places orders anywhere.
"""
import json, os, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
ALERT_APR = 25.0        # flag above this annualized
WATCH_APR = 15.0        # include in "watching" summary
DEDUP_H = 48

def get(url, payload=None):
    if payload is not None:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())

def scan():
    now = time.time()
    state = {"alerted": {}}
    if os.path.exists(STATE):
        try: state = json.load(open(STATE))
        except Exception: pass
    alerts = []
    watched = []
    # --- Binance (via relay). ACTIONABLE-ONLY (owner Oct 27 2026): an alert is a
    # trade the ArbStream bot can actually use => hedgeable (real spot leg) AND
    # funding above the bot's entry gate. Everything else stays in the watch list.
    spot_assets = set()
    try:
        ei = get("https://arbstream.duckdns.org/api/v3/exchangeInfo")
        spot_assets = {s["baseAsset"] for s in ei.get("symbols", [])
                       if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT"}
    except Exception:
        spot_assets = set()
    try:
        d = get("https://arbstream.duckdns.org/fapi/v1/premiumIndex")
        for x in d:
            try:
                ann = float(x["lastFundingRate"])*3*365*100
                base = x["symbol"].replace("USDT", "")
                hedgeable = base in spot_assets
                if ann > ALERT_APR:
                    if hedgeable:
                        alerts.append(dict(venue="Binance", coin=x["symbol"], apr=ann,
                                           note="HEDGEABLE: short perp + spot leg — bot can trade this"))
                    # unhedgeable (tokenized-equity etc.) => watch list only, never an alert
                elif ann > WATCH_APR:
                    watched.append(("Binance", x["symbol"], ann))
            except Exception: pass
    except Exception as e:
        print(json.dumps({"error": f"binance: {e}"})); return
    # --- dYdX v4 ---
    try:
        d = get("https://indexer.dydx.trade/v4/perpetualMarkets")
        for m in d.get("markets", {}).values():
            try:
                f = float(m.get("nextFundingRate") or 0)
                ann = f*24*365*100
                if ann > WATCH_APR:
                    watched.append(("dYdX", m["ticker"], ann))
            except Exception: pass
    except Exception: pass
    # --- Hyperliquid ---
    try:
        meta, ctxs = get("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"})
        for m, c in zip(meta["universe"], ctxs):
            try:
                f = float(c["funding"]); oi = float(c.get("openInterest") or 0)
                ann = f*24*365*100
                if ann > WATCH_APR and oi > 5e6:
                    watched.append(("Hyperliquid", m["name"], ann))
            except Exception: pass
    except Exception: pass
    # dedupe: alert only new (or >48h since last alert for same coin)
    fresh = []
    for a in alerts:
        k = f"{a['venue']}:{a['coin']}"
        last = state["alerted"].get(k, 0)
        if now - last > DEDUP_H*3600:
            fresh.append(a); state["alerted"][k] = now
    # prune state
    state["alerted"] = {k: v for k, v in state["alerted"].items() if now - v < 7*86400}
    json.dump(state, open(STATE, "w"))
    watched.sort(key=lambda w: -w[2])
    out = dict(ts=now, alerts=fresh, n_watch=len(watched),
               top_watch=[dict(venue=v, coin=c, apr=round(a,1)) for v,c,a in watched[:5]])
    print(json.dumps(out))

if __name__ == "__main__":
    scan()
