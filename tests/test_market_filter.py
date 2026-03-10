"""Tests for strategy/market_filter.py — market discovery, filtering, categorization, ranking."""

import asyncio
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from strategy.market_filter import (
    VALID_CATEGORIES,
    categorize_market,
    discover_markets,
    extract_resolution_params,
    filter_markets,
    rank_candidates,
    _get_liquidity,
    _get_volume_24h,
    _parse_end_date,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_market(
    condition_id: str = "cond_1",
    question: str = "Will BTC be above $100k?",
    liquidity: float = 5000.0,
    volume_24h: float = 200.0,
    days_until_end: int = 14,
    num_tokens: int = 2,
    category: str = "",
) -> dict:
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_until_end)
    tokens = [{"token_id": f"tok_{i}"} for i in range(num_tokens)]
    m = {
        "condition_id": condition_id,
        "question": question,
        "liquidity": liquidity,
        "volume24hr": volume_24h,
        "end_date_iso": end.isoformat(),
        "tokens": tokens,
    }
    if category:
        m["_category"] = category
    return m


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_get_liquidity(self) -> None:
        assert _get_liquidity({"liquidity": 1234.5}) == 1234.5
        assert _get_liquidity({"totalLiquidity": "999"}) == 999.0
        assert _get_liquidity({}) == 0.0
        assert _get_liquidity({"liquidity": "invalid"}) == 0.0

    def test_get_volume_24h(self) -> None:
        assert _get_volume_24h({"volume24hr": 500}) == 500.0
        assert _get_volume_24h({"volume_24h": "123.45"}) == 123.45
        assert _get_volume_24h({}) == 0.0

    def test_parse_end_date_iso(self) -> None:
        dt = _parse_end_date({"end_date_iso": "2026-04-01T00:00:00+00:00"})
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 4

    def test_parse_end_date_z_suffix(self) -> None:
        dt = _parse_end_date({"end_date": "2026-05-15T12:00:00Z"})
        assert dt is not None
        assert dt.month == 5

    def test_parse_end_date_missing(self) -> None:
        assert _parse_end_date({}) is None

    def test_parse_end_date_invalid(self) -> None:
        assert _parse_end_date({"end_date": "not-a-date"}) is None


# ---------------------------------------------------------------------------
# Filter pipeline tests
# ---------------------------------------------------------------------------

class TestFilterMarkets:
    @pytest.fixture
    def mock_client(self) -> AsyncMock:
        client = AsyncMock(spec=ClobClientWrapper)
        client.get_spread = AsyncMock(return_value=0.03)
        return client

    @pytest.mark.asyncio
    async def test_binary_only_filter(self, mock_client: AsyncMock) -> None:
        """Non-binary markets (!=2 tokens) are eliminated."""
        markets = [
            _make_market(condition_id="binary", num_tokens=2),
            _make_market(condition_id="multi", num_tokens=3),
            _make_market(condition_id="single", num_tokens=1),
        ]
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_open_positions.return_value = []
            result = await filter_markets(markets, mock_client)
        assert len(result) == 1
        assert result[0]["condition_id"] == "binary"

    @pytest.mark.asyncio
    async def test_liquidity_filter(self, mock_client: AsyncMock) -> None:
        """Markets outside liquidity band are eliminated."""
        markets = [
            _make_market(condition_id="low", liquidity=100),      # below MIN
            _make_market(condition_id="good", liquidity=5000),     # in band
            _make_market(condition_id="high", liquidity=100000),   # above MAX
        ]
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_open_positions.return_value = []
            result = await filter_markets(markets, mock_client)
        assert len(result) == 1
        assert result[0]["condition_id"] == "good"

    @pytest.mark.asyncio
    async def test_time_to_resolution_filter(self, mock_client: AsyncMock) -> None:
        """Markets too close or too far from resolution are eliminated."""
        markets = [
            _make_market(condition_id="too_soon", days_until_end=0),
            _make_market(condition_id="good", days_until_end=30),
            _make_market(condition_id="too_far", days_until_end=200),
        ]
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_open_positions.return_value = []
            result = await filter_markets(markets, mock_client)
        assert len(result) == 1
        assert result[0]["condition_id"] == "good"

    @pytest.mark.asyncio
    async def test_spread_filter(self, mock_client: AsyncMock) -> None:
        """Markets with spread >= MAX_SPREAD are eliminated."""
        mock_client.get_spread = AsyncMock(side_effect=[0.03, 0.15])
        markets = [
            _make_market(condition_id="tight", liquidity=5000, days_until_end=30),
            _make_market(condition_id="wide", liquidity=5000, days_until_end=30),
        ]
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_open_positions.return_value = []
            result = await filter_markets(markets, mock_client)
        assert len(result) == 1
        assert result[0]["condition_id"] == "tight"

    @pytest.mark.asyncio
    async def test_volume_filter(self, mock_client: AsyncMock) -> None:
        """Markets with < MIN_24H_VOLUME are eliminated."""
        markets = [
            _make_market(condition_id="active", volume_24h=500),
            _make_market(condition_id="dead", volume_24h=10),
        ]
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_open_positions.return_value = []
            result = await filter_markets(markets, mock_client)
        assert len(result) == 1
        assert result[0]["condition_id"] == "active"

    @pytest.mark.asyncio
    async def test_position_filter(self, mock_client: AsyncMock) -> None:
        """Markets where we already hold a position are eliminated."""
        markets = [
            _make_market(condition_id="cond_new"),
            _make_market(condition_id="cond_existing"),
        ]
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_open_positions.return_value = [
                {"market_id": "cond_existing", "size": 50.0}
            ]
            result = await filter_markets(markets, mock_client)
        assert len(result) == 1
        assert result[0]["condition_id"] == "cond_new"

    @pytest.mark.asyncio
    async def test_full_pipeline(self, mock_client: AsyncMock) -> None:
        """A valid market passes all filters."""
        markets = [_make_market(
            condition_id="perfect",
            liquidity=5000,
            volume_24h=300,
            days_until_end=14,
            num_tokens=2,
        )]
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_open_positions.return_value = []
            result = await filter_markets(markets, mock_client)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Categorization tests
# ---------------------------------------------------------------------------

class TestCategorizeMarket:
    @pytest.mark.asyncio
    async def test_categorize_returns_valid_category(self) -> None:
        llm = AsyncMock(spec=LLMClient)
        llm.call = AsyncMock(return_value="crypto")
        market = _make_market(question="Will BTC be above $100k by end of 2026?")
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_cached_market.return_value = None
            result = await categorize_market(market, llm)
        assert result == "crypto"
        assert result in VALID_CATEGORIES

    @pytest.mark.asyncio
    async def test_categorize_uses_cache(self) -> None:
        llm = AsyncMock(spec=LLMClient)
        market = _make_market(condition_id="cached_cond")
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_cached_market.return_value = {"category": "politics", "data": {}}
            result = await categorize_market(market, llm)
        assert result == "politics"
        llm.call.assert_not_called()

    @pytest.mark.asyncio
    async def test_categorize_invalid_llm_response_defaults_to_other(self) -> None:
        llm = AsyncMock(spec=LLMClient)
        llm.call = AsyncMock(return_value="INVALID_GARBAGE")
        market = _make_market()
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_cached_market.return_value = None
            result = await categorize_market(market, llm)
        assert result == "other"

    @pytest.mark.asyncio
    async def test_categorize_llm_failure_defaults_to_other(self) -> None:
        llm = AsyncMock(spec=LLMClient)
        llm.call = AsyncMock(side_effect=Exception("LLM down"))
        market = _make_market()
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_cached_market.return_value = None
            result = await categorize_market(market, llm)
        assert result == "other"


# ---------------------------------------------------------------------------
# Resolution params tests
# ---------------------------------------------------------------------------

class TestExtractResolutionParams:
    @pytest.mark.asyncio
    async def test_skip_non_econ_crypto(self) -> None:
        llm = AsyncMock(spec=LLMClient)
        result = await extract_resolution_params("Will X win?", "politics", llm)
        assert result is None
        llm.call_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_extract_economics_params(self) -> None:
        llm = AsyncMock(spec=LLMClient)
        llm.call_json = AsyncMock(return_value={
            "indicator_type": "rate",
            "metric_name": "federal_funds_rate",
            "target_value": None,
            "target_direction": "cut",
            "target_date": "2026-06-01",
            "coin_id": None,
            "resolution_source": "FOMC announcement",
        })
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_cached_market.return_value = None
            result = await extract_resolution_params(
                "Will the Fed cut rates in June 2026?",
                "economics",
                llm,
                condition_id="cond_fed",
            )
        assert result is not None
        assert result["indicator_type"] == "rate"
        assert result["target_direction"] == "cut"

    @pytest.mark.asyncio
    async def test_extract_crypto_params(self) -> None:
        llm = AsyncMock(spec=LLMClient)
        llm.call_json = AsyncMock(return_value={
            "indicator_type": "price",
            "metric_name": "bitcoin_price",
            "target_value": 100000,
            "target_direction": "above",
            "target_date": "2026-12-31",
            "coin_id": "bitcoin",
            "resolution_source": None,
        })
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_cached_market.return_value = None
            result = await extract_resolution_params(
                "Will BTC be above $100k by end of 2026?",
                "crypto",
                llm,
                condition_id="cond_btc",
            )
        assert result is not None
        assert result["coin_id"] == "bitcoin"
        assert result["target_direction"] == "above"

    @pytest.mark.asyncio
    async def test_extract_uses_cache(self) -> None:
        llm = AsyncMock(spec=LLMClient)
        cached_params = {"indicator_type": "rate", "target_direction": "hike"}
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_cached_market.return_value = {
                "data": {"_resolution_params": cached_params},
                "category": "economics",
            }
            result = await extract_resolution_params(
                "Will the Fed hike?", "economics", llm, condition_id="cond_x"
            )
        assert result == cached_params
        llm.call_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_extract_llm_failure_returns_none(self) -> None:
        llm = AsyncMock(spec=LLMClient)
        llm.call_json = AsyncMock(side_effect=Exception("LLM error"))
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_cached_market.return_value = None
            result = await extract_resolution_params(
                "Will BTC hit $200k?", "crypto", llm
            )
        assert result is None


# ---------------------------------------------------------------------------
# Ranking tests
# ---------------------------------------------------------------------------

class TestRankCandidates:
    def test_ranking_order(self) -> None:
        """Economics/crypto markets with good timing and liquidity score highest."""
        markets = [
            _make_market(
                condition_id="best",
                days_until_end=14, liquidity=5000, volume_24h=600,
                category="economics",
            ),
            _make_market(
                condition_id="mid",
                days_until_end=40, liquidity=800, volume_24h=200,
                category="politics",
            ),
            _make_market(
                condition_id="low",
                days_until_end=60, liquidity=600, volume_24h=50,
                category="entertainment",
            ),
        ]
        ranked = rank_candidates(markets)
        assert ranked[0]["condition_id"] == "best"
        assert ranked[0]["_score"] >= ranked[1]["_score"]
        assert ranked[1]["_score"] >= ranked[2]["_score"]

    def test_scoring_components(self) -> None:
        """Verify individual scoring components."""
        # Resolution 1-4 weeks (+3), liquidity $1k-$10k (+2), economics (+2), vol>500 (+1) = 8
        market = _make_market(
            days_until_end=14, liquidity=5000, volume_24h=600, category="economics",
        )
        ranked = rank_candidates([market])
        assert ranked[0]["_score"] == 8

    def test_scoring_mid_range(self) -> None:
        """Resolution 4-8 weeks (+1), liquidity $500-$1k (+1), politics (+1), vol<=500 (+0) = 3."""
        market = _make_market(
            days_until_end=35, liquidity=800, volume_24h=200, category="politics",
        )
        ranked = rank_candidates([market])
        assert ranked[0]["_score"] == 3

    def test_empty_list(self) -> None:
        ranked = rank_candidates([])
        assert ranked == []

    def test_crypto_gets_plus_two(self) -> None:
        market = _make_market(
            days_until_end=14, liquidity=5000, volume_24h=600, category="crypto",
        )
        ranked = rank_candidates([market])
        # 3 (time) + 2 (liquidity) + 2 (crypto) + 1 (volume) = 8
        assert ranked[0]["_score"] == 8


# ---------------------------------------------------------------------------
# Discover markets tests
# ---------------------------------------------------------------------------

class TestDiscoverMarkets:
    @pytest.mark.asyncio
    async def test_discover_fetches_and_caches(self) -> None:
        """discover_markets should fetch from Gamma API and cache results."""
        # Gamma API returns camelCase fields
        gamma_markets = [
            {
                "conditionId": "m1",
                "question": "Will BTC hit 100k?",
                "clobTokenIds": ["tok_0", "tok_1"],
                "outcomes": ["Yes", "No"],
                "outcomePrices": ["0.65", "0.35"],
                "liquidity": "5000",
                "liquidityNum": 5000,
                "volume": "10000",
                "volumeNum": 10000,
                "volume24hr": 200,
                "endDate": "2026-06-01T00:00:00Z",
                "spread": 0.02,
                "id": "123",
            },
            {
                "conditionId": "m2",
                "question": "Will ETH hit 10k?",
                "clobTokenIds": ["tok_2", "tok_3"],
                "outcomes": ["Yes", "No"],
                "outcomePrices": ["0.40", "0.60"],
                "liquidity": "3000",
                "liquidityNum": 3000,
                "volume": "8000",
                "volumeNum": 8000,
                "volume24hr": 150,
                "endDate": "2026-07-01T00:00:00Z",
                "spread": 0.03,
                "id": "456",
            },
        ]

        # Mock aiohttp response
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=gamma_markets)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("strategy.market_filter.db") as mock_db, \
             patch("strategy.market_filter.aiohttp.ClientSession", return_value=mock_session):
            mock_db.get_db.return_value = MagicMock()
            mock_db.get_db.return_value.execute.return_value.fetchall.return_value = []
            mock_db.get_cached_market.return_value = None
            markets = await discover_markets()

        assert len(markets) == 2
        assert markets[0]["condition_id"] == "m1"
        assert markets[0]["liquidity"] == 5000
        assert len(markets[0]["tokens"]) == 2
        assert markets[0]["tokens"][0]["token_id"] == "tok_0"


# Use ClobClientWrapper for type hints in fixtures
from core.client import ClobClientWrapper
from core.llm import LLMClient
