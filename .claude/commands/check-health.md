Run a quick health check of all external dependencies and report status:

1. Check OpenRouter connectivity — make a test call to the cheap model
2. Check Gamma API — fetch one page of active markets
3. Check CoinGecko API — fetch BTC price
4. Check wallet balances (USDC + MATIC) via core/wallet.py
5. Check SQLite database — verify all expected tables exist in data/bot.db
6. Check for any stale orders or orphaned positions in the database
7. Report LLM costs for today and this month

For each check, report: OK / WARNING / FAILED with details.
Run `pytest tests/ -x -q` at the end to verify test suite health.
