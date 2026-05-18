"""Temporal context injection for LLM prompts.

Every LLM call that involves probability estimation must know today's date
and the exact days remaining until resolution. No model knows what day it is.
"""

import logging
from datetime import datetime, timezone

from dateutil import parser as dateutil_parser

logger = logging.getLogger(__name__)


def parse_end_date(end_date_str: str) -> datetime | None:
    """Parse a market end date string into a timezone-aware UTC datetime.

    Handles ISO 8601, Polymarket's varied formats, and falls back to
    dateutil.parser.parse(). Returns None if parsing fails entirely.
    """
    if not end_date_str:
        return None

    # Try stdlib first (handles most ISO 8601)
    try:
        dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        pass

    # Fall back to dateutil for non-standard formats
    try:
        dt = dateutil_parser.parse(end_date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError) as e:
        logger.warning("Failed to parse end date '%s': %s", end_date_str, e)
        return None


def compute_urgency_tier(days_remaining: float) -> str:
    """Classify the urgency based on days remaining.

    Returns one of: 'imminent', 'short_term', 'medium', 'long'.
    """
    if days_remaining < 5:
        return "imminent"
    elif days_remaining < 14:
        return "short_term"
    elif days_remaining < 30:
        return "medium"
    else:
        return "long"


def build_date_context(
    end_date_str: str,
    now: datetime | None = None,
) -> dict[str, str | float | None]:
    """Build temporal context dict for injection into prompts.

    Returns a dict with keys:
        today_str: e.g. "2026-03-11"
        current_year: e.g. "2026"
        end_date_str: the original end date string
        days_remaining: float or None if unparseable
        hours_remaining: float or None
        urgency_tier: str or None
    """
    if now is None:
        now = datetime.now(timezone.utc)

    today_str = now.strftime("%Y-%m-%d")
    current_year = now.strftime("%Y")

    end_dt = parse_end_date(end_date_str)

    if end_dt is None:
        return {
            "today_str": today_str,
            "current_year": current_year,
            "end_date_str": end_date_str,
            "days_remaining": None,
            "hours_remaining": None,
            "urgency_tier": None,
        }

    delta = end_dt - now
    days_remaining = max(0.0, delta.total_seconds() / 86400)
    hours_remaining = max(0.0, delta.total_seconds() / 3600)
    urgency_tier = compute_urgency_tier(days_remaining)

    return {
        "today_str": today_str,
        "current_year": current_year,
        "end_date_str": end_date_str,
        "days_remaining": days_remaining,
        "hours_remaining": hours_remaining,
        "urgency_tier": urgency_tier,
    }


def format_date_context_line(end_date_str: str, now: datetime | None = None) -> str:
    """Build a single-line date context string for cheap model prompts.

    Example: "Today is 2026-03-11. The market resolves on 2026-06-30, which is 111 days from now."
    Returns empty string if end date is unparseable (market should be skipped).
    """
    ctx = build_date_context(end_date_str, now=now)
    days = ctx["days_remaining"]

    if days is None:
        return f"Today is {ctx['today_str']} (year {ctx['current_year']}). The market resolution date could not be determined."

    days_int = int(round(days))
    return (
        f"Today is {ctx['today_str']} (year {ctx['current_year']}). "
        f"The market resolves on {end_date_str}, which is {days_int} days from now."
    )


def build_frontier_system_prompt(
    end_date_str: str,
    now: datetime | None = None,
) -> str:
    """Build the dynamic system prompt for the frontier model.

    Includes: today's date, days/hours remaining, urgency tier, and
    calibration guidance that scales with time remaining.
    """
    ctx = build_date_context(end_date_str, now=now)

    lines = [
        f"The current date is {ctx['today_str']}. The current year is {ctx['current_year']}. "
        f"Do NOT assume the year is 2024 or 2025 — it is {ctx['current_year']}.",
    ]

    days = ctx["days_remaining"]
    hours = ctx["hours_remaining"]
    urgency = ctx["urgency_tier"]

    if days is not None:
        days_int = int(round(days))
        hours_int = int(round(hours))
        lines.append(
            f"The market resolves on {end_date_str}. "
            f"That is {days_int} days ({hours_int} hours) from now. "
            f"Urgency tier: {urgency}."
        )

        # Calibration guidance by urgency
        if urgency == "imminent":
            lines.append(
                "CALIBRATION: This market resolves in less than 5 days. "
                "With very little time for conditions to change, your estimate should be driven "
                "by current observable data (prices, levels, confirmed facts). "
                "Probabilities should be pushed toward 0 or 1 when the data clearly supports it. "
                "If the math model says 3%, trust it — don't round up to 'near the market' at 5%."
            )
        elif urgency == "short_term":
            lines.append(
                "CALIBRATION: This market resolves in 5-14 days. "
                "Moderate uncertainty is appropriate, but most major factors are already known. "
                "Your quantitative signals (resolution_crypto) are well-calibrated for this timeframe. "
                "Trust them over sentiment-based signals."
            )
        elif urgency == "medium":
            lines.append(
                "CALIBRATION: This market resolves in 14-30 days. "
                "More uncertainty is appropriate. Trends, momentum, and structural factors matter. "
                "Quantitative models remain useful but have wider confidence intervals."
            )
        else:  # long
            lines.append(
                "CALIBRATION: This market resolves in more than 30 days. "
                "High uncertainty is expected. Base rates and structural factors dominate. "
                "Be cautious about overweighting recent news for long-dated events."
            )
    else:
        lines.append(
            "WARNING: The market resolution date could not be parsed. "
            "Treat time-to-resolution as unknown and be conservative."
        )

    lines.append(
        "IMPORTANT: Your job is to estimate the TRUE probability from the evidence, not to "
        "track the market price. Mid-to-low liquidity crypto prediction markets are frequently "
        "mispriced. If your data-driven analysis yields a different number than the market, "
        "COMMIT to your estimate. A well-calibrated math model disagreeing with a thin market "
        "is a trading opportunity, not a reason to second-guess. Do NOT split the difference "
        "between your analysis and the market price."
    )

    return "\n\n".join(lines)
