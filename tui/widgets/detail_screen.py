"""Market detail modal — drill-down view with full signal evidence and charts."""

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
from tui.widgets.charts import (
    comparison_bars,
    horizontal_bar,
    probability_comparison,
    signal_weights_table,
    vol_comparison,
    C_TEXT,
    C_DIM,
    C_GREEN,
    C_RED,
    C_YELLOW,
    C_ACCENT,
    C_MUTED,
    C_BG,
)


def _safe_json_loads(val: Any) -> Any:
    """Parse JSON string or return as-is."""
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val
    return val


def _build_market_info(market: dict[str, Any]) -> str:
    """Section A: Market info block with visual indicators."""
    question = market.get("question", "???")
    condition_id = market.get("conditionId", market.get("condition_id", "???"))
    category = market.get("_category", "unknown")

    # Prices
    yes_p_val = None
    no_p_val = None
    prices_raw = market.get("outcomePrices", "[]")
    prices = _safe_json_loads(prices_raw)
    if isinstance(prices, list) and len(prices) >= 2:
        try:
            yes_p_val = float(prices[0])
            no_p_val = float(prices[1])
        except (TypeError, ValueError):
            pass
    if yes_p_val is None:
        tokens = market.get("tokens", [])
        for tok in tokens:
            outcome = str(tok.get("outcome", "")).upper()
            price = tok.get("price")
            if price is not None:
                try:
                    if outcome == "YES":
                        yes_p_val = float(price)
                    elif outcome == "NO":
                        no_p_val = float(price)
                except (TypeError, ValueError):
                    pass

    yes_p = f"{yes_p_val:.1%}" if yes_p_val is not None else "---"
    no_p = f"{no_p_val:.1%}" if no_p_val is not None else "---"

    liq = float(market.get("liquidityNum", market.get("liquidity", 0)) or 0)
    vol = float(market.get("volume24hr", 0) or 0)
    spread = market.get("spread")
    spread_str = f"{float(spread):.4f}" if spread is not None else "---"

    end_date = market.get("endDate", market.get("end_date_iso", ""))
    ctx = build_date_context(end_date) if end_date else {}
    days = ctx.get("days_remaining")
    urgency = ctx.get("urgency_tier", "unknown")
    days_str = f"{int(days)} days" if days is not None else "unknown"

    # Urgency color
    urgency_colors = {
        "imminent": C_RED, "short_term": C_YELLOW,
        "medium": C_ACCENT, "long": C_DIM,
    }
    urg_color = urgency_colors.get(urgency, C_DIM)

    lines = [
        f"[bold {C_TEXT}]{'═' * 60}[/]",
        f"[bold {C_TEXT}]{question}[/]",
        f"[{C_DIM}]ID: {condition_id}  |  Category: {category}[/]",
        f"[{C_DIM}]{'─' * 60}[/]",
        "",
    ]

    # Price bar (visual YES/NO split)
    if yes_p_val is not None:
        yes_bar = horizontal_bar(yes_p_val, 1.0, 25, C_GREEN, show_value=False)
        no_bar_val = no_p_val if no_p_val is not None else 1.0 - yes_p_val
        no_bar = horizontal_bar(no_bar_val, 1.0, 25, C_RED, show_value=False)
        lines.append(f"[{C_MUTED}]YES {yes_p:>6}[/]  {yes_bar}    [{C_MUTED}]NO {no_p:>6}[/]  {no_bar}")
    else:
        lines.append(f"[{C_MUTED}]YES:[/] {yes_p}    [{C_MUTED}]NO:[/] {no_p}")

    lines.extend([
        "",
        f"[{C_MUTED}]Liquidity:[/] [{C_TEXT}]${liq:,.0f}[/]    "
        f"[{C_MUTED}]Volume 24h:[/] [{C_TEXT}]${vol:,.0f}[/]    "
        f"[{C_MUTED}]Spread:[/] [{C_TEXT}]{spread_str}[/]",
        f"[{C_MUTED}]End Date:[/] [{C_TEXT}]{end_date[:10] if end_date else '---'}[/]    "
        f"[{C_MUTED}]Remaining:[/] [{C_TEXT}]{days_str}[/]    "
        f"[{C_MUTED}]Urgency:[/] [{urg_color}]{urgency}[/]",
    ])

    return "\n".join(lines)


def _build_signals_section(agg: AggregatedSignal) -> str:
    """Section B: Individual signals with raw evidence and visual comparison."""
    lines = [
        "",
        f"[bold {C_TEXT}]╔══ PROBABILITY COMPARISON ══╗[/]",
    ]

    # Visual probability bars
    signal_list = [
        (s.source, s.probability, s.confidence)
        for s in agg.individual_signals
        if s.probability is not None
    ]
    lines.append(probability_comparison(
        market_price=agg.market_price,
        raw_estimate=agg.final_probability,
        effective_prob=agg.preliminary_probability,
        signals=signal_list,
    ))

    # Signal weights table
    lines.append("")
    signal_rows = []
    for signal in agg.individual_signals:
        mult = SIGNAL_WEIGHT_MULTIPLIERS.get(signal.source, 1.0)
        ew = _compute_effective_weight(signal)
        signal_rows.append((signal.source, signal.probability, signal.confidence, mult, ew))
    lines.append(signal_weights_table(signal_rows))

    # Individual signal details
    lines.append("")
    lines.append(f"[bold {C_TEXT}]╔══ SIGNAL DETAILS ══╗[/]")

    for signal in agg.individual_signals:
        resolution_tag = f" [{C_RED}](RESOLUTION SOURCE)[/]" if signal.source.startswith("resolution_") else ""
        multiplier = SIGNAL_WEIGHT_MULTIPLIERS.get(signal.source, 1.0)
        ew = _compute_effective_weight(signal)

        lines.append("")
        lines.append(f"[bold {C_ACCENT}]── {signal.source.upper()}{resolution_tag} ──[/]")

        if signal.probability is not None:
            prob_bar = horizontal_bar(signal.probability, 1.0, 20, C_ACCENT)
            lines.append(f"  [{C_MUTED}]Probability:[/]  {prob_bar}")
        else:
            lines.append(f"  [{C_MUTED}]Probability:[/]  [{C_DIM}]--- (no data)[/]")

        conf_bar = horizontal_bar(signal.confidence, 1.0, 20, C_YELLOW)
        lines.append(f"  [{C_MUTED}]Confidence:[/]   {conf_bar}")
        lines.append(f"  [{C_MUTED}]Weight:[/]       [{C_TEXT}]{signal.confidence:.2f} × {multiplier:.1f}x = {ew:.2f}[/]")
        lines.append(f"  [{C_MUTED}]Data Points:[/]  [{C_TEXT}]{signal.data_points}[/]")
        lines.append(f"  [{C_MUTED}]Reasoning:[/]    [{C_TEXT}]{signal.reasoning}[/]")

        # Raw evidence
        evidence = _format_raw_evidence(signal)
        if evidence:
            lines.append(f"  [{C_MUTED}]Raw Evidence:[/]")
            lines.append(f"[{C_DIM}]{evidence}[/]")

    return "\n".join(lines)


def _build_crypto_section(agg: AggregatedSignal) -> str:
    """Section B2: Crypto-specific data with vol chart and model comparison."""
    crypto_raw = None
    for signal in agg.individual_signals:
        if signal.source == "resolution_crypto" and signal.raw_data:
            crypto_raw = signal.raw_data
            break

    if not crypto_raw:
        return ""

    lines = [
        "",
        f"[bold {C_TEXT}]╔══ CRYPTO MODEL DATA ══╗[/]",
    ]

    current = crypto_raw.get("current_price")
    target = crypto_raw.get("target_price")
    coin_id = crypto_raw.get("coin_id", "?")
    direction = crypto_raw.get("direction", "above")
    distance = crypto_raw.get("distance_pct", 0)

    if current and target:
        lines.append(
            f"[{C_MUTED}]Coin:[/] [{C_TEXT}]{coin_id}[/]    "
            f"[{C_MUTED}]Current:[/] [{C_TEXT}]${current:,.2f}[/]    "
            f"[{C_MUTED}]Target:[/] [{C_TEXT}]${target:,.2f}[/] ({direction}, {distance:+.1f}%)"
        )

    # Volatility comparison
    hist_vol = crypto_raw.get("historical_vol", 0)
    ewm_vol = crypto_raw.get("ewm_vol", 0)
    st_vol = crypto_raw.get("short_term_vol", 0)
    deribit_iv = crypto_raw.get("deribit_iv")
    selected_vol = crypto_raw.get("annualized_vol", 0)
    vol_source = crypto_raw.get("vol_source", "unknown")

    if hist_vol > 0 or ewm_vol > 0:
        lines.append("")
        lines.append(vol_comparison(
            historical=hist_vol,
            ewm=ewm_vol,
            short_term=st_vol,
            deribit_iv=deribit_iv,
            selected=selected_vol,
            selected_source=vol_source,
        ))

    # Drift
    drift = crypto_raw.get("shrunk_drift")
    raw_drift = crypto_raw.get("realized_drift")
    if drift is not None:
        drift_str = f"{drift:+.1%}/yr"
        if raw_drift is not None and abs(raw_drift - drift) > 0.01:
            drift_str += f"  (raw: {raw_drift:+.1%}, shrunk)"
        lines.append(f"\n[{C_MUTED}]Drift:[/] [{C_TEXT}]{drift_str}[/]")

    # Model probabilities comparison
    res_type = crypto_raw.get("resolution_type", "barrier")
    terminal_p = crypto_raw.get("terminal_prob")
    barrier_p = crypto_raw.get("barrier_prob")
    if terminal_p is not None and barrier_p is not None:
        lines.append("")
        lines.append(f"[{C_TEXT}]Probability Models (resolution type: {res_type}):[/]")

        t_bar = horizontal_bar(terminal_p, 1.0, 25, C_MUTED)
        b_bar = horizontal_bar(barrier_p, 1.0, 25, C_ACCENT)
        lines.append(f"  [{C_MUTED}]Terminal (at expiry):[/]  {t_bar}")
        lines.append(f"  [{C_MUTED}]Barrier (any touch): [/]  {b_bar}")

        selected_label = "barrier" if res_type == "barrier" else "terminal"
        lines.append(f"  [{C_GREEN}]→ Using {selected_label} model[/]")

    # Days remaining + trend
    days = crypto_raw.get("days_remaining")
    trend = crypto_raw.get("trend")
    if days is not None:
        lines.append(f"[{C_MUTED}]Days remaining:[/] [{C_TEXT}]{days:.0f}[/]")
    if trend:
        lines.append(f"[{C_MUTED}]90d Trend:[/] [{C_TEXT}]{trend}[/]")

    return "\n".join(lines)


def _build_math_section(agg: AggregatedSignal) -> str:
    """Section C: Aggregation math breakdown with visual indicators."""
    lines = [
        "",
        f"[bold {C_TEXT}]╔══ AGGREGATION MATH ══╗[/]",
        "",
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
        contrib = signal.probability * ew
        lines.append(
            f"  [{C_MUTED}]{signal.source:<20}[/] "
            f"[{C_TEXT}]{signal.probability:.4f} × {ew:.2f} = {contrib:.4f}[/]"
        )

    lines.append(f"  [{C_BG}]{'─' * 50}[/]")
    lines.append(f"  [{C_MUTED}]Weighted Sum:[/]       [{C_TEXT}]{weighted_sum:.4f}[/]")
    lines.append(f"  [{C_MUTED}]Total Weight:[/]       [{C_TEXT}]{total_weight:.4f}[/]")
    lines.append(f"  [{C_MUTED}]Preliminary Est:[/]    [{C_TEXT}]{agg.preliminary_probability:.4f}[/]")

    return "\n".join(lines)


def _build_frontier_section(agg: AggregatedSignal) -> str:
    """Section D: Frontier model decision with visual divergence indicator."""
    divergence = abs(agg.final_probability - agg.market_price)

    # Divergence color
    if divergence > 0.30:
        div_color = C_RED
    elif divergence > 0.15:
        div_color = C_YELLOW
    else:
        div_color = C_GREEN

    lines = [
        "",
        f"[bold {C_TEXT}]╔══ FRONTIER MODEL DECISION ══╗[/]",
        "",
    ]

    # Final probability bar
    prob_bar = horizontal_bar(agg.final_probability, 1.0, 25, C_ACCENT)
    lines.append(f"[{C_MUTED}]Final Probability:[/]   {prob_bar}")

    conf_bar = horizontal_bar(agg.confidence, 1.0, 25, C_GREEN if agg.confidence > 0.5 else C_YELLOW)
    lines.append(f"[{C_MUTED}]Confidence:[/]          {conf_bar}")

    div_bar = horizontal_bar(divergence, 0.5, 25, div_color)
    lines.append(f"[{C_MUTED}]Divergence:[/]          {div_bar}")

    lines.extend([
        "",
        f"[{C_MUTED}]Signals Agreement:[/]   [{C_TEXT}]{agg.signals_agreement}[/]",
        f"[{C_MUTED}]Market Efficiency:[/]   [{C_TEXT}]{agg.market_efficiency}[/]",
        "",
        f"[{C_MUTED}]Reasoning:[/]",
        f"[{C_TEXT}]{agg.reasoning}[/]",
    ])

    if agg.skipped:
        lines.append("")
        lines.append(f"[bold {C_RED}]SKIPPED: {agg.skip_reason}[/]")

    return "\n".join(lines)


class MarketDetailScreen(ModalScreen[None]):
    """Full-screen drill-down modal for market details with charts."""

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
            content += "\n" + _build_crypto_section(self._aggregation)
            content += "\n" + _build_math_section(self._aggregation)
            content += "\n" + _build_frontier_section(self._aggregation)
        elif self._market_data.get("_category"):
            content += f"\n\n[{C_YELLOW}]Aggregation ran but returned no result (insufficient signals or low frontier confidence).[/]"
            content += f"\n[{C_DIM}]Category: {self._market_data.get('_category', 'unknown')}[/]"
        else:
            content += f"\n\n[{C_DIM}]No aggregation data available. Run aggregate to see full signal details.[/]"

        content += f"\n\n[dim]Press Escape to close[/]"

        with VerticalScroll():
            yield Static(content, markup=True)
