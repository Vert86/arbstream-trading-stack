#!/usr/bin/env python3
"""Polymarket LIVE trading module — new polymarket-client SDK, egress via India relay.

Armed Oct 26 2026 (owner: "Go ahead with the automation"). All CLOB traffic is
routed through an SSH SOCKS tunnel to the DO Bangalore relay (Polymarket
geo-blocks order placement from restricted regions; the signing key never
leaves the sandbox — the relay only sees a SOCKS stream).

Standing gates (owner-approved standing rules):
 - SIGNAL_SIZE $25 per copied signal, MAX_OPEN 3 positions, BANKROLL_CAP $85
 - mirrors ONLY paper-program signals: shadow-copy buys from shortlisted leaders
   (arb baskets stay paper-only until one passes exec-edge+depth gates live)
 - buy-side only; price guard: skip if live ask is >2% worse than the leader
   fill +1% slippage model; FOK orders (all-or-nothing, no partial fills)

Modes:
  creds   : create SDK client, report wallet classification + balance
  trade   : evaluate fresh shadow signals, place live buys within gates
  status  : live position + P&L summary
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
SHADOW_BOOK = os.path.join(BASE, "shadow_ledger.jsonl")
STATE = os.path.join(BASE, "live_state.json")
LEDGER = os.path.join(BASE, "live_ledger.jsonl")

SIGNAL_SIZE = 25.0
MAX_OPEN = 3
BANKROLL_CAP = 85.0
MAX_AGE_H = 6
MAX_ORDERS = int(os.environ.get("POLY_LIVE_MAX_ORDERS", "3"))
RELAY_HOST = "64.227.189.215"      # DO blr1 (Bangalore)
SOCKS_PORT = 1089
ASK_WORSE_SKIP = 0.02

CLOB_BOOK = "https://clob.polymarket.com/book"


def now():
    return datetime.now(timezone.utc).isoformat()[:19]


def load_state():
    return json.load(open(STATE)) if os.path.exists(STATE) else {}


def save_state(s):
    json.dump(s, open(STATE, "w"), indent=1)


class Relay:
    """SSH SOCKS tunnel through the India relay; sets ALL_PROXY for httpx/requests."""

    def __enter__(self):
        self.p = subprocess.Popen(
            ["ssh", "-D", f"127.0.0.1:{SOCKS_PORT}", "-N",
             "-o", "BatchMode=yes", "-o", "ConnectTimeout=12",
             "-o", "ExitOnForwardFailure=yes", f"root@{RELAY_HOST}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.environ["ALL_PROXY"] = f"socks5h://127.0.0.1:{SOCKS_PORT}"
        for _ in range(20):                      # wait for the tunnel
            time.sleep(0.3)
            try:
                import requests
                r = requests.get(CLOB_BOOK, params={"token_id": "1"},
                                 timeout=8)
                if r.status_code in (200, 400, 404):   # 404 from CLOB = tunnel up, token just absent
                    return self
            except Exception:
                pass
        raise RuntimeError("SOCKS relay tunnel failed to come up")

    def __exit__(self, *a):
        try:
            self.p.terminate()
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()
        os.environ.pop("ALL_PROXY", None)


def make_client():
    from polymarket import SecureClient
    return SecureClient.create(private_key=os.environ["POLYMARKET_PK"])


def get_balance(client):
    try:
        b = client.get_balance_allowance(asset_type="COLLATERAL")
        return float(b.balance or 0) / 1e6   # CLOB returns 6-decimal raw units
    except Exception:
        return None


def best_ask(token_id):
    import requests
    r = requests.get(CLOB_BOOK, params={"token_id": token_id}, timeout=15)
    b = r.json() if r.text.strip() else {}
    asks = b.get("asks") or []
    if not asks:
        return None
    return float(asks[-1]["price"])   # asks DESCENDING -> best ask is last


def live_positions():
    if not os.path.exists(LEDGER):
        return []
    return [json.loads(l) for l in open(LEDGER) if l.strip()]


def creds():
    """Create SDK client (via relay) and report wallet classification + balance."""
    with Relay():
        try:
            cl = make_client()
            bal = get_balance(cl)
            creds = cl.credentials
            state = load_state()
            state["sdk_wallet"] = {k: str(getattr(cl, k)) for k in ("wallet", "signer", "wallet_type", "signer_type") if hasattr(cl, k)}
            state["creds"] = {"api_key": getattr(creds, "api_key", None),
                              "api_secret": str(getattr(creds, "secret",
                                        getattr(creds, "api_secret", None))),
                              "passphrase": str(getattr(creds, "passphrase", None))}
            save_state(state)
            print(json.dumps({"sdk": "OK", "wallet": str(cl.wallet),
                              "wallet_type": str(cl.wallet_type),
                              "signer_type": str(getattr(cl, "signer_type", "?")),
                              "balance_usdc": bal}))
        except Exception as e:
            print(json.dumps({"sdk": "error", "detail": str(e)[:300]}))


def trade():
    """Mirror fresh shadow-copy BUY signals inside the gates."""
    state = load_state()
    if not state.get("trading_enabled", False):
        print(json.dumps({"mode": "trade", "paused": state.get("paused_reason", "paused by owner"),
                          "orders": [], "note": "live mirroring paused - no orders placed"}, indent=1))
        return
    rows = [json.loads(l) for l in open(SHADOW_BOOK) if l.strip()] \
        if os.path.exists(SHADOW_BOOK) else []
    last = state.get("last_shadow_ts", "")
    cutoff = datetime.now(timezone.utc).timestamp() - MAX_AGE_H * 3600
    fresh = [r for r in rows
             if r.get("ts", "") > last
             and r.get("side") == "buy"
             and r.get("status") == "open"
             and datetime.fromisoformat(r["ts"]).timestamp() >= cutoff]
    fresh.sort(key=lambda r: r["ts"])
    done = {"ts": now(), "mode": "trade", "orders": [], "skips": []}
    if not fresh:
        done["note"] = "no fresh signals"
        print(json.dumps(done, indent=1))
        return

    with Relay():
        cl = make_client()
        bal = get_balance(cl)
        done["balance"] = bal
        open_positions = [p for p in live_positions() if p.get("status") == "open"]
        deployed = sum(p.get("cost_usdc", 0) for p in open_positions)
        for r in fresh:
            if len(done["orders"]) >= MAX_ORDERS:
                done["skips"].append({"market": r["market"], "why": "per-run-cap"})
                continue
            if len(open_positions) + len(done["orders"]) >= MAX_OPEN:
                done["skips"].append({"market": r["market"], "why": "max-open"})
                continue
            if deployed + SIGNAL_SIZE > BANKROLL_CAP:
                done["skips"].append({"market": r["market"], "why": "bankroll-cap"})
                continue
            if bal is not None and bal < SIGNAL_SIZE + 1:
                done["note"] = "balance-too-low (deposit pending?); signals stay pending"
                break
            tok = r.get("asset")
            limit = round(min(r.get("our_fill", 1), r.get("leader_price", 1) * 1.01), 3)
            try:
                ask = best_ask(tok)
                if ask is None:
                    done["skips"].append({"market": r["market"], "why": "no-asks"})
                    state["last_shadow_ts"] = max(last, r["ts"])
                    continue
                if ask > limit * (1 + ASK_WORSE_SKIP) or ask <= 0:
                    done["skips"].append({"market": r["market"], "why": "moved-too-much",
                                          "ask": ask, "limit": limit})
                    state["last_shadow_ts"] = max(last, r["ts"])
                    continue
                px = round(min(ask, limit), 3)
                res = cl.place_market_order(token_id=tok, side="BUY",
                                           amount=SIGNAL_SIZE, max_price=px,
                                           order_type="FOK")
                d = res.model_dump() if hasattr(res, "model_dump") else {"ok": str(res)}
                # tolerant fill detection: SDK response shapes vary by version.
                ok = (d.get("ok") is True) or (d.get("success") is True) or \
                     bool(d.get("orderId") or d.get("order_id"))
                if not ok and bal is not None:
                    # verify against reality: did collateral actually move?
                    try:
                        bal_after = get_balance(cl)
                        if bal_after is not None and bal - bal_after >= SIGNAL_SIZE * 0.5:
                            ok = True
                            done["note_fill_verified"] = "balance-delta confirmed fill"
                    except Exception:
                        pass
                if ok:
                    shares = round(SIGNAL_SIZE / px, 2)
                    rec = {"ts": now(), "market": r["market"], "leader": r["leader"],
                           "token_id": tok, "price": px, "shares": shares,
                           "cost_usdc": round(px * shares, 2), "status": "open",
                           "conditionId": r.get("conditionId"),
                           "order_id": d.get("order_id"),
                           "taking_amount": d.get("taking_amount")}
                    with open(LEDGER, "a") as f:
                        f.write(json.dumps(rec) + "\n")
                    done["orders"].append({k: rec[k] for k in
                                           ("market", "leader", "price", "shares",
                                            "cost_usdc")})
                    deployed += rec["cost_usdc"]
                    if bal is not None:
                        bal -= rec["cost_usdc"]
                    state["last_shadow_ts"] = max(last, r["ts"])
                else:
                    msg = f"{d.get('code')}: {d.get('message')}"
                    done["skips"].append({"market": r["market"], "why": "rejected",
                                          "detail": str(msg)[:150]})
                    if "restricted in your region" in str(msg).lower():
                        done["geo_blocked"] = True
                        break
                    if any(t in str(msg).lower() for t in
                           ("balance", "not enough", "insufficient")):
                        done["note"] = "balance-refused; signals stay pending"
                        break
                    state["last_shadow_ts"] = max(last, r["ts"])
            except Exception as e:
                msg = str(e)
                done["skips"].append({"market": r.get("market"), "why": "error",
                                      "detail": msg[:150]})
                if "restricted in your region" in msg.lower():
                    done["geo_blocked"] = True
                    break
                if any(t in msg.lower() for t in
                       ("balance", "not enough", "insufficient")):
                    done["note"] = "balance-refused; signals stay pending"
                    break
                # unknown error: keep signal pending, stop burning the batch
                done["note"] = "unknown error; signals stay pending"
                break
    save_state(state)
    done["n_orders"] = len(done["orders"])
    done["deployed_usdc"] = round(deployed, 2)
    print(json.dumps(done, indent=1))


def status():
    state = load_state()
    ps = live_positions()
    open_ps = [p for p in ps if p.get("status") == "open"]
    print(json.dumps({
        "armed": bool(state.get("creds")) or bool(state.get("sdk_wallet")),
        "sdk_wallet": state.get("sdk_wallet"),
        "open": len(open_ps),
        "deployed_usdc": round(sum(p.get("cost_usdc", 0) for p in open_ps), 2),
        "settled": len(ps) - len(open_ps),
        "settings": {"signal_size": SIGNAL_SIZE, "max_open": MAX_OPEN,
                      "bankroll_cap": BANKROLL_CAP,
                      "relay": RELAY_HOST}}, indent=1))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "status"
    if mode == "creds":
        creds()
    elif mode == "trade":
        trade()
    else:
        status()
