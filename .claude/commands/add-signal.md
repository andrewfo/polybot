Scaffold and implement a new signal provider.

Usage: /add-signal <provider_name> <description>
Example: /add-signal social_sentiment "Twitter/X sentiment analysis for crypto tokens"

1. Create `signals/$ARGUMENTS.py` with a class inheriting from `SignalProvider` (see `signals/base.py`)
2. Implement `get_signal()` following the same patterns as existing providers:
   - Use appropriate LLM tier (cheap for data processing, NEVER frontier)
   - Return `SignalResult` with all fields populated
   - Handle errors gracefully (return confidence=0 on failure)
   - Cache results appropriately
3. Register the new provider in `signals/aggregator.py`:
   - Add to `SIGNAL_WEIGHT_MULTIPLIERS` with appropriate weight
   - Add to the provider list in `SignalAggregator.__init__()`
4. Create `tests/test_$ARGUMENTS.py` with:
   - Mocked external API responses
   - Test: valid input → returns SignalResult with probability
   - Test: invalid/missing data → returns confidence=0
   - Test: API failure → graceful degradation
5. Run tests: `pytest tests/test_$ARGUMENTS.py -v`
6. Run full suite: `pytest tests/ -x -q`
