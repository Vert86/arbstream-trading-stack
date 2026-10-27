#!/usr/bin/env python3
"""test_replay.py — replay history through the LIVE decision+sizing code
(signals.py) and verify exact reproduction of the reference engine.

Deployed config since Oct 26 2026: ETH-only, vol-based sizing
($10 target risk per trade, stop distance = entry to 10-day Donchian low on
core, 2.5xATR14 on tactical), core waterline gate (not modeled in replay;
reference uses the same sizing formula from signals.py).
"""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signals

FEE = 0.00075
DATA = "/root/bt_data.json"
SYMBOLS = ["ETHUSDT"]


def replay_core(daily, i0, i1):
    trades = []
    pos = False; epx = 0.0; qty = 0.0; cash = 170.0
    for i in range(i0, i1):
        lo = max(0, i - 419)
        cl = [b[4] for b in daily[lo:i + 1]]
        h = [b[2] for b in daily[lo:i + 1]]
        l = [b[3] for b in daily[lo:i + 1]]
        act, meta = signals.core_decision(cl, h, l, pos)
        c = cl[-1]; t = daily[i][0]
        if pos and act == "exit":
            px = c * (1 - FEE)
            trades.append((t, "exit", px / epx - 1))
            cash += qty * px; pos = False; qty = 0.0
        if not pos and act == "enter":
            dhi, dlo = signals.donchian_levels(h, l)
            size = signals.core_size(cash, c, dlo)
            if size > 0:
                epx = c * (1 + FEE)
                qty = size / epx; cash -= size; pos = True
                trades.append((t, "enter", None))
    return trades


def ref_core(daily, i0, i1):
    """Independent reference: same rules, sizing written out longhand."""
    trades = []
    pos = False; epx = 0.0; qty = 0.0; cash = 170.0
    for i in range(i0, i1):
        c = daily[i][4]; t = daily[i][0]
        sma = sum(b[4] for b in daily[i - 199:i + 1]) / 200
        hh = max(b[2] for b in daily[i - 20:i])
        ll = min(b[3] for b in daily[i - 10:i])
        if pos and ((c < sma) or (c < ll)):
            px = c * (1 - FEE)
            trades.append((t, "exit", px / epx - 1))
            cash += qty * px; pos = False; qty = 0.0
        if not pos and c > sma and c > hh:
            px = c * (1 + FEE)
            sd = px - ll
            if sd > 0:
                size = min(cash, 10.0 / (sd / px))
                epx = px; qty = size / px; cash -= size; pos = True
                trades.append((t, "enter", None))
    return trades


def replay_tac(h4, reg_on, i0, i1):
    trades = []
    pos_hi = None; epx = 0.0; qty = 0.0; cash = 170.0
    for i in range(i0, i1):
        lo = max(0, i - 299)
        cl = [b[4] for b in h4[lo:i + 1]]
        h = [b[2] for b in h4[lo:i + 1]]
        l = [b[3] for b in h4[lo:i + 1]]
        act, meta = signals.tactical_decision(cl, h, l, reg_on[i], pos_hi)
        c = cl[-1]; t = h4[i][0]
        if pos_hi is not None and act == "exit":
            px = c * (1 - FEE)
            trades.append((t, "exit", px / epx - 1))
            cash += qty * px; pos_hi = None; qty = 0.0
        if pos_hi is None and act == "enter":
            size = signals.tactical_size(cash, c, meta.get("atr"))
            if size > 0:
                epx = c * (1 + FEE)
                qty = size / epx; cash -= size; pos_hi = c
                trades.append((t, "enter", None))
        elif pos_hi is not None and act == "hold":
            pos_hi = meta["hi_close"]
    return trades


def ref_tac(h4, reg_on, i0, i1):
    trades = []
    pos = None; epx = 0.0; qty = 0.0; cash = 170.0; hi = 0.0
    h = [b[2] for b in h4]; l = [b[3] for b in h4]; c = [b[4] for b in h4]
    for i in range(i0, i1):
        t, ci = h4[i][0], c[i]
        # wilder ATR over trailing 300 bars (live window)
        lo = max(0, i - 299)
        a = signals.wilder_atr(h[lo:i + 1], l[lo:i + 1], c[lo:i + 1])
        hh = max(h[j] for j in range(i - 20, i))
        if pos is not None:
            hi = max(hi, ci)
            tr = hi - 2.5 * a
            if (not reg_on[i]) or ci < tr:
                px = ci * (1 - FEE)
                trades.append((t, "exit", px / epx - 1))
                cash += qty * px; pos = None; qty = 0.0
        if pos is None and reg_on[i] and ci > hh and a:
            frac = 2.5 * a / ci
            size = min(cash, 10.0 / frac)
            epx = ci * (1 + FEE)
            qty = size / epx; cash -= size; pos = ci; hi = ci
            trades.append((t, "enter", None))
    return trades


def reg_series(h4, daily, s200):
    out = [False] * len(h4); di = 0
    for i, b in enumerate(h4):
        t = b[0]
        while di < len(daily) - 1 and daily[di + 1][0] <= t: di += 1
        j = di if daily[di][0] + 86400000 <= t else di - 1
        if j >= 199 and s200[j] is not None:
            out[i] = daily[j][4] > s200[j]
    return out


if __name__ == "__main__":
    D = json.load(open(DATA))
    ok = True
    for sym in SYMBOLS:
        daily = D[sym]["daily"]; h4 = D[sym]["h4"]
        s200 = [None] * len(daily); s = 0.0
        for i, b in enumerate(daily):
            s += b[4]
            if i >= 200: s -= daily[i - 200][4]
            if i >= 199: s200[i] = s / 200
        i0 = 210
        r = replay_core(daily, i0, len(daily))
        f = ref_core(daily, i0, len(daily))
        m = r == f
        ok &= m
        ne_r = sum(1 for x in r if x[1] == "enter"); ne_f = sum(1 for x in f if x[1] == "enter")
        print(f"{sym} CORE : replay entries={ne_r} vs ref={ne_f} -> {'EXACT MATCH' if m else 'MISMATCH'}")
        if not m:
            for a, b in zip(r, f):
                if a != b: print("   first diff:", a, "vs", b); break
        reg = reg_series(h4, daily, s200)
        r = replay_tac(h4, reg, i0, len(h4))
        f = ref_tac(h4, reg, i0, len(h4))
        m = r == f
        ok &= m
        ne_r = sum(1 for x in r if x[1] == "enter"); ne_f = sum(1 for x in f if x[1] == "enter")
        print(f"{sym} TACT : replay entries={ne_r} vs ref={ne_f} -> {'EXACT MATCH' if m else 'MISMATCH'}")
        if not m:
            for a, b in zip(r, f):
                if a != b: print("   first diff:", a, "vs", b); break
    print("REPLAY TEST:", "PASS — live code and reference are one system" if ok else "FAIL")
    sys.exit(0 if ok else 1)
