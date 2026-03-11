"""Sports odds signal provider using The Odds API.

Fetches betting odds from 40+ bookmakers for sports markets.
Converts bookmaker odds to implied probabilities for comparison
with Polymarket prices.

Requires ODDS_API_KEY (free: 500 requests/month from the-odds-api.com).
"""

import logging
import os
import time
from collections.abc import Callable
from typing import Any, Optional

import aiohttp

from config.settings import ODDS_API_KEY
from core import db
from core.llm import LLMClient
from signals.base import SignalProvider, SignalResult

logger = logging.getLogger(__name__)

_signal_cache: dict[str, tuple[SignalResult, float]] = {}
CACHE_TTL_SECONDS = 900  # 15 minutes — odds change frequently

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
USER_AGENT = "polymarket-bot/1.0"

# Sports keys mapped to common names
SPORT_KEYS = {
    "nfl": "americanfootball_nfl",
    "nba": "basketball_nba",
    "mlb": "baseball_mlb",
    "nhl": "icehockey_nhl",
    "ncaaf": "americanfootball_ncaaf",
    "ncaab": "basketball_ncaab",
    "mls": "soccer_usa_mls",
    "epl": "soccer_epl",
    "champions_league": "soccer_uefa_champs_league",
    "ufc": "mma_mixed_martial_arts",
    "boxing": "boxing_boxing",
    "tennis": "tennis_atp_french_open",
    "golf": "golf_pga_championship",
}

# Categories that this provider handles
HANDLED_CATEGORIES = {"sports"}


async def _fetch_odds_for_sport(
    session: aiohttp.ClientSession,
    sport_key: str,
    api_key: str,
) -> list[dict[str, Any]]:
    """Fetch odds for a specific sport from The Odds API."""
    events: list[dict[str, Any]] = []
    try:
        params = {
            "apiKey": api_key,
            "regions": "us,eu",
            "markets": "h2h,totals,spreads",
            "oddsFormat": "decimal",
        }
        async with session.get(
            f"{ODDS_API_BASE}/sports/{sport_key}/odds",
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.warning("Odds API returned %d for sport %s", resp.status, sport_key)
                return []
            events = await resp.json()
    except Exception as e:
        logger.warning("Error fetching odds for %s: %s", sport_key, e)
    return events if isinstance(events, list) else []


def _decimal_odds_to_prob(decimal_odds: float) -> float:
    """Convert decimal odds to implied probability."""
    if decimal_odds <= 1.0:
        return 1.0
    return 1.0 / decimal_odds


def _extract_consensus_odds(event: dict[str, Any]) -> dict[str, float]:
    """Extract consensus implied probabilities from bookmaker odds.

    Averages across all bookmakers, removing vig via normalization.
    """
    bookmakers = event.get("bookmakers", [])
    if not bookmakers:
        return {}

    # Aggregate h2h (moneyline) odds
    outcome_probs: dict[str, list[float]] = {}
    for bm in bookmakers:
        for market in bm.get("markets", []):
            if market.get("key") != "h2h":
                continue
            for outcome in market.get("outcomes", []):
                name = outcome.get("name", "")
                price = outcome.get("price", 0)
                if price > 1.0:
                    prob = _decimal_odds_to_prob(price)
                    outcome_probs.setdefault(name, []).append(prob)

    # Average across bookmakers and normalize to remove vig
    consensus: dict[str, float] = {}
    for name, probs in outcome_probs.items():
        consensus[name] = sum(probs) / len(probs)

    # Normalize
    total = sum(consensus.values())
    if total > 0:
        consensus = {k: v / total for k, v in consensus.items()}

    return consensus


class SportsOddsSignalProvider(SignalProvider):
    """Sports odds signal provider.

    Pipeline:
    1. Check if market is sports category — skip otherwise
    2. Use cheap LLM to identify sport type and teams/participants
    3. Fetch odds from The Odds API
    4. Match event to market question
    5. Convert bookmaker consensus to probability
    """

    name: str = "sports_odds"

    ProgressCallback = Callable[[str, str, str], None]

    def __init__(
        self,
        llm: LLMClient,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self._llm = llm
        self._on_progress = on_progress

    def _emit(self, question: str, stage: str, detail: str = "") -> None:
        if self._on_progress:
            try:
                self._on_progress(question, stage, detail)
            except Exception:
                pass

    async def get_signal(
        self,
        market_question: str,
        market_category: str,
        market_end_date: str,
        **kwargs: Any,
    ) -> SignalResult:
        # Only handle sports markets
        if market_category not in HANDLED_CATEGORIES:
            return SignalResult(
                source="sports_odds",
                probability=None,
                confidence=0.0,
                reasoning=f"Category '{market_category}' not handled by sports odds provider",
                model_used="none",
                data_points=0,
            )

        # Check API key
        if not ODDS_API_KEY:
            return SignalResult(
                source="sports_odds",
                probability=None,
                confidence=0.0,
                reasoning="ODDS_API_KEY not configured",
                model_used="none",
                data_points=0,
            )

        cache_key = market_question
        if cache_key in _signal_cache:
            cached_result, cached_time = _signal_cache[cache_key]
            if time.monotonic() - cached_time < CACHE_TTL_SECONDS:
                self._emit(market_question, "cache")
                return cached_result

        try:
            result = await self._run_pipeline(market_question, market_end_date)
        except Exception as e:
            logger.error("Sports odds signal failed for '%s': %s", market_question[:60], e)
            self._emit(market_question, "error", str(e)[:100])
            result = SignalResult(
                source="sports_odds",
                probability=None,
                confidence=0.0,
                reasoning=f"Pipeline error: {e}",
                model_used="none",
                data_points=0,
                raw_data={"error": str(e)},
            )

        _signal_cache[cache_key] = (result, time.monotonic())
        self._log_signal(market_question, result)
        self._emit(market_question, "done", result.reasoning[:100])
        return result

    def _log_signal(self, market_question: str, result: SignalResult) -> None:
        try:
            db.record_signal(
                market_id=market_question[:200],
                signal_source=result.source,
                probability=result.probability if result.probability is not None else -1.0,
                confidence=result.confidence,
                reasoning=result.reasoning[:1000],
                model_used=result.model_used,
            )
        except Exception as e:
            logger.warning("Failed to log sports_odds signal to DB: %s", e)

    async def _run_pipeline(
        self,
        market_question: str,
        market_end_date: str,
    ) -> SignalResult:
        # Step 1: Identify the sport and teams
        self._emit(market_question, "identify", "extracting sport/teams")
        sport_info = await self._identify_sport(market_question)

        sport_key = sport_info.get("sport_key", "")
        team_a = sport_info.get("team_a", "")
        team_b = sport_info.get("team_b", "")
        target_outcome = sport_info.get("target_outcome", "")

        if not sport_key:
            return SignalResult(
                source="sports_odds",
                probability=None,
                confidence=0.0,
                reasoning=f"Could not identify sport type from question",
                model_used="cheap",
                data_points=0,
                raw_data={"sport_info": sport_info},
            )

        # Map common names to API sport keys
        api_sport_key = SPORT_KEYS.get(sport_key.lower(), sport_key)

        # Step 2: Fetch odds
        self._emit(market_question, "fetch", f"fetching {api_sport_key} odds")
        async with aiohttp.ClientSession() as session:
            events = await _fetch_odds_for_sport(session, api_sport_key, ODDS_API_KEY)

        if not events:
            return SignalResult(
                source="sports_odds",
                probability=None,
                confidence=0.0,
                reasoning=f"No odds data found for sport '{api_sport_key}'",
                model_used="cheap",
                data_points=0,
                raw_data={"sport_key": api_sport_key},
            )

        # Step 3: Find matching event
        self._emit(market_question, "matching", f"{len(events)} events found")
        matched_event = self._find_matching_event(events, team_a, team_b)

        if not matched_event:
            return SignalResult(
                source="sports_odds",
                probability=None,
                confidence=0.0,
                reasoning=f"No matching event found for {team_a} vs {team_b} in {api_sport_key}",
                model_used="cheap",
                data_points=len(events),
                raw_data={"sport_key": api_sport_key, "teams": [team_a, team_b]},
            )

        # Step 4: Extract consensus probability
        consensus = _extract_consensus_odds(matched_event)
        if not consensus:
            return SignalResult(
                source="sports_odds",
                probability=None,
                confidence=0.0,
                reasoning="No bookmaker odds available for matched event",
                model_used="cheap",
                data_points=0,
                raw_data={"matched_event": matched_event.get("id", "")},
            )

        # Find the probability for the target outcome
        prob = self._match_outcome_prob(consensus, target_outcome, team_a)

        num_bookmakers = len(matched_event.get("bookmakers", []))
        confidence = min(0.9, 0.5 + num_bookmakers * 0.05)  # More bookmakers = more confident

        return SignalResult(
            source="sports_odds",
            probability=prob,
            confidence=confidence,
            reasoning=(
                f"Consensus from {num_bookmakers} bookmakers: "
                f"{', '.join(f'{k}={v:.2f}' for k, v in consensus.items())}"
            ),
            model_used="cheap",
            data_points=num_bookmakers,
            raw_data={
                "consensus_odds": consensus,
                "bookmaker_count": num_bookmakers,
                "event": matched_event.get("id", ""),
                "home_team": matched_event.get("home_team", ""),
                "away_team": matched_event.get("away_team", ""),
            },
        )

    async def _identify_sport(self, market_question: str) -> dict[str, str]:
        """Use cheap LLM to identify sport, teams, and target outcome."""
        sport_list = ", ".join(SPORT_KEYS.keys())
        prompt = (
            f'Given this sports prediction market question: "{market_question}"\n'
            f'Identify:\n'
            f'1. The sport key (one of: {sport_list})\n'
            f'2. Team/participant A (the one the market asks about)\n'
            f'3. Team/participant B (opponent, if applicable)\n'
            f'4. Target outcome (e.g., "Team A wins", "over 200.5")\n'
            f'\n'
            f'Respond as JSON:\n'
            f'{{"sport_key": "nba", "team_a": "...", "team_b": "...", "target_outcome": "..."}}'
        )
        try:
            result = await self._llm.call_json(prompt, task_type="extract")
            return result if isinstance(result, dict) else {}
        except Exception as e:
            logger.warning("Failed to identify sport: %s", e)
            return {}

    def _find_matching_event(
        self,
        events: list[dict[str, Any]],
        team_a: str,
        team_b: str,
    ) -> dict[str, Any] | None:
        """Find the event that best matches the teams."""
        team_a_lower = team_a.lower()
        team_b_lower = team_b.lower()

        best_match: dict[str, Any] | None = None
        best_score = 0

        for event in events:
            home = event.get("home_team", "").lower()
            away = event.get("away_team", "").lower()

            score = 0
            for team_query in [team_a_lower, team_b_lower]:
                if not team_query:
                    continue
                for team_name in [home, away]:
                    # Check if any word from query appears in team name
                    query_words = team_query.split()
                    for word in query_words:
                        if len(word) > 2 and word in team_name:
                            score += 1

            if score > best_score:
                best_score = score
                best_match = event

        return best_match if best_score >= 1 else None

    def _match_outcome_prob(
        self,
        consensus: dict[str, float],
        target_outcome: str,
        team_a: str,
    ) -> float:
        """Match the target outcome to a consensus probability."""
        target_lower = target_outcome.lower()
        team_a_lower = team_a.lower()

        # Try exact name match first
        for name, prob in consensus.items():
            if name.lower() == team_a_lower:
                return prob

        # Try partial match
        for name, prob in consensus.items():
            name_lower = name.lower()
            if team_a_lower in name_lower or name_lower in team_a_lower:
                return prob
            # Check individual words
            for word in team_a_lower.split():
                if len(word) > 2 and word in name_lower:
                    return prob

        # Default: return first outcome probability
        if consensus:
            return list(consensus.values())[0]
        return 0.5


def clear_signal_cache() -> None:
    _signal_cache.clear()
