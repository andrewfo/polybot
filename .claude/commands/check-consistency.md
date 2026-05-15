Audit the codebase for cross-file consistency — imports that resolve, config keys that exist, DB columns that match, signal providers that are registered, and dataclass fields that align at every boundary.

Run each check below. For each, report OK or list every violation found with file:line references.

## 1. Import Resolution
- For every `from X import Y` and `import X` in all `.py` files under `config/`, `core/`, `signals/`, `strategy/`, `monitoring/`, `web/`, and `main.py`:
  - Verify the module `X` exists as a file or package
  - Verify the name `Y` is actually defined/exported in that module (function, class, constant, or variable)
- Flag any circular import risks (A imports B which imports A at module level)

## 2. Config Key Consistency
- Read `config/settings.py` and collect every top-level variable name defined there
- Grep all other `.py` files for `from config.settings import ...` or `settings.SOMETHING`
- For every imported/referenced config name, verify it actually exists in `config/settings.py`
- For every config variable in `settings.py`, check if it's used anywhere — flag truly unused ones

## 3. DB Schema ↔ Code Alignment
- Read `core/db.py` and extract every table's column schema from `ensure_tables()`
- Find all `db["table_name"].insert(...)`, `db["table_name"].upsert(...)`, and raw SQL `INSERT INTO` calls
- Verify every column written to in inserts/upserts actually exists in the table schema
- Verify column names in `SELECT` queries and dict key accesses on query results match the schema
- Check that functions like `record_trade()`, `upsert_position()`, `record_signal()` pass all required (non-nullable) columns

## 4. Signal Provider Registration
- Read `signals/aggregator.py` and identify which signal providers are instantiated and called
- Verify each provider class exists and is importable from its declared module
- Verify each provider's `get_signal()` return type matches `SignalResult` from `signals/base.py`
- Verify the `source_name` each provider uses matches the keys in `DEFAULT_SIGNAL_WEIGHT_MULTIPLIERS`
- Verify calibration weight keys in `config/settings.py` match the `source_name` values

## 5. Dataclass Field Contracts
- For `SignalResult`, `AggregatedSignal`, `TradeDecision`, `DepthAnalysis`, and any other dataclasses:
  - Find every place instances are created — verify all required fields are passed
  - Find every place fields are accessed (`.field_name`) — verify those fields exist on the dataclass
  - Check that field types are compatible at handoff points (e.g., aggregator output → Kelly input)

## 6. Function Signature Contracts
- For key public functions (`aggregate()`, `kelly_size()`, `check_depth()`, `execute_trade()`, `discover_markets()`, `filter_markets()`):
  - Find all call sites
  - Verify argument count and keyword names match the function signature
  - Flag any calls passing arguments that the function doesn't accept

## 7. Web API ↔ Backend Alignment
- Read `web/server.py` endpoint handlers
- Verify every DB query or function call in an endpoint references tables/functions that exist
- Verify JSON response keys returned to the frontend match what `frontend/src/` components expect
- Check that API route paths in the backend match the fetch URLs in the frontend

## 8. Test ↔ Source Alignment
- For each test file in `tests/`:
  - Verify the module it imports still exists
  - Verify mocked function/class names match the real signatures
  - Flag tests that reference functions or classes that have been renamed or removed

## Output Format

Produce a summary table:

| Check | Status | Issues |
|-------|--------|--------|
| Import Resolution | OK / N issues | ... |
| Config Keys | OK / N issues | ... |
| DB Schema | OK / N issues | ... |
| Signal Registration | OK / N issues | ... |
| Dataclass Fields | OK / N issues | ... |
| Function Signatures | OK / N issues | ... |
| Web API Alignment | OK / N issues | ... |
| Test Alignment | OK / N issues | ... |

Then list every issue found with:
- **File:line** where the problem is
- **What's wrong** (missing import target, mismatched column, etc.)
- **Suggested fix** (one-liner)
