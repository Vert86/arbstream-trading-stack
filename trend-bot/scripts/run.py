#!/usr/bin/env python3
"""
Trend Bot V1 (paper) — Binance BTC/ETH, TWO LAYERS, 200D SMA regime gate on both.

CORE (daily): Donchian 20/10 breakout + 200D SMA gate. $340 paper, $170/sym.
  Backtest Oct 25 2026 (2y real data, all-param plateau positive):
  BTC +136.7%/2y PF 3.26 maxDD -21% (n=18); ETH +31.8% (n=15).
TACTICAL (4h): Donchian 20 entry + 2.5xATR14 trailing exit + 200D SMA gate.
  Separate $340 sandbox ledger, $170/sym.
  Backtest (2y, sensitivity-verified): BTC +29.2% PF 1.72 maxDD -8.6% (34 trades,
  1.4/mo); ETH +39.1% PF 1.90 maxDD -20.3% (22 trades, 0.9/mo).
  NOTE: user's proposed 14/5 variant tested WORSE (PF 1.16) — D20+ATR trail is
  the profitable shape. Do not change without backtest + owner approval.

Fees modeled 0.075%/side. Long-only. Regime off = flat (no new entries, force exit).
Gate amendment (user-approved Oct 25 2026): trend systems exempt from 60% WR floor;
gate = PF>2 + maxDD<25% + positive through regime change. (Core passes; tactical
PF 1.7-1.9 with lower DD — validated as the frequency layer.)
PAPER ONLY. Live requires explicit owner sign-off after paper validation.

Modes: scan (default, trades), status (read-only).
State: <skill dir>/state.json.
"""
import json, os, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signals  # shared signal module — same code path as backtests/replays

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
BASE = "https://arbstream.duckdns.org/api/v3/klines"  # relay -> api.binance.com
FEE = 0.00075
START_CAPITAL = 340.0
PER_SYM = 170.0
SYMBOLS = ["ETHUSDT"]  # BTC removed Oct 26 2026: core beat B&H in only 8/30 rolling starts
CORE_WATERLINE = 340.0 * 0.75  # declared gate: never risk >25% of DEPOSITED capital
ATR_TRAIL = 2.5   # tactical exit multiplier
DON_N = 20        # breakout lookback (both layers)
PB_DEPTH = 0.01       # pullback-variant limit depth (owner Oct 28 2026; sweep 0.5-2% was noise-level)
PB_TIMEOUT_DAYS = 3

def get_klines(sym, interval, limit=420):
    url = f"{BASE}?symbol={sym}&interval={interval}&limit={limit}"
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read())

def atr14_from(h, l, c):
    """Wilder-ish ATR14 on completed arrays (returns last value)."""
    import numpy as np
    h, l, c = np.array(h), np.array(l), np.array(c)
    prev = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h-l, np.maximum(abs(h-prev), abs(l-prev)))
    k = 2.0/15
    a = tr[0]
    for x in tr[1:]:
        a = x*k + a*(1-k)
    return a

def run(mode="scan"):
    state = {"cash": START_CAPITAL, "positions": {}, "trades": [],
             "equity_hwm": START_CAPITAL,
             "tactical": {"cash": START_CAPITAL, "positions": {}, "trades": []}}
    if os.path.exists(STATE):
        state = json.load(open(STATE))
        state.setdefault("tactical", {"cash": START_CAPITAL, "positions": {}, "trades": []})
    tac = state["tactical"]
    result = {"mode": mode, "actions": [], "equity": None, "tactical_equity": None, "pullback": {},
              "marks": {}, "regime": {}, "errors": []}
    # daily regime is shared by both layers
    daily = {}
    for sym in SYMBOLS:
        try:
            k = get_klines(sym, "1d", 420)
        except Exception as e:
            result["errors"].append(f"{sym}: daily klines failed: {e}")
            continue
        closes = [float(x[4]) for x in k[:-1]]
        if len(closes) < 210:
            result["errors"].append(f"{sym}: insufficient daily history"); continue
        sma200 = sum(closes[-200:])/200
        daily[sym] = dict(sma200=sma200, k=k)
        c = closes[-1]
        result["regime"][sym] = dict(close=c, sma200=round(sma200,2),
                                     regime_on=c > sma200,
                                     don_hi=max(float(x[2]) for x in k[-22:-2]),
                                     don_lo=min(float(x[3]) for x in k[-12:-2]))
    # ---------- CORE (daily Donchian 20/10) — shared decision path ----------
    for sym in SYMBOLS:
        if sym not in daily: continue
        d = daily[sym]; k = d["k"]; reg_on = result["regime"][sym]["regime_on"]
        closes = [float(x[4]) for x in k[:-1]]
        hcomp = [float(x[2]) for x in k[:-1]]
        lcomp = [float(x[3]) for x in k[:-1]]
        c = closes[-1]
        cur_px = float(k[-1][4])
        hh20 = result["regime"][sym]["don_hi"]; ll10 = result["regime"][sym]["don_lo"]
        pos = state["positions"].get(sym)
        act, meta = signals.core_decision(closes[-421:], hcomp[-421:], lcomp[-421:], pos is not None)
        if pos and mode == "scan" and act == "exit":
            reason = "daily donchian low" if meta["reason"] == "donchian_low_10d" else "regime off (200D)"
            if True:
                state["trades"].append(dict(ts=int(time.time()*1000), sym=sym, side="sell",
                    px=round(px,2), qty=pos["qty"], pnl=round(pnl,2),
                    pnl_pct=round(pnl/pos["invested"]*100,2), reason=reason, layer="core"))
                result["actions"].append(f"CORE SELL {sym} @ {px:.2f} pnl {pnl:+.2f} ({pnl/pos['invested']*100:+.1f}%) — {reason}")
                del state["positions"][sym]
        if not state["positions"].get(sym) and mode == "scan" and act == "enter":
            size = signals.core_size(state["cash"], cur_px, ll10)
            eq_now = state["cash"] + sum(p["qty"]*cur_px for p in state["positions"].values())
            gate_ok = eq_now >= CORE_WATERLINE
            if size >= 20.0 and gate_ok:
                px = cur_px*(1+FEE)
                state["cash"] -= size
                state["positions"][sym] = dict(qty=size/px, invested=round(size,2),
                    entry_px=round(px,2), entry_ts=int(time.time()*1000))
                state["trades"].append(dict(ts=int(time.time()*1000), sym=sym, side="buy",
                    px=round(px,2), reason="daily 20d breakout, vol-sized", layer="core"))
                result["actions"].append(f"CORE BUY {sym} @ {px:.2f} (${size:.0f} vol-sized, stop {ll10:.2f}, daily 20d high {hh20:.2f} broken)")
            elif not gate_ok:
                result["actions"].append(f"CORE entry blocked: waterline gate (equity ${eq_now:.0f} < ${CORE_WATERLINE:.0f})")
        if state["positions"].get(sym):
            p = state["positions"][sym]
            result["marks"][sym] = dict(entry=p["entry_px"], now=cur_px,
                pnl_pct=round((cur_px*(1-FEE)-p["entry_px"]*(1+FEE))/p["entry_px"]*(1+FEE)*100, 2))

    # ---------- PULLBACK VARIANT (forward-paper sidecar, owner Oct 28 2026) ----------
    # Same core rules; entry only differs: on a core breakout signal, rest a
    # limit at signal_close*(1-PB_DEPTH) instead of buying immediately.
    # Fill: any following day's low <= limit (limit fills; fee as taker for
    # simplicity). Cancel: daily close < decision-day 10d low. Timeout 3 days
    # -> market entry at close. Backtest Oct 28 (ETH 2018-2026, n=28): fill
    # 28/28 at 1%, P&L vs core within noise (-$17..+$19 across 0.5-2%) - the
    # point of the sidecar is forward data, not an in-sample edge claim.
    pbv = state.setdefault("pullback", {"cash": START_CAPITAL, "pending": {},
                                        "positions": {}, "trades": []})
    for sym in SYMBOLS:
        if sym not in daily: continue
        k = daily[sym]["k"]; reg = result["regime"][sym]
        closes = [float(x[4]) for x in k[:-1]]
        hcomp = [float(x[2]) for x in k[:-1]]
        lcomp = [float(x[3]) for x in k[:-1]]
        c = closes[-1]; cur_px = float(k[-1][4])
        vpos = pbv["positions"].get(sym)
        vpend = pbv["pending"].get(sym)
        vact, vmeta = signals.core_decision(closes[-421:], hcomp[-421:], lcomp[-421:], vpos is not None)
        if vpos and mode == "scan" and vact == "exit":
            pnl = (cur_px*(1-FEE) - vpos["entry_px"]) * vpos["qty"]
            pbv["cash"] += vpos["qty"] * cur_px * (1-FEE)
            pbv["trades"].append(dict(ts=int(time.time()*1000), sym=sym, side="sell",
                px=round(cur_px*(1-FEE),2), qty=vpos["qty"], pnl=round(pnl,2),
                reason="mirror core exit", layer="pullback"))
            result["actions"].append(f"PULLBACK SELL {sym} @ {cur_px*(1-FEE):.2f} pnl {pnl:+.2f} (mirrors core exit)")
            del pbv["positions"][sym]
        if mode == "scan" and vact == "enter" and not vpos and not vpend:
            pbv["pending"][sym] = dict(limit=round(c*(1-PB_DEPTH),2), stop=reg["don_lo"],
                                       sig_c=c, created=int(time.time()*1000))
            result["actions"].append(f"PULLBACK order placed {sym}: limit {c*(1-PB_DEPTH):.2f} (signal close {c:.2f} -{PB_DEPTH*100:.1f}%, stop {reg['don_lo']:.2f})")
        if vpend and mode == "scan":
            if c < vpend["stop"]:
                result["actions"].append(f"PULLBACK {sym} cancelled: daily close {c:.2f} < stop {vpend['stop']:.2f} (breakout failed)")
                del pbv["pending"][sym]
            elif float(k[-1][3]) <= vpend["limit"]:
                px = vpend["limit"]*(1+FEE)
                size = signals.core_size(pbv["cash"], px, vpend["stop"])
                if size >= 20.0:
                    pbv["positions"][sym] = dict(qty=size/px, invested=round(size,2), entry_px=round(px,2),
                                                 entry_ts=int(time.time()*1000))
                    pbv["cash"] -= size
                    pbv["trades"].append(dict(ts=int(time.time()*1000), sym=sym, side="buy",
                        px=round(px,2), reason=f"pullback fill (limit {vpend['limit']:.2f}, signal {vpend['sig_c']:.2f})", layer="pullback"))
                    result["actions"].append(f"PULLBACK BUY {sym} @ {px:.2f} (limit filled; saved {(vpend['sig_c']-vpend['limit'])/vpend['sig_c']*1e4:.0f}bp vs signal close)")
                    del pbv["pending"][sym]
            elif (time.time()*1000 - vpend["created"]) / 86400000 >= PB_TIMEOUT_DAYS:
                px = cur_px*(1+FEE)
                size = signals.core_size(pbv["cash"], px, vpend["stop"])
                if size >= 20.0:
                    pbv["positions"][sym] = dict(qty=size/px, invested=round(size,2), entry_px=round(px,2),
                                                 entry_ts=int(time.time()*1000))
                    pbv["cash"] -= size
                    pbv["trades"].append(dict(ts=int(time.time()*1000), sym=sym, side="buy",
                        px=round(px,2), reason="pullback timeout - market entry", layer="pullback"))
                    result["actions"].append(f"PULLBACK BUY {sym} @ {px:.2f} (3-day timeout, market entry)")
                del pbv["pending"][sym]
        if pbv["positions"].get(sym):
            p = pbv["positions"][sym]
            result["marks"]["PB-"+sym] = dict(entry=p["entry_px"], now=cur_px,
                pnl_pct=round((cur_px*(1-FEE)-p["entry_px"]*(1+FEE))/p["entry_px"]*(1+FEE)*100, 2))
    pb_eq = pbv["cash"] + sum(p["qty"] * float(daily[s]["k"][-1][4]) for s, p in pbv["positions"].items() if s in daily)
    result["pullback"] = dict(equity=round(pb_eq,2), cash=round(pbv["cash"],2),
                              open=list(pbv["positions"].keys()),
                              pending={s: dict(limit=v["limit"], age_days=round((time.time()*1000-v["created"])/86400000,1)) for s,v in pbv["pending"].items()},
                              trades=len(pbv["trades"]))

    # ---------- TACTICAL (4h Donchian 20 + 2.5xATR trail) ----------
    for sym in SYMBOLS:
        if sym not in daily: continue
        reg_on = result["regime"][sym]["regime_on"]
        try:
            k = get_klines(sym, "4h", 300)
        except Exception as e:
            result["errors"].append(f"{sym}: 4h klines failed: {e}"); continue
        h4 = [float(x[2]) for x in k[:-1]]; l4 = [float(x[3]) for x in k[:-1]]
        c4 = [float(x[4]) for x in k[:-1]]
        if len(c4) < 60: continue
        cur_px = float(k[-1][4])
        pos = tac["positions"].get(sym)
        tact_act, tact_meta = signals.tactical_decision(
            c4[-301:], h4[-301:], l4[-301:], reg_on,
            pos["hi_close"] if pos else None)
        a = tact_meta.get("atr"); hh = tact_meta.get("don_hi", 0)
        if pos and mode == "scan" and tact_act == "exit":
            reason = "4h ATR trail" if tact_meta["reason"] == "atr_trail" else "regime off (200D)"
            if True:
                tac["trades"].append(dict(ts=int(time.time()*1000), sym=sym, side="sell",
                    px=round(px,2), qty=pos["qty"], pnl=round(pnl,2),
                    pnl_pct=round(pnl/pos["invested"]*100,2), reason=reason, layer="tactical"))
                result["actions"].append(f"TACTICAL SELL {sym} @ {px:.2f} pnl {pnl:+.2f} ({pnl/pos['invested']*100:+.1f}%) — {reason}")
                del tac["positions"][sym]
            elif pos and tact_act == "hold" and "hi_close" in tact_meta:
                pos["hi_close"] = tact_meta["hi_close"]
        if not tac["positions"].get(sym) and mode == "scan" and tact_act == "enter":
            size = signals.tactical_size(tac["cash"], cur_px, tact_meta.get("atr"))
            if size >= 20.0:
                px = cur_px*(1+FEE)
                tac["cash"] -= size
                tac["positions"][sym] = dict(qty=size/px, invested=round(size,2),
                    entry_px=round(px,2), hi_close=c4[-1], entry_ts=int(time.time()*1000))
                tac["trades"].append(dict(ts=int(time.time()*1000), sym=sym, side="buy",
                    px=round(px,2), reason="4h 20-bar breakout, vol-sized", layer="tactical"))
                result["actions"].append(f"TACTICAL BUY {sym} @ {px:.2f} (${size:.0f} vol-sized, 4h 20-bar high {hh:.2f} broken)")
        if tac["positions"].get(sym):
            p = tac["positions"][sym]
            result["marks"].setdefault("T4H-"+sym, dict(entry=p["entry_px"], now=cur_px,
                pnl_pct=round((cur_px*(1-FEE)-p["entry_px"]*(1+FEE))/p["entry_px"]*(1+FEE)*100, 2)))
    # ---------- equity marks ----------
    eq_core = state["cash"]
    eq_tac = tac["cash"]
    for sym in SYMBOLS:
        try:
            px = float(get_klines(sym, "1d", 2)[-1][4])
        except Exception:
            px = None
        if px:
            if state["positions"].get(sym): eq_core += state["positions"][sym]["qty"]*px
            if tac["positions"].get(sym): eq_tac += tac["positions"][sym]["qty"]*px
    state["equity_hwm"] = max(state.get("equity_hwm", START_CAPITAL), eq_core)
    if mode == "scan":
        json.dump(state, open(STATE,"w"), indent=1)
    result["equity"] = round(eq_core,2)
    result["cash"] = round(state["cash"],2)
    result["tactical_equity"] = round(eq_tac,2)
    result["tactical_cash"] = round(tac["cash"],2)
    result["open"] = {s: {**p, "pnl_pct": result["marks"].get(s,{}).get("pnl_pct")}
                      for s,p in state["positions"].items()}
    result["tactical_open"] = {("T4H-"+s): {**p, "pnl_pct": result["marks"].get("T4H-"+s,{}).get("pnl_pct")}
                               for s,p in tac["positions"].items()}
    result["n_trades"] = len(state["trades"]) + len(tac["trades"])
    result["idle_cash"] = {"core": round(state["cash"],2), "tactical": round(tac["cash"],2),
        "note": "idle paper cash; live deployment parks this in yield (Earn/funding)"}
    print(json.dumps(result, indent=1))

if __name__ == "__main__":
    mode = "scan"
    if len(sys.argv) > 1 and sys.argv[1] == "status": mode = "status"
    if os.environ.get("TREND_STATUS") == "1": mode = "status"
    run(mode)
