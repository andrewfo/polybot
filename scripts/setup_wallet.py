#!/usr/bin/env python3
"""Interactive wallet setup helper for Polymarket trading bot.

One-time script that walks the user through:
1. Checking for a Polygon wallet (private key)
2. Checking USDC balance on Polygon
3. Checking MATIC balance for gas
4. Verifying Polymarket API credentials
5. Testing that the CLOB client can authenticate

Usage:
    python scripts/setup_wallet.py
"""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


def print_header(title: str) -> None:
    """Print a formatted section header."""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def print_ok(msg: str) -> None:
    """Print a success message."""
    print(f"  [OK] {msg}")


def print_fail(msg: str) -> None:
    """Print a failure message."""
    print(f"  [FAIL] {msg}")


def print_info(msg: str) -> None:
    """Print an info message."""
    print(f"  [INFO] {msg}")


def check_private_key() -> bool:
    """Check if PRIVATE_KEY is set and valid."""
    print_header("Step 1: Polygon Wallet (Private Key)")

    private_key = os.environ.get("PRIVATE_KEY", "")
    if not private_key:
        print_fail("PRIVATE_KEY not found in environment / .env file.")
        print_info("To create a Polygon wallet:")
        print_info("  1. Install MetaMask (browser extension) or use any Ethereum wallet")
        print_info("  2. Create a new wallet or import an existing one")
        print_info("  3. Switch to Polygon network")
        print_info("  4. Export your private key from wallet settings")
        print_info("  5. Add to .env: PRIVATE_KEY=your_private_key_here")
        return False

    try:
        from eth_account import Account
        if not private_key.startswith("0x"):
            private_key = "0x" + private_key
        account = Account.from_key(private_key)
        print_ok(f"Valid private key. Derived address: {account.address}")
        return True
    except Exception as e:
        print_fail(f"Invalid private key: {e}")
        print_info("Make sure your private key is a valid 64-character hex string.")
        return False


def check_usdc_balance() -> bool:
    """Check USDC balance on Polygon."""
    print_header("Step 2: USDC Balance on Polygon")

    try:
        from core.wallet import Wallet
        wallet = Wallet()
        balance = wallet.get_usdc_balance()
        if balance > 0:
            print_ok(f"USDC balance: ${balance:.2f}")
            if balance < 50:
                print_info("Consider adding more USDC for meaningful trading. $50-100 recommended to start.")
            return True
        else:
            print_fail("USDC balance is $0.00")
            print_info("To get USDC on Polygon:")
            print_info("  1. Buy USDC on an exchange (Coinbase, Kraken, etc.)")
            print_info("  2. Withdraw to your Polygon address (make sure to select Polygon network!)")
            print_info("  3. Or bridge from Ethereum using https://wallet.polygon.technology/")
            return False
    except Exception as e:
        print_fail(f"Could not check USDC balance: {e}")
        return False


def check_matic_balance() -> bool:
    """Check MATIC balance for gas."""
    print_header("Step 3: MATIC Balance (Gas)")

    try:
        from core.wallet import Wallet
        wallet = Wallet()
        balance = wallet.get_matic_balance()
        has_gas = wallet.has_sufficient_gas()

        if has_gas:
            print_ok(f"MATIC balance: {balance:.4f} MATIC (sufficient for gas)")
            return True
        elif balance > 0:
            print_fail(f"MATIC balance: {balance:.4f} MATIC (low — need > 0.1 MATIC)")
            print_info("Add more MATIC for gas fees. 0.5-1.0 MATIC is plenty.")
            return False
        else:
            print_fail("No MATIC balance. You need MATIC for gas fees on Polygon.")
            print_info("To get MATIC:")
            print_info("  1. Buy POL/MATIC on an exchange")
            print_info("  2. Withdraw to your Polygon address")
            print_info("  3. 0.5-1.0 MATIC is enough for hundreds of transactions")
            return False
    except Exception as e:
        print_fail(f"Could not check MATIC balance: {e}")
        return False


def check_api_credentials() -> bool:
    """Check Polymarket API credentials."""
    print_header("Step 4: Polymarket API Credentials")

    api_key = os.environ.get("POLYMARKET_API_KEY", "")
    api_secret = os.environ.get("POLYMARKET_API_SECRET", "")
    api_passphrase = os.environ.get("POLYMARKET_API_PASSPHRASE", "")

    if not api_key or not api_secret or not api_passphrase:
        missing = []
        if not api_key:
            missing.append("POLYMARKET_API_KEY")
        if not api_secret:
            missing.append("POLYMARKET_API_SECRET")
        if not api_passphrase:
            missing.append("POLYMARKET_API_PASSPHRASE")

        print_fail(f"Missing credentials: {', '.join(missing)}")
        print_info("To get Polymarket API credentials:")
        print_info("  1. Go to https://polymarket.com and create an account")
        print_info("  2. Go to Settings → API Keys")
        print_info("  3. Generate new API keys")
        print_info("  4. Add all three values to your .env file")
        return False

    print_ok("All API credential env vars are set")
    return True


def check_clob_connection() -> bool:
    """Test CLOB client authentication."""
    print_header("Step 5: CLOB Client Connection Test")

    try:
        from py_clob_client.client import ClobClient

        private_key = os.environ.get("PRIVATE_KEY", "")
        if not private_key.startswith("0x"):
            private_key = "0x" + private_key

        client = ClobClient(
            "https://clob.polymarket.com",
            key=private_key,
            chain_id=137,
        )

        # Try to fetch markets as a connectivity test
        result = client.get_markets()
        if result:
            market_count = len(result) if isinstance(result, list) else len(result.get("data", []))
            print_ok(f"Successfully connected to Polymarket CLOB API ({market_count} markets found)")
            return True
        else:
            print_fail("Connected but received empty response")
            return False

    except Exception as e:
        print_fail(f"Could not connect to CLOB API: {e}")
        print_info("Check your internet connection and API credentials.")
        return False


def main() -> None:
    """Run all setup checks."""
    print("\n" + "=" * 60)
    print("  Polymarket Trading Bot — Wallet Setup")
    print("  This script checks your environment is ready for trading.")
    print("=" * 60)

    results: dict[str, bool] = {}

    # Step 1: Private key
    results["private_key"] = check_private_key()
    if not results["private_key"]:
        print("\n[!] Fix Step 1 before continuing. The remaining steps require a valid private key.")
        sys.exit(1)

    # Steps 2-3: Balances
    results["usdc"] = check_usdc_balance()
    results["matic"] = check_matic_balance()

    # Step 4: API credentials
    results["api_creds"] = check_api_credentials()

    # Step 5: CLOB connection
    results["clob"] = check_clob_connection()

    # Summary
    print_header("Setup Summary")
    all_pass = True
    for step, passed in results.items():
        status = "[OK]" if passed else "[FAIL]"
        print(f"  {status} {step}")
        if not passed:
            all_pass = False

    if all_pass:
        print("\n  All checks passed! You're ready to run the bot.")
        print("  Start with paper trading: python scripts/dry_run.py")
    else:
        print("\n  Some checks failed. Fix the issues above and re-run this script.")
        print("  See .env.example for all required configuration values.")

    print()


if __name__ == "__main__":
    main()
