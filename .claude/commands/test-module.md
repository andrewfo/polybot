Run focused tests for a specific module and diagnose any failures.

Usage: /test-module <module_name>
Example: /test-module aggregator, /test-module kelly, /test-module executor

1. Find the test file: `tests/test_$ARGUMENTS.py`
2. Run it with verbose output: `pytest tests/test_$ARGUMENTS.py -v --tb=long`
3. If any tests fail:
   a. Read the failing test to understand what it expects
   b. Read the source module to find the bug
   c. Fix the issue (in the source, not the test, unless the test is wrong)
   d. Re-run to confirm the fix
   e. Run the full test suite to check for regressions: `pytest tests/ -x -q`
4. Report: tests passed, tests fixed, any remaining issues
