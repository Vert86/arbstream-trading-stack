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


def paper_gate():
    """Live trading is only allowed after a genuine paper run today.

    Fail closed: missing feed, stale feed, or a payload without scan provenance
    (engine + scan_ok) all block live trading. This is the check the 15:55 paper
    run exists to satisfy.
    """
    try:
        req = urllib.request.Request(FEED["paper"], headers={"User-Agent": "alpaca-gate"})
        d = json.loads(urllib.request.urlopen(req, timeout=30).read())
    except Exception as e:
        return False, f"paper feed unreadable: {type(e).__name__}: {str(e)[:60]}"
    if not d.get("scan_ok"):
        return False, f"paper scan_ok={d.get('scan_ok')!r} errors={d.get('scan_errors')}"
    if not d.get("engine"):
        return False, "paper payload has no engine provenance (hand-built payload?)"
    try:
        ts = datetime.fromisoformat(d["updated_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
    except Exception as e:
        return False, f"paper updated_at unparseable: {e}"
    if age > GATE_MAX_AGE_H:
        return False, f"paper run is {age:.1f}h old (max {GATE_MAX_AGE_H}h)"
    return True, f"paper ok, {age:.1f}h old, engine={d['engine']}, positions={len(d.get('positions') or [])}"


def main():
    missing = [n for n, v in (("DASHBOARD_INGEST_TOKEN", TOKEN), ("ALPACA_PAPER_KEY", PAPER_KEY)) if not v]
    if missing:
        print(json.dumps({"error": f"missing env: {', '.join(missing)}", "orders": 0}))
        return
    live = "--live" in sys.argv
    if live:
        base, key, sec = LIVE_URL, LIVE_KEY, LIVE_SEC
        ok, why = paper_gate()
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
