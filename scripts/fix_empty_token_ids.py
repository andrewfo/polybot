"""One-time script to repair trades and positions with empty token_id.

Looks up correct token_id from market_cache and updates the rows.
BUY_YES -> tokens[0], BUY_NO -> tokens[1].
"""

import json
import sqlite3
import sys

DB_PATH = "data/bot.db"


def fix_empty_token_ids() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Find trades with empty token_id
    cur.execute("SELECT rowid, * FROM trades WHERE token_id = '' OR token_id IS NULL")
    bad_trades = cur.fetchall()
    print(f"Found {len(bad_trades)} trades with empty token_id")

    for trade in bad_trades:
        market_id = trade["market_id"]
        side = trade["side"]

        # Look up token IDs from market_cache
        cur.execute("SELECT data FROM market_cache WHERE condition_id = ?", (market_id,))
        cache_row = cur.fetchone()
        if not cache_row:
            print(f"  SKIP trade rowid={trade['rowid']}: no cache for market_id={market_id}")
            continue

        market_data = json.loads(cache_row["data"])

        # Extract token IDs (try both Gamma formats)
        token_ids = []
        raw = market_data.get("clobTokenIds") or []
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                raw = []
        if raw and isinstance(raw[0], str):
            token_ids = list(raw)

        if not token_ids:
            tokens = market_data.get("tokens") or []
            if isinstance(tokens, str):
                try:
                    tokens = json.loads(tokens)
                except (json.JSONDecodeError, TypeError):
                    tokens = []
            if tokens and isinstance(tokens[0], dict):
                token_ids = [t["token_id"] for t in tokens if "token_id" in t]

        if len(token_ids) < 2:
            print(f"  SKIP trade rowid={trade['rowid']}: insufficient token_ids={token_ids}")
            continue

        # BUY_YES -> tokens[0], BUY_NO -> tokens[1]
        idx = 0 if side == "BUY_YES" else 1
        correct_token_id = token_ids[idx]

        cur.execute("UPDATE trades SET token_id = ? WHERE rowid = ?", (correct_token_id, trade["rowid"]))
        print(f"  FIXED trade rowid={trade['rowid']}: side={side} -> token_id={correct_token_id[:20]}...")

    # Fix positions with empty token_id
    cur.execute("SELECT rowid, * FROM positions WHERE token_id = '' OR token_id IS NULL")
    bad_positions = cur.fetchall()
    print(f"\nFound {len(bad_positions)} positions with empty token_id")

    for pos in bad_positions:
        market_id = pos["market_id"]
        side = pos["side"]

        cur.execute("SELECT data FROM market_cache WHERE condition_id = ?", (market_id,))
        cache_row = cur.fetchone()
        if not cache_row:
            print(f"  SKIP position rowid={pos['rowid']}: no cache for market_id={market_id}")
            continue

        market_data = json.loads(cache_row["data"])

        token_ids = []
        raw = market_data.get("clobTokenIds") or []
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                raw = []
        if raw and isinstance(raw[0], str):
            token_ids = list(raw)

        if not token_ids:
            tokens = market_data.get("tokens") or []
            if isinstance(tokens, str):
                try:
                    tokens = json.loads(tokens)
                except (json.JSONDecodeError, TypeError):
                    tokens = []
            if tokens and isinstance(tokens[0], dict):
                token_ids = [t["token_id"] for t in tokens if "token_id" in t]

        if len(token_ids) < 2:
            print(f"  SKIP position rowid={pos['rowid']}: insufficient token_ids={token_ids}")
            continue

        idx = 0 if side == "BUY_YES" else 1
        correct_token_id = token_ids[idx]

        # Position uses token_id as PK — need to handle potential conflicts
        # Check if a position with this token_id already exists
        cur.execute("SELECT rowid FROM positions WHERE token_id = ?", (correct_token_id,))
        existing = cur.fetchone()
        if existing and existing["rowid"] != pos["rowid"]:
            # Delete the duplicate (empty-key) row
            cur.execute("DELETE FROM positions WHERE rowid = ?", (pos["rowid"],))
            print(f"  DELETED duplicate position rowid={pos['rowid']} (correct token already exists)")
        else:
            cur.execute("UPDATE positions SET token_id = ? WHERE rowid = ?", (correct_token_id, pos["rowid"]))
            print(f"  FIXED position rowid={pos['rowid']}: side={side} -> token_id={correct_token_id[:20]}...")

    conn.commit()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    fix_empty_token_ids()
