"""Tests for core/client.py fill-parsing helpers.

The ClobClientWrapper class itself requires a live private key to instantiate,
so we only test the standalone parsing functions here.
"""

import pytest

from core.client import _parse_fill_from_order, _parse_fill_from_trades


class TestParseFillFromOrder:
    def test_canonical_fields(self):
        order = {"size_matched": "50.0", "price": "0.4900"}
        result = _parse_fill_from_order(order)
        assert result == {"fill_price": 0.49, "filled_size": 50.0}

    def test_camelcase_fields(self):
        order = {"sizeMatched": 25.0, "avgFillPrice": 0.61}
        result = _parse_fill_from_order(order)
        assert result == {"fill_price": 0.61, "filled_size": 25.0}

    def test_alternate_field_names(self):
        order = {"filled_size": 10.0, "average_price": 0.7}
        result = _parse_fill_from_order(order)
        assert result == {"fill_price": 0.7, "filled_size": 10.0}

    def test_unfilled_order_returns_none(self):
        # size_matched=0 means no fills yet
        assert _parse_fill_from_order({"size_matched": "0", "price": "0.50"}) is None

    def test_missing_size_returns_none(self):
        assert _parse_fill_from_order({"price": "0.50"}) is None

    def test_missing_price_returns_none(self):
        assert _parse_fill_from_order({"size_matched": "10"}) is None

    def test_out_of_range_price_returns_none(self):
        # Prices for binary markets must live in (0, 1) — reject 1.5 as garbage
        assert _parse_fill_from_order({"size_matched": "10", "price": "1.5"}) is None
        assert _parse_fill_from_order({"size_matched": "10", "price": "0"}) is None

    def test_non_dict_input(self):
        assert _parse_fill_from_order(None) is None
        assert _parse_fill_from_order("string") is None
        assert _parse_fill_from_order([1, 2, 3]) is None

    def test_garbage_numeric_value_falls_through(self):
        # Bad price should be skipped; result must be None when no usable fields
        order = {"size_matched": "garbage", "price": "0.5"}
        assert _parse_fill_from_order(order) is None


class TestParseFillFromTrades:
    def test_single_full_fill(self):
        trades = [
            {"order_id": "ord-1", "size": "20.0", "price": "0.45"},
            {"order_id": "ord-other", "size": "5.0", "price": "0.99"},  # not ours
        ]
        result = _parse_fill_from_trades(trades, "ord-1")
        assert result == {"fill_price": 0.45, "filled_size": 20.0}

    def test_multiple_partial_fills_weighted(self):
        # 10 @ 0.50, 10 @ 0.40 -> avg 0.45
        trades = [
            {"order_id": "ord-1", "size": "10", "price": "0.50"},
            {"order_id": "ord-1", "size": "10", "price": "0.40"},
        ]
        result = _parse_fill_from_trades(trades, "ord-1")
        assert result["fill_price"] == pytest.approx(0.45)
        assert result["filled_size"] == pytest.approx(20.0)

    def test_alternate_id_field(self):
        trades = [{"orderID": "ord-1", "size": "5", "price": "0.6"}]
        result = _parse_fill_from_trades(trades, "ord-1")
        assert result == {"fill_price": 0.6, "filled_size": 5.0}

    def test_dict_wrapper(self):
        # Some endpoints wrap the list in {"data": [...]}
        trades = {"data": [{"order_id": "ord-1", "size": "3", "price": "0.7"}]}
        result = _parse_fill_from_trades(trades, "ord-1")
        assert result["fill_price"] == pytest.approx(0.7)
        assert result["filled_size"] == pytest.approx(3.0)

    def test_no_matching_order(self):
        trades = [{"order_id": "ord-other", "size": "10", "price": "0.5"}]
        assert _parse_fill_from_trades(trades, "ord-1") is None

    def test_empty_order_id_returns_none(self):
        trades = [{"order_id": "ord-1", "size": "10", "price": "0.5"}]
        assert _parse_fill_from_trades(trades, "") is None

    def test_malformed_input(self):
        assert _parse_fill_from_trades(None, "ord-1") is None
        assert _parse_fill_from_trades("not-a-list", "ord-1") is None

    def test_skips_garbage_entries(self):
        trades = [
            "not-a-dict",
            {"order_id": "ord-1", "size": "bad", "price": "0.5"},  # bad size
            {"order_id": "ord-1", "size": "5", "price": "9.9"},   # bad price
            {"order_id": "ord-1", "size": "10", "price": "0.5"},  # good
        ]
        result = _parse_fill_from_trades(trades, "ord-1")
        assert result == {"fill_price": 0.5, "filled_size": 10.0}
