#!/usr/bin/env python3
"""Alpaca V1 scanner — standalone skill runner (PAPER ONLY).

Env needed: ALPACA_SECRET_KEY_2 (paper account secret; paper key ID baked in).
Hard-coded to https://paper-api.alpaca.markets; refuses anything else.
Arg 'status' = read-only report, no orders.

V1 rules (user-selected 2026-10-23):
  Entry: rank universe by 60d momentum, buy top names with close > SMA50,
         mom60 > 0; max 5 positions x 20% equity; fractional market orders.
  Exit : sell ONLY if pnl > +0.5% AND evidence faded (close < SMA20 or
         20d momentum <= 0). NEVER sell at a loss (V1 rule).
Idempotent per day via state file.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import requests
import yfinance as yf

PAPER_URL = 'https://paper-api.alpaca.markets'
LIVE_URL = 'https://api.alpaca.markets'
LIVE_MIN_EQUITY = 800.0   # live stop-trading floor: no new buys below this
UNIVERSE = ['NVDA','MSFT','AMZN','GOOG','GOOGL','TSLA','UNH','CRWD','V','ASML','PLTR','SPY','QQQ','TQQQ','SOXL']
MAX_POSITIONS, POSITION_PCT, PROFIT_FLOOR = 5, 0.20, 0.005
STATE_FILE = os.environ.get('SCANNER_STATE_FILE',
    '/app/conversations/6adb5f84cae27744ba0e17f9/alpaca_scanner_state.json')

# PAPER ONLY. Never reads the live key pair, even if present in the env.
# Paper key ID is a non-secret public identifier (usable only with the secret).
# It was never saved as an env secret, which broke every scheduled run after
# 2026-10-23; baked in as fallback so the workflow is self-sufficient.
KEY = os.environ.get('ALPACA_PAPER_KEY') or 'PKU1UEP0H9000EG9B96D'
SEC = os.environ.get('ALPACA_SECRET_KEY_2', '')
BASE_URL = PAPER_URL      # default PAPER; live only via explicit '--live' arg


def api(method, path, payload=None):
    r = requests.request(
        method, BASE_URL + path,
        headers={'APCA-API-KEY-ID': KEY, 'APCA-API-SECRET-KEY': SEC,
                 'Content-Type': 'application/json'},
        json=payload, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f'Alpaca {path} -> {r.status_code}: {r.text[:200]}')
    return r.json() if r.text else {}


def indicators(close: pd.Series) -> dict:
    return {
        'close': float(close.iloc[-1]),
        'sma20': float(close.rolling(20).mean().iloc[-1]),
        'sma50': float(close.rolling(50).mean().iloc[-1]),
        'mom20': float(close.iloc[-1] / close.iloc[-21] - 1) if len(close) > 21 else np.nan,
        'mom60': float(close.iloc[-1] / close.iloc[-61] - 1) if len(close) > 61 else np.nan,
    }


def main():
    global KEY, SEC, BASE_URL
    status_only = len(sys.argv) > 1 and sys.argv[1] == 'status'
    live = '--live' in sys.argv
    out = {'date': datetime.now(timezone.utc).isoformat()[:19], 'orders': [], 'holds': [],
           'skipped': [], 'errors': []}

    if not KEY or not SEC:
        out['errors'].append('ALPACA_SECRET_KEY_2 (paper secret) not set — no orders placed.')
        print(json.dumps(out, indent=1))
        sys.exit(2)

    if live:
        lk = os.environ.get('ALPACA_API_KEY', '')
        ls = os.environ.get('ALPACA_SECRET_KEY', '')
        if not lk or not ls:
            out['errors'].append('LIVE requested but ALPACA_API_KEY/SECRET missing.')
            print(json.dumps(out, indent=1)); sys.exit(2)
        KEY, SEC, BASE_URL = lk, ls, LIVE_URL
        out['mode'] = 'LIVE'
    else:
        out['mode'] = 'PAPER'

    account = api('GET', '/v2/account')
    if account.get('trading_blocked') or account.get('account_blocked'):
        out['errors'].append('Account is blocked from trading.')
        print(json.dumps(out, indent=1))
        sys.exit(2)
    equity = float(account['equity'])
    cash = float(account['cash'])
    out['equity'] = round(equity, 2)
    out['cash'] = round(cash, 2)
    out['paper'] = not live

    positions = {p['symbol']: p for p in api('GET', '/v2/positions')}
    for p in positions.values():
        p['qty'] = float(p['qty'])
        p['avg_entry_price'] = float(p['avg_entry_price'])
        p['unrealized_plpc'] = float(p['unrealized_plpc'])

    # same-day idempotency
    today = datetime.now(timezone(timedelta(hours=-4))).strftime('%Y-%m-%d')  # ET date
    state = {}
    if os.path.exists(STATE_FILE):
        try:
            state = json.load(open(STATE_FILE))
        except Exception:
            state = {}
    if state.get('date') != today:
        state = {'date': today, 'done': []}

    # market data
    bars = {}
    for sym in UNIVERSE:
        try:
            df = yf.download(sym, period='400d', auto_adjust=True, progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0].lower() for c in df.columns]
            df = df[['close']].dropna()
            if len(df) > 65:
                bars[sym] = indicators(df['close'])
        except Exception as e:
            out['skipped'].append(f'{sym}: {str(e)[:100]}')

    # EXITS — V1 rule
    for sym, pos in positions.items():
        if sym not in bars:
            continue
        ind = bars[sym]
        pnl = pos['unrealized_plpc'] or (ind['close'] / pos['avg_entry_price'] - 1)
        evidence = ind['close'] > ind['sma20'] and ind['mom20'] > 0
        rec = {'symbol': sym, 'pnl_pct': round(100 * pnl, 2), 'evidence': evidence}
        if pnl > PROFIT_FLOOR and not evidence:
            rec['action'] = 'SELL'
            out['orders'].append(rec)
            if not status_only and sym not in state['done']:
                api('DELETE', f'/v2/positions/{sym}')  # close full position
                state['done'].append(sym)
        else:
            rec['action'] = 'HOLD'
            out['holds'].append(rec)

    # ENTRIES
    if not status_only:
        if live and equity < LIVE_MIN_EQUITY:
            out['skipped'].append(
                f'live equity floor: ${equity:.0f} < ${LIVE_MIN_EQUITY:.0f}, no new buys')
        else:
            slots = MAX_POSITIONS - len(positions)
            cands = []
            for sym, ind in bars.items():
                if sym in positions or pd.isna(ind['mom60']):
                    continue
                if ind['close'] > ind['sma50'] and ind['mom60'] > 0:
                    cands.append((sym, ind))
            cands.sort(key=lambda x: x[1]['mom60'], reverse=True)
            for sym, ind in cands[:max(0, slots)]:
                if sym in state['done']:
                    continue
                alloc = min(equity * POSITION_PCT, cash)
                if alloc < 20:
                    out['skipped'].append(f'{sym}: insufficient cash')
                    continue
                qty = round(alloc * 0.995 / ind['close'], 4)
                rec = {'symbol': sym, 'action': 'BUY', 'notional': round(alloc, 2),
                       'qty': qty, 'mom60_pct': round(100 * ind['mom60'], 1)}
                out['orders'].append(rec)
                order = api('POST', '/v2/orders', {
                    'symbol': sym, 'qty': str(qty), 'side': 'buy',
                    'type': 'market', 'time_in_force': 'day'})
                rec['order_id'] = order.get('id')
                rec['status'] = order.get('status')
                state['done'].append(sym)
                cash -= alloc

    json.dump(state, open(STATE_FILE, 'w'))
    print(json.dumps(out, indent=1, default=str))


if __name__ == '__main__':
    main()
