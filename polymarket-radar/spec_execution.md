
## Basket execution (built Oct 29 2026)

`basket_executor.py` executes multi-leg BUY_ALL_YES baskets all-or-nothing.

- **plan** (default): read-only; prints legs, asks, shares, depth and net edge from live books.
- **live**: TRIPLE-GATED (--live + env POLYMARKET_BASKET_LIVE=1 + --event-slug & --max-usd)
  on top of the owner sign-off rule. Equal shares per leg; thinnest-depth leg first;
  any leg failure triggers FOK sells of filled legs at best bid and logs the unwind
  (naked_exposure=true if a sell fails). Requires net edge >= 0.5% and scan age <= 45 min.
- **settle**: leg-scoped settlement, same rule as the paper book (fixed Oct 29).

### Settlement scoping fix (Oct 29 2026)
Polymarket events carry extra sub-markets (a 2-outcome House basket can live in an
8-market event). The settler previously counted YES winners across ALL event markets,
which would flag valid baskets MULTIPLE_YES on resolution. Settlement is now scoped to
the basket's stored legs; legacy rows recover legs from arb_candidates.jsonl.
New paper rows store `legs` + `n_outcomes`.

### Go-live criteria (owner-agreed)
1. Nov 4 settlement: realized net edge >= 0.7% across >= 20 paper baskets.
2. Executor tested on a single live basket only after (1), with owner sign-off.
3. Capital of at least a few hundred USD per basket set; $74 cannot express the trade.
