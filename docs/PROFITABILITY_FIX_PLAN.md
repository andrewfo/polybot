# Profitability Fix Plan

**Date:** 2026-06-11
**Status:** Proposed
**Based on:** Analysis of `data/bot.db` (65 paper trades, May 18–20 2026) and the pipeline code as of commit `2f53704`.

---

## 1. Diagnosis: why the bot is not profitable

All trading to date is paper, spans only ~48 hours (May 18–20), and **predates the realistic-pricing fixes** (`283cfa0`, `bc3a131`, May 21–22). Headline numbers from the DB:

| Metric | Value |
|---|---|
| Gross paper PnL (59 closed trades) | **+$39.58** (47W / 12L) |
| LLM spend, same 48h window | **$25.37** ($18.35 Opus, $6.97 Sonar, $0.05 cheap) |
| Avg bet size | $6.33 |
| LLM cost per filled trade | ~$0.39 (~6% of notional) |
| Frontier calls ending in "edge below threshold" skip | 746 / 892 (84%) |
| Profit concentration | 37% of PnL from one market (BTC $85k, 12 trades) |

Signal quality (Brier scores, 241 resolved calibration samples — lower is better, 0.25 ≈ always predicting 50%):

| Signal | Brier | Verdict |
|---|---|---|
| `resolution_crypto` (CoinGecko math, free) | **0.048** | Excellent — the only real edge source |
| `web_search` (Sonar, paid) | 0.251 | Zero information |
| `onchain_flow` | 0.253 | Zero information |
| Frontier (Opus) estimate | 0.054 | Did **not** beat market price baseline (0.051, n=7 markets) |

### Root causes, ranked

1. **Fixed LLM cost per decision swamps the edge.** Every candidate market gets 1 Sonar + 1 Opus call (`signals/aggregator.py:606`) before the edge check in `strategy/kelly.py:147`. 84% of Opus calls produce a skip. At $6 average bets, even a true 5% edge yields ~$0.30 expected value per trade vs ~$0.39 in LLM cost.
2. **Reported PnL is inflated.** All trades used the old paper engine: entries at limit price, exits at mid, `fill_price` NULL everywhere. Average trade return of +11.7% of notional would be cut roughly in half by realistic bid-side exits and spread costs.
3. **Two of four signals are noise, one of them paid.** `web_search` and `onchain_flow` carry no predictive information yet dilute `resolution_crypto` in the weighted aggregate, and Sonar is 27% of total LLM spend.
4. **The frontier model adds nothing over the market price** on the sample so far, while costing the most.
5. **Learning engine trained on bad data.** It raised `KELLY_FRACTION` 0.25 → 0.312 citing a "92% win rate" measured under the optimistic pricing regime.
6. **Tight TP/SL churns away edge.** TP +12% / SL −10% pays the spread on every round trip; the best-calibrated signal (`resolution_crypto`, Brier 0.048) argues for holding short-dated positions to resolution instead.

---

## 2. Fix plan

Ordered by impact. Each phase is independently shippable; run the full test suite after each.

### Phase 1 — Stop paying for nothing (cost gate) ✅ highest impact

**Status: IMPLEMENTED (2026-06-11).** Free providers run first; `web_search` (Sonar) and the frontier call are skipped when the free-signal preliminary edge is below `PRE_FRONTIER_EDGE_THRESHOLD` (0.03, env-overridable, learning-tunable, 0 disables). Gate only fires when the free signals alone satisfy the minimum-signal requirements, so no market is skipped for lack of the Sonar input. Skips are recorded in `frontier_decisions` (`skip_reason = "prelim edge below pre-frontier gate"`) and the free signals still feed calibration. Covered by `TestPreFrontierGate` (6 tests).

**Goal:** eliminate the ~80% of Sonar + Opus spend that ends in a skip.

1. In `signals/aggregator.py`, after the cheap signals are collected and the preliminary weighted probability is computed (currently ~line 574), add a **pre-frontier edge gate**:
   - Compute `prelim_edge = abs(preliminary_prob - market_price)`.
   - If `prelim_edge < PRE_FRONTIER_EDGE_THRESHOLD`, return a skip result *without* calling the frontier model. Record it in `frontier_decisions` with `skip_reason = "prelim edge below pre-gate"` so the learning engine still sees it.
2. Move the Sonar `web_search` call **behind the same gate** (or remove it entirely per Phase 2): run the free signals first (`resolution_crypto`, `prediction_markets`, `onchain_flow`), gate, and only then spend money on Sonar + frontier.
3. New setting in `config/settings.py`: `PRE_FRONTIER_EDGE_THRESHOLD = 0.03` (env-overridable, slightly below `MIN_EDGE_THRESHOLD = 0.04` so borderline cases still reach the frontier). Register it with `get_effective_param()` so learning can tune it.
4. Tests: gate skips frontier when prelim edge is low; gate passes when high; skip is logged; no Sonar call when gated.

**Expected effect:** frontier call volume drops from ~890/2d toward ~150/2d → LLM cost from ~$12.7/day to roughly $2–3/day.

### Phase 2 — Cut the paid dead signal, repair the free one

**Status: IMPLEMENTED (2026-06-11).**
- `web_search` benched: weight 0 default, provider not constructed unless `ENABLE_WEB_SEARCH_SIGNAL=true` (no Sonar spend). `onchain_flow` weight 0 in both default and event multiplier sets; still runs and logs calibration.
- Earn-back path in `signals/calibration.py`: a benched source (default weight 0) regains weight once rolling Brier < `BENCHED_EARN_BACK_BRIER` (0.20) over ≥ `BENCHED_EARN_BACK_MIN_SAMPLES` (30) resolved samples, re-entering at `1.0 × ratio`.
- `RESOLUTION_SIGNAL_WEIGHT` raised 1.3 → 2.5 (Brier 0.048 — the only real edge source).
- `/improve-signal onchain_flow` run: diagnosis showed the signal was market-blind — `probability = 0.5 ± tanh(pressure)` produced ~0.48 for every market (all 81 resolved predictions in one bin; identical output for a YES-resolving BTC market and a NO-resolving ETH market on the same day). Fix: probability now anchors on a market-specific baseline (driftless normal approx from target distance, time to expiry, and 14-day realized daily vol the coin fetcher already downloads; barrier markets use the reflection-principle touch probability), tilted by the flow adjustment. Flat 0.5 anchor retained as fallback for event markets / missing data.
- `record_prediction` now upserts the open prediction per (market, source) — 30-minute churn previously created 15+ duplicate calibration rows per market, letting one resolution count as 15 earn-back samples.

**Goal:** stop paying Sonar for a Brier-0.25 signal, and turn `onchain_flow` (free, but currently also Brier ~0.25) into a contributor instead of dilution.

1. Set the aggregation weight of `web_search` to 0 by default and gate it behind a config flag (`ENABLE_WEB_SEARCH_SIGNAL = False`) so Sonar is not called at all when disabled. Keep the provider and its calibration logging so it can earn its way back in via `signals/calibration.py` multipliers.
2. **Improve `onchain_flow` via the `/improve-signal onchain_flow` skill** rather than benching it. It is free (no LLM), so the only cost of keeping it is dilution — worth fixing. The skill's analysis should be driven by the 81 resolved calibration samples; likely weaknesses to investigate:
   - Its inputs (DeFi Llama stablecoins/TVL, Fear & Greed, CoinGecko global) are slow macro aggregates being applied to short-dated price-target markets — probable horizon mismatch.
   - `avg_pred` 0.484 vs `avg_outcome` 0.568 suggests it hugs 0.5 and is systematically under-confident on YES outcomes.
3. While the refactor is being validated, set `onchain_flow`'s aggregation weight to 0 (calibration logging stays on) and let the dynamic multipliers in `signals/calibration.py` restore its weight once its rolling Brier beats a threshold (e.g. < 0.20 over ≥30 resolved samples).
4. Increase the relative weight of `resolution_crypto` for price-target markets; keep `prediction_markets` as the cross-check.
5. Tests: aggregator produces valid output with two active signals; Sonar not called when disabled; calibration rows still recorded for zero-weight signals that ran; weight restoration path for a signal whose rolling Brier improves.

**Expected effect:** removes ~$3.5/day Sonar cost; preliminary probability quality improves immediately (less dilution of the 0.048-Brier signal) and again later if the rebuilt `onchain_flow` earns back weight.

### Phase 3 — Reset poisoned learning state

**Goal:** stop acting on conclusions drawn from pre-fix optimistic data.

1. Deactivate the `KELLY_FRACTION` 0.25→0.312 override in `parameter_overrides` (set `active = 0`), reverting to the configured 0.25.
2. Add a one-off migration/script in `scripts/` that tags all `signal_calibration`, `trades`, and `frontier_decisions` rows with `timestamp < 2026-05-22T20:30:00Z` (the realistic-pricing commit time) as pre-fix — either a new `data_regime` column or an exclusion cutoff constant the learning engine respects.
3. Update `monitoring/learning.py` to ignore pre-fix rows when computing win rates, edge efficiency, and recommendations.
4. Tests: learning engine excludes pre-fix rows; override deactivation path works.

### Phase 4 — Re-validate under honest pricing (measurement, not code)

**Goal:** establish whether real edge exists net of spreads. This is the go/no-go gate for everything else.

1. Run paper trading with `REALISTIC_PRICING = true` for **at least 7 days / ≥100 closed trades** (the prior sample was 48h and one BTC trend drove 37% of profit).
2. Track per the Section 10 paper-summary endpoint: net PnL **after subtracting LLM costs from `llm_costs` over the same window**, win rate, avg return per trade, profit concentration (top market's share of PnL), and Brier per signal.
3. Success criteria to consider live trading: net-of-LLM-cost PnL > 0, top market < 25% of profit, frontier Brier ≤ market-price Brier on ≥30 resolved markets.
4. If frontier still fails to beat the market-price baseline after this run, demote the frontier call: use the preliminary (math-weighted) probability for sizing and reserve Opus for high-edge confirmations only (e.g. `prelim_edge > 0.08`).

### Phase 5 — Fix the trade-management economics

**Goal:** stop paying spread to cap winners.

1. Add a hold-to-resolution path: when `resolution_crypto` is the dominant signal and time-to-expiry is short (e.g. < 7 days), skip the +12% take-profit and hold to resolution (keep the stop-loss as disaster protection).
2. Otherwise widen `TAKE_PROFIT_PCT` (e.g. 0.12 → 0.25) so each round trip clears spread + fee drag with room to spare; keep tick-aware stop floor as is.
3. Make both behaviors env-overridable settings (`HOLD_TO_RESOLUTION_DAYS`, existing `TAKE_PROFIT_PCT`) and visible to the learning engine.
4. Tests: hold-to-resolution path bypasses TP but not SL; TP still fires outside the short-dated window.

---

## 3. Order of execution

```
Phase 1 (cost gate)                ──┐
Phase 2 (signal cuts + /improve-signal onchain_flow) ──┼── ship together, then …
Phase 3 (learning reset)           ──┘
        │
Phase 4 (7-day honest paper run)  ← go/no-go measurement
        │
Phase 5 (TP/hold-to-resolution)   ← can ship during the Phase 4 run; restarts the measurement clock
```

## 4. What success looks like

- LLM spend ≤ $3/day at current scan volume.
- A ≥7-day realistic-pricing paper run with **net PnL (after LLM costs) > 0** and no single market > 25% of profit.
- Learning recommendations derived only from post-fix data.
- Rebuilt `onchain_flow` either earns back aggregation weight (rolling Brier < 0.20 over ≥30 resolved samples) or stays at zero weight with calibration logging on.
- A documented decision on the frontier model's role backed by ≥30 resolved-market Brier comparison vs the market-price baseline.
