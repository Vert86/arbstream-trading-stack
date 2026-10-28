# Cloudbet Paper Validator

Paper-only betting validation harness (NO real money). Validates the fundamental-tilt
model against DraftKings odds via ESPN's free API, settles with real results.

Run:  cd /app/cloudbet_validator && python3 scan.py && python3 settle.py && python3 metrics.py

## Status (Oct 18 2026)
- CORRECTION Oct 28 (owner review): DraftKings is a RECREATIONAL book, not 'sharp'; the sharp benchmark would be Pinnacle-style closing lines (optional upgrade via a free odds API key). CLV here is measured vs DK's own close — the venue we would actually bet.\n- STRUCTURAL CEILING (null hypothesis, stated plainly): standings/scoring/injuries are public and already in the DK price. If DK prices that information efficiently, expected CLV of this tilt is ~0 minus vig, and the strategy dies at the n=100 CLV gate. The forward test exists to measure exactly this.\n- PRE-REGISTRATION RESTART (Oct 28): the v5 NBA >=8-gp gate was designed AFTER seeing the 2W/10 NBA cohort — post-hoc fitting, owner-flagged. The gate stays but the binding n=100 CLV gate now applies to the v5 cohort only (n=0 as of Oct 28 04:37 UTC); the ungated rule's early-NBA bets are shadow-logged (nba_early_gate tag) so fresh data judges the rule.\n- Cloudbet's own API is temporarily degraded: main markets (match_odds/moneyline)
  return SELECTION_DISABLED with price 0 for most near-kickoff events, platform-wide
  rebuild per https://github.com/cloudbet/docs/discussions/170 (user asked to enable
  per-account access: issue #203). Until restored, DK-via-ESPN is the odds source.
- Cloudbet Trading v4 endpoints are Cloudflare-blocked from datacenter IPs (US + SG tested).
- Model: fair = de-margined DK 2-way; fundamental tilt (winPct, netPPG, injuries, HFA)
  blended at 25% in logit space; bet only edges +2%..+10% at odds 1.50-3.00.
- Gates (pre-registered): >=100 settled bets AND ROI > 0 AND avg CLV > 0 before any
  discussion of wiring real money. If avg CLV < 0 at n=100, strategy is dead, stop.
