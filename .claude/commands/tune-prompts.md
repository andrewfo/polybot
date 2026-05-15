Analyze the frontier model's decision history, identify systematic error patterns, and rewrite prompt templates to fix them.

This skill targets the prompts in `signals/aggregator.py` (the frontier prompt) and `signals/temporal.py` (the system prompt / calibration guidance). These are the highest-leverage prompts in the bot — they directly control trade decisions.

## Step 1: Pull frontier decision outcomes

Query `data/bot.db` for frontier decisions that have resolved (matched against signal_calibration or trade PnL):

```sql
-- Frontier decisions with known outcomes
SELECT
  f.market_id,
  f.estimated_prob,
  f.effective_prob,
  f.market_price,
  f.edge,
  f.confidence,
  f.should_trade,
  f.skip_reason,
  f.timestamp,
  t.pnl,
  t.status AS trade_status
FROM frontier_decisions f
LEFT JOIN trades t ON f.market_id = t.market_id
ORDER BY f.timestamp DESC;

-- Calibration outcomes for frontier (aggregator source)
SELECT
  predicted_probability,
  actual_outcome,
  market_question,
  resolved_at
FROM signal_calibration
WHERE signal_source = 'resolution_crypto' AND actual_outcome IS NOT NULL
ORDER BY resolved_at DESC;

-- Full aggregator signals with prompts (raw_data contains the prompts sent)
SELECT
  market_id,
  probability,
  confidence,
  reasoning,
  raw_data,
  timestamp
FROM signals
WHERE signal_source = 'aggregator'
ORDER BY timestamp DESC
LIMIT 30;
```

If fewer than 10 frontier decisions with outcomes exist, report insufficient data. Do a prompt quality review instead (skip to Step 3 focusing on prompt engineering best practices rather than data-driven fixes).

## Step 2: Diagnose frontier error patterns

Analyze the data to find systematic mistakes. Look for:

### 2a. Directional bias
```sql
-- Is the frontier systematically bullish or bearish?
SELECT
  AVG(f.estimated_prob - sc.actual_outcome) AS mean_bias,
  AVG(ABS(f.estimated_prob - sc.actual_outcome)) AS mean_abs_error,
  COUNT(*) AS n
FROM frontier_decisions f
JOIN signal_calibration sc ON f.market_id = sc.market_id
WHERE sc.actual_outcome IS NOT NULL AND sc.signal_source = 'resolution_crypto';
```

### 2b. Calibration by probability range
Does the frontier get it wrong more at high/low probabilities?
```sql
SELECT
  CASE
    WHEN estimated_prob < 0.3 THEN 'low (0-0.3)'
    WHEN estimated_prob < 0.7 THEN 'mid (0.3-0.7)'
    ELSE 'high (0.7-1.0)'
  END AS band,
  AVG(estimated_prob) AS avg_estimate,
  COUNT(*) AS n
FROM frontier_decisions
GROUP BY band;
```

### 2c. Market price anchoring
Is the frontier just echoing the market price instead of forming independent estimates?
```sql
SELECT
  AVG(ABS(estimated_prob - market_price)) AS avg_divergence_from_market,
  AVG(ABS(estimated_prob - 0.5)) AS avg_extremity
FROM frontier_decisions;
```

If avg_divergence < 0.05, the frontier is anchoring too heavily to market price despite the prompt telling it not to.

### 2d. Confidence calibration
When the frontier says confidence=0.8, is it actually right 80% of the time?
```sql
SELECT
  CASE
    WHEN f.confidence < 0.5 THEN 'low (<0.5)'
    WHEN f.confidence < 0.75 THEN 'mid (0.5-0.75)'
    ELSE 'high (0.75+)'
  END AS conf_band,
  AVG(ABS(f.estimated_prob - sc.actual_outcome)) AS avg_error,
  COUNT(*) AS n
FROM frontier_decisions f
JOIN signal_calibration sc ON f.market_id = sc.market_id
WHERE sc.actual_outcome IS NOT NULL AND sc.signal_source = 'resolution_crypto'
GROUP BY conf_band;
```

If high-confidence decisions have the same error rate as low-confidence ones, confidence scoring is broken.

### 2e. Skipped market analysis
Were skips correct? Did we leave money on the table?
```sql
SELECT
  skip_reason,
  COUNT(*) AS n,
  AVG(estimated_prob) AS avg_est,
  AVG(market_price_at_skip) AS avg_market_price
FROM skipped_markets
GROUP BY skip_reason;
```

### 2f. Read actual frontier reasoning
Pull the last 15-20 frontier reasoning strings from the signals table:
```sql
SELECT reasoning, probability, confidence, timestamp
FROM signals
WHERE signal_source = 'aggregator'
ORDER BY timestamp DESC
LIMIT 20;
```

Look for repeated phrases, hedging patterns, or reasoning that doesn't match outcomes. Common issues:
- "Given the uncertainty..." used to justify staying near market price
- Ignoring the math signal's probability in favor of vague qualitative reasoning
- Treating all signals equally despite weight multipliers
- Not accounting for time-to-resolution properly

## Step 3: Read current prompt templates

Read these files completely:
- `signals/aggregator.py` — focus on `_build_frontier_prompt()` (the user prompt) and the system prompt call
- `signals/temporal.py` — focus on `build_frontier_system_prompt()` (system prompt with calibration guidance)

Map each error pattern from Step 2 to a specific section of the prompt that causes it.

## Step 4: Rewrite the prompts

Edit the prompt templates in `signals/aggregator.py` and/or `signals/temporal.py`. Guidelines:

### What to fix based on common patterns:
- **Market price anchoring**: Strengthen the "do not anchor" instruction. Move the market price AFTER the signal data so the model sees evidence first. Add an explicit instruction to state a probability before seeing the market price.
- **Ignoring math signals**: Add stronger weighting language for DIRECT RESOLUTION SOURCE signals. Consider adding "If your estimate differs from the math model by more than X, explain why."
- **Overconfidence**: Add base rate reminders. Add "Consider: what would have to be true for this estimate to be wrong?"
- **Underconfidence / timidity**: Remove hedging-inducing language. Add "If the data strongly supports a probability, commit to it."
- **Bad confidence scoring**: Add explicit calibration for confidence: "confidence=0.8 means you expect to be within 0.1 of the true probability 80% of the time"
- **Time blindness**: Strengthen the temporal calibration in the system prompt if the model is treating imminent and long-dated markets the same way
- **Reasoning quality**: Add "Your reasoning must cite specific data points, not generic statements about uncertainty"

### Rules for prompt changes:
- Keep changes targeted. Don't rewrite the entire prompt if one section is the problem.
- The JSON output schema must not change.
- Don't add new fields to the response — the parser in `aggregate()` must still work.
- Preserve the `date_context_line` and `system_prompt` injection points.
- Test that the prompt still renders correctly by reading the format strings.

## Step 5: Validate

1. Run `pytest tests/test_aggregator.py -v` — all tests must pass
2. Run `pytest tests/ -x -q` — no regressions
3. Summarize: what error patterns you found, what prompt changes you made, and the expected impact on frontier decision quality
