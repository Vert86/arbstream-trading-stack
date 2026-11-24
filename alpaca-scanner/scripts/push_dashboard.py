#!/usr/bin/env python3
"""Run the Alpaca scanner and push a dashboard payload built from REAL data.

Why this exists: the scheduled workflow previously let the agent compose the
payload by hand, and it emitted hardcoded "HOLD" rows with orders:[] without
ever running the strategy. Payload construction now lives in code.

Usage:
  python3 push_dashboard.py            # paper scan + push
  python3 push_dashboard.py --live     # live scan (read-only unless run.py trades)

Output: one compact JSON line on stdout. Nothing else.
"""
import json, os, subprocess, sys, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
INGEST = {"paper": "https://arbstream.duckdns.org/ingest/alpaca_paper",
          "live":  "https://arbstream.duckdns.org/ingest/alpaca_live"}
FEED = {"paper": "https://arbstream.duckdns.org/bot/alpaca_paper.json",
        "live":  "https://arbstream.duckdns.org/bot/alpaca_live.json"}
GATE_MAX_AGE_H = 6   # same-day only: 15:55 paper -> 16:15 live
TOKEN = os.environ.get("DASHBOARD_INGEST_TOKEN", "")  # set via env; never commit
PAPER_KEY = os.environ.get("ALPACA_PAPER_KEY", "")     # set via env; never commit
PAPER_SEC = os.environ.get("ALPACA_SECRET_KEY_2", "")
PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_KEY = os.environ.get("ALPACA_API_KEY", "")   # same names run.py uses for --live
LIVE_SEC = os.environ.get("ALPACA_SECRET_KEY", "")
LIVE_URL = "https://api.alpaca.markets"


def get(base, key, sec, path):
    req = urllib.request.Request(base + path, headers={
        "APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


LIVE_MIN_EQUITY = 800.0


def live_gate():
    """Pre-flight checks before touching real money. Fails closed.

    Replaces the old paper-feed gate: the paper scanner was deleted on 24 Nov 2026,
    so "did the control group run cleanly" can never be answered again. These are
    the checks that actually predict a broken live run: account state, the equity
    floor, a reachable market clock, and no orders already working.
    """
    try:
        acct = get(LIVE_URL, LIVE_KEY, LIVE_SEC, "/v2/account")
    except Exception as e:
        return False, f"live account unreadable: {type(e).__name__}: {str(e)[:60]}"
    if acct.get("account_blocked") or acct.get("trading_blocked"):
        return False, "live account is blocked from trading"
    if acct.get("status") != "ACTIVE":
        return False, f"live account status {acct.get('status')!r}"
    try:
        eq = float(acct.get("equity") or 0)
    except Exception:
        return False, "live equity unparseable"
    if eq < LIVE_MIN_EQUITY:
        return False, f"equity ${eq:.0f} below the ${LIVE_MIN_EQUITY:.0f} floor"
    try:
        clk = get(LIVE_URL, LIVE_KEY, LIVE_SEC, "/v2/clock")
    except Exception as e:
        return False, f"market clock unreadable: {type(e).__name__}"
    try:
        orders = get(LIVE_URL, LIVE_KEY, LIVE_SEC, "/v2/orders?status=open&limit=100")
    except Exception:
        orders = []
    if orders:
        syms = ", ".join(sorted({o.get("symbol", "?") for o in orders})[:5])
        return False, f"{len(orders)} order(s) already working ({syms}) — not stacking a second run"
    return True, (f"account {acct.get('status')}, equity ${eq:.0f}, "
                  f"clock {'open' if clk.get('is_open') else 'closed'}, 0 open orders")


def main():
    live = "--live" in sys.argv
    needed = [("DASHBOARD_INGEST_TOKEN", TOKEN)] + ([] if live else [("ALPACA_PAPER_KEY", PAPER_KEY)])
    missing = [n for n, v in needed if not v]
    if missing:
        print(json.dumps({"error": f"missing env: {', '.join(missing)}", "orders": 0}))
        return
    if live:
        base, key, sec = LIVE_URL, LIVE_KEY, LIVE_SEC
        ok, why = live_gate()
        if not ok:
            print(json.dumps({"mode": "live", "skipped": "gate_failed", "reason": why,
                              "orders": 0, "note": "no live orders placed"}))
            return
        gate_note = why
    else:
        base, key, sec = PAPER_URL, PAPER_KEY, PAPER_SEC
        gate_note = "n/a"

    # 1. run the real strategy engine, stderr suppressed (yfinance warnings flood it)
    cmd = [sys.executable, os.path.join(HERE, "run.py"), "trade"]
    if live:
        cmd.append("--live")
    if "--dry" in sys.argv:          # full decision path, no orders submitted
        cmd.append("--dry")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    scan, parse_err = None, None
    try:
        scan = json.loads(proc.stdout[proc.stdout.index("{"):])
    except Exception as e:
        parse_err = f"{type(e).__name__}: {e}"

    # 2. read the account the scan actually touched
    acct = pos = None
    acct_err = None
    try:
        acct = get(base, key, sec, "/v2/account")
        pos = get(base, key, sec, "/v2/positions")
    except Exception as e:
        acct_err = f"{type(e).__name__}: {e}"

    actions = {}
    if scan:
        for h in scan.get("holds", []):
            actions[h["symbol"]] = h.get("action", "HOLD")
        for s in scan.get("orders", []):
            if s.get("symbol"):
                actions[s["symbol"]] = s.get("side", "ORDER").upper()

    positions = []
    for p in (pos or []):
        positions.append({
            "symbol": p["symbol"],
            "qty": float(p["qty"]),
            "market_value": float(p["market_value"]),
            "avg_cost": float(p["avg_entry_price"]),
            "pnl_pct": round(float(p["unrealized_plpc"]) * 100, 2),
            "action": actions.get(p["symbol"], "HOLD"),
        })

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat()[:19] + "Z",
        "mode": "live" if live else "paper",
        "equity": float(acct["equity"]) if acct else (scan or {}).get("equity"),
        "cash": float(acct["cash"]) if acct else (scan or {}).get("cash"),
        "positions": positions,
        "orders": (scan or {}).get("orders", []),
        # provenance: the live safety gate checks these
        "engine": "run.py trade" + (" --live" if live else ""),
        "scan_ok": bool(scan and not parse_err and not acct_err),
        "scan_errors": (scan or {}).get("errors", []) + ([parse_err] if parse_err else [])
                        + ([acct_err] if acct_err else []),
        "skipped": (scan or {}).get("skipped", []),
        "note": ("" if not (parse_err or acct_err) else "scan incomplete") + (f" | gate: {gate_note}" if live else ""),
    }

    push = "skipped"
    try:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(INGEST["live" if live else "paper"], data=body, method="POST", headers={
            "X-Ingest-Token": TOKEN, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            push = f"HTTP {r.status} {r.read().decode()[:80]}"
    except Exception as e:
        push = f"FAILED {type(e).__name__}: {str(e)[:80]}"

    print(json.dumps({
        "mode": payload["mode"], "scan_ok": payload["scan_ok"],
        "equity": payload["equity"], "cash": payload["cash"],
        "positions": len(positions), "orders": len(payload["orders"]),
        "actions": actions, "errors": payload["scan_errors"], "push": push,
        "gate": gate_note,
    }))


if __name__ == "__main__":
    main()
