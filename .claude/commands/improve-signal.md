Analyze a signal provider's historical performance and refactor its code to fix weaknesses.

Usage: /improve-signal <signal_name>
Example: /improve-signal resolution_crypto
Example: /improve-signal web_search
Valid signals: resolution_crypto, web_search, prediction_markets, onchain_flow

## Step 1: Pull calibration data from the DB

Query `data/bot.db` to extract this signal's track record:

```sql
-- Brier scores: predicted vs actual for this source
SELECT predicted_probability, actual_outcome, market_question, resolved_at
FROM signal_calibration
WHERE signal_source = '$ARGUMENTS' AND actual_outcome IS NOT NULL
ORDER BY resolved_at DESC;

-- Count total resolved predictions
SELECT COUNT(*) FROM signal_calibration
WHERE signal_source = '$ARGUMENTS' AND actual_outcome IS NOT NULL;

-- Binned calibration: group predictions into 0.1-wide buckets
-- For each bucket compute avg predicted prob vs avg actual outcome
SELECT
  CAST(predicted_probability * 10 AS INT) / 10.0 AS bin_start,
  AVG(predicted_probability) AS avg_predicted,
  AVG(actual_outcome) AS avg_actual,
  COUNT(*) AS n,
  AVG((predicted_probability - actual_outcome) * (predicted_probability - actual_outcome)) AS brier
FROM signal_calibration
WHERE signal_source = '$ARGUMENTS' AND actual_outcome IS NOT NULL
GROUP BY CAST(predicted_probability * 10 AS INT)
ORDER BY bin_start;
```

If fewer than 5 resolved predictions exist, report that there's insufficient data to improve this signal empirically. Instead, do a code-quality review of the signal (skip to Step 3 with a focus on code patterns rather than data-driven fixes).

## Step 2: Diagnose miscalibration patterns

From the binned calibration data, identify:

1. **Overconfident ranges**: bins where avg_predicted is much higher than avg_actual (signal says YES too often)
2. **Underconfident ranges**: bins where avg_predicted is much lower than avg_actual (signal says YES too rarely)
3. **Dead zones**: probability ranges the signal never outputs (lacks granularity)
4. **Extreme bias**: does the signal systematically skew toward 0.5 (timid) or toward extremes (reckless)?
5. **Worst bins**: which buckets have the highest Brier scores?

Also cross-reference with frontier decisions to see if the frontier model is correcting this signal's estimates in a consistent direction:

```sql
-- Compare this signal's estimates to frontier final probabilities
SELECT
  s.probability AS signal_prob,
  s.confidence AS signal_conf,
  f.estimated_prob AS frontier_prob,
  f.effective_prob AS effective_prob,
  s.timestamp
FROM signals s
JOIN frontier_decisions f ON s.market_id = f.market_id
WHERE s.signal_source = 'aggregator_input_$ARGUMENTS'
ORDER BY s.timestamp DESC
LIMIT 50;
```

If the frontier consistently adjusts this signal in the same direction, that's a strong hint about what needs fixing in the signal code.

## Step 3: Read the signal's source code

Read `signals/$ARGUMENTS.py` fully. Also read:
- `signals/base.py` (SignalResult contract)
- `signals/calibration.py` (how multipliers are computed)
- `config/settings.py` (relevant config values for this signal)

Understand:
- What data sources it uses
- How it computes probability and confidence
- What heuristics or thresholds it applies
- Where the LLM is involved (if at all)
- What raw_data it passes to the aggregator

## Step 4: Identify specific code fixes

Map each miscalibration pattern from Step 2 to a root cause in the code. Common fixes:

- **Overconfident at extremes**: add regression toward 0.5 (shrinkage), widen confidence intervals, reduce confidence when data is thin
- **Underconfident / too timid**: the signal may be applying too much smoothing, or defaulting to 0.5 too aggressively on partial data
- **Systematic bias in one direction**: check if thresholds, comparisons, or math formulas have an asymmetry. For resolution_crypto, check if the log-normal model assumptions (drift, vol) are causing directional bias
- **Dead zones**: the signal may have coarse bucketing or if/else logic that skips intermediate values
- **LLM prompt issues** (web_search, prediction_markets): if the cheap model is interpreting evidence incorrectly, fix the prompt template — add calibration instructions, examples of correct outputs, or constraints
- **Stale data**: check cache TTLs and whether the signal is working with outdated information
- **Confidence scoring**: if confidence doesn't correlate with actual accuracy, recalibrate the confidence formula

## Step 5: Implement the fixes

Edit `signals/$ARGUMENTS.py` with targeted changes. Rules:
- Only change what the data says is broken. Don't refactor for aesthetics.
- If fixing a math model (resolution_crypto), show the before/after formula and explain why the new version is more calibrated.
- If fixing an LLM prompt (web_search), keep the prompt changes minimal and targeted — add calibration guardrails, not wholesale rewrites.
- Preserve the SignalResult interface exactly.
- Keep all existing tests passing.

## Step 6: Validate

1. Run `pytest tests/test_$ARGUMENTS.py -v` — all existing tests must pass
2. Run `pytest tests/ -x -q` — no regressions
3. Summarize: what was wrong, what you changed, and what improvement to expect (in terms of calibration, not just code quality)
