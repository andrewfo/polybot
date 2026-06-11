"""Global test fixtures — ensure no test ever touches the production database."""

import uuid
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point core.db at a temporary database for every test automatically."""
    import core.db as db_mod

    test_db = tmp_path / f"test_{uuid.uuid4().hex[:8]}.db"
    monkeypatch.setattr(db_mod, "DB_PATH", test_db)
    # Reset cached connection so get_db() opens the new path
    monkeypatch.setattr(db_mod, "_shared_db", None)
    db_mod.ensure_tables()


@pytest.fixture(autouse=True)
def _disable_coingecko_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable CoinGecko request spacing and long 429 backoff in tests.

    Tests that reach the real API can hit genuine 429s (especially while the
    bot is running on the same IP) — without this they'd sleep 30-90s each.
    """
    import core as core_mod

    monkeypatch.setattr(core_mod, "COINGECKO_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(core_mod, "RATE_LIMIT_BACKOFF_BASE", 0.05)
