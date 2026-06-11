"""One-off migration: reset learning state poisoned by pre-fix optimistic data.

Phase 3 of docs/PROFITABILITY_FIX_PLAN.md. All trading data recorded before
the realistic-pricing fixes (LEARNING_DATA_CUTOFF, 2026-05-22T20:30:00Z) used
entries at limit price and exits at mid, inflating win rates and edge — and
the learning engine acted on it (KELLY_FRACTION raised 0.25 -> 0.312 citing a
"92% win rate"). This script:

1. Deactivates every active row in ``parameter_overrides`` (all of them were
   derived from pre-fix data), reverting each parameter to its configured
   default — notably KELLY_FRACTION back to 0.25.
2. Adds a ``data_regime`` column to ``trades``, ``frontier_decisions``,
   ``signal_calibration``, and ``skipped_markets`` and tags every row as
   ``pre_fix`` or ``post_fix`` relative to the cutoff. The tag is an audit
   aid — enforcement lives in the timestamp filters in monitoring/learning.py
   and signals/calibration.py, which also cover rows written after this
   script runs.

Idempotent: safe to re-run; existing tags are recomputed from timestamps.

Usage:
    python scripts/reset_learning_state.py
"""

import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import LEARNING_DATA_CUTOFF  # noqa: E402
from core import db  # noqa: E402

logger = logging.getLogger(__name__)

# Tables whose rows get tagged with a data regime, keyed by timestamp column.
REGIME_TABLES: dict[str, str] = {
    "trades": "timestamp",
    "frontier_decisions": "timestamp",
    "signal_calibration": "timestamp",
    "skipped_markets": "timestamp",
}


def deactivate_overrides() -> list[dict[str, Any]]:
    """Set active=0 on every active parameter override. Returns the rows deactivated."""
    d = db.get_db()
    if "parameter_overrides" not in d.table_names():
        return []
    deactivated: list[dict[str, Any]] = []
    for row in list(d["parameter_overrides"].rows_where("active = 1")):
        d["parameter_overrides"].update(row["parameter"], {"active": 0})
        deactivated.append(dict(row))
        logger.info(
            "Deactivated override %s: %.4f -> reverting to configured %.4f (applied %s)",
            row["parameter"], row["current_value"], row["original_value"], row["applied_at"],
        )
    return deactivated


def tag_data_regimes(cutoff: str = LEARNING_DATA_CUTOFF) -> dict[str, dict[str, int]]:
    """Tag rows in each regime table as pre_fix/post_fix relative to ``cutoff``.

    Returns {table: {"pre_fix": n, "post_fix": n}}.
    """
    d = db.get_db()
    counts: dict[str, dict[str, int]] = {}
    for table, ts_col in REGIME_TABLES.items():
        if table not in d.table_names():
            continue
        columns = {col.name for col in d[table].columns}
        if "data_regime" not in columns:
            d.execute(f"ALTER TABLE {table} ADD COLUMN data_regime TEXT")
            logger.info("Added data_regime column to %s", table)
        d.execute(
            f"UPDATE {table} SET data_regime = "
            f"CASE WHEN {ts_col} < ? THEN 'pre_fix' ELSE 'post_fix' END",
            [cutoff],
        )
        row = d.execute(
            f"SELECT SUM(data_regime = 'pre_fix'), SUM(data_regime = 'post_fix') "
            f"FROM {table}"
        ).fetchone()
        counts[table] = {"pre_fix": int(row[0] or 0), "post_fix": int(row[1] or 0)}
    return counts


def reset_learning_state(cutoff: str = LEARNING_DATA_CUTOFF) -> dict[str, Any]:
    """Run the full reset. Returns a summary dict for logging/tests."""
    deactivated = deactivate_overrides()
    counts = tag_data_regimes(cutoff)
    return {
        "cutoff": cutoff,
        "overrides_deactivated": [r["parameter"] for r in deactivated],
        "regime_counts": counts,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    summary = reset_learning_state()
    logger.info("Cutoff: %s", summary["cutoff"])
    if summary["overrides_deactivated"]:
        logger.info("Overrides deactivated: %s", ", ".join(summary["overrides_deactivated"]))
    else:
        logger.info("No active overrides to deactivate")
    for table, c in summary["regime_counts"].items():
        logger.info("%s: %d pre_fix, %d post_fix", table, c["pre_fix"], c["post_fix"])


if __name__ == "__main__":
    main()
