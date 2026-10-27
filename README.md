# ArbStream Trading Stack

Personal automated-trading research and execution stack. Every strategy here was
built with a validated-backtest-first discipline: research → backtest on real data →
paper/shadow → live only after explicit owner sign-off. Standing gates live in the
individual project docs and are frozen without owner approval.

## Projects

### `funding-radar/`
Live funding-rate monitor across Binance, dYdX and Hyperliquid (public APIs only).
Alerts are **actionable-only**: hedgeable Binance carries (real spot leg + funding
above 25% APR). Unhedgeable tokenized-equity perps and other venues are watch-list
only and never reported. Verified live scan showed ~7 of ~60 high-APR coins have a
real spot leg; hedgeable spikes (e.g. HUMA 66% APR) typically fade within a day.

### `trend-bot/` (paper)
ETH-only Donchian 20/10 breakout core + 200D SMA regime gate, vol-sized at $10 risk
per trade, deposit-waterline entry block. Two layers: daily core ($340 paper) and a
4h tactical sandbox. Includes a **pullback-entry sidecar**: on a core signal it rests
a limit at signal close −1%, cancels on a stop break, 3-day timeout. Backtest (ETH
2018-2026, n=28): fills 28/28 within 3 days (consistent with the validated intraday
mean-reversion), but P&L vs core is noise-level — forward paper decides.

### `polymarket-radar/`
Polymarket copy-shadow + arb radar with live-mirror engine (currently PAUSED by owner).
- Fee model: taker fees per the Mar 30 2026 schedule (crypto 0.07, sports 0.05,
  finance/politics/mentions/tech 0.04, other 0.05, geopolitics free;
  fee = C × rate × p(1−p)).
- Settlement engine: gamma by condition-id/event-slug, CLOB via relay for archived
  markets. First scorecard: 204 settled shadow bets, −$700 with fees, −13.7% per-bet
  ROI (t −1.8); leaders perform worse than market-implied odds.
- CLV tracking (spread-aware): mid + spread captured at +1h and +24h per signal;
  observations wider than 5% dropped. Primary decision metric for any mirror re-arm.
- Arb gate: net-of-fee edge must annualize above the idle-USDC baseline (~4%) over
  the event's lockup period, plus depth checks. Election-week short-dated baskets
  pass the math but capacity is only tens of dollars.

### `cloudbet-validator/`
Paper sports-betting validator (never places real bets). Scans priced games,
settles paper positions, tracks ROI and closing-line value. Real account balance is
read-only monitored via the Cloudbet Account API.

### `alpaca-scanner/`
Momentum V1 (buy above SMA50, never sell at a loss, ratchet profits), hard $800
equity floor. Paper account runs as the control group; live account self-gates on
paper success. Paper default; `--live` requires explicit authorization.

## Research record (selected verdicts)

- **Deriv synthetic indices**: negative EV structurally at every tested leverage —
  gross ≈ 0, net EV = commission. No rebuild.
- **MAX (MaiCoin) grid**: intraday mean-reversion is real (heteroskedasticity-robust
  VR 0.82-0.90 out to 4h; Binance control 0.95-0.90), and a correctly specified grid
  collects ~9.4 completed cycles/week (~44%/yr advertised-style on $10k, compounding)
  — but total P&L only beats 50/50 hold by +0.8%/yr over 7.8y, concentrated in two
  survivor years, with −60% drawdown in 2022. No build: opposite bet to the trend
  core, deposit-waterline breach. The cheaper use of the intraday reversion is the
  pullback-entry sidecar above.
- **Polymarket copy-mirroring**: paused; leaders show negative CLV at their own fills.

## Security

No credentials in this repo. `live_state.json` (CLOB credentials) is git-ignored;
secrets are injected via environment. Wallet isolation: dedicated MetaMask wallet
for all Polymarket/DEX activity.

## Layout notes

Each project is self-contained with its own state/ledger files (paper data is
included for transparency). Skills are designed to be relocated: all paths derive
from `__file__`.
