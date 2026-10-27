# Alpaca Scanner (V1 profit-ratchet) — DAILY PAPER TRADING

Runs the V1 scanner strategy on the user's **Alpaca PAPER account** (never live):

- Universe: NVDA,MSFT,AMZN,GOOGL,TSLA,UNH,CRWD,V,ASML,PLTR,SPY,QQQ,TQQQ,SOXL
- Entry: rank by 60-day momentum; buy names with close > SMA50 and mom60 > 0,
  top 5 positions, 20% of equity each (fractional market orders near close).
- Exit (V1, user-selected Oct 23 2026): sell ONLY when in profit (>+0.5%) AND
  upward evidence faded (close < SMA20 OR 20d momentum <= 0).
  Positions in a loss are NEVER sold (held until green).
- PDT-safe: no same-day round trips.

## Requirements

Environment (set via secrets): `ALPACA_SECRET_KEY_2` (paper secret). The paper key ID is baked into the script (public identifier).
Hard-coded to `https://paper-api.alpaca.markets` — refuses live endpoints.
If the paper secret is missing, the script exits with a clear message and NO orders. Arg 'status' = read-only report, no orders. Arg '--live' (with any mode) switches to the REAL account (api.alpaca.markets, keys ALPACA_API_KEY/ALPACA_SECRET_KEY) with a hard safety floor: no new buys if equity < $800. Owner authorized live V1 trading on the $1,010 account Oct 25 2026; paper account continues as A/B control.

## Usage

    run_skill('alpaca-scanner')            # scan + place paper orders
    run_skill('alpaca-scanner', 'status')  # show positions vs exit rules only

Output: JSON with equity, exits, entries, holds, and any skips.
Idempotent: re-running the same day will not duplicate orders.

Backtest basis: 2015–2026, 41.4% CAGR, 98.7% WR on closed trades,
but -57% max drawdown and multi-year underwater locks are the known cost
of the never-realize-losses rule (user accepted this explicitly).
