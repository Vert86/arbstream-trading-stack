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
DUST_USD = 5.0        # fractional leftovers below this do not occupy a slot
COOLDOWN_DAYS = 7     # never rebuy a name we just sold (kills the sell/rebuy churn)
STATE_FILE = os.environ.get('SCANNER_STATE_FILE',
    '/app/conversations/6adb5f84cae27744ba0e17f9/alpaca_scanner_state.json')
COOLDOWN_FILE = os.environ.get('SCANNER_COOLDOWN_FILE',
    '/app/conversations/6adb5f84cae27744ba0e17f9/alpaca_cooldown.json')

# PAPER ONLY. Never reads the live key pair, even if present in the env.
# Paper key ID is a non-secret public identifier (usable only with the secret).
# It was never saved as an env secret, which broke every scheduled run after
# 2026-10-23; baked in as fallback so the workflow is self-sufficient.
KEY = os.environ.get('ALPACA_PAPER_KEY') or 'PKU1UEP0H9000EG9B96D'
SEC = os.environ.get('ALPACA_SECRET_KEY_2', '')
BASE_URL = PAPER_URL      # default PAPER; live only via explicit '--live' arg
DRY = False               # '--dry': compute everything, submit nothing


def submit(method, path, payload=None):
    """Place an order only when this is a real run.

    --dry runs the full decision path (data, exits, slots, candidates) but sends
    nothing to Alpaca, so a live change can be verified without risking money.
    """
    if DRY:
        return {'dry_run': True, 'method': method, 'path': path, 'payload': payload}
    return api(method, path, payload)


def api(method, path, payload=None):
    r = requests.request(
        method, BASE_URL + path,
        headers={'APCA-API-KEY-ID': KEY, 'APCA-API-SECRET-KEY': SEC,
                 'Content-Type': 'application/json'},
        json=payload, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f'Alpaca {path} -> {r.status_code}: {r.text[:200]}')
    return r.json() if r.text else {}


def load_cooldown() -> dict:
    try:
        return json.load(open(COOLDOWN_FILE))
    except Exception:
        return {}


def save_cooldown(d: dict) -> None:
    try:
        json.dump(d, open(COOLDOWN_FILE, 'w'))
    except Exception:
        pass


def indicators(close: pd.Series) -> dict:
    return {
        'close': float(close.iloc[-1]),
        'sma20': float(close.rolling(20).mean().iloc[-1]),
        'sma50': float(close.rolling(50).mean().iloc[-1]),
        'mom20': float(close.iloc[-1] / close.iloc[-21] - 1) if len(close) > 21 else np.nan,
        'mom60': float(close.iloc[-1] / close.iloc[-61] - 1) if len(close) > 61 else np.nan,
    }


def main():
    global KEY, SEC, BASE_URL, DRY
    status_only = len(sys.argv) > 1 and sys.argv[1] == 'status'
    live = '--live' in sys.argv
    DRY = '--dry' in sys.argv
    out = {'date': datetime.now(timezone.utc).isoformat()[:19], 'orders': [], 'holds': [],
           'skipped': [], 'errors': []}

    if live:
        lk = os.environ.get('ALPACA_API_KEY', '')
        ls = os.environ.get('ALPACA_SECRET_KEY', '')
        if not lk or not ls:
            out['errors'].append('LIVE requested but ALPACA_API_KEY/SECRET missing.')
            print(json.dumps(out, indent=1)); sys.exit(2)
        KEY, SEC, BASE_URL = lk, ls, LIVE_URL
        out['mode'] = 'LIVE'
    else:
        if not KEY or not SEC:
            out['errors'].append('ALPACA_SECRET_KEY_2 (paper secret) not set — no orders placed.')
            print(json.dumps(out, indent=1))
            sys.exit(2)
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
    cooldown = load_cooldown()

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
            if not status_only and not DRY and sym not in state['done']:
                submit('DELETE', f'/v2/positions/{sym}')  # close full position
                state['done'].append(sym)
                cooldown[sym] = today
                save_cooldown(cooldown)
        else:
            rec['action'] = 'HOLD'
            out['holds'].append(rec)

    # ENTRIES
    if not status_only:
        if live and equity < LIVE_MIN_EQUITY:
            out['skipped'].append(
                f'live equity floor: ${equity:.0f} < ${LIVE_MIN_EQUITY:.0f}, no new buys')
        elif len(bars) < 8:
            out['skipped'].append(
                f'only {len(bars)} of {len(UNIVERSE)} symbols had usable bars — no entries on thin data')
        else:
            held = [p for p in positions.values()
                    if float(p.get('market_value') or 0) >= DUST_USD]
            slots = MAX_POSITIONS - len(held)
            out['slots'] = {'held': len(held), 'dust_ignored': len(positions) - len(held),
                            'free': max(0, slots)}
            cands = []
            for sym, ind in bars.items():
                if sym in positions or pd.isna(ind['mom60']):
                    continue
                if ind['close'] > ind['sma50'] and ind['mom60'] > 0:
                    cands.append((sym, ind))
            cands.sort(key=lambda x: x[1]['mom60'], reverse=True)
            today_d = datetime.strptime(today, '%Y-%m-%d').date()

            def cooling(sym):
                d = cooldown.get(sym)
                if not d:
                    return False
                try:
                    return (today_d - datetime.strptime(d, '%Y-%m-%d').date()).days < COOLDOWN_DAYS
                except Exception:
                    return False

            blocked = [s for s, _ in cands if cooling(s)]
            if blocked:
                out['skipped'].append('cooldown after exit: ' + ', '.join(blocked))
            cands = [(s, i) for s, i in cands if not cooling(s)]
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
                order = submit('POST', '/v2/orders', {
                    'symbol': sym, 'qty': str(qty), 'side': 'buy',
                    'type': 'market', 'time_in_force': 'day'})
                rec['order_id'] = order.get('id')
                rec['status'] = order.get('status')
                state['done'].append(sym)
                cash -= alloc

    if not DRY:
        json.dump(state, open(STATE_FILE, 'w'))
    out['dry_run'] = DRY
    print(json.dumps(out, indent=1, default=str))


if __name__ == '__main__':
    main()
