Run the trading pipeline manually for testing/debugging:

1. Start the FastAPI server if not running: `python main.py --web`
2. Run a single discovery cycle: call `discover_markets()` → `filter_markets()` → log results
3. Pick the top 3 candidates from the filtered list
4. For each candidate, run the full signal pipeline:
   a. Collect signals from all 3 providers
   b. Aggregate signals (frontier model call)
   c. Calculate Kelly sizing
   d. Run depth analysis
   e. Log the TradeDecision (do NOT execute — dry run only)
5. Report: markets scanned, candidates found, trade decisions, total LLM cost

This is a dry run — no orders are placed. Use this to verify the pipeline end-to-end.
