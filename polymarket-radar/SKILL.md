# polymarket-radar

Polymarket arb/copy-shadow radar with live mirror trading. Runs the full pipeline and pushes the dashboard feed.

## Run

```
run_skill polymarket-radar            # full run (live mirroring enabled)
run_skill polymarket-radar -- dry     # no live orders (settle/shadow/scan/news/spreads/creds + push)
run_skill polymarket-radar -- status   # ledgers only, no venue calls
```

Full run executes, in order: `scanner.py settle → shadow → scan → news → spreads`, then `live.py creds` (real CLOB balance via Bangalore relay) and `live.py trade` (mirrors fresh shadow BUY signals at $25 within gates: max 3 open, $85 bankroll cap, price guard vs leader fill +1% slippage model, FOK, 6h freshness), then pushes the feed to the ArbStream dashboard ingest.

## Output

A JSON summary: `modes` (each pipeline step's parsed output), `feed` (what was pushed), `live_orders_this_run`, `pushed_feed` (HTTP 200 = ok), `errors`, `ok`.

## Invariants

- Live = copy-shadow mirror only. Arb baskets stay paper until one passes live gates.
- Sizing/gates immutable without owner approval: $25/signal, max 3 open, $85 cap, buy-side only.
- Never print, copy, or log the private key (env `POLYMARKET_PK`).
- Engine files (scanner.py, live.py, ledgers, state) live next to this run.py.
