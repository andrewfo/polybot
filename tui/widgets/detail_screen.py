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
        "[bold #00ff41]═══ MARKET INFO ═══[/]",
        "",
        f"[#00ff41]Question:[/] {question}",
        f"[#00ff41]Category:[/] {category}",
        f"[#00ff41]Condition ID:[/] {condition_id}",
        "",
        f"[#00ff41]YES:[/] {yes_p}    [#00ff41]NO:[/] {no_p}    [#00ff41]Spread:[/] {spread_str}",
        f"[#00ff41]Liquidity:[/] ${liq:,.0f}    [#00ff41]Volume 24h:[/] ${vol:,.0f}",
        f"[#00ff41]End Date:[/] {end_date[:10] if end_date else '---'}    [#00ff41]Days Remaining:[/] {days_str}    [#00ff41]Urgency:[/] {urgency}",
    ]
    return "\n".join(lines)


def _build_signals_section(agg: AggregatedSignal) -> str:
    """Section B: Individual signals with raw evidence."""
    lines = [
        "",
        "[bold #00ff41]═══ INDIVIDUAL SIGNALS ═══[/]",
    ]

    for signal in agg.individual_signals:
        resolution_tag = " [bold #ff0040](DIRECT RESOLUTION SOURCE)[/]" if signal.source.startswith("resolution_") else ""
        multiplier = SIGNAL_WEIGHT_MULTIPLIERS.get(signal.source, 1.0)
        ew = _compute_effective_weight(signal)

        lines.append("")
        lines.append(f"[bold #00ff41]── {signal.source.upper()}{resolution_tag} ──[/]")
        lines.append(f"  [#00ff41]Probability:[/] {signal.probability:.2%}" if signal.probability is not None else "  [#00ff41]Probability:[/] ---")
        lines.append(f"  [#00ff41]Confidence:[/] {signal.confidence:.2%}")
        lines.append(f"  [#00ff41]Weight:[/] {signal.confidence:.2f} × {multiplier:.1f}x = {ew:.2f}")
        lines.append(f"  [#00ff41]Data Points:[/] {signal.data_points}")
        lines.append(f"  [#00ff41]Reasoning:[/] {signal.reasoning}")

        # Raw evidence
        evidence = _format_raw_evidence(signal)
        if evidence:
            lines.append(f"  [#00ff41]Raw Data:[/]")
            lines.append(evidence)

    return "\n".join(lines)


def _build_math_section(agg: AggregatedSignal) -> str:
    """Section C: Aggregation math breakdown."""
    lines = [
        "",
        "[bold #00ff41]═══ AGGREGATION MATH ═══[/]",
        "",
        "[#00ff41]Source Multipliers:[/]",
        "  news: 1.0x  |  polling: 1.5x  |  econ: 2.0x  |  crypto: 2.0x",
        "",
        "[#00ff41]Per-Signal Weight Calculation:[/]",
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
            f"  {signal.source}: {signal.confidence:.2f} × {multiplier:.1f}x = {ew:.2f}  "
            f"(prob {signal.probability:.2f} × weight {ew:.2f} = {signal.probability * ew:.3f})"
        )

    lines.append("")
    lines.append(f"[#00ff41]Weighted Sum:[/] {weighted_sum:.4f}")
    lines.append(f"[#00ff41]Total Weight:[/] {total_weight:.4f}")
    lines.append(f"[#00ff41]Preliminary Estimate:[/] {agg.preliminary_probability:.4f}")

    return "\n".join(lines)


def _build_frontier_section(agg: AggregatedSignal) -> str:
    """Section D: Frontier model decision."""
    divergence = abs(agg.final_probability - agg.market_price)

    lines = [
        "",
        "[bold #00ff41]═══ FRONTIER MODEL DECISION ═══[/]",
        "",
        f"[#00ff41]Final Probability:[/] {agg.final_probability:.2%}",
        f"[#00ff41]Confidence:[/] {agg.confidence:.2%}",
        f"[#00ff41]Signals Agreement:[/] {agg.signals_agreement}",
        f"[#00ff41]Market Efficiency:[/] {agg.market_efficiency}",
        f"[#00ff41]Divergence from Market:[/] {divergence:.2%} (|{agg.final_probability:.2f} - {agg.market_price:.2f}|)",
        "",
        f"[#00ff41]Reasoning:[/] {agg.reasoning}",
    ]

    if agg.skipped:
        lines.append("")
        lines.append(f"[bold #ff0040]SKIPPED:[/] {agg.skip_reason}")

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
        background: #0a0a0a;
        border: solid #00ff41;
        padding: 1 2;
    }
    MarketDetailScreen Static {
        color: #00cc33;
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
        else:
            content += "\n\n[#007a20]No aggregation data available for this market. Run aggregate to see full signal details.[/]"

        content += "\n\n[dim]Press Escape to close[/]"

        with VerticalScroll():
            yield Static(content, markup=True)
