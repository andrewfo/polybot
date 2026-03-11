"""Market detail modal — drill-down view with full signal evidence."""

import json
from datetime import datetime, timezone
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from signals.aggregator import (
    AggregatedSignal,
    SIGNAL_WEIGHT_MULTIPLIERS,
    _compute_effective_weight,
    _format_raw_evidence,
)
from signals.temporal import build_date_context, compute_urgency_tier


def _safe_json_loads(val: Any) -> Any:
    """Parse JSON string or return as-is."""
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val
    return val


def _build_market_info(market: dict[str, Any]) -> str:
    """Section A: Market info block."""
    question = market.get("question", "???")
    condition_id = market.get("conditionId", market.get("condition_id", "???"))
    category = market.get("_category", "unknown")

    # Prices
    prices_raw = market.get("outcomePrices", "[]")
    prices = _safe_json_loads(prices_raw)
    if isinstance(prices, list) and len(prices) >= 2:
        yes_p = f"{float(prices[0]):.1%}"
        no_p = f"{float(prices[1]):.1%}"
    else:
        yes_p = "---"
        no_p = "---"

    liq = float(market.get("liquidityNum", market.get("liquidity", 0)) or 0)
    vol = float(market.get("volume24hr", 0) or 0)
    spread = market.get("spread")
    if spread is not None:
        spread_str = f"{float(spread):.4f}"
    else:
        spread_str = "---"

    end_date = market.get("endDate", market.get("end_date_iso", ""))
    ctx = build_date_context(end_date) if end_date else {}
    days = ctx.get("days_remaining")
    urgency = ctx.get("urgency_tier", "unknown")
    days_str = f"{int(days)} days" if days is not None else "unknown"

    lines = [
        "[bold #e0e8f0]\u2550\u2550\u2550 MARKET INFO \u2550\u2550\u2550[/]",
        "",
        f"[#4488cc]Question:[/] {question}",
        f"[#4488cc]Category:[/] {category}",
        f"[#4488cc]Condition ID:[/] {condition_id}",
        "",
        f"[#4488cc]YES:[/] {yes_p}    [#4488cc]NO:[/] {no_p}    [#4488cc]Spread:[/] {spread_str}",
        f"[#4488cc]Liquidity:[/] ${liq:,.0f}    [#4488cc]Volume 24h:[/] ${vol:,.0f}",
        f"[#4488cc]End Date:[/] {end_date[:10] if end_date else '---'}    [#4488cc]Days Remaining:[/] {days_str}    [#4488cc]Urgency:[/] {urgency}",
    ]
    return "\n".join(lines)


def _build_signals_section(agg: AggregatedSignal) -> str:
    """Section B: Individual signals with raw evidence."""
    lines = [
        "",
        "[bold #e0e8f0]\u2550\u2550\u2550 INDIVIDUAL SIGNALS \u2550\u2550\u2550[/]",
    ]

    for signal in agg.individual_signals:
        resolution_tag = " [bold #cc4444](DIRECT RESOLUTION SOURCE)[/]" if signal.source.startswith("resolution_") else ""
        multiplier = SIGNAL_WEIGHT_MULTIPLIERS.get(signal.source, 1.0)
        ew = _compute_effective_weight(signal)

        lines.append("")
        lines.append(f"[bold #4488cc]\u2500\u2500 {signal.source.upper()}{resolution_tag} \u2500\u2500[/]")
        lines.append(f"  [#4488cc]Probability:[/] {signal.probability:.2%}" if signal.probability is not None else "  [#4488cc]Probability:[/] ---")
        lines.append(f"  [#4488cc]Confidence:[/] {signal.confidence:.2%}")
        lines.append(f"  [#4488cc]Weight:[/] {signal.confidence:.2f} \u00d7 {multiplier:.1f}x = {ew:.2f}")
        lines.append(f"  [#4488cc]Data Points:[/] {signal.data_points}")
        lines.append(f"  [#4488cc]Reasoning:[/] {signal.reasoning}")

        # Raw evidence
        evidence = _format_raw_evidence(signal)
        if evidence:
            lines.append(f"  [#4488cc]Raw Data:[/]")
            lines.append(evidence)

    return "\n".join(lines)


def _build_math_section(agg: AggregatedSignal) -> str:
    """Section C: Aggregation math breakdown."""
    lines = [
        "",
        "[bold #e0e8f0]\u2550\u2550\u2550 AGGREGATION MATH \u2550\u2550\u2550[/]",
        "",
        "[#4488cc]Source Multipliers:[/]",
        "  news: 1.0x  |  polling: 1.5x  |  econ: 2.0x  |  crypto: 2.0x",
        "",
        "[#4488cc]Per-Signal Weight Calculation:[/]",
    ]

    total_weight = 0.0
    weighted_sum = 0.0
    for signal in agg.individual_signals:
        if signal.probability is None or signal.confidence <= 0:
            continue
        multiplier = SIGNAL_WEIGHT_MULTIPLIERS.get(signal.source, 1.0)
        ew = _compute_effective_weight(signal)
        total_weight += ew
        weighted_sum += signal.probability * ew
        lines.append(
            f"  {signal.source}: {signal.confidence:.2f} \u00d7 {multiplier:.1f}x = {ew:.2f}  "
            f"(prob {signal.probability:.2f} \u00d7 weight {ew:.2f} = {signal.probability * ew:.3f})"
        )

    lines.append("")
    lines.append(f"[#4488cc]Weighted Sum:[/] {weighted_sum:.4f}")
    lines.append(f"[#4488cc]Total Weight:[/] {total_weight:.4f}")
    lines.append(f"[#4488cc]Preliminary Estimate:[/] {agg.preliminary_probability:.4f}")

    return "\n".join(lines)


def _build_frontier_section(agg: AggregatedSignal) -> str:
    """Section D: Frontier model decision."""
    divergence = abs(agg.final_probability - agg.market_price)

    lines = [
        "",
        "[bold #e0e8f0]\u2550\u2550\u2550 FRONTIER MODEL DECISION \u2550\u2550\u2550[/]",
        "",
        f"[#4488cc]Final Probability:[/] {agg.final_probability:.2%}",
        f"[#4488cc]Confidence:[/] {agg.confidence:.2%}",
        f"[#4488cc]Signals Agreement:[/] {agg.signals_agreement}",
        f"[#4488cc]Market Efficiency:[/] {agg.market_efficiency}",
        f"[#4488cc]Divergence from Market:[/] {divergence:.2%} (|{agg.final_probability:.2f} - {agg.market_price:.2f}|)",
        "",
        f"[#4488cc]Reasoning:[/] {agg.reasoning}",
    ]

    if agg.skipped:
        lines.append("")
        lines.append(f"[bold #cc4444]SKIPPED:[/] {agg.skip_reason}")

    return "\n".join(lines)


class MarketDetailScreen(ModalScreen[None]):
    """Full-screen drill-down modal for market details."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
    ]

    DEFAULT_CSS = """
    MarketDetailScreen {
        align: center middle;
    }
    MarketDetailScreen > VerticalScroll {
        width: 90%;
        height: 90%;
        background: #0a1628;
        border: solid #4488cc;
        padding: 1 2;
    }
    MarketDetailScreen Static {
        color: #8899aa;
        width: 1fr;
    }
    """

    def __init__(
        self,
        market_data: dict[str, Any],
        aggregation: AggregatedSignal | None = None,
    ) -> None:
        super().__init__()
        self._market_data = market_data
        self._aggregation = aggregation

    def compose(self) -> ComposeResult:
        content = _build_market_info(self._market_data)

        if self._aggregation is not None:
            content += "\n" + _build_signals_section(self._aggregation)
            content += "\n" + _build_math_section(self._aggregation)
            content += "\n" + _build_frontier_section(self._aggregation)
        elif self._market_data.get("_category"):
            content += "\n\n[#ccaa44]Aggregation ran but returned no result (insufficient signals or low frontier confidence).[/]"
            content += f"\n[#667788]Category: {self._market_data.get('_category', 'unknown')}[/]"
        else:
            content += "\n\n[#667788]No aggregation data available for this market. Run aggregate to see full signal details.[/]"

        content += "\n\n[dim]Press Escape to close[/]"

        with VerticalScroll():
            yield Static(content, markup=True)
