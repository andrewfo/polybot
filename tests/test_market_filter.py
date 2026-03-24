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
    _get_outcome_prices,
    _get_spread,
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
    volume_24h: float = 600.0,
    days_until_end: int = 14,
    num_tokens: int = 2,
    category: str = "",
    yes_price: float = 0.55,
    spread: float | None = 0.02,
    _resolution_params: dict | None = None,
) -> dict:
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_until_end)
    no_price = round(1.0 - yes_price, 4)
    tokens = [
        {"token_id": f"tok_{i}", "price": str(yes_price if i == 0 else no_price)}
        for i in range(num_tokens)
    ]
    m: dict = {
        "condition_id": condition_id,
        "question": question,
        "liquidity": liquidity,
        "volume24hr": volume_24h,
        "end_date_iso": end.isoformat(),
        "tokens": tokens,
        "spread": spread,
    }
    if category:
        m["_category"] = category
    if _resolution_params is not None:
        m["_resolution_params"] = _resolution_params
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
    @pytest.mark.asyncio
    async def test_binary_only_filter(self) -> None:
        """Non-binary markets (!=2 tokens) are eliminated."""
        markets = [
            _make_market(condition_id="binary", num_tokens=2),
            _make_market(condition_id="multi", num_tokens=3),
            _make_market(condition_id="single", num_tokens=1),
        ]
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_open_positions.return_value = []
            result = await filter_markets(markets)
        assert len(result) == 1
        assert result[0]["condition_id"] == "binary"

    @pytest.mark.asyncio
    async def test_liquidity_filter(self) -> None:
        """Markets outside liquidity band are eliminated."""
        markets = [
            _make_market(condition_id="low", liquidity=100),      # below MIN
            _make_market(condition_id="good", liquidity=5000),     # in band
            _make_market(condition_id="high", liquidity=600000),   # above MAX
        ]
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_open_positions.return_value = []
            result = await filter_markets(markets)
        assert len(result) == 1
        assert result[0]["condition_id"] == "good"

    @pytest.mark.asyncio
    async def test_time_to_resolution_filter(self) -> None:
        """Markets too close or too far from resolution are eliminated."""
        markets = [
            _make_market(condition_id="too_soon", days_until_end=0),
            _make_market(condition_id="good", days_until_end=30),
            _make_market(condition_id="too_far", days_until_end=200),
        ]
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_open_positions.return_value = []
            result = await filter_markets(markets)
        assert len(result) == 1
        assert result[0]["condition_id"] == "good"

    @pytest.mark.asyncio
    async def test_spread_filter(self) -> None:
        """Markets with spread > MAX_SPREAD are eliminated."""
        markets = [
            _make_market(condition_id="tight", liquidity=5000, days_until_end=30, spread=0.03),
            _make_market(condition_id="wide", liquidity=5000, days_until_end=30, spread=0.15),
        ]
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_open_positions.return_value = []
            result = await filter_markets(markets)
        assert len(result) == 1
        assert result[0]["condition_id"] == "tight"

    @pytest.mark.asyncio
    async def test_near_certain_filter(self) -> None:
        """Markets with outcome prices <= 0.02 or >= 0.98 are eliminated."""
        markets = [
            _make_market(condition_id="balanced", yes_price=0.55),
            _make_market(condition_id="near_yes", yes_price=0.99),
            _make_market(condition_id="near_no", yes_price=0.01),
        ]
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_open_positions.return_value = []
            result = await filter_markets(markets)
        assert len(result) == 1
        assert result[0]["condition_id"] == "balanced"

    @pytest.mark.asyncio
    async def test_position_filter(self) -> None:
        """Markets where we already hold a position are eliminated."""
        markets = [
            _make_market(condition_id="cond_new"),
            _make_market(condition_id="cond_existing"),
        ]
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_open_positions.return_value = [
                {"market_id": "cond_existing", "size": 50.0}
            ]
            result = await filter_markets(markets)
        assert len(result) == 1
        assert result[0]["condition_id"] == "cond_new"

    @pytest.mark.asyncio
    async def test_full_pipeline(self) -> None:
        """A valid market passes all filters."""
        markets = [_make_market(
            condition_id="perfect",
            liquidity=5000,
            volume_24h=600,
            days_until_end=14,
            num_tokens=2,
        )]
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_open_positions.return_value = []
            result = await filter_markets(markets)
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
        market = _make_market(question="Will the President win the election?")
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_cached_market.return_value = None
            result = await categorize_market(market, llm)
        assert result == "other"

    @pytest.mark.asyncio
    async def test_categorize_llm_failure_defaults_to_other(self) -> None:
        llm = AsyncMock(spec=LLMClient)
        llm.call = AsyncMock(side_effect=Exception("LLM down"))
        market = _make_market(question="Will the President win the election?")
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_cached_market.return_value = None
            result = await categorize_market(market, llm)
        assert result == "other"


# ---------------------------------------------------------------------------
# Resolution params tests
# ---------------------------------------------------------------------------

class TestExtractResolutionParams:
    @pytest.mark.asyncio
    async def test_skip_non_crypto(self) -> None:
        llm = AsyncMock(spec=LLMClient)
        result = await extract_resolution_params("Will X win?", "other", llm)
        assert result is None
        llm.call_json.assert_not_called()

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
        cached_params = {"indicator_type": "price", "target_direction": "above", "coin_id": "bitcoin"}
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_cached_market.return_value = {
                "data": {"_resolution_params": cached_params},
                "category": "crypto",
            }
            result = await extract_resolution_params(
                "Will BTC reach $200k?", "crypto", llm, condition_id="cond_x"
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
        """Markets with higher adjusted edge score highest."""
        markets = [
            _make_market(
                condition_id="best",
                days_until_end=5, liquidity=5000, volume_24h=600,
                category="crypto",
                _resolution_params={"coin_id": "bitcoin", "target_value": 100000, "target_direction": "above", "resolution_type": "barrier"},
            ),
            _make_market(
                condition_id="mid",
                days_until_end=25, liquidity=800, volume_24h=200,
                category="politics",
            ),
            _make_market(
                condition_id="low",
                days_until_end=28, liquidity=600, volume_24h=50,
                category="entertainment",
            ),
        ]
        # Give the best market a model edge
        markets[0]["_model_edge"] = 0.10
        ranked = rank_candidates(markets)
        assert ranked[0]["condition_id"] == "best"

    def test_scoring_components(self) -> None:
        """Verify continuous time score and Kelly leverage."""
        import math
        # Resolution 10d → time_score = 5*exp(-0.5*((10-5)/4)^2) ≈ 3.51
        # liquidity $1k-$10k (+2), vol>500 (+1), price 0.15-0.85 (+2)
        market = _make_market(
            days_until_end=10, liquidity=5000, volume_24h=600, category="crypto",
        )
        ranked = rank_candidates([market])
        expected_time = 5.0 * math.exp(-0.5 * ((10 - 5) / 4) ** 2)
        assert abs(ranked[0]["_time_score"] - round(expected_time, 2)) < 0.1
        assert ranked[0]["_score"] >= 5.0  # time + liquidity + volume + price

    def test_scoring_mid_range(self) -> None:
        """Resolution 25d, liquidity $500-$1k — lower total score."""
        market = _make_market(
            days_until_end=25, liquidity=800, volume_24h=200, category="other",
        )
        ranked = rank_candidates([market])
        # time_score for 25d ≈ 5*exp(-0.5*((25-5)/4)^2) ≈ very small
        # liquidity +1, price +2 → score ≈ 3 + small time
        assert ranked[0]["_score"] >= 3.0

    def test_empty_list(self) -> None:
        ranked = rank_candidates([])
        assert ranked == []

    def test_kelly_adjusted_ranking(self) -> None:
        """Kelly leverage is capped at 3.0x to prevent extreme-price dominance."""
        # Market at 0.50 → Kelly leverage = min(3.0, 1/(0.25)) = 3.0
        # Market at 0.20 → Kelly leverage = min(3.0, 1/(0.16)) = 3.0 (capped)
        # With same edge and same capped leverage, add different edges to test ranking
        m1 = _make_market(condition_id="mid_price", yes_price=0.50, days_until_end=5)
        m1["_model_edge"] = 0.05
        m2 = _make_market(condition_id="higher_edge", yes_price=0.20, days_until_end=5)
        m2["_model_edge"] = 0.06  # slightly higher raw edge wins
        ranked = rank_candidates([m1, m2])
        # higher raw edge wins since leverage is capped equally
        assert ranked[0]["condition_id"] == "higher_edge"


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


from strategy.market_filter import (
    _is_crypto_keyword_match,
    filter_computable_markets,
    validate_resolution_params,
)
from core.llm import LLMClient


# ---------------------------------------------------------------------------
# Keyword categorization tests (Change 3)
# ---------------------------------------------------------------------------

class TestCryptoKeywordMatch:
    def test_bitcoin_question(self) -> None:
        assert _is_crypto_keyword_match("Will Bitcoin hit $100k?") is True

    def test_btc_ticker(self) -> None:
        assert _is_crypto_keyword_match("Will BTC be above $100k by June 2026?") is True

    def test_ethereum_question(self) -> None:
        assert _is_crypto_keyword_match("Will Ethereum reach $5000?") is True

    def test_generic_crypto_term(self) -> None:
        assert _is_crypto_keyword_match("Will the crypto market cap exceed $3T?") is True

    def test_defi_term(self) -> None:
        assert _is_crypto_keyword_match("Will DeFi TVL exceed $200B?") is True

    def test_non_crypto_politics(self) -> None:
        assert _is_crypto_keyword_match("Will the President win the election?") is False

    def test_non_crypto_sports(self) -> None:
        assert _is_crypto_keyword_match("Will the Lakers win the NBA championship?") is False

    def test_bigram_match(self) -> None:
        assert _is_crypto_keyword_match("Will a spot ETF be approved for Bitcoin?") is True

    def test_case_insensitive(self) -> None:
        assert _is_crypto_keyword_match("SOLANA price prediction") is True

    def test_empty_question(self) -> None:
        assert _is_crypto_keyword_match("") is False


# ---------------------------------------------------------------------------
# Computability filter tests
# ---------------------------------------------------------------------------

class TestValidateResolutionParams:
    """Test that resolution params are validated for math model compatibility."""

    def test_valid_barrier_params(self) -> None:
        m = _make_market(_resolution_params={
            "coin_id": "bitcoin", "target_value": 100000,
            "target_direction": "above", "resolution_type": "barrier",
        })
        ok, reason = validate_resolution_params(m)
        assert ok is True

    def test_valid_terminal_params(self) -> None:
        m = _make_market(_resolution_params={
            "coin_id": "ethereum", "target_value": 5000,
            "target_direction": "below", "resolution_type": "terminal",
        })
        ok, reason = validate_resolution_params(m)
        assert ok is True

    def test_no_resolution_params(self) -> None:
        m = _make_market()
        ok, reason = validate_resolution_params(m)
        assert ok is False
        assert "no resolution params" in reason

    def test_no_coin_id(self) -> None:
        m = _make_market(_resolution_params={
            "coin_id": None, "target_value": 100000,
            "target_direction": "above", "resolution_type": "barrier",
        })
        ok, reason = validate_resolution_params(m)
        assert ok is False
        assert "coin_id" in reason

    def test_no_target_value(self) -> None:
        m = _make_market(_resolution_params={
            "coin_id": "bitcoin", "target_value": None,
            "target_direction": "above", "resolution_type": "barrier",
        })
        ok, reason = validate_resolution_params(m)
        assert ok is False
        assert "target_value" in reason

    def test_non_numeric_target(self) -> None:
        m = _make_market(_resolution_params={
            "coin_id": "bitcoin", "target_value": "approval",
            "target_direction": "above", "resolution_type": "barrier",
        })
        ok, reason = validate_resolution_params(m)
        assert ok is False
        assert "not numeric" in reason

    def test_negative_target(self) -> None:
        m = _make_market(_resolution_params={
            "coin_id": "bitcoin", "target_value": -100,
            "target_direction": "above", "resolution_type": "barrier",
        })
        ok, reason = validate_resolution_params(m)
        assert ok is False
        assert "not a positive price" in reason

    def test_direction_other_rejected(self) -> None:
        m = _make_market(_resolution_params={
            "coin_id": "bitcoin", "target_value": 100000,
            "target_direction": "other", "resolution_type": "barrier",
        })
        ok, reason = validate_resolution_params(m)
        assert ok is False
        assert "target_direction" in reason

    def test_bad_resolution_type(self) -> None:
        m = _make_market(_resolution_params={
            "coin_id": "bitcoin", "target_value": 100000,
            "target_direction": "above", "resolution_type": "unknown",
        })
        ok, reason = validate_resolution_params(m)
        assert ok is False
        assert "resolution_type" in reason


class TestFilterComputableMarkets:
    """Test that non-computable markets are filtered out."""

    def test_keeps_valid_markets(self) -> None:
        m1 = _make_market(condition_id="a", question="Will BTC hit $100k?",
                          _resolution_params={
                              "coin_id": "bitcoin", "target_value": 100000,
                              "target_direction": "above", "resolution_type": "barrier",
                          })
        m2 = _make_market(condition_id="b", question="Will ETH drop below $2000?",
                          _resolution_params={
                              "coin_id": "ethereum", "target_value": 2000,
                              "target_direction": "below", "resolution_type": "terminal",
                          })
        result = filter_computable_markets([m1, m2])
        assert len(result) == 2

    def test_rejects_event_markets(self) -> None:
        # Crypto event question — no price target
        m1 = _make_market(condition_id="a", question="Will SEC approve Bitcoin ETF?",
                          _resolution_params={
                              "coin_id": None, "target_value": None,
                              "target_direction": "other", "resolution_type": "barrier",
                          })
        # Valid price target
        m2 = _make_market(condition_id="b", question="Will BTC hit $100k?",
                          _resolution_params={
                              "coin_id": "bitcoin", "target_value": 100000,
                              "target_direction": "above", "resolution_type": "barrier",
                          })
        result = filter_computable_markets([m1, m2])
        assert len(result) == 1
        assert result[0]["condition_id"] == "b"

    def test_rejects_markets_without_params(self) -> None:
        m = _make_market(condition_id="a", question="Will Binance get hacked?")
        result = filter_computable_markets([m])
        assert len(result) == 0


class TestCategorizeMarketKeywords:
    """Test that keyword matching is tried before LLM."""

    @pytest.mark.asyncio
    async def test_keyword_match_skips_llm(self) -> None:
        llm = AsyncMock(spec=LLMClient)
        market = _make_market(question="Will Bitcoin hit $100k?")
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_cached_market.return_value = None
            result = await categorize_market(market, llm)
        assert result == "crypto"
        llm.call.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_crypto_falls_through_to_llm(self) -> None:
        llm = AsyncMock(spec=LLMClient)
        llm.call = AsyncMock(return_value="other")
        market = _make_market(question="Will it rain in London tomorrow?")
        with patch("strategy.market_filter.db") as mock_db:
            mock_db.get_cached_market.return_value = None
            result = await categorize_market(market, llm)
        assert result == "other"
        llm.call.assert_called_once()
