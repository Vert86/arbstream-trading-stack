"""Cloudbet Feed API client (read-only). Auth via CLOUDBET_API_KEY env."""
import json
import os
import time
import urllib.request
import urllib.error

BASE = "https://sports-api.cloudbet.com/pub/v2/odds"
_cache = {}


def _hdrs():
    key = os.environ.get("CLOUDBET_API_KEY", "")
    return {"Authorization": f"Bearer {key}"}


def get(path, cache_sec=120, retries=2):
    """GET {BASE}/{path} returning parsed JSON, with tiny in-process cache."""
    now = time.time()
    c = _cache.get(path)
    if c and now - c[0] < cache_sec:
        return c[1]
    url = f"{BASE}/{path}"
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=_hdrs())
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode())
            _cache[path] = (now, data)
            return data
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"cloudbet GET {path} failed: {last_err}")


def list_competitions(sport):
    d = get(f"sports/{sport}")
    comps = []
    for cat in d.get("categories", []):
        for c in cat.get("competitions", []):
            comps.append({"key": c["key"], "name": c["name"], "n": c.get("eventCount", 0)})
    return comps


def list_games(competition):
    """Return real games (home+away set, TRADING or pre-event) for a competition."""
    d = get(f"competitions/{competition}")
    games = []
    for e in d.get("events", []):
        if not e.get("home") or not e.get("away"):
            continue
        games.append({
            "id": e["id"],
            "home": e["home"].get("name"),
            "away": e["away"].get("name"),
            "cutoff": e.get("cutoffTime"),
            "status": e.get("status"),
            "name": e.get("name"),
        })
    return games


def get_event(event_id):
    return get(f"events/{event_id}", cache_sec=90)


def main_market(event, prefixes):
    """Find the full-game 2-way/3-way main market for an event.
    prefixes: list of sport prefixes, e.g. ['basketball'].
    Returns list of selections [{outcome, price}] or None."""
    mkts = event.get("markets", {})
    cands = []
    for pref in prefixes:
        cands += [f"{pref}.moneyline", f"{pref}.1x2", f"{pref}.12.full_time"]
    best = None
    for key in cands:
        m = mkts.get(key)
        if not m:
            continue
        for sub_key, sm in m.get("submarkets", {}).items():
            if "period=ft" in sub_key or sub_key == "" or "period=ot" in sub_key:
                sels = [s for s in sm.get("selections", [])
                        if s.get("status") == "SELECTION_ENABLED"
                        and s.get("price") and float(s["price"]) > 1.01
                        and s.get("side") == "BACK"]
                outcomes = {s["outcome"] for s in sels}
                if {"home", "away"} <= outcomes and len(sels) <= 4:
                    if best is None or "period=ft" in sub_key:
                        best = [{"outcome": s["outcome"], "price": float(s["price"]),
                                 "market": key, "submarket": sub_key} for s in sels]
    return best


def fair_probs(selections):
    """Multiplicative de-margin of 2/3-way odds -> {outcome: fair_prob}, plus margin."""
    inv = {s["outcome"]: 1.0 / s["price"] for s in selections}
    overround = sum(inv.values())
    if overround <= 0:
        return None, None
    return {k: v / overround for k, v in inv.items()}, overround - 1.0
