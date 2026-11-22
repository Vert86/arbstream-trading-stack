"""Tennis + MMA price scan off the Cloudbet feed.

Honest limitation, stated in the output: without a second book or a validated rating
model there is no independent anchor for these sports, so the de-margined Cloudbet
price IS the estimate and the measured edge is ~0 by construction. This reports what
is actually bettable, it does not invent edges.

Market keys differ by sport: MMA prices a real 3-way mma.1x2 (draw is a real outcome
at ~30+), tennis has NO match-winner market - only correct_score and set props - so the
match-winner price is reconstructed by summing the correct-score lines.
"""
import sys, json
sys.path.insert(0, "/app/cloudbet_validator")
import cloudbet

TENNIS = ["tennis-atp-atp-world-tour-finals-men-singles"]
MMA = ["mma-international-ufc", "mma-international-tc5e1-oktagon-98",
       "mma-international-tc5c8-ksw-123"]


def de_margin(prices):
    inv = {k: 1.0 / v for k, v in prices.items() if v and v > 1.01}
    s = sum(inv.values())
    return ({k: round(v / s, 4) for k, v in inv.items()}, round((s - 1) * 100, 2)) if s else ({}, None)


def _sels(sv):
    return {s.get("outcome"): s.get("price") for s in sv.get("selections", [])
            if (s.get("price") or 0) > 1.01 and s.get("status") == "SELECTION_ENABLED"}


def scan(key, sport):
    out = []
    try:
        games = cloudbet.list_games(key)
    except Exception:
        return out
    for g in games:
        if g.get("status") not in ("TRADING", "PRE_EVENT"):
            continue
        try:
            ev = cloudbet.get_event(g["id"])
        except Exception:
            continue
        rows = {}
        for mn, m in (ev.get("markets") or {}).items():
            for sub, sv in (m.get("submarkets") or {}).items():
                sels = _sels(sv)
                if len(sels) < 2:
                    continue
                if mn.endswith(".1x2") and "period=default" in sub:
                    rows["winner"] = ("1x2", sels)
                elif mn == "tennis.correct_score":
                    # reconstruct match winner: home = score=a:b where a>b
                    h = a = 0.0
                    for o, p in sels.items():
                        sc = o.replace("score=", "")
                        if ":" not in sc:
                            continue
                        x, y = sc.split(":")
                        if x.isdigit() and y.isdigit():
                            if int(x) > int(y): h += 1.0 / p
                            else: a += 1.0 / p
                    if h and a:
                        rows["winner"] = ("derived_from_correct_score",
                                          {"home": round(1 / h, 3), "away": round(1 / a, 3)})
                elif mn == "tennis.team_to_win_a_set" and "winner" not in rows:
                    rows.setdefault("set_prop", ("team_to_win_a_set", sels))
        for label, (mkt, sels) in rows.items():
            if label not in ("winner",):
                continue
            fair, vig = de_margin(sels)
            out.append({"sport": sport, "event_id": g["id"], "match": g.get("name"),
                        "cutoff": g.get("cutoff"), "market": mkt, "prices": sels,
                        "fair": fair, "vig_pct": vig, "anchor": None,
                        "note": "no independent anchor: edge ~0 by construction"})
    return out


if __name__ == "__main__":
    rows = scan_all = []
    for k in TENNIS:
        rows += scan(k, "tennis")
    for k in MMA:
        rows += scan(k, "mma")
    print(json.dumps({"rows": rows,
                      "note": "no independent anchor for tennis/mma: edge is ~0 by construction. "
                              "Report only, never bets."}, indent=1))
