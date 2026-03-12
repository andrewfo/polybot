"""Bets tab — displays Kelly-sized trade decisions with rich visual breakdowns."""

from typing import Any

from rich.markup import escape as esc
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import DataTable, Label, Static
from rich.text import Text

from config.settings import POLYMARKET_FEE_RATE, TEST_BANKROLL
from signals.aggregator import AggregatedSignal, SIGNAL_WEIGHT_MULTIPLIERS
from tui.messages import BetUpdate
from tui.widgets.charts import (
    kelly_breakdown,
    probability_comparison,
    signal_weights_table,
    price_chart,
    vol_comparison,
    sparkline,
    C_TEXT,
    C_DIM,
    C_GREEN,
    C_RED,
    C_YELLOW,
    C_ACCENT,
    C_MUTED,
    C_BG,
)


class BetsPanel(Vertical):
    """Shows all Kelly criterion trade decisions with sizing, charts, and reasoning."""

    DEFAULT_CSS = """
    BetsPanel {
        height: 1fr;
        background: #0a1628;
    }
    BetsPanel .bets-header {
        height: 1;
        padding: 0 2;
        text-style: bold;
        color: #e0e8f0;
    }
    BetsPanel .bets-status {
        height: 1;
        padding: 0 2;
        color: #667788;
    }
    BetsPanel .bets-split {
        height: 1fr;
    }
    BetsPanel .bets-table-section {
        height: auto;
        max-height: 40%;
    }
    BetsPanel .bets-detail-section {
        height: 1fr;
        min-height: 60%;
        border-top: solid #2a3a5a;
    }
    BetsPanel .bets-detail-section .section-label {
        height: 1;
        padding: 0 2;
        text-style: bold;
        color: #e0e8f0;
    }
    BetsPanel .bets-detail-section VerticalScroll {
        height: 1fr;
        background: #0d1f3c;
        border: solid #2a3a5a;
    }
    BetsPanel .bets-detail-section Static {
        width: 1fr;
        color: #8899aa;
    }
    BetsPanel DataTable {
        height: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._bets_count = 0
        self._skips_count = 0
        self._decisions: list[tuple[Any, Any, dict | None]] = []  # (TradeDecision, AggregatedSignal|None, market_data)

    def compose(self) -> ComposeResult:
        yield Label("KELLY BET SIZING", classes="bets-header")
        yield Label("Waiting for aggregated signals...", id="bets-status", classes="bets-status")
        with Vertical(classes="bets-split"):
            with Vertical(classes="bets-table-section"):
                yield DataTable(id="bets-table")
            with Vertical(classes="bets-detail-section"):
                yield Label("SELECT A ROW FOR DETAILS ↑", classes="section-label")
                with VerticalScroll(id="bets-detail-scroll"):
                    yield Static(
                        f"[{C_DIM}]Click or arrow-key to a row above to see the "
                        f"full Kelly breakdown, probability comparison chart, "
                        f"and signal analysis.[/]",
                        id="bets-detail",
                        markup=True,
                    )

    def on_mount(self) -> None:
        table = self.query_one("#bets-table", DataTable)
        table.add_columns(
            "Market", "Side", "Edge", "Raw→Eff", "Bet $", "EV $", "Kelly%", "Conf", "Status",
        )
        table.cursor_type = "row"

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Show full details when a row is selected."""
        row_index = event.cursor_row
        if 0 <= row_index < len(self._decisions):
            d, agg, mkt = self._decisions[row_index]
            detail = self.query_one("#bets-detail", Static)
            content = self._build_full_detail(d, agg, mkt)
            detail.update(content)

    def on_bet_update(self, event: BetUpdate) -> None:
        """Handle a new Kelly trade decision."""
        d = event.decision
        agg = event.aggregation
        mkt = event.market_data
        self._decisions.append((d, agg, mkt))

        table = self.query_one("#bets-table", DataTable)
        status_label = self.query_one("#bets-status", Label)

        question_short = d.market_question[:40]
        if len(d.market_question) > 40:
            question_short += "..."

        # Raw→Effective column shows the confidence blending at a glance
        blend_str = f"{d.estimated_prob:.0%}→{d.effective_prob:.0%}"

        if d.should_trade:
            self._bets_count += 1
            table.add_row(
                question_short,
                d.side.replace("BUY_", ""),
                f"{d.edge:.1%}",
                blend_str,
                f"${d.bet_size_usd:.2f}",
                f"${d.expected_value:.2f}",
                f"{d.adjusted_fraction:.1%}",
                f"{d.confidence:.0%}",
                "TRADE",
            )
        else:
            self._skips_count += 1
            table.add_row(
                question_short,
                "---",
                f"{d.edge:.1%}",
                blend_str,
                "---",
                "---",
                "---",
                f"{d.confidence:.0%}",
                d.skip_reason[:15],
            )

        status_label.update(
            f"Bets: {self._bets_count} trades, {self._skips_count} skipped"
        )

        # Auto-show detail for the latest entry
        detail = self.query_one("#bets-detail", Static)
        content = self._build_full_detail(d, agg, mkt)
        detail.update(content)

    def _build_full_detail(
        self,
        d: Any,
        agg: AggregatedSignal | None,
        mkt: dict[str, Any] | None,
    ) -> str:
        """Build the full detail view with charts and math breakdown."""
        sections: list[str] = []

        # ── Header ──
        trade_color = C_GREEN if d.should_trade else C_RED
        status = "TRADE" if d.should_trade else f"SKIP — {esc(d.skip_reason)}"
        sections.append(
            f"[bold {trade_color}]{'═' * 60}[/]\n"
            f"[bold {C_TEXT}]{esc(d.market_question)}[/]\n"
            f"[{C_DIM}]ID: {esc(d.market_id)}  |  Token: {esc(d.token_id)}[/]\n"
            f"[bold {trade_color}]Decision: {status}[/]"
        )

        # ── Probability Comparison Chart ──
        signal_list = None
        if agg and agg.individual_signals:
            signal_list = [
                (s.source, s.probability, s.confidence)
                for s in agg.individual_signals
                if s.probability is not None
            ]

        sections.append("")
        sections.append(f"[bold {C_TEXT}]╔══ PROBABILITY COMPARISON ══╗[/]")
        sections.append(probability_comparison(
            market_price=d.market_price,
            raw_estimate=d.estimated_prob,
            effective_prob=d.effective_prob,
            signals=signal_list,
        ))

        # ── Signal Weights Table ──
        if agg and agg.individual_signals:
            sections.append("")
            signal_rows = []
            for s in agg.individual_signals:
                mult = SIGNAL_WEIGHT_MULTIPLIERS.get(s.source, 1.0)
                ew = s.confidence * mult
                signal_rows.append((s.source, s.probability, s.confidence, mult, ew))
            sections.append(signal_weights_table(signal_rows))

            # Preliminary vs final
            sections.append("")
            sections.append(
                f"[{C_MUTED}]Preliminary (weighted avg):  [{C_TEXT}]{agg.preliminary_probability:.4f}[/]"
            )
            sections.append(
                f"[{C_MUTED}]Frontier final:              [{C_TEXT}]{agg.final_probability:.4f}[/]"
            )
            sections.append(
                f"[{C_MUTED}]Signals agreement:           [{C_TEXT}]{agg.signals_agreement}[/]"
            )
            sections.append(
                f"[{C_MUTED}]Market efficiency:           [{C_TEXT}]{agg.market_efficiency}[/]"
            )

        # ── Price Chart (from resolution_crypto raw data) ──
        crypto_raw = self._get_crypto_raw_data(agg)
        if crypto_raw:
            sections.append("")
            sections.append(f"[bold {C_TEXT}]╔══ CRYPTO MARKET DATA ══╗[/]")

            current = crypto_raw.get("current_price")
            target = crypto_raw.get("target_price")
            direction = crypto_raw.get("direction", "above")
            coin_id = crypto_raw.get("coin_id", "?")

            if current and target:
                distance = crypto_raw.get("distance_pct", 0)
                sections.append(
                    f"[{C_MUTED}]Coin:[/] [{C_TEXT}]{coin_id}[/]  "
                    f"[{C_MUTED}]Current:[/] [{C_TEXT}]${current:,.2f}[/]  "
                    f"[{C_MUTED}]Target:[/] [{C_TEXT}]${target:,.2f}[/] ({direction}, {distance:+.1f}%)"
                )

            # Vol comparison chart
            hist_vol = crypto_raw.get("historical_vol", 0)
            ewm_vol = crypto_raw.get("ewm_vol", 0)
            st_vol = crypto_raw.get("short_term_vol", 0)
            deribit_iv = crypto_raw.get("deribit_iv")
            selected_vol = crypto_raw.get("annualized_vol", 0)
            vol_source = crypto_raw.get("vol_source", "unknown")

            if hist_vol > 0 or ewm_vol > 0:
                sections.append("")
                sections.append(vol_comparison(
                    historical=hist_vol,
                    ewm=ewm_vol,
                    short_term=st_vol,
                    deribit_iv=deribit_iv,
                    selected=selected_vol,
                    selected_source=vol_source,
                ))

            # Drift info
            drift = crypto_raw.get("shrunk_drift")
            raw_drift = crypto_raw.get("realized_drift")
            if drift is not None:
                drift_str = f"{drift:+.1%}/yr"
                if raw_drift is not None and abs(raw_drift - drift) > 0.01:
                    drift_str += f"  (raw: {raw_drift:+.1%}, shrunk)"
                sections.append(f"[{C_MUTED}]Drift:[/] [{C_TEXT}]{drift_str}[/]")

            # Resolution type + model probabilities
            res_type = crypto_raw.get("resolution_type", "barrier")
            terminal_p = crypto_raw.get("terminal_prob")
            barrier_p = crypto_raw.get("barrier_prob")
            if terminal_p is not None and barrier_p is not None:
                sections.append("")
                sections.append(f"[{C_MUTED}]Resolution type:[/] [{C_TEXT}]{res_type}[/]")

                from tui.widgets.charts import horizontal_bar
                t_bar = horizontal_bar(terminal_p, 1.0, 25, C_MUTED)
                b_bar = horizontal_bar(barrier_p, 1.0, 25, C_ACCENT)
                sections.append(f"[{C_MUTED}]Terminal (at expiry): [/]{t_bar}")
                sections.append(f"[{C_MUTED}]Barrier (any touch):  [/]{b_bar}")

                selected_label = "barrier" if res_type == "barrier" else "terminal"
                sections.append(
                    f"[{C_GREEN}]  → Using {selected_label} model[/]"
                )

            # Trend
            trend = crypto_raw.get("trend")
            if trend:
                sections.append(f"[{C_MUTED}]90d Trend:[/] [{C_TEXT}]{esc(str(trend))}[/]")

        # ── Kelly Math Breakdown ──
        sections.append("")
        sections.append(kelly_breakdown(
            estimated_prob=d.estimated_prob,
            effective_prob=d.effective_prob,
            market_price=d.market_price,
            confidence=d.confidence,
            edge=d.edge,
            full_kelly=d.full_kelly_fraction,
            adjusted_kelly=d.adjusted_fraction,
            bet_size=d.bet_size_usd,
            bankroll=TEST_BANKROLL,
            side=d.side,
            fee_rate=POLYMARKET_FEE_RATE,
        ))

        # ── Frontier Reasoning ──
        if agg:
            sections.append("")
            sections.append(f"[bold {C_TEXT}]╔══ FRONTIER MODEL REASONING ══╗[/]")
            sections.append(f"[{C_TEXT}]{esc(agg.reasoning)}[/]")

        sections.append(f"\n[{C_DIM}]{'─' * 60}[/]")

        return "\n".join(sections)

    def _get_crypto_raw_data(self, agg: AggregatedSignal | None) -> dict[str, Any] | None:
        """Extract raw crypto resolution data from the aggregation."""
        if not agg or not agg.individual_signals:
            return None
        for signal in agg.individual_signals:
            if signal.source == "resolution_crypto" and signal.raw_data:
                return signal.raw_data
        return None
