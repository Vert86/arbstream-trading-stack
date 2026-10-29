#!/usr/bin/env python3
"""polymarket-radar skill runner.

Runs the full radar pipeline and pushes the dashboard feed. Modes:
  full  (default) — settle, shadow, scan, news, spreads, live creds, LIVE TRADE, push feed
  dry            — same but live trade is REPLACED by live status (no orders can be placed); push feed
  status         — just print ledgers/live state as JSON (no venue calls, no push)

Safety: never prints the private key. Live gates live in live.py: $25/signal,
max 3 open, $85 bankroll cap, price guard vs leader fill, FOK, 6h freshness.
"""
import json, os, subprocess, sys, urllib.request, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
FEED_URL = "https://arbstream.duckdns.org/ingest/polymarket"
TOKEN = os.environ.get("ARBSTREAM_INGEST_TOKEN", "")  # set via env; never commit
CAP = 4000  # per-mode output cap in summary


def run_mode(mode):
    """Run scanner.py <mode> and return (parsed_json_or_none, raw_text)."""
    p = subprocess.run([sys.executable, os.path.join(BASE, "scanner.py"), mode],
                       cwd=BASE, capture_output=True, text=True, timeout=900)
    raw = (p.stdout or "").strip()
    if p.returncode != 0:
        return None, ("ERROR rc=%s: %s" % (p.returncode, (p.stderr or raw)[:300]))
    try:
        return json.loads(raw), raw
    except Exception:
        return None, raw


def jload(name):
    p = os.path.join(BASE, name)
    if not os.path.exists(p):
        return []
    out = []
    for l in open(p):
        if l.strip():
            try:
                out.append(json.loads(l))
            except Exception:
                pass
    return out


def clip(s):
    return s if len(s) <= CAP else s[:CAP] + "…(%d more chars)" % (len(s) - CAP)


def build_feed(live_cred, live_out):
    paper = jload("paper_ledger.jsonl")
    shadow = jload("shadow_ledger.jsonl")
    live = jload("live_ledger.jsonl")
    p_set = [r for r in paper if r.get("status") == "settled"]
    p_pnl = sum(r.get("pnl_usdc") or r.get("realized_pnl_usdc") or 0 for r in p_set)
    s_set = [r for r in shadow if r.get("status") == "settled"]
    s_pnl = sum(r.get("pnl_usdc") or r.get("realized_pnl_usdc") or 0 for r in s_set)
    l_open = [r for r in live if r.get("status") == "open"]
    bal = None
    if live_cred:
        try:
            bal = float(json.loads(live_cred).get("balance_usdc"))
        except Exception:
            bal = None
    dep = None
    if live_out:
        try:
            dep = float(json.loads(live_out).get("deployed_usdc"))
        except Exception:
            pass
    return {
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "paper": {"open_bets": sum(1 for r in paper if r.get("status") == "open"),
                  "wins": sum(1 for r in p_set if (r.get("pnl_usdc") or 0) > 0),
                  "losses": sum(1 for r in p_set if (r.get("pnl_usdc") or 0) < 0),
                  "pnl": round(p_pnl, 2), "bankroll": 500},
        "shadow": {"signals_total": len(shadow),
                   "open": sum(1 for r in shadow if r.get("status") == "open"),
                   "pnl": round(s_pnl, 2), "size_per_signal": 25,
                   "recent": shadow[-12:]},
        "live": {"armed": bool(live_cred and json.loads(live_cred).get("sdk") == "OK"),
                 "balance_usdc": bal,
                 "open_positions": len(l_open),
                 "deployed_usdc": dep if dep is not None else round(sum(r.get("cost_usdc", 0) for r in l_open), 2),
                 "recent_orders": live[-5:]},
        "note": "live mirroring active — copy-shadow only",
    }


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    summary = {"mode": mode, "ok": True, "errors": [], "modes": {}}

    if mode == "status":
        print(json.dumps({"paper": len(jload("paper_ledger.jsonl")),
                          "shadow": len(jload("shadow_ledger.jsonl")),
                          "live": jload("live_ledger.jsonl")}, indent=1))
        return

    # settle resolved LIVE baskets too (no-op until a live basket exists)
    try:
        p = subprocess.run([sys.executable, os.path.join(BASE, "basket_executor.py"), "settle"],
                           cwd=BASE, capture_output=True, text=True, timeout=300)
        summary["modes"]["basket_settle"] = json.loads(p.stdout) if p.stdout.strip() else None
    except Exception as ex:
        summary["errors"].append("basket_settle: %s" % str(ex)[:150])

    for m in ("settle", "shadow", "scan", "news", "spreads"):
        parsed, raw = run_mode(m)
        summary["modes"][m] = parsed if parsed is not None else clip(raw)
        if parsed is None and raw.startswith("ERROR"):
            summary["errors"].append("%s: %s" % (m, raw[:200]))

    # live module
    def run_live(*args):
        p = subprocess.run([sys.executable, os.path.join(BASE, "live.py")] + list(args),
                           cwd=BASE, capture_output=True, text=True, timeout=900)
        out = (p.stdout or "").strip()
        if p.returncode != 0:
            summary["errors"].append("live %s: %s" % (" ".join(args), (p.stderr or out)[:300]))
            summary["ok"] = False
            return None
        try:
            return json.loads(out)
        except Exception:
            return None

    live_cred_raw = None
    p = subprocess.run([sys.executable, os.path.join(BASE, "live.py"), "creds"],
                       cwd=BASE, capture_output=True, text=True, timeout=900)
    live_cred_raw = (p.stdout or "").strip()
    if p.returncode != 0:
        summary["errors"].append("live creds: %s" % (p.stderr or live_cred_raw)[:300])
        summary["ok"] = False
        try:
            summary["modes"]["live"] = json.loads(live_cred_raw)
        except Exception:
            summary["modes"]["live"] = clip(live_cred_raw)
    else:
        summary["modes"]["live"] = json.loads(live_cred_raw) if live_cred_raw.startswith("{") else clip(live_cred_raw)

    live_out = None
    if mode == "dry":
        live_out = run_live("status")
    else:
        live_out = run_live("trade")
    if live_out is not None:
        summary["modes"]["live_action"] = live_out
        summary["live_orders_this_run"] = live_out.get("orders", [])
        if live_out.get("geo_blocked"):
            summary["errors"].append("live: geo-blocked this run")

    feed = build_feed(live_cred_raw if live_cred_raw and live_cred_raw.startswith("{") else None, None)
    summary["feed"] = feed

    # push to dashboard
    push_code = None
    try:
        req = urllib.request.Request(FEED_URL, method="POST",
                                    data=json.dumps(feed).encode(),
                                    headers={"X-Ingest-Token": TOKEN,
                                             "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            push_code = r.status
    except Exception as e:
        push_code = "ERR %s" % str(e)[:150]
        summary["ok"] = False
    summary["pushed_feed"] = push_code

    print(json.dumps(summary, indent=1, default=str))


if __name__ == "__main__":
    main()
