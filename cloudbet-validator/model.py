"""Model v4: market-anchored probability + zero-anchored fundamental tilt.

Honest design notes (v4, after NBA 0-6 postmortem):
- The de-margined sharp-book price IS the probability baseline. It already
  contains home advantage, rest, matchup history, everything the market knows.
- v1-v3 defects fixed here:
  (1) Early-season standings (1-4 games) fed W_WINPCT=12 -> saturated +/-7.5pt
      "fundamental" margins that were pure noise. Now shrunk by games played.
  (2) HFA was added on top of a market price that already prices HFA
      (double counting, systematic home bias).
  (3) Blending toward a ~0.5-anchored p_fund drags every longshot toward 50%
      and manufactures edges with ZERO fundamental signal. The tilt is now a
      point-margin delta added in logit space: zero signal -> p_model == fair,
      so market-only EV (~ -2% vs the book margin) never produces a bet.
- Edge = model_prob * price - 1; only bet +2%..+10% at odds 1.5-3.0.
"""
import datetime as dt
import math

import espn

W_WINPCT = 12.0   # full-season winPct difference -> points equivalent
W_NETPPG = 0.9    # per-game scoring margin -> points equivalent (league units)
TILT_WEIGHT = 0.25  # logit-space weight of the fundamental margin delta
MAX_TILT_PTS = 5.0   # cap fundamental margin swing in league points
MAX_SHIFT_LOGIT = 0.10  # hard cap: model can diverge at most this far from market in logits

MODEL_VERSION = 5   # v5 Oct 28: NBA tilt gated on >=8 games played (week-1 standings/injury noise: NBA cohort 2W/10, -56.5% ROI at gp 0-2)
SHADOW_EDGE_MIN = 0.005  # near-miss shadow band: 0.5%-2% edges, logged for floor calibration, never counted in the gate cohort
MIN_EDGE = 0.02      # require >= +2% EV vs book odds (never negative)
ODDS_MIN, ODDS_MAX = 1.5, 3.0
MAX_EDGE = 0.10  # larger edges at sharp books = suspected miscalibration, skip


def logistic(x):
    return 1.0 / (1.0 + math.exp(-x))


def logit(p):
    p = max(0.02, min(0.97, p))
    return math.log(p / (1 - p))


def fund_margin(league, h, a, h_inj, a_inj):
    """Estimated game margin in league units (positive = home wins by this much).
    Standings components are shrunk by games played; HFA deliberately absent."""
    cfg = espn.LEAGUES[league]
    netppg_h = h.get("netPPG", 0.0) or 0.0
    netppg_a = a.get("netPPG", 0.0) or 0.0
    # NHL: ESPN pointsFor is league points, not goals -> ignore implausible values
    if league == "nhl":
        netppg_h = netppg_h if abs(netppg_h) < 3 else 0.0
        netppg_a = netppg_a if abs(netppg_a) < 3 else 0.0
    gp_h = h.get("gp") or 1
    gp_a = a.get("gp") or 1
    # v5 gate (UNVALIDATED RULE — tested prospectively, not imposed as truth):
    # NBA early-season standings + preseason injury lists are hypothesized noise
    # (rule was designed AFTER the 2W/10 gp 0-2 cohort; owner correctly flagged
    # post-hoc fitting). The gate suppresses REAL bets but scan.py logs what the
    # ungated rule WOULD have bet into the shadow ledger, so fresh data — not the
    # fitting sample — decides whether the gate earns its keep.
    # NCAAB guard (Oct 28 2026): before the current season populates, ESPN can still
    # serve LAST season's records (gp 15-30 in October). Those are stale rosters -
    # zero the tilt rather than bet on them.
    if league == "ncaab" and min(gp_h, gp_a) >= 15 and dt.date.today().month in (10, 11):
        return (0.0, False)
    k = min(1.0, min(gp_h, gp_a) / float(cfg.get("gp_ref", 15)))
    margin = ((h["winPct"] - a["winPct"]) * W_WINPCT + (netppg_h - netppg_a) * W_NETPPG) * k
    margin += (a_inj - h_inj) * cfg["inj_pts"]
    margin = max(-MAX_TILT_PTS, min(MAX_TILT_PTS, margin))
    NBA_GATE = league == "nba" and min(gp_h, gp_a) < 8
    return (0.0, True) if NBA_GATE else (margin, False)


def p_fund(league, h, a, h_inj, a_inj):
    """Legacy-compatible probability view of the margin (for logging only)."""
    cfg = espn.LEAGUES[league]
    m, _g = fund_margin(league, h, a, h_inj, a_inj)
    return logistic(m / cfg["scale"])


def model_prob(fair_prob, league, margin):
    """p_model = market fair adjusted by a capped logit shift from the margin.
    margin > 0 favors home; the away side applies the negated shift."""
    cfg = espn.LEAGUES[league]
    shift = TILT_WEIGHT * (margin / cfg["scale"])
    shift = max(-MAX_SHIFT_LOGIT, min(MAX_SHIFT_LOGIT, shift))
    p = 1 / (1 + math.exp(-(logit(fair_prob) + shift)))
    return max(0.02, min(0.97, p))


def evaluate(league, teams, inj, home_abbr, away_abbr, fair_probs_map, cloudbet_prices):
    """Evaluate a game. Returns dict with per-outcome edges or None if data missing."""
    h = teams.get(home_abbr)
    a = teams.get(away_abbr)
    if not h or not a:
        return None
    h_inj = inj.get(home_abbr, 0)
    a_inj = inj.get(away_abbr, 0)
    m_raw, gated = fund_margin(league, h, a, h_inj, a_inj)
    m_use = 0.0 if gated else m_raw          # real decisions: gate suppresses tilt
    pf = logistic(m_raw / espn.LEAGUES[league]["scale"])   # logged view stays ungated
    out = {}
    for side in ("home", "away"):
        fair = fair_probs_map.get(side)
        price = cloudbet_prices.get(side)
        if fair is None or not price:
            continue
        pm = model_prob(fair, league, m_use if side == "home" else -m_use)
        pm_u = model_prob(fair, league, m_raw if side == "home" else -m_raw)
        out[side] = {
            "price": price,
            "fair": round(fair, 4),
            "p_fund": round(pf if side == "home" else 1 - pf, 4),
            "p_model": round(pm, 4),
            "p_model_ungated": round(pm_u, 4),
            "edge": round(pm * price - 1, 4),
            "edge_ungated": round(pm_u * price - 1, 4),
            "nba_gate": gated,
        }
    return out if out else None
