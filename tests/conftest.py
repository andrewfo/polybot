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
