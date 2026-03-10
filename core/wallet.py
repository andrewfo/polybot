"""Wallet management for Polygon/Polymarket.

Handles private key loading, address derivation, and balance checks
for USDC, MATIC, and Polymarket proxy wallet. All balance checks
cache for 60 seconds to avoid spamming the RPC.
"""

import logging
import os
import time
from typing import Any

from eth_account import Account
from web3 import Web3

logger = logging.getLogger(__name__)

# Polygon USDC contract address (PoS bridged)
USDC_CONTRACT_ADDRESS = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"

# Minimal ERC-20 ABI for balanceOf + decimals
ERC20_ABI: list[dict[str, Any]] = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
]

# Cache TTL in seconds
BALANCE_CACHE_TTL = 60

# Minimum MATIC for gas (~100 transactions)
MIN_GAS_MATIC = 0.1


class WalletError(Exception):
    """Raised on wallet-related failures."""
    pass


class Wallet:
    """Polygon wallet with cached balance checks."""

    def __init__(
        self,
        private_key: str | None = None,
        rpc_url: str | None = None,
    ) -> None:
        self._private_key = private_key or os.environ.get("PRIVATE_KEY")
        if not self._private_key:
            raise WalletError(
                "Private key required. Set PRIVATE_KEY in your .env file. "
                "See .env.example for setup instructions."
            )

        # Normalize: add 0x prefix if missing
        if not self._private_key.startswith("0x"):
            self._private_key = "0x" + self._private_key

        # Derive address
        try:
            account = Account.from_key(self._private_key)
            self._address: str = account.address
        except Exception as e:
            raise WalletError(
                f"Invalid private key. Check PRIVATE_KEY in .env: {e}"
            ) from e

        # Connect to Polygon RPC
        rpc = rpc_url or os.environ.get("POLYGON_RPC_URL", "https://polygon-rpc.com")
        self._w3 = Web3(Web3.HTTPProvider(rpc))

        # USDC contract
        self._usdc_contract = self._w3.eth.contract(
            address=Web3.to_checksum_address(USDC_CONTRACT_ADDRESS),
            abi=ERC20_ABI,
        )

        # Balance cache: key -> (value, timestamp)
        self._cache: dict[str, tuple[float, float]] = {}

        logger.info("Wallet initialized for address %s", self._address)

    @property
    def address(self) -> str:
        """Return the derived Polygon address."""
        return self._address

    @property
    def private_key(self) -> str:
        """Return the private key (needed for CLOB client init)."""
        return self._private_key

    def _get_cached(self, key: str) -> float | None:
        """Return cached value if still valid, else None."""
        if key in self._cache:
            value, ts = self._cache[key]
            if time.monotonic() - ts < BALANCE_CACHE_TTL:
                return value
        return None

    def _set_cached(self, key: str, value: float) -> None:
        """Store a value in the cache."""
        self._cache[key] = (value, time.monotonic())

    def get_usdc_balance(self) -> float:
        """Get USDC balance on Polygon (cached for 60s)."""
        cached = self._get_cached("usdc")
        if cached is not None:
            return cached

        try:
            raw_balance = self._usdc_contract.functions.balanceOf(
                Web3.to_checksum_address(self._address)
            ).call()
            decimals = self._usdc_contract.functions.decimals().call()
            balance = raw_balance / (10 ** decimals)
            self._set_cached("usdc", balance)
            logger.debug("USDC balance: %.2f", balance)
            return balance
        except Exception as e:
            logger.error("Failed to fetch USDC balance: %s", e)
            raise WalletError(f"Failed to fetch USDC balance: {e}") from e

    def get_matic_balance(self) -> float:
        """Get MATIC balance for gas (cached for 60s)."""
        cached = self._get_cached("matic")
        if cached is not None:
            return cached

        try:
            raw_balance = self._w3.eth.get_balance(
                Web3.to_checksum_address(self._address)
            )
            balance = float(Web3.from_wei(raw_balance, "ether"))
            self._set_cached("matic", balance)
            logger.debug("MATIC balance: %.4f", balance)
            return balance
        except Exception as e:
            logger.error("Failed to fetch MATIC balance: %s", e)
            raise WalletError(f"Failed to fetch MATIC balance: {e}") from e

    def get_polymarket_balance(self) -> float:
        """Get USDC available in Polymarket proxy wallet.

        This uses the same USDC balance check since Polymarket's proxy wallet
        deposits are reflected in the on-chain USDC balance for the user's
        address. For the actual collateral balance, the CLOB client provides
        this info at order-placement time.
        """
        # Polymarket proxy wallet balance is obtained through the CLOB API,
        # but we provide this as a convenience wrapper. The core/client.py
        # ClobClientWrapper.get_balance() method provides the authoritative value.
        return self.get_usdc_balance()

    def has_sufficient_gas(self) -> bool:
        """Return True if MATIC balance > 0.1 (enough for ~100 transactions)."""
        try:
            return self.get_matic_balance() > MIN_GAS_MATIC
        except WalletError:
            return False

    def clear_cache(self) -> None:
        """Clear the balance cache."""
        self._cache.clear()
