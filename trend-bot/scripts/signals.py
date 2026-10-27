"""signals.py — THE single source of truth for trend-bot decisions.

Both the live scanner (run.py) and every backtest/replay MUST import decision
logic from here. No implementation may re-derive levels inline. This file exists
because the original deployed bot computed Donchian windows with an off-by-one
(include of the signal candle) that made entries impossible while the backtest,
a separate implementation, used correct windows: two systems, one claimed rule.

Conventions (fixed, do not change without owner sign-off):
  - Input arrays are COMPLETED candles; the LAST element is the decision candle.
  - Donchian entry/exit levels are computed over the PRIOR N candles, EXCLUDING
    the decision candle.
  - Regime gate: close > SMA200 (daily closes) on the decision candle.
  - Tactical exit: trailing stop = max(close since entry) - 2.5 * ATR14 (Wilder,
    k=2/15) evaluated on the decision candle.
"""

DON_ENTRY_N = 20   # breakout lookback (core daily + tactical 4h)
DON_EXIT_N = 10     # core daily exit lookback
SMA_N = 200         # regime gate
ATR_TRAIL = 2.5     # tactical trailing-stop multiple
ATR_N = 14


def donchian_levels(highs, lows, entry_n=DON_ENTRY_N, exit_n=DON_EXIT_N):
    """Prior-N Donchian levels EXCLUDING the last (decision) candle."""
    return max(highs[-entry_n - 1:-1]), min(lows[-exit_n - 1:-1])


def sma(vals, n=SMA_N):
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def wilder_atr(highs, lows, closes, n=ATR_N):
    """Wilder ATR14 (k=2/15), recursive from the start of the given window.
    Callers must pass the same window length (live passes its full 300-bar
    completed history) so backtests and live agree exactly."""
    if len(closes) < n + 1:
        return None
    k = 2.0 / (n + 1)
    a = highs[0] - lows[0]
    for i in range(1, len(closes)):
        pc = closes[i - 1]
        tr = max(highs[i] - lows[i], max(abs(highs[i] - pc), abs(lows[i] - pc)))
        a = tr * k + a * (1 - k)
    return a


def core_decision(closes, highs, lows, in_position):
    """Core daily layer. Returns (action, meta).
    action in {'enter', 'exit', 'hold'}; exit_meta gives the reason."""
    c = closes[-1]
    s = sma(closes)
    hh, ll = donchian_levels(highs, lows)
    regime_on = s is not None and c > s
    if in_position:
        if not regime_on:
            return 'exit', {'reason': 'regime_off_200d', 'don_lo': ll, 'sma200': s}
        if c < ll:
            return 'exit', {'reason': 'donchian_low_10d', 'don_lo': ll, 'sma200': s}
        return 'hold', {'don_lo': ll, 'sma200': s}
    if regime_on and c > hh:
        return 'enter', {'don_hi': hh, 'sma200': s}
    return 'hold', {'don_hi': hh, 'sma200': s}


def tactical_decision(closes, highs, lows, regime_on, pos_hi_close=None):
    """Tactical 4h layer. pos_hi_close=None means flat.
    Returns (action, meta) with meta['hi_close'] to persist while in a trade."""
    c = closes[-1]
    hh, _ = donchian_levels(highs, lows, entry_n=DON_ENTRY_N, exit_n=DON_ENTRY_N)
    a = wilder_atr(highs, lows, closes)
    if pos_hi_close is not None:
        hi = max(pos_hi_close, c)
        trail = hi - ATR_TRAIL * a if a else None
        if not regime_on:
            return 'exit', {'reason': 'regime_off_200d'}
        if a and c < trail:
            return 'exit', {'reason': 'atr_trail', 'trail': trail}
        return 'hold', {'hi_close': hi, 'trail': trail}
    if regime_on and c > hh:
        return 'enter', {'don_hi': hh, 'atr': a}
    return 'hold', {'don_hi': hh}


# ---- vol-based sizing (declared Oct 26 2026, target risk $10/trade) ----
RISK_PER_TRADE = 10.0

def core_size(cash, c, don_lo, risk=RISK_PER_TRADE, fee=0.00075):
    """Notional such that entry-to-Donchian-low stop distance = risk dollars.
    Capped by available cash. Returns 0.0 if no valid stop distance."""
    px = c * (1 + fee)
    sd = px - don_lo
    if sd <= 0 or px <= 0:
        return 0.0
    return min(cash, risk / (sd / px))

def tactical_size(cash, c, atr, risk=RISK_PER_TRADE, trail_m=ATR_TRAIL):
    """Notional such that the 2.5xATR trailing stop = risk dollars. Capped by cash."""
    if not atr or c <= 0:
        return 0.0
    frac = trail_m * atr / c
    if frac <= 0:
        return 0.0
    return min(cash, risk / frac)
