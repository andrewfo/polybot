"""Unit tests for temporal context injection (signals/temporal.py)."""

from datetime import datetime, timezone

import pytest

from signals.temporal import (
    build_date_context,
    build_frontier_system_prompt,
    compute_urgency_tier,
    format_date_context_line,
    parse_end_date,
)


# ---------------------------------------------------------------------------
# parse_end_date
# ---------------------------------------------------------------------------

class TestParseEndDate:
    def test_iso_with_z(self):
        dt = parse_end_date("2026-06-30T23:59:59Z")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 30
        assert dt.tzinfo is not None

    def test_iso_with_offset(self):
        dt = parse_end_date("2026-12-31T00:00:00+00:00")
        assert dt is not None
        assert dt.year == 2026

    def test_iso_date_only(self):
        dt = parse_end_date("2026-06-30")
        assert dt is not None
        assert dt.year == 2026
        assert dt.tzinfo is not None

    def test_dateutil_fallback(self):
        dt = parse_end_date("June 30, 2026")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6

    def test_empty_string(self):
        assert parse_end_date("") is None

    def test_garbage(self):
        assert parse_end_date("not-a-date-at-all") is None


# ---------------------------------------------------------------------------
# compute_urgency_tier
# ---------------------------------------------------------------------------

class TestUrgencyTier:
    def test_imminent(self):
        assert compute_urgency_tier(0) == "imminent"
        assert compute_urgency_tier(3) == "imminent"
        assert compute_urgency_tier(4.9) == "imminent"

    def test_short_term(self):
        assert compute_urgency_tier(5) == "short_term"
        assert compute_urgency_tier(10) == "short_term"
        assert compute_urgency_tier(13.9) == "short_term"

    def test_medium(self):
        assert compute_urgency_tier(14) == "medium"
        assert compute_urgency_tier(20) == "medium"
        assert compute_urgency_tier(29.9) == "medium"

    def test_long(self):
        assert compute_urgency_tier(30) == "long"
        assert compute_urgency_tier(365) == "long"


# ---------------------------------------------------------------------------
# build_date_context
# ---------------------------------------------------------------------------

class TestBuildDateContext:
    def test_known_date_and_end(self):
        now = datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)
        ctx = build_date_context("2026-06-30T00:00:00Z", now=now)
        assert ctx["today_str"] == "2026-03-11"
        assert ctx["current_year"] == "2026"
        assert ctx["days_remaining"] is not None
        # 2026-03-11 to 2026-06-30 = 111 days
        assert abs(ctx["days_remaining"] - 110.5) < 1.0
        assert ctx["urgency_tier"] == "long"

    def test_unparseable_end_date(self):
        now = datetime(2026, 3, 11, tzinfo=timezone.utc)
        ctx = build_date_context("garbage", now=now)
        assert ctx["today_str"] == "2026-03-11"
        assert ctx["days_remaining"] is None
        assert ctx["urgency_tier"] is None

    def test_past_end_date_clamps_to_zero(self):
        now = datetime(2026, 7, 1, tzinfo=timezone.utc)
        ctx = build_date_context("2026-06-30T00:00:00Z", now=now)
        assert ctx["days_remaining"] == 0.0


# ---------------------------------------------------------------------------
# format_date_context_line
# ---------------------------------------------------------------------------

class TestFormatDateContextLine:
    def test_includes_year_and_days(self):
        now = datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)
        line = format_date_context_line("2026-06-30T00:00:00Z", now=now)
        assert "2026" in line
        assert "2026-03-11" in line
        assert "111" in line or "110" in line  # ±1 day rounding
        assert "days from now" in line

    def test_unparseable_date_still_has_today(self):
        now = datetime(2026, 3, 11, tzinfo=timezone.utc)
        line = format_date_context_line("garbage", now=now)
        assert "2026-03-11" in line
        assert "could not be determined" in line


# ---------------------------------------------------------------------------
# build_frontier_system_prompt
# ---------------------------------------------------------------------------

class TestBuildFrontierSystemPrompt:
    def test_contains_correct_year(self):
        now = datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)
        prompt = build_frontier_system_prompt("2026-06-30", now=now)
        assert "2026" in prompt
        assert "Do NOT assume the year is 2024 or 2025" in prompt

    def test_contains_days_remaining(self):
        now = datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)
        prompt = build_frontier_system_prompt("2026-06-30T00:00:00Z", now=now)
        # Should mention ~111 days
        assert "111" in prompt or "110" in prompt

    def test_imminent_calibration(self):
        now = datetime(2026, 6, 28, tzinfo=timezone.utc)
        prompt = build_frontier_system_prompt("2026-06-30T00:00:00Z", now=now)
        assert "imminent" in prompt.lower()
        assert "less than 5 days" in prompt

    def test_short_term_calibration(self):
        now = datetime(2026, 6, 20, tzinfo=timezone.utc)
        prompt = build_frontier_system_prompt("2026-06-30T00:00:00Z", now=now)
        assert "short_term" in prompt
        assert "5-14 days" in prompt

    def test_medium_calibration(self):
        now = datetime(2026, 6, 10, tzinfo=timezone.utc)
        prompt = build_frontier_system_prompt("2026-06-30T00:00:00Z", now=now)
        assert "medium" in prompt
        assert "14-30 days" in prompt

    def test_long_calibration(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        prompt = build_frontier_system_prompt("2026-06-30T00:00:00Z", now=now)
        assert "more than 30 days" in prompt

    def test_unparseable_end_date_warning(self):
        prompt = build_frontier_system_prompt("not-a-date")
        assert "could not be parsed" in prompt

    def test_market_price_divergence_warning(self):
        prompt = build_frontier_system_prompt("2026-06-30")
        assert "true probability" in prompt.lower()
        assert "mispriced" in prompt.lower()
