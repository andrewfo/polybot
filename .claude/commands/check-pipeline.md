Verify that every stage of the trading pipeline is properly connected — data flows through, types match, and no stage silently drops or ignores information from upstream.

Walk the pipeline end-to-end and check each handoff:

1. **Market discovery → filtering** (`strategy/market_filter.py`)
   - `discover_markets()` output fields are all consumed by `filter_markets()` and `rank_candidates()`
   - Gamma API fields (liquidity, volume, spread, clobTokenIds, tokens, endDate) are parsed and forwarded — not silently dropped
   - `extract_clob_token_ids()` output is available downstream for execution
   - Market cache (`db.cache_market`) stores enough fields for later stages to use

2. **Filtering → signal collection** (`signals/aggregator.py`, individual providers)
   - Fields passed to `aggregator.aggregate()` match what providers need in `get_signal()`
   - `resolution_keywords` from `extract_resolution_params()` are forwarded via `**kwargs`
   - Check each provider actually receives and uses the kwargs it needs (especially `resolution_crypto`)

3. **Signal providers → aggregator**
   - Every provider returns a proper `SignalResult` (not raw dicts)
   - `raw_data` dict from each provider contains the fields that `_format_raw_evidence()` expects
   - Any new fields added to a provider's `raw_data` are also handled in `_format_raw_evidence()`
   - Calibration weights from `signals/calibration.py` are actually loaded and applied (not stale defaults)

4. **Aggregator → Kelly sizing** (`strategy/kelly.py`)
   - `AggregatedSignal` fields map correctly to `TradeDecision` inputs
   - `final_probability`, `confidence`, `market_price` are all forwarded
   - Token IDs flow through from market discovery to `TradeDecision.token_id`

5. **Kelly → depth analysis** (`strategy/depth.py`)
   - `bet_size_usd` from Kelly is passed to depth analysis
   - `DepthAnalysis.adjusted_bet_usd` feeds back to override Kelly's sizing
   - Token IDs match between Kelly output and depth input

6. **Kelly/depth → executor** (`strategy/executor.py`)
   - `TradeDecision` is fully consumed by executor (paper or live)
   - All DB recording functions (`db.record_trade`, `db.upsert_position`) receive the right fields
   - No fields are silently None when the DB expects non-null

7. **Executor → learning/calibration feedback loop**
   - `db.record_frontier_decision()` and `db.record_skipped_market()` are called at the right points
   - `signals/calibration.py` predictions are recorded during aggregation (`record_prediction`)
   - `monitoring/learning.py` analyses can read back the data they need from DB tables
   - Learning overrides via `get_effective_param()` actually affect the parameters they target
   - Check `get_active_overrides()` returns are used in Kelly, executor, and market filter

8. **Web API data freshness** (`web/server.py`)
   - Dashboard endpoints return data from the same DB tables the pipeline writes to
   - No stale caches or disconnected data sources between pipeline and API

For each handoff, report:
- OK: data flows correctly
- MISMATCH: field names, types, or values don't align (give specifics)
- GAP: data is produced upstream but ignored or unavailable downstream
- STALE: a module reads cached/default data when fresh data exists

Summarize with a table of all 8 handoffs and their status.
