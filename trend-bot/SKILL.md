# Trend Bot (V1 daily Donchian + 200D regime filter) — Binance PAPER TRADING

Runs the backtest-winning strategy (Oct 25 2026 investigation, 2y real data,
all-parameter plateau positive) as a PAPER bot on Binance BTC/ETH:

- Long-only. Universe: BTCUSDT, ETHUSDT.
- Entry: completed daily close > prior 20-day Donchian high AND close > 200D SMA.
- Exit: completed daily close < prior 10-day Donchian low, OR close < 200D SMA (regime off).
- Fees modeled 0.075%/side. Paper capital $340 ($170 per symbol).
- Backtest basis: BTC +136.7%/2y PF 3.26 maxDD -21% (18 trades); ETH +31.8% (15 trades).
- Trades RARELY (~4-9 entries/year). It checks daily but mostly does nothing — by design.

Gate amendment (user-approved Oct 25 2026): trend systems are exempt from the
60% WR floor (structurally impossible); gate = PF > 2, maxDD < 25%, positive
through a regime change. PAPER ONLY. Live trading on real Binance funds
requires explicit owner sign-off after paper validation.

## Usage

    run_skill('trend-bot')             # daily scan + paper trades
    run_skill('trend-bot', 'status')   # read-only: positions vs rules, no trades

Output: JSON with actions, equity, marks, regime state, errors.
Idempotent: entries fire only on the day the 20-day high is first exceeded;
re-running the same day will not re-buy (position already exists). Exits fire
once (position removed from state).

Klines fetched via the user's relay https://arbstream.duckdns.org (no API key needed,
public market data only — no account endpoints, no orders ever sent to Binance).
State file: scripts/state.json in this skill's directory.
