"""Unicode chart rendering utilities for the TUI.

Renders horizontal bar charts, sparklines, and comparison visualizations
using Rich markup. All functions return strings ready for Rich rendering.
"""

from typing import Any

# Block characters for sparklines (8 levels)
SPARK_CHARS = "▁▂▃▄▅▆▇█"

# Bar characters
BAR_FULL = "█"
BAR_MED = "▓"
BAR_LIGHT = "░"
BAR_EMPTY = "░"

# Theme colors
C_ACCENT = "#4488cc"
C_GREEN = "#44aa66"
C_RED = "#cc4444"
C_YELLOW = "#ccaa44"
C_TEXT = "#e0e8f0"
C_DIM = "#667788"
C_MUTED = "#8899aa"
C_BG = "#2a3a5a"


def horizontal_bar(
    value: float,
    max_value: float = 1.0,
    width: int = 30,
    fill_color: str = C_ACCENT,
    empty_color: str = C_BG,
    show_value: bool = True,
    fmt: str = ".0%",
) -> str:
    """Render a single horizontal bar.

    Args:
        value: The value to display.
        max_value: Maximum scale value.
        width: Total bar width in characters.
        fill_color: Rich color for filled portion.
        empty_color: Rich color for empty portion.
        show_value: Whether to append the formatted value.
        fmt: Format spec for the value label.
    """
    ratio = max(0.0, min(1.0, value / max_value)) if max_value > 0 else 0
    filled = int(ratio * width)
    empty = width - filled

    bar = f"[{fill_color}]{BAR_FULL * filled}[/][{empty_color}]{BAR_EMPTY * empty}[/]"
    if show_value:
        bar += f"  [{C_TEXT}]{value:{fmt}}[/]"
    return bar


def comparison_bars(
    items: list[tuple[str, float, str]],
    max_value: float = 1.0,
    label_width: int = 18,
    bar_width: int = 30,
    fmt: str = ".0%",
) -> str:
    """Render multiple labeled horizontal bars for comparison.

    Args:
        items: List of (label, value, color) tuples.
        max_value: Scale maximum.
        label_width: Width to pad labels to.
        bar_width: Width of each bar.
        fmt: Format spec for value labels.
    """
    lines = []
    for label, value, color in items:
        padded_label = label[:label_width].ljust(label_width)
        bar = horizontal_bar(value, max_value, bar_width, color, fmt=fmt)
        lines.append(f"[{C_MUTED}]{padded_label}[/] {bar}")
    return "\n".join(lines)


def probability_comparison(
    market_price: float,
    raw_estimate: float,
    effective_prob: float,
    signals: list[tuple[str, float | None, float]] | None = None,
) -> str:
    """Render a probability comparison chart.

    Shows market price, raw estimate, blended estimate, and individual
    signal probabilities as horizontal bars.

    Args:
        market_price: Current market implied probability.
        raw_estimate: Raw frontier model estimate.
        effective_prob: Confidence-blended probability.
        signals: Optional list of (source, probability, confidence) tuples.
    """
    items: list[tuple[str, float, str]] = [
        ("Market Price", market_price, C_DIM),
        ("Raw Estimate", raw_estimate, C_YELLOW),
        ("Effective (blend)", effective_prob, C_GREEN if effective_prob > market_price else C_RED),
    ]

    if signals:
        items.append(("", -1, ""))  # spacer
        for source, prob, conf in signals:
            if prob is not None and prob >= 0:
                short_source = source.replace("resolution_", "res_").replace("prediction_", "pred_")
                label = f"  {short_source}"
                items.append((label, prob, C_ACCENT))

    lines = []
    for label, value, color in items:
        if value < 0:
            lines.append("")
            continue
        padded = label[:18].ljust(18)
        bar = horizontal_bar(value, 1.0, 30, color)
        lines.append(f"[{C_MUTED}]{padded}[/] {bar}")

    return "\n".join(lines)


def vol_comparison(
    historical: float,
    ewm: float,
    short_term: float,
    deribit_iv: float | None,
    selected: float,
    selected_source: str,
) -> str:
    """Render a volatility comparison chart.

    Shows all vol estimates as horizontal bars with the selected one highlighted.
    """
    max_vol = max(historical, ewm, short_term, deribit_iv or 0, selected, 0.01)
    # Round up to nearest 0.2 for nice scale
    scale = max(0.2, ((max_vol // 0.2) + 1) * 0.2)

    items: list[tuple[str, float, str]] = [
        ("Historical", historical, C_MUTED),
        ("EWM (recent)", ewm, C_MUTED),
        ("7-day short", short_term, C_MUTED),
    ]
    if deribit_iv is not None:
        items.append(("Deribit IV", deribit_iv, C_ACCENT))

    items.append((f"Selected →", selected, C_GREEN))

    lines = [f"[{C_TEXT}]Volatility Estimates (annualized):[/]"]
    for label, value, color in items:
        padded = label[:18].ljust(18)
        bar = horizontal_bar(value, scale, 30, color)
        lines.append(f"[{C_MUTED}]{padded}[/] {bar}")

    lines.append(f"[{C_DIM}]Source: {selected_source}[/]")
    return "\n".join(lines)


def sparkline(
    values: list[float],
    width: int = 50,
    label_start: str = "",
    label_end: str = "",
    color: str = C_ACCENT,
) -> str:
    """Render a sparkline from a series of values.

    Downsamples to `width` characters using min-max bucketing.

    Args:
        values: Series of numeric values.
        width: Number of characters wide.
        label_start: Label for the start (e.g., price).
        label_end: Label for the end (e.g., current price).
        color: Rich color for the sparkline.
    """
    if not values or len(values) < 2:
        return f"[{C_DIM}]Insufficient data for sparkline[/]"

    # Downsample using averaging
    n = len(values)
    if n > width:
        bucket_size = n / width
        sampled = []
        for i in range(width):
            start_idx = int(i * bucket_size)
            end_idx = int((i + 1) * bucket_size)
            bucket = values[start_idx:end_idx]
            sampled.append(sum(bucket) / len(bucket))
    else:
        sampled = values

    min_v = min(sampled)
    max_v = max(sampled)
    span = max_v - min_v

    if span == 0:
        chars = SPARK_CHARS[3] * len(sampled)
    else:
        chars = ""
        for v in sampled:
            idx = int((v - min_v) / span * 7)
            idx = max(0, min(7, idx))
            chars += SPARK_CHARS[idx]

    parts = [f"[{color}]{chars}[/]"]
    if label_start or label_end:
        parts.insert(0, f"[{C_DIM}]{label_start}[/] ")
        parts.append(f" [{C_DIM}]{label_end}[/]")

    return "".join(parts)


def kelly_breakdown(
    estimated_prob: float,
    effective_prob: float,
    market_price: float,
    confidence: float,
    edge: float,
    full_kelly: float,
    adjusted_kelly: float,
    bet_size: float,
    bankroll: float,
    side: str,
    fee_rate: float = 0.02,
) -> str:
    """Render the full Kelly criterion math breakdown.

    Shows step-by-step: raw estimate → confidence blend → edge → Kelly → bet.
    """
    # Side info
    if side == "BUY_YES":
        cost = market_price
        gross_profit = 1.0 - market_price
        net_profit = gross_profit * (1.0 - fee_rate)
        odds = net_profit / cost if cost > 0 else 0
    else:
        cost = 1.0 - market_price
        gross_profit = market_price
        net_profit = gross_profit * (1.0 - fee_rate)
        odds = net_profit / cost if cost > 0 else 0

    lines = [
        f"[bold {C_TEXT}]╔══ KELLY CRITERION BREAKDOWN ══╗[/]",
        "",
        f"[{C_TEXT}]Step 1: Confidence Blending[/]",
        f"[{C_MUTED}]  Raw frontier estimate:  {estimated_prob:.4f}[/]",
        f"[{C_MUTED}]  Market price:           {market_price:.4f}[/]",
        f"[{C_MUTED}]  Confidence:             {confidence:.4f}[/]",
        f"[{C_ACCENT}]  effective = {confidence:.2f} × {estimated_prob:.4f} + {1-confidence:.2f} × {market_price:.4f}[/]",
        f"[{C_TEXT}]  Effective probability:  {effective_prob:.4f}[/]",
        "",
        f"[{C_TEXT}]Step 2: Edge Calculation[/]",
        f"[{C_MUTED}]  Side: {side}[/]",
    ]

    if side == "BUY_YES":
        lines.append(f"[{C_ACCENT}]  edge = effective - market = {effective_prob:.4f} - {market_price:.4f}[/]")
    else:
        lines.append(f"[{C_ACCENT}]  edge = market - effective = {market_price:.4f} - {effective_prob:.4f}[/]")

    lines.extend([
        f"[{C_TEXT}]  Edge:                   {edge:.4f} ({edge:.1%})[/]",
        "",
        f"[{C_TEXT}]Step 3: Fee-Adjusted Odds[/]",
        f"[{C_MUTED}]  Cost per share:         ${cost:.4f}[/]",
        f"[{C_MUTED}]  Gross profit/share:     ${gross_profit:.4f}[/]",
        f"[{C_MUTED}]  Fee ({fee_rate:.0%}):               -${gross_profit * fee_rate:.4f}[/]",
        f"[{C_MUTED}]  Net profit/share:       ${net_profit:.4f}[/]",
        f"[{C_TEXT}]  Odds (b):               {odds:.4f}[/]",
        "",
        f"[{C_TEXT}]Step 4: Kelly Formula[/]",
        f"[{C_ACCENT}]  f* = (b×p - q) / b[/]",
    ])

    if side == "BUY_YES":
        p, q = effective_prob, 1 - effective_prob
    else:
        p, q = 1 - effective_prob, effective_prob

    lines.extend([
        f"[{C_MUTED}]  p = {p:.4f}, q = {q:.4f}, b = {odds:.4f}[/]",
        f"[{C_MUTED}]  b×p = {odds * p:.4f}, b×p - q = {odds * p - q:.4f}[/]",
        f"[{C_TEXT}]  Full Kelly (f*):        {full_kelly:.4f} ({full_kelly:.1%})[/]",
        f"[{C_TEXT}]  Fractional (×0.25):     {adjusted_kelly:.4f} ({adjusted_kelly:.1%})[/]",
        "",
        f"[{C_TEXT}]Step 5: Bet Sizing[/]",
        f"[{C_MUTED}]  Bankroll:               ${bankroll:,.2f}[/]",
        f"[{C_ACCENT}]  bet = bankroll × adj_kelly = ${bankroll:,.2f} × {adjusted_kelly:.4f}[/]",
    ])

    bet_color = C_GREEN if bet_size > 0 else C_RED
    lines.extend([
        f"[bold {bet_color}]  Bet Size:               ${bet_size:.2f}[/]",
        "",
    ])

    # Visual sizing bar
    if bankroll > 0 and bet_size > 0:
        pct = bet_size / bankroll
        bar = horizontal_bar(pct, 1.0, 30, bet_color, show_value=False)
        lines.append(f"[{C_MUTED}]  Bankroll usage:  [/]{bar}  [{C_TEXT}]{pct:.1%}[/]")

    # Edge bar
    if edge > 0:
        bar = horizontal_bar(edge, 0.5, 30, C_GREEN if edge > 0.10 else C_YELLOW, show_value=False)
        lines.append(f"[{C_MUTED}]  Edge:            [/]{bar}  [{C_TEXT}]{edge:.1%}[/]")

    lines.append(f"[bold {C_TEXT}]╚{'═' * 34}╝[/]")
    return "\n".join(lines)


def price_chart(
    prices: list[list[float]],
    target_price: float | None = None,
    width: int = 50,
) -> str:
    """Render a price sparkline with optional target line.

    Args:
        prices: List of [timestamp_ms, price] pairs.
        target_price: Optional target price to mark.
        width: Chart width in characters.
    """
    if not prices or len(prices) < 3:
        return f"[{C_DIM}]Insufficient price history[/]"

    price_values = [p[1] for p in prices if len(p) >= 2 and p[1] > 0]
    if not price_values:
        return f"[{C_DIM}]No valid prices[/]"

    first_price = price_values[0]
    last_price = price_values[-1]
    min_price = min(price_values)
    max_price = max(price_values)
    change_pct = ((last_price - first_price) / first_price * 100) if first_price > 0 else 0

    trend_color = C_GREEN if change_pct >= 0 else C_RED
    spark = sparkline(
        price_values, width,
        label_start=f"${first_price:,.0f}",
        label_end=f"${last_price:,.0f}",
        color=trend_color,
    )

    lines = [
        f"[{C_TEXT}]90-Day Price Chart:[/]",
        spark,
        f"[{C_DIM}]  Range: ${min_price:,.0f} – ${max_price:,.0f}  |  Change: {change_pct:+.1f}%[/]",
    ]

    if target_price is not None:
        distance = ((target_price - last_price) / last_price * 100) if last_price > 0 else 0
        target_color = C_RED if abs(distance) > 30 else C_YELLOW if abs(distance) > 10 else C_GREEN
        lines.append(f"[{target_color}]  Target: ${target_price:,.0f} ({distance:+.1f}% from current)[/]")

    return "\n".join(lines)


def signal_weights_table(
    signals: list[tuple[str, float | None, float, float, float]],
) -> str:
    """Render a detailed signal weights table.

    Args:
        signals: List of (source, probability, confidence, multiplier, effective_weight) tuples.
    """
    lines = [
        f"[{C_TEXT}]Signal Weights:[/]",
        f"[{C_DIM}]{'Source':<20} {'Prob':>6} {'Conf':>6} {'Mult':>5} {'Weight':>7} {'Contrib':>8}[/]",
        f"[{C_BG}]{'─' * 58}[/]",
    ]

    total_weight = sum(ew for _, _, _, _, ew in signals if ew > 0)

    for source, prob, conf, mult, ew in signals:
        short = source.replace("resolution_", "res_").replace("prediction_", "pred_")
        prob_str = f"{prob:.2%}" if prob is not None else "  ---"
        contrib = prob * ew if prob is not None and ew > 0 else 0
        pct_of_total = (ew / total_weight * 100) if total_weight > 0 else 0
        lines.append(
            f"[{C_MUTED}]{short:<20}[/] [{C_TEXT}]{prob_str:>6}[/] "
            f"[{C_MUTED}]{conf:>5.0%}[/] [{C_MUTED}]{mult:>4.1f}x[/] "
            f"[{C_TEXT}]{ew:>6.2f}[/] [{C_ACCENT}]{contrib:>7.4f}[/]"
        )

    lines.append(f"[{C_BG}]{'─' * 58}[/]")
    if total_weight > 0:
        lines.append(f"[{C_MUTED}]{'Total Weight:':<20}[/] [{C_TEXT}]{' ':>19}{total_weight:>6.2f}[/]")

    return "\n".join(lines)
