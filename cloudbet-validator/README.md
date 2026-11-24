# Cloudbet live betting

Real-money daily betting on Cloudbet. One $4 flat bet per day, and only when the
draw-adjusted EV is positive (owner rule, 22 Nov 2026).

- `daily_analysis.py` (in the conversation's cloudbet_daily dir) prices the board.
  It lives there because it imports `model.py` and `cloudbet.py` from here.
- `cloudbet.py` - read-only Cloudbet feed client (list_games, get_event, main_market).
- `model.py` - fundamental tilt model (used by the live job, keep it).
- `other_sports.py` - tennis/MMA scan, watch only, never bet.
- `live_bets.jsonl` - every real bet placed, one JSON row per bet.
- `pick_today.json` - today's decision, including the skip flag.
- `API_NOTES.md` - Cloudbet placement API notes (Cloudflare, header auth, schemas).

The paper validation harness (scan/settle/metrics/espn_odds + state) was deleted on
24 Nov 2026 at the owner's request. Do not rebuild it.
