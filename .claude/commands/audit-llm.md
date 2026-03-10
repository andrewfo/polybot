Audit all LLM calls across the codebase for routing compliance:

1. Find every call to `llm.cheap()`, `llm.frontier()`, or `llm.call()` in the project
2. For each call, verify it uses the correct tier per the TASK_ROUTING map in core/llm.py
3. Verify no frontier tasks can silently fall back to cheap models
4. Verify every call includes cost tracking (logged to llm_costs table)
5. Check that rate limiting semaphores are applied correctly

Report any violations found.
