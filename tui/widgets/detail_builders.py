"""Unified detail view builders — single source of truth for market analysis rendering.

Consolidates builder functions previously spread across detail_screen.py and bets_panel.py
into one entry point: build_full_analysis(market_data, aggregation, decision).
"""

import json
from datetime import datetime, timezone
from typing import Any

from rich.markup import escape as esc

from signals.aggregator import (
    AggregatedSignal,
    SIGNAL_WEIGHT_MULTIPLIERS,
    _compute_effective_weight,
    _format_raw_evidence,
)
from signals.temporal import build_date_context
from tui.widgets.charts import (
    horizontal_bar,
    kelly_breakdown,
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


def _section_header(title: str) -> str:
    """Render a clean section header: '  TITLE ──────────'."""
    bar = "\u2500" * max(1, 56 - len(title))
    return f"[bold {C_TEXT}]  {title} {bar}[/]"


def _build_market_info(market: dict[str, Any]) -> str:
    """Market header: question, prices, liquidity, time remaining."""
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

    urgency_colors = {
        "imminent": C_RED, "short_term": C_YELLOW,
        "medium": C_ACCENT, "long": C_DIM,
    }
    urg_color = urgency_colors.get(urgency, C_DIM)

    lines = [
        _section_header("MARKET INFO"),
        "",
        f"[bold {C_TEXT}]{esc(question)}[/]",
        f"[{C_DIM}]ID: {esc(condition_id)}  |  Category: {esc(category)}[/]",
        "",
    ]

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


def _build_probability_section(
    agg: AggregatedSignal,
    decision: Any | None = None,
) -> str:
    """Probability comparison bars + signal weights table."""
    lines = [
        "",
        _section_header("PROBABILITY COMPARISON"),
    ]

    signal_list = [
        (s.source, s.probability, s.confidence)
        for s in agg.individual_signals
        if s.probability is not None
    ]

    # Use decision's effective_prob if available, otherwise use preliminary
    effective = decision.effective_prob if decision else agg.preliminary_probability
    raw_est = decision.estimated_prob if decision else agg.final_probability

    lines.append(probability_comparison(
        market_price=agg.market_price,
        raw_estimate=raw_est,
        effective_prob=effective,
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

    # Preliminary vs final + agreement
    lines.extend([
        "",
        f"[{C_MUTED}]Preliminary (weighted avg):  [{C_TEXT}]{agg.preliminary_probability:.4f}[/]",
        f"[{C_MUTED}]Frontier final:              [{C_TEXT}]{agg.final_probability:.4f}[/]",
        f"[{C_MUTED}]Signals agreement:           [{C_TEXT}]{agg.signals_agreement}[/]",
        f"[{C_MUTED}]Market efficiency:           [{C_TEXT}]{agg.market_efficiency}[/]",
    ])

    return "\n".join(lines)


def _get_crypto_raw_data(agg: AggregatedSignal) -> dict[str, Any] | None:
    """Extract raw crypto resolution data from the aggregation."""
    if not agg or not agg.individual_signals:
        return None
    for signal in agg.individual_signals:
        if signal.source == "resolution_crypto" and signal.raw_data:
            return signal.raw_data
    return None


def _build_crypto_section(agg: AggregatedSignal) -> str:
    """Crypto model data: price/target, vol comparison, barrier/terminal."""
    crypto_raw = _get_crypto_raw_data(agg)
    if not crypto_raw:
        return ""

    lines = [
        "",
        _section_header("CRYPTO MODEL DATA"),
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
        lines.append(f"  [{C_GREEN}]-> Using {selected_label} model[/]")

    # Days remaining + trend
    days = crypto_raw.get("days_remaining")
    trend = crypto_raw.get("trend")
    if days is not None:
        lines.append(f"[{C_MUTED}]Days remaining:[/] [{C_TEXT}]{days:.0f}[/]")
    if trend:
        lines.append(f"[{C_MUTED}]90d Trend:[/] [{C_TEXT}]{esc(str(trend))}[/]")

    return "\n".join(lines)


def _build_kelly_section(decision: Any) -> str:
    """Kelly decision summary: side, edge, bet size, EV, depth."""
    from config.settings import POLYMARKET_FEE_RATE, TEST_BANKROLL

    trade_color = C_GREEN if decision.should_trade else C_RED
    status = "TRADE" if decision.should_trade else f"SKIP \u2014 {esc(decision.skip_reason)}"

    lines = [
        "",
        _section_header("KELLY SIZING"),
        "",
        f"[bold {trade_color}]Decision: {status}[/]",
        f"[{C_DIM}]Token: {esc(decision.token_id)}[/]",
    ]

    if decision.should_trade:
        depth_info = ""
        if decision.depth_adjusted:
            depth_info = f"  [{C_YELLOW}][depth-adjusted, slippage={decision.depth_slippage:.1%}][/{C_YELLOW}]"
        elif decision.depth_total_usd > 0:
            depth_info = f"  [{C_DIM}][depth=${decision.depth_total_usd:.0f}, slippage={decision.depth_slippage:.1%}][/{C_DIM}]"

        lines.extend([
            f"[{C_MUTED}]Side:[/]     [{C_TEXT}]{decision.side}[/]",
            f"[{C_MUTED}]Edge:[/]     [{C_TEXT}]{decision.edge:.2%}[/]",
            f"[{C_MUTED}]Bet:[/]      [{C_TEXT}]${decision.bet_size_usd:.2f}[/]{depth_info}",
            f"[{C_MUTED}]EV:[/]       [{C_TEXT}]${decision.expected_value:.2f}[/]",
            f"[{C_MUTED}]Kelly:[/]    [{C_TEXT}]{decision.adjusted_fraction:.1%}[/]",
        ])

    # Full Kelly math breakdown
    lines.append("")
    lines.append(kelly_breakdown(
        estimated_prob=decision.estimated_prob,
        effective_prob=decision.effective_prob,
        market_price=decision.market_price,
        confidence=decision.confidence,
        edge=decision.edge,
        full_kelly=decision.full_kelly_fraction,
        adjusted_kelly=decision.adjusted_fraction,
        bet_size=decision.bet_size_usd,
        bankroll=TEST_BANKROLL,
        side=decision.side,
        fee_rate=POLYMARKET_FEE_RATE,
    ))

    return "\n".join(lines)


def _build_frontier_section(agg: AggregatedSignal) -> str:
    """Frontier reasoning: full text, divergence indicator."""
    divergence = abs(agg.final_probability - agg.market_price)

    if divergence > 0.30:
        div_color = C_RED
    elif divergence > 0.15:
        div_color = C_YELLOW
    else:
        div_color = C_GREEN

    lines = [
        "",
        _section_header("FRONTIER REASONING"),
        "",
    ]

    prob_bar = horizontal_bar(agg.final_probability, 1.0, 25, C_ACCENT)
    lines.append(f"[{C_MUTED}]Final Probability:[/]   {prob_bar}")

    conf_bar = horizontal_bar(agg.confidence, 1.0, 25, C_GREEN if agg.confidence > 0.5 else C_YELLOW)
    lines.append(f"[{C_MUTED}]Confidence:[/]          {conf_bar}")

    div_bar = horizontal_bar(divergence, 0.5, 25, div_color)
    lines.append(f"[{C_MUTED}]Divergence:[/]          {div_bar}")

    lines.extend([
        "",
        f"[{C_TEXT}]{esc(agg.reasoning)}[/]",
    ])

    if agg.skipped:
        lines.append("")
        lines.append(f"[bold {C_RED}]SKIPPED: {esc(agg.skip_reason)}[/]")

    return "\n".join(lines)


def build_full_analysis(
    market_data: dict[str, Any],
    aggregation: AggregatedSignal | None = None,
    decision: Any | None = None,
) -> str:
    """Build the complete unified detail view for a market.

    Single entry point that renders all sections:
    1. Market info (question, prices, liquidity, time remaining)
    2. Probability comparison bars + signal weights table
    3. Crypto model data (if crypto)
    4. Kelly decision summary (if decision available)
    5. Frontier reasoning (full text)

    Args:
        market_data: Gamma API market dict
        aggregation: AggregatedSignal from frontier model (or None)
        decision: TradeDecision from Kelly sizing (or None)

    Returns:
        Rich markup string for rendering in a Static widget.
    """
    sections: list[str] = []

    # 1. Market info
    sections.append(_build_market_info(market_data))

    if aggregation is not None:
        # 2. Probability comparison + signal weights
        sections.append(_build_probability_section(aggregation, decision))

        # 3. Crypto model data
        crypto = _build_crypto_section(aggregation)
        if crypto:
            sections.append(crypto)

        # 4. Kelly sizing
        if decision is not None:
            sections.append(_build_kelly_section(decision))

        # 5. Frontier reasoning
        sections.append(_build_frontier_section(aggregation))

    elif market_data.get("_category"):
        sections.append(
            f"\n\n[{C_YELLOW}]Aggregation ran but returned no result "
            f"(insufficient signals or low frontier confidence).[/]"
        )
        sections.append(f"[{C_DIM}]Category: {market_data.get('_category', 'unknown')}[/]")
    else:
        sections.append(f"\n\n[{C_DIM}]No aggregation data available. Run aggregate to see full signal details.[/]")

    return "\n".join(sections)
