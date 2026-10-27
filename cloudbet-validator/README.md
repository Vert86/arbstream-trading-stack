# Cloudbet Paper Validator

Paper-only betting validation harness (NO real money). Validates the fundamental-tilt
model against DraftKings odds via ESPN's free API, settles with real results.

Run:  cd /app/cloudbet_validator && python3 scan.py && python3 settle.py && python3 metrics.py

## Status (Oct 18 2026)
- Cloudbet's own API is temporarily degraded: main markets (match_odds/moneyline)
  return SELECTION_DISABLED with price 0 for most near-kickoff events, platform-wide
  rebuild per https://github.com/cloudbet/docs/discussions/170 (user asked to enable
  per-account access: issue #203). Until restored, DK-via-ESPN is the odds source.
- Cloudbet Trading v4 endpoints are Cloudflare-blocked from datacenter IPs (US + SG tested).
- Model: fair = de-margined DK 2-way; fundamental tilt (winPct, netPPG, injuries, HFA)
  blended at 25% in logit space; bet only edges +2%..+10% at odds 1.50-3.00.
- Gates (pre-registered): >=100 settled bets AND ROI > 0 AND avg CLV > 0 before any
  discussion of wiring real money. If avg CLV < 0 at n=100, strategy is dead, stop.
