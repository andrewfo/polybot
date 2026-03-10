#!/usr/bin/env python3
"""CLI tool for manual testing of the Polymarket bot pipeline.

Usage:
    python scripts/cli.py markets              # Discover markets, show count + sample
    python scripts/cli.py filter --top 5       # Full pipeline: discover → filter → categorize → rank
    python scripts/cli.py categorize "Question" # Classify a question into a category
    python scripts/cli.py llm-test "Prompt"    # Send prompt to cheap model
    python scripts/cli.py costs                # Show LLM cost summary from DB
    python scripts/cli.py wallet               # Show wallet address + balances
"""

import argparse
import asyncio
import logging
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _require_env(name: str) -> str:
    """Return env var value or exit with clear error."""
    val = os.environ.get(name, "")
    if not val:
        print(f"[ERROR] {name} not set. Add it to your .env file.")
        sys.exit(1)
    return val


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def cmd_markets(args: argparse.Namespace) -> None:
    """Discover markets and show count + sample table."""
    _require_env("PRIVATE_KEY")

    from core.client import ClobClientWrapper
    from strategy.market_filter import discover_markets

    client = ClobClientWrapper()
    markets = await discover_markets(client)

    print(f"\nDiscovered {len(markets)} markets\n")

    # Show first 10 as sample
    limit = min(10, len(markets))
    print(f"{'#':<4} {'Question':<60} {'Liquidity':>10} {'End Date':>12}")
    print("-" * 90)
    for i, m in enumerate(markets[:limit]):
        question = m.get("question", "???")[:58]
        liq = m.get("liquidity", m.get("volume", 0))
        end = m.get("end_date_iso", m.get("end_date", ""))[:10]
        print(f"{i+1:<4} {question:<60} ${float(liq or 0):>9,.0f} {end:>12}")

    if len(markets) > limit:
        print(f"\n... and {len(markets) - limit} more. Use 'filter' to narrow down.")


async def cmd_filter(args: argparse.Namespace) -> None:
    """Full pipeline: discover → filter → categorize → rank, show top N."""
    _require_env("PRIVATE_KEY")
    _require_env("OPENROUTER_API_KEY")

    from core.client import ClobClientWrapper
    from core.llm import LLMClient
    from strategy.market_filter import (
        categorize_market,
        discover_markets,
        extract_resolution_params,
        filter_markets,
        rank_candidates,
    )

    client = ClobClientWrapper()

    async with LLMClient() as llm:
        # Discover
        markets = await discover_markets(client)
        print(f"Discovered {len(markets)} markets")

        # Filter
        filtered = await filter_markets(markets, client)
        print(f"Filtered to {len(filtered)} candidates")

        # Categorize
        for m in filtered:
            category = await categorize_market(m, llm)
            m["_category"] = category

        # Extract resolution params for econ/crypto
        for m in filtered:
            cat = m.get("_category", "")
            if cat in ("economics", "crypto"):
                params = await extract_resolution_params(
                    m.get("question", ""), cat, llm,
                    condition_id=m.get("condition_id", ""),
                )
                if params:
                    m["_resolution_params"] = params

        # Rank
        ranked = rank_candidates(filtered)

        top_n = args.top
        show = ranked[:top_n]
        print(f"\nTop {len(show)} markets by score:\n")
        print(f"{'#':<4} {'Score':>5} {'Cat':<12} {'Question':<50} {'Liq':>8}")
        print("-" * 83)
        for i, m in enumerate(show):
            question = m.get("question", "???")[:48]
            score = m.get("_score", 0)
            cat = m.get("_category", "?")
            liq = m.get("liquidity", m.get("volume", 0))
            print(f"{i+1:<4} {score:>5} {cat:<12} {question:<50} ${float(liq or 0):>7,.0f}")


async def cmd_categorize(args: argparse.Namespace) -> None:
    """Classify a question into a category using cheap LLM."""
    _require_env("OPENROUTER_API_KEY")

    from core.llm import LLMClient

    question = args.question

    # Build a minimal market dict for categorize_market
    from strategy.market_filter import categorize_market

    market = {"condition_id": "", "question": question}

    async with LLMClient() as llm:
        category = await categorize_market(market, llm)

    print(f"\nQuestion:  {question}")
    print(f"Category:  {category}")


async def cmd_llm_test(args: argparse.Namespace) -> None:
    """Send a prompt to the cheap model and show the response."""
    _require_env("OPENROUTER_API_KEY")

    from core.llm import LLMClient

    prompt = args.prompt

    async with LLMClient() as llm:
        response = await llm.call(prompt, task_type="summarize")

    print(f"\nPrompt:   {prompt}")
    print(f"Response: {response}")


async def cmd_costs(args: argparse.Namespace) -> None:
    """Show LLM cost summary from the database."""
    from core.db import get_db, get_daily_llm_cost, get_monthly_llm_cost

    daily = get_daily_llm_cost()
    monthly = get_monthly_llm_cost()

    print(f"\nLLM Cost Summary")
    print(f"-" * 40)
    print(f"  Today:      ${daily:.4f}")
    print(f"  This month: ${monthly:.4f}")

    # Show breakdown by model
    db = get_db()
    try:
        rows = list(db.execute(
            "SELECT model, COUNT(*) as calls, SUM(input_tokens) as inp, "
            "SUM(output_tokens) as outp, SUM(cost_usd) as cost "
            "FROM llm_costs GROUP BY model ORDER BY cost DESC"
        ).fetchall())
        if rows:
            print(f"\nBy model:")
            print(f"  {'Model':<40} {'Calls':>6} {'In Tok':>8} {'Out Tok':>8} {'Cost':>8}")
            print(f"  {'-'*74}")
            for row in rows:
                print(f"  {row[0]:<40} {row[1]:>6} {row[2]:>8} {row[3]:>8} ${row[4]:>7.4f}")
    except Exception:
        pass

    # Show breakdown by task type
    try:
        rows = list(db.execute(
            "SELECT task_type, COUNT(*) as calls, SUM(cost_usd) as cost "
            "FROM llm_costs GROUP BY task_type ORDER BY cost DESC"
        ).fetchall())
        if rows:
            print(f"\nBy task:")
            print(f"  {'Task':<25} {'Calls':>6} {'Cost':>8}")
            print(f"  {'-'*42}")
            for row in rows:
                print(f"  {row[0]:<25} {row[1]:>6} ${row[2]:>7.4f}")
    except Exception:
        pass

    print()


async def cmd_wallet(args: argparse.Namespace) -> None:
    """Show wallet address and balances."""
    _require_env("PRIVATE_KEY")

    from core.wallet import Wallet

    wallet = Wallet()
    address = wallet.address

    print(f"\nWallet")
    print(f"-" * 50)
    print(f"  Address: {address}")

    try:
        usdc = wallet.get_usdc_balance()
        print(f"  USDC:    ${usdc:.2f}")
    except Exception as e:
        print(f"  USDC:    [error] {e}")

    try:
        matic = wallet.get_matic_balance()
        has_gas = wallet.has_sufficient_gas()
        gas_status = "OK" if has_gas else "LOW"
        print(f"  MATIC:   {matic:.4f} ({gas_status})")
    except Exception as e:
        print(f"  MATIC:   [error] {e}")

    print()


# ---------------------------------------------------------------------------
# CLI setup
# ---------------------------------------------------------------------------

def main() -> None:
    """Parse arguments and dispatch to the appropriate command handler."""
    parser = argparse.ArgumentParser(
        description="Polymarket Bot CLI - manual testing tool",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # markets
    subparsers.add_parser("markets", help="Discover markets, show count + sample table")

    # filter
    p_filter = subparsers.add_parser("filter", help="Full pipeline: discover -> filter -> categorize -> rank")
    p_filter.add_argument("--top", type=int, default=10, help="Number of top markets to show (default: 10)")

    # categorize
    p_cat = subparsers.add_parser("categorize", help="Classify a question into a category")
    p_cat.add_argument("question", help="The market question to classify")

    # llm-test
    p_llm = subparsers.add_parser("llm-test", help="Send prompt to cheap model, show response")
    p_llm.add_argument("prompt", help="The prompt to send")

    # costs
    subparsers.add_parser("costs", help="Show LLM cost summary from DB")

    # wallet
    subparsers.add_parser("wallet", help="Show wallet address + balances")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    handlers = {
        "markets": cmd_markets,
        "filter": cmd_filter,
        "categorize": cmd_categorize,
        "llm-test": cmd_llm_test,
        "costs": cmd_costs,
        "wallet": cmd_wallet,
    }

    handler = handlers[args.command]
    asyncio.run(handler(args))


if __name__ == "__main__":
    main()
