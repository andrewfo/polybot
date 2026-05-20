"""Continuous learning engine — mines historical data to improve bot performance.

Analyzes frontier decisions, skipped markets, trade outcomes, signal accuracy,
and confidence calibration to produce actionable insights and adaptive parameter
recommendations. Designed to run periodically (e.g., after each aggregation cycle)
so the bot continuously improves from its own paper/live trading history.

Learning loops:
1. Frontier decision bias analysis — systematic over/under-estimation
2. Skipped market retrospective — were our skips correct?
3. Confidence calibration — is confidence=0.8 actually 80% accurate?
4. Edge realization — predicted edge vs actual P&L
5. Signal provider accuracy by market features (vol regime, time-to-expiry)
6. LLM cost-effectiveness — ROI per frontier call
7. Adaptive parameter recommendations — Kelly fraction, edge threshold, etc.
"""

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core import db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes for learning report
# ---------------------------------------------------------------------------

@dataclass
class BiasReport:
    """Frontier model systematic bias analysis."""
    mean_bias: float = 0.0          # avg(estimated_prob - actual_outcome), >0 = overestimates YES
    abs_mean_error: float = 0.0     # avg|estimated_prob - actual_outcome|
    median_abs_error: float = 0.0
    sample_count: int = 0
    bias_by_confidence_band: dict[str, float] = field(default_factory=dict)  # "low/mid/high" -> bias
    bias_by_price_band: dict[str, float] = field(default_factory=dict)       # "0-0.3/0.3-0.7/0.7-1" -> bias
    calibration_curve: list[dict[str, float]] = field(default_factory=list)  # [{bin_center, predicted_mean, actual_mean, count}]
    bias_by_regime: dict[str, float] = field(default_factory=dict)  # regime -> mean bias


@dataclass
class SkipRetroReport:
    """Analysis of markets we skipped — would they have been profitable?"""
    total_skipped: int = 0
    resolved_skipped: int = 0
    would_have_been_correct: int = 0   # our estimate was on the right side
    would_have_profited: int = 0       # edge was real (estimate closer to outcome than market)
    by_skip_reason: dict[str, dict[str, Any]] = field(default_factory=dict)
    missed_profit_estimate: float = 0.0  # rough $ we left on table
    skip_by_regime: dict[str, dict[str, Any]] = field(default_factory=dict)  # regime -> skip stats


@dataclass
class EdgeRealizationReport:
    """Did predicted edge actually materialize in P&L?"""
    total_trades: int = 0
    avg_predicted_edge: float = 0.0
    avg_realized_return: float = 0.0
    edge_efficiency: float = 0.0     # realized / predicted (1.0 = perfect)
    win_rate: float = 0.0
    profit_factor: float = 0.0       # gross_wins / gross_losses
    avg_win: float = 0.0
    avg_loss: float = 0.0
    by_confidence_band: dict[str, dict[str, float]] = field(default_factory=dict)
    by_edge_band: dict[str, dict[str, float]] = field(default_factory=dict)
    edge_by_regime: dict[str, dict[str, float]] = field(default_factory=dict)  # regime -> edge stats


@dataclass
class SignalFeatureReport:
    """Signal accuracy broken down by market features."""
    by_vol_regime: dict[str, dict[str, float]] = field(default_factory=dict)
    by_time_to_expiry: dict[str, dict[str, float]] = field(default_factory=dict)
    by_resolution_type: dict[str, dict[str, float]] = field(default_factory=dict)
    by_source: dict[str, dict[str, float]] = field(default_factory=dict)


@dataclass
class CostEffectivenessReport:
    """LLM cost vs value generated."""
    total_llm_cost: float = 0.0
    frontier_cost: float = 0.0
    cheap_cost: float = 0.0
    sonar_cost: float = 0.0
    cost_per_trade: float = 0.0
    cost_per_profitable_trade: float = 0.0
    frontier_calls_per_trade: float = 0.0
    roi: float = 0.0  # total_pnl / total_llm_cost


@dataclass
class ParameterRecommendation:
    """Adaptive parameter suggestion based on historical data."""
    parameter: str
    current_value: float
    recommended_value: float
    reason: str
    confidence: float  # 0-1, how sure we are about this recommendation
    sample_count: int


@dataclass
class LearningReport:
    """Complete learning report combining all analyses."""
    timestamp: str = ""
    bias: BiasReport = field(default_factory=BiasReport)
    skip_retro: SkipRetroReport = field(default_factory=SkipRetroReport)
    edge_realization: EdgeRealizationReport = field(default_factory=EdgeRealizationReport)
    signal_features: SignalFeatureReport = field(default_factory=SignalFeatureReport)
    cost_effectiveness: CostEffectivenessReport = field(default_factory=CostEffectivenessReport)
    recommendations: list[ParameterRecommendation] = field(default_factory=list)
    data_sufficiency: dict[str, bool] = field(default_factory=dict)  # which analyses have enough data
    applied_overrides: list[str] = field(default_factory=list)  # parameters auto-applied this cycle
    reverted_overrides: list[str] = field(default_factory=list)  # parameters auto-reverted this cycle
    current_regime: str = ""  # market regime at time of report


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------

def analyze_frontier_bias() -> BiasReport:
    """Analyze systematic bias in frontier model probability estimates.

    Joins frontier_decisions with signal_calibration resolutions to see
    how our estimates compared to actual outcomes. Uses time-decay weighting
    (exp(-age/45)) so recent data dominates.
    """
    report = BiasReport()
    try:
        d = db.get_db()

        # Get frontier decisions for markets that have resolved (with resolution timestamp)
        rows = list(d.execute(
            """
            SELECT fd.estimated_prob, fd.effective_prob, fd.market_price, fd.confidence,
                   sc.actual_outcome, sc.resolved_at
            FROM frontier_decisions fd
            INNER JOIN (
                SELECT market_id, actual_outcome, MAX(resolved_at) as resolved_at
                FROM signal_calibration
                WHERE actual_outcome IS NOT NULL
                GROUP BY market_id
            ) sc ON fd.market_id = sc.market_id
            WHERE fd.should_trade = 1 OR fd.should_trade = 0
            """
        ).fetchall())

        if not rows:
            return report

        report.sample_count = len(rows)
        now = datetime.now(timezone.utc)

        # Weighted accumulators
        total_weight = 0.0
        weighted_bias_sum = 0.0
        weighted_abs_error_sum = 0.0
        abs_errors: list[float] = []

        # Bands for grouping (store (bias, weight) tuples)
        conf_bands: dict[str, list[tuple[float, float]]] = {"low": [], "mid": [], "high": []}
        price_bands: dict[str, list[tuple[float, float]]] = {"0-0.3": [], "0.3-0.7": [], "0.7-1.0": []}
        regime_bands: dict[str, list[tuple[float, float]]] = {}

        # Calibration curve bins (10 bins) — store (predicted, actual, weight)
        cal_bins: dict[int, list[tuple[float, float, float]]] = {i: [] for i in range(10)}

        for row in rows:
            est_prob = float(row[0])
            eff_prob = float(row[1])
            mkt_price = float(row[2])
            conf = float(row[3])
            actual = float(row[4])
            resolved_at_str = row[5]

            # Compute time-decay weight
            age_days = 0.0
            if resolved_at_str:
                try:
                    resolved_dt = datetime.fromisoformat(resolved_at_str)
                    if resolved_dt.tzinfo is None:
                        resolved_dt = resolved_dt.replace(tzinfo=timezone.utc)
                    age_days = (now - resolved_dt).total_seconds() / 86400
                except (ValueError, TypeError):
                    pass
            weight = math.exp(-age_days / 45.0)

            bias = est_prob - actual
            total_weight += weight
            weighted_bias_sum += bias * weight
            weighted_abs_error_sum += abs(bias) * weight
            abs_errors.append(abs(bias))

            # Confidence bands
            if conf < 0.4:
                conf_bands["low"].append((bias, weight))
            elif conf < 0.7:
                conf_bands["mid"].append((bias, weight))
            else:
                conf_bands["high"].append((bias, weight))

            # Price bands
            if mkt_price < 0.3:
                price_bands["0-0.3"].append((bias, weight))
            elif mkt_price < 0.7:
                price_bands["0.3-0.7"].append((bias, weight))
            else:
                price_bands["0.7-1.0"].append((bias, weight))

            # Regime dimension
            regime = _lookup_regime_for_date(resolved_at_str)
            if regime:
                regime_bands.setdefault(regime, []).append((bias, weight))

            # Calibration curve
            bin_idx = min(9, int(est_prob * 10))
            cal_bins[bin_idx].append((est_prob, actual, weight))

        if total_weight > 0:
            report.mean_bias = weighted_bias_sum / total_weight
            report.abs_mean_error = weighted_abs_error_sum / total_weight
        sorted_errors = sorted(abs_errors)
        report.median_abs_error = sorted_errors[len(sorted_errors) // 2]

        def _weighted_avg(pairs: list[tuple[float, float]]) -> float:
            w_sum = sum(w for _, w in pairs)
            if w_sum == 0:
                return 0.0
            return sum(v * w for v, w in pairs) / w_sum

        for band, vals in conf_bands.items():
            if vals:
                report.bias_by_confidence_band[band] = round(_weighted_avg(vals), 4)

        for band, vals in price_bands.items():
            if vals:
                report.bias_by_price_band[band] = round(_weighted_avg(vals), 4)

        if regime_bands:
            report.bias_by_regime = {
                regime: round(_weighted_avg(vals), 4)
                for regime, vals in regime_bands.items() if vals
            }

        for bin_idx, triples in cal_bins.items():
            if triples:
                bin_center = (bin_idx + 0.5) / 10
                w_sum = sum(w for _, _, w in triples)
                pred_mean = sum(p * w for p, _, w in triples) / w_sum if w_sum > 0 else 0
                actual_mean = sum(a * w for _, a, w in triples) / w_sum if w_sum > 0 else 0
                report.calibration_curve.append({
                    "bin_center": round(bin_center, 2),
                    "predicted_mean": round(pred_mean, 3),
                    "actual_mean": round(actual_mean, 3),
                    "count": len(triples),
                })

    except Exception as e:
        logger.warning("Frontier bias analysis failed: %s", e)

    return report


def analyze_skipped_markets() -> SkipRetroReport:
    """Retrospective analysis of markets we chose not to trade.

    For resolved skipped markets, checks whether our estimate was actually
    closer to the outcome than the market price — i.e., did we have real edge
    that we threw away? Uses time-decay weighting (exp(-age/45)).
    """
    report = SkipRetroReport()
    try:
        d = db.get_db()
        rows = list(d.execute(
            "SELECT market_id, skip_reason, market_price_at_skip, "
            "estimated_prob, confidence, resolution_outcome, timestamp "
            "FROM skipped_markets"
        ).fetchall())

        if not rows:
            return report

        now = datetime.now(timezone.utc)
        report.total_skipped = len(rows)
        reason_stats: dict[str, dict[str, Any]] = {}
        regime_stats: dict[str, dict[str, Any]] = {}

        for row in rows:
            reason = row[1] or "unknown"
            mkt_price = float(row[2]) if row[2] else 0.5
            # Distinguish "no estimate produced" (None) from a real 0.5 estimate.
            # Skips that fire before the frontier (e.g. "no usable signals") have
            # est_prob = None — counting them as 0.5 makes them silently always
            # appear neither correct nor profitable.
            has_estimate = row[3] is not None
            est_prob = float(row[3]) if has_estimate else 0.5
            outcome = row[5]
            ts_str = row[6]

            # Compute time-decay weight
            age_days = 0.0
            if ts_str:
                try:
                    ts_dt = datetime.fromisoformat(ts_str)
                    if ts_dt.tzinfo is None:
                        ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                    age_days = (now - ts_dt).total_seconds() / 86400
                except (ValueError, TypeError):
                    pass
            weight = math.exp(-age_days / 45.0)

            if reason not in reason_stats:
                reason_stats[reason] = {
                    "total": 0, "resolved": 0, "correct": 0,
                    "profited": 0, "missed_edge_sum": 0.0,
                    "weighted_total": 0.0, "weighted_profited": 0.0,
                    "unevaluable": 0,
                }
            reason_stats[reason]["total"] += 1
            reason_stats[reason]["weighted_total"] += weight

            if outcome is not None:
                actual = float(outcome)
                report.resolved_skipped += 1
                reason_stats[reason]["resolved"] += 1

                # Pre-frontier skips have no estimate to evaluate against.
                if not has_estimate:
                    reason_stats[reason]["unevaluable"] += 1
                    continue

                # Would our estimate have been on the correct side?
                est_side_correct = (est_prob > 0.5 and actual == 1.0) or (est_prob < 0.5 and actual == 0.0)
                if est_side_correct:
                    report.would_have_been_correct += 1
                    reason_stats[reason]["correct"] += 1

                # Would we have profited? (our estimate closer to outcome than market)
                our_error = abs(est_prob - actual)
                mkt_error = abs(mkt_price - actual)
                if our_error < mkt_error:
                    report.would_have_profited += 1
                    reason_stats[reason]["profited"] += 1
                    reason_stats[reason]["weighted_profited"] += weight
                    # Rough profit estimate: edge × hypothetical $10 bet (weighted)
                    edge = mkt_error - our_error
                    reason_stats[reason]["missed_edge_sum"] += edge * 10 * weight
                    report.missed_profit_estimate += edge * 10 * weight

                # Regime dimension
                regime = _lookup_regime_for_date(ts_str)
                if regime:
                    if regime not in regime_stats:
                        regime_stats[regime] = {"total": 0, "profited": 0}
                    regime_stats[regime]["total"] += 1
                    if our_error < mkt_error:
                        regime_stats[regime]["profited"] += 1

        report.by_skip_reason = reason_stats
        if regime_stats:
            report.skip_by_regime = regime_stats

    except Exception as e:
        logger.warning("Skipped market analysis failed: %s", e)

    return report


def analyze_edge_realization() -> EdgeRealizationReport:
    """Compare predicted edge to actual realized P&L on closed trades.

    Joins frontier_decisions (predicted edge) with positions (realized P&L)
    to see if our edge estimates are accurate. Uses the dedicated realized_pnl
    column (not unrealized_pnl) to avoid corruption from post-close price updates.
    """
    report = EdgeRealizationReport()
    try:
        d = db.get_db()

        # Get closed positions with their frontier decision data
        # Use realized_pnl column (falls back to unrealized_pnl for pre-migration data)
        rows = list(d.execute(
            """
            SELECT fd.edge, fd.confidence, fd.estimated_prob, fd.market_price,
                   fd.bet_size_usd,
                   COALESCE(p.realized_pnl, p.unrealized_pnl) as rpnl,
                   p.avg_entry, p.size, fd.timestamp
            FROM frontier_decisions fd
            INNER JOIN positions p ON fd.market_id = p.market_id
            WHERE fd.should_trade = 1 AND p.status = 'closed'
                  AND COALESCE(p.realized_pnl, p.unrealized_pnl) IS NOT NULL
            """
        ).fetchall())

        if not rows:
            return report

        report.total_trades = len(rows)
        predicted_edges: list[float] = []
        realized_returns: list[float] = []
        wins = 0
        gross_wins = 0.0
        gross_losses = 0.0

        conf_perf: dict[str, list[float]] = {"low": [], "mid": [], "high": []}
        edge_perf: dict[str, list[float]] = {"small": [], "medium": [], "large": []}
        regime_perf: dict[str, list[float]] = {}

        for row in rows:
            edge = float(row[0])
            conf = float(row[1])
            bet_size = float(row[4])
            realized_pnl = float(row[5])
            entry = float(row[6])
            size = float(row[7])
            fd_timestamp = row[8]

            predicted_edges.append(edge)
            ret = realized_pnl / bet_size if bet_size > 0 else 0
            realized_returns.append(ret)

            if realized_pnl > 0:
                wins += 1
                gross_wins += realized_pnl
            else:
                gross_losses += abs(realized_pnl)

            # Confidence bands
            if conf < 0.4:
                conf_perf["low"].append(ret)
            elif conf < 0.7:
                conf_perf["mid"].append(ret)
            else:
                conf_perf["high"].append(ret)

            # Edge bands
            if edge < 0.05:
                edge_perf["small"].append(ret)
            elif edge < 0.10:
                edge_perf["medium"].append(ret)
            else:
                edge_perf["large"].append(ret)

            # Regime dimension
            regime = _lookup_regime_for_date(fd_timestamp)
            if regime:
                regime_perf.setdefault(regime, []).append(ret)

        report.avg_predicted_edge = sum(predicted_edges) / len(predicted_edges)
        report.avg_realized_return = sum(realized_returns) / len(realized_returns)
        report.edge_efficiency = (
            report.avg_realized_return / report.avg_predicted_edge
            if report.avg_predicted_edge > 0 else 0
        )
        report.win_rate = wins / len(rows) if rows else 0
        report.profit_factor = gross_wins / gross_losses if gross_losses > 0 else float('inf')
        report.avg_win = gross_wins / wins if wins > 0 else 0
        report.avg_loss = gross_losses / (len(rows) - wins) if (len(rows) - wins) > 0 else 0

        for band, returns in conf_perf.items():
            if returns:
                report.by_confidence_band[band] = {
                    "avg_return": sum(returns) / len(returns),
                    "win_rate": sum(1 for r in returns if r > 0) / len(returns),
                    "count": len(returns),
                }

        for band, returns in edge_perf.items():
            if returns:
                report.by_edge_band[band] = {
                    "avg_return": sum(returns) / len(returns),
                    "win_rate": sum(1 for r in returns if r > 0) / len(returns),
                    "count": len(returns),
                }

        if regime_perf:
            report.edge_by_regime = {
                regime: {
                    "avg_return": sum(returns) / len(returns),
                    "win_rate": sum(1 for r in returns if r > 0) / len(returns),
                    "count": len(returns),
                }
                for regime, returns in regime_perf.items() if returns
            }

    except Exception as e:
        logger.warning("Edge realization analysis failed: %s", e)

    return report


def analyze_signal_features() -> SignalFeatureReport:
    """Break down signal accuracy by market features.

    Mines the raw_data JSON stored in the signals table to correlate
    accuracy with volatility regime, time-to-expiry, resolution type, etc.
    """
    report = SignalFeatureReport()
    try:
        d = db.get_db()

        # Get signals that have corresponding resolved calibration data
        rows = list(d.execute(
            """
            SELECT s.signal_source, s.probability, s.raw_data, sc.actual_outcome
            FROM signals s
            INNER JOIN (
                SELECT market_id, actual_outcome
                FROM signal_calibration
                WHERE actual_outcome IS NOT NULL
                GROUP BY market_id
            ) sc ON s.market_id = sc.market_id
            WHERE s.probability IS NOT NULL
            """
        ).fetchall())

        if not rows:
            return report

        # Group by source
        source_errors: dict[str, list[float]] = {}
        vol_errors: dict[str, list[float]] = {}
        time_errors: dict[str, list[float]] = {}
        rtype_errors: dict[str, list[float]] = {}

        for row in rows:
            source = row[0]
            prob = float(row[1])
            raw_str = row[2]
            actual = float(row[3])
            error = (prob - actual) ** 2  # Brier score

            source_errors.setdefault(source, []).append(error)

            # Parse raw_data for features
            if raw_str:
                try:
                    raw = json.loads(raw_str)
                except (json.JSONDecodeError, TypeError):
                    raw = {}

                # Vol regime (from resolution_crypto)
                vol_regime = raw.get("vol_regime")
                if vol_regime:
                    vol_errors.setdefault(vol_regime, []).append(error)

                # Days remaining
                days_rem = raw.get("days_remaining")
                if days_rem is not None:
                    try:
                        days = float(days_rem)
                        if days < 7:
                            bucket = "<7d"
                        elif days < 14:
                            bucket = "7-14d"
                        elif days < 21:
                            bucket = "14-21d"
                        else:
                            bucket = "21d+"
                        time_errors.setdefault(bucket, []).append(error)
                    except (ValueError, TypeError):
                        pass

                # Resolution type
                res_type = raw.get("resolution_type")
                if res_type:
                    rtype_errors.setdefault(res_type, []).append(error)

        def _summarize(errors: dict[str, list[float]]) -> dict[str, dict[str, float]]:
            result: dict[str, dict[str, float]] = {}
            for key, errs in errors.items():
                if errs:
                    result[key] = {
                        "brier_score": round(sum(errs) / len(errs), 4),
                        "count": len(errs),
                        "best_case": round(min(errs), 4),
                        "worst_case": round(max(errs), 4),
                    }
            return result

        report.by_source = _summarize(source_errors)
        report.by_vol_regime = _summarize(vol_errors)
        report.by_time_to_expiry = _summarize(time_errors)
        report.by_resolution_type = _summarize(rtype_errors)

    except Exception as e:
        logger.warning("Signal feature analysis failed: %s", e)

    return report


def analyze_cost_effectiveness() -> CostEffectivenessReport:
    """Analyze LLM cost relative to trading performance.

    Computes ROI metrics: cost per trade, cost per profitable trade,
    and overall LLM spend vs P&L.
    """
    report = CostEffectivenessReport()
    try:
        d = db.get_db()

        # Total LLM costs by model category
        cost_rows = list(d.execute(
            "SELECT model, SUM(cost_usd) FROM llm_costs GROUP BY model"
        ).fetchall())

        for row in cost_rows:
            model = row[0] or ""
            cost = float(row[1])
            report.total_llm_cost += cost

            if "claude" in model.lower() or "opus" in model.lower():
                report.frontier_cost += cost
            elif "sonar" in model.lower() or "perplexity" in model.lower():
                report.sonar_cost += cost
            else:
                report.cheap_cost += cost

        # Trade counts
        trade_count_rows = list(d.execute(
            "SELECT COUNT(*) FROM positions WHERE status = 'closed'"
        ).fetchall())
        total_trades = int(trade_count_rows[0][0]) if trade_count_rows else 0

        profitable_rows = list(d.execute(
            "SELECT COUNT(*) FROM positions WHERE status = 'closed' AND unrealized_pnl > 0"
        ).fetchall())
        profitable_trades = int(profitable_rows[0][0]) if profitable_rows else 0

        # Total realized P&L
        total_pnl = db.get_total_pnl()

        # Frontier call count
        frontier_calls = list(d.execute(
            "SELECT COUNT(*) FROM frontier_decisions"
        ).fetchall())
        frontier_count = int(frontier_calls[0][0]) if frontier_calls else 0

        if total_trades > 0:
            report.cost_per_trade = report.total_llm_cost / total_trades
            report.frontier_calls_per_trade = frontier_count / total_trades
        if profitable_trades > 0:
            report.cost_per_profitable_trade = report.total_llm_cost / profitable_trades
        if report.total_llm_cost > 0:
            report.roi = total_pnl / report.total_llm_cost

    except Exception as e:
        logger.warning("Cost effectiveness analysis failed: %s", e)

    return report


def compute_parameter_recommendations(
    bias: BiasReport,
    skip_retro: SkipRetroReport,
    edge_real: EdgeRealizationReport,
    signal_features: SignalFeatureReport,
) -> list[ParameterRecommendation]:
    """Generate adaptive parameter recommendations based on learning data.

    These are SUGGESTIONS that get logged and surfaced in the UI.
    Actual parameter changes require human review or explicit auto-apply.
    """
    from config.settings import (
        KELLY_FRACTION as _DEF_KELLY_FRACTION,
        MIN_EDGE_THRESHOLD as _DEF_MIN_EDGE_THRESHOLD,
        MIN_CONFIDENCE_BLEND as _DEF_MIN_CONFIDENCE_BLEND,
        TAKE_PROFIT_PCT as _DEF_TAKE_PROFIT_PCT,
        STOP_LOSS_PCT as _DEF_STOP_LOSS_PCT,
        get_effective_param,
    )

    # Use the effective (override-aware) value as the "current" baseline so we
    # don't recommend a value that's already been applied via an active override.
    KELLY_FRACTION = get_effective_param("KELLY_FRACTION", _DEF_KELLY_FRACTION)
    MIN_EDGE_THRESHOLD = get_effective_param("MIN_EDGE_THRESHOLD", _DEF_MIN_EDGE_THRESHOLD)
    MIN_CONFIDENCE_BLEND = get_effective_param("MIN_CONFIDENCE_BLEND", _DEF_MIN_CONFIDENCE_BLEND)
    TAKE_PROFIT_PCT = get_effective_param("TAKE_PROFIT_PCT", _DEF_TAKE_PROFIT_PCT)
    STOP_LOSS_PCT = get_effective_param("STOP_LOSS_PCT", _DEF_STOP_LOSS_PCT)

    recs: list[ParameterRecommendation] = []

    # --- Kelly fraction adjustment ---
    if edge_real.total_trades >= 10:
        if edge_real.win_rate > 0.6 and edge_real.edge_efficiency > 0.8:
            # We're winning consistently with good edge realization — can afford more Kelly
            new_kelly = min(0.40, KELLY_FRACTION * 1.25)
            if new_kelly != KELLY_FRACTION:
                recs.append(ParameterRecommendation(
                    parameter="KELLY_FRACTION",
                    current_value=KELLY_FRACTION,
                    recommended_value=round(new_kelly, 3),
                    reason=f"Win rate {edge_real.win_rate:.0%} with {edge_real.edge_efficiency:.0%} edge efficiency suggests room to increase Kelly",
                    confidence=min(0.8, edge_real.total_trades / 50),
                    sample_count=edge_real.total_trades,
                ))
        elif edge_real.win_rate < 0.4 or edge_real.edge_efficiency < 0.3:
            # We're losing — reduce Kelly
            new_kelly = max(0.10, KELLY_FRACTION * 0.75)
            if new_kelly != KELLY_FRACTION:
                recs.append(ParameterRecommendation(
                    parameter="KELLY_FRACTION",
                    current_value=KELLY_FRACTION,
                    recommended_value=round(new_kelly, 3),
                    reason=f"Win rate {edge_real.win_rate:.0%} with {edge_real.edge_efficiency:.0%} edge efficiency — reduce exposure",
                    confidence=min(0.8, edge_real.total_trades / 30),
                    sample_count=edge_real.total_trades,
                ))

    # --- Edge threshold adjustment ---
    if edge_real.total_trades >= 10:
        # Check if small-edge trades are unprofitable
        small_edge = edge_real.by_edge_band.get("small", {})
        if small_edge and small_edge.get("count", 0) >= 5:
            if small_edge.get("avg_return", 0) < 0:
                # Small-edge trades are losing money — raise threshold
                recs.append(ParameterRecommendation(
                    parameter="MIN_EDGE_THRESHOLD",
                    current_value=MIN_EDGE_THRESHOLD,
                    recommended_value=0.04,
                    reason=f"Small-edge trades (<5%) avg return {small_edge['avg_return']:.1%} — raise threshold to filter them out",
                    confidence=min(0.7, small_edge["count"] / 20),
                    sample_count=int(small_edge["count"]),
                ))
            elif small_edge.get("avg_return", 0) > 0.05:
                # Small-edge trades are profitable — could lower threshold
                new_thresh = max(0.01, MIN_EDGE_THRESHOLD * 0.75)
                if new_thresh != MIN_EDGE_THRESHOLD:
                    recs.append(ParameterRecommendation(
                        parameter="MIN_EDGE_THRESHOLD",
                        current_value=MIN_EDGE_THRESHOLD,
                        recommended_value=round(new_thresh, 3),
                        reason=f"Small-edge trades avg return {small_edge['avg_return']:.1%} — could capture more of these",
                        confidence=min(0.6, small_edge["count"] / 20),
                        sample_count=int(small_edge["count"]),
                    ))

    # --- Confidence blend adjustment ---
    if bias.sample_count >= 15:
        # Check if we systematically overestimate
        if bias.mean_bias > 0.05:
            # We overestimate YES probability — increase blending toward market
            new_blend = max(0.10, MIN_CONFIDENCE_BLEND - 0.03)
            recs.append(ParameterRecommendation(
                parameter="MIN_CONFIDENCE_BLEND",
                current_value=MIN_CONFIDENCE_BLEND,
                recommended_value=new_blend,
                reason=f"Systematic YES overestimation bias of {bias.mean_bias:.3f} — blend more toward market",
                confidence=min(0.7, bias.sample_count / 50),
                sample_count=bias.sample_count,
            ))
        elif bias.mean_bias < -0.05:
            # We underestimate — trust our model more
            new_blend = min(0.25, MIN_CONFIDENCE_BLEND + 0.03)
            recs.append(ParameterRecommendation(
                parameter="MIN_CONFIDENCE_BLEND",
                current_value=MIN_CONFIDENCE_BLEND,
                recommended_value=new_blend,
                reason=f"Systematic underestimation bias of {bias.mean_bias:.3f} — trust model estimates more",
                confidence=min(0.7, bias.sample_count / 50),
                sample_count=bias.sample_count,
            ))

    # --- Take-profit / stop-loss tuning ---
    if edge_real.total_trades >= 15:
        if edge_real.avg_win > 0 and edge_real.avg_loss > 0:
            win_loss_ratio = edge_real.avg_win / edge_real.avg_loss
            if win_loss_ratio < 1.0 and edge_real.win_rate > 0.5:
                # Winning often but wins are too small — widen take-profit
                new_tp = min(0.40, TAKE_PROFIT_PCT * 1.25)
                recs.append(ParameterRecommendation(
                    parameter="TAKE_PROFIT_PCT",
                    current_value=TAKE_PROFIT_PCT,
                    recommended_value=round(new_tp, 3),
                    reason=f"Win/loss ratio {win_loss_ratio:.2f} — letting winners run longer could improve total return",
                    confidence=0.5,
                    sample_count=edge_real.total_trades,
                ))
            if edge_real.avg_loss > edge_real.avg_win * 2:
                # Losses are much bigger than wins — tighten stop-loss
                new_sl = max(0.08, STOP_LOSS_PCT * 0.80)
                recs.append(ParameterRecommendation(
                    parameter="STOP_LOSS_PCT",
                    current_value=STOP_LOSS_PCT,
                    recommended_value=round(new_sl, 3),
                    reason=f"Avg loss (${edge_real.avg_loss:.2f}) >> avg win (${edge_real.avg_win:.2f}) — tighten stop-loss",
                    confidence=0.5,
                    sample_count=edge_real.total_trades,
                ))

    # --- Skipped market insights ---
    if skip_retro.resolved_skipped >= 10:
        skip_accuracy = 1.0 - (skip_retro.would_have_profited / skip_retro.resolved_skipped)
        if skip_accuracy < 0.5:
            # We're skipping more markets we should have traded than ones we shouldn't
            # Look at which skip reasons are worst
            worst_reason = ""
            worst_rate = 1.0
            for reason, stats in skip_retro.by_skip_reason.items():
                if stats.get("resolved", 0) >= 3:
                    skip_correctness = 1.0 - (stats.get("profited", 0) / stats["resolved"])
                    if skip_correctness < worst_rate:
                        worst_rate = skip_correctness
                        worst_reason = reason

            if worst_reason:
                recs.append(ParameterRecommendation(
                    parameter=f"SKIP_FILTER:{worst_reason}",
                    current_value=0,
                    recommended_value=0,
                    reason=f"Skip reason '{worst_reason}' is wrong {(1-worst_rate):.0%} of the time — consider relaxing this filter",
                    confidence=min(0.6, skip_retro.resolved_skipped / 30),
                    sample_count=skip_retro.resolved_skipped,
                ))

    # Drop no-op recommendations where recommended == current effective value
    # (e.g. when an active override already sits at the recommended value).
    filtered: list[ParameterRecommendation] = []
    for rec in recs:
        if rec.parameter.startswith("SKIP_FILTER:"):
            filtered.append(rec)
            continue
        if abs(rec.recommended_value - rec.current_value) < 1e-6:
            logger.debug(
                "Skip no-op recommendation: %s already at %.4f (active override)",
                rec.parameter, rec.current_value,
            )
            continue
        filtered.append(rec)
    return filtered


# ---------------------------------------------------------------------------
# Market regime classification (Improvement 5)
# ---------------------------------------------------------------------------

def _lookup_regime_for_date(timestamp_str: str | None) -> str:
    """Look up the cached market regime for a given timestamp's date.

    Returns the regime string or "" if not available.
    """
    if not timestamp_str:
        return ""
    try:
        dt = datetime.fromisoformat(timestamp_str)
        date_key = dt.strftime("%Y-%m-%d")
        d = db.get_db()
        if "market_regimes" not in d.table_names():
            return ""
        try:
            row = d["market_regimes"].get(date_key)
            return row["regime"] if row else ""
        except Exception:
            return ""
    except (ValueError, TypeError):
        return ""


async def classify_and_store_regime() -> str:
    """Classify current crypto market regime using BTC 30-day price data.

    Fetches BTC price history from CoinGecko, computes 30-day return and
    annualized volatility, classifies into regime, and caches in DB.

    Returns: "trending_up", "trending_down", "sideways", or "high_vol"
    """
    import aiohttp

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Check cache first
    d = db.get_db()
    try:
        existing = d["market_regimes"].get(today)
        if existing:
            return existing["regime"]
    except Exception:
        pass

    # Fetch 31 days of BTC data from CoinGecko
    regime = "sideways"
    btc_return = 0.0
    btc_vol = 0.0

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",
                params={"vs_currency": "usd", "days": "30", "interval": "daily"},
            ) as resp:
                if resp.status != 200:
                    logger.debug("CoinGecko regime fetch failed: HTTP %d", resp.status)
                    return regime
                data = await resp.json()

        prices = data.get("prices", [])
        if len(prices) < 2:
            return regime

        # Extract daily closing prices
        daily_prices = [p[1] for p in prices]
        first_price = daily_prices[0]
        last_price = daily_prices[-1]

        # 30-day return
        btc_return = (last_price - first_price) / first_price if first_price > 0 else 0

        # Annualized volatility from daily log returns
        log_returns = []
        for i in range(1, len(daily_prices)):
            if daily_prices[i] > 0 and daily_prices[i - 1] > 0:
                log_returns.append(math.log(daily_prices[i] / daily_prices[i - 1]))

        if log_returns:
            mean_lr = sum(log_returns) / len(log_returns)
            variance = sum((lr - mean_lr) ** 2 for lr in log_returns) / max(1, len(log_returns) - 1)
            daily_vol = math.sqrt(variance)
            btc_vol = daily_vol * math.sqrt(365)

        # Classify
        if btc_vol > 0.80:
            regime = "high_vol"
        elif btc_return > 0.15:
            regime = "trending_up"
        elif btc_return < -0.15:
            regime = "trending_down"
        else:
            regime = "sideways"

    except Exception as e:
        logger.debug("Regime classification failed: %s", e)
        return regime

    # Cache in DB
    try:
        d["market_regimes"].upsert({
            "date": today,
            "regime": regime,
            "btc_30d_return": round(btc_return, 4),
            "btc_30d_vol": round(btc_vol, 4),
        }, pk="date")
        logger.info("Market regime classified: %s (BTC 30d return=%.1f%%, vol=%.0f%%)",
                     regime, btc_return * 100, btc_vol * 100)
    except Exception as e:
        logger.debug("Failed to cache regime: %s", e)

    return regime


# ---------------------------------------------------------------------------
# Auto-apply recommendations (Improvement 1E)
# ---------------------------------------------------------------------------

AUTO_APPLY_MIN_CONFIDENCE = 0.7
AUTO_APPLY_MIN_SAMPLES = 30
MAX_CHANGE_PER_CYCLE_PCT = 0.10  # max 10% change per cycle

# Hard floor/ceiling per parameter
PARAMETER_LIMITS: dict[str, tuple[float, float]] = {
    "KELLY_FRACTION": (0.05, 0.50),
    "MIN_EDGE_THRESHOLD": (0.01, 0.08),
    "MIN_CONFIDENCE_BLEND": (0.05, 0.30),
    "TAKE_PROFIT_PCT": (0.05, 0.40),
    "STOP_LOSS_PCT": (0.05, 0.25),
}


def apply_recommendations(
    recs: list[ParameterRecommendation],
    report_timestamp: str = "",
) -> list[str]:
    """Auto-apply recommendations that meet confidence/sample thresholds.

    Guardrails:
    - confidence >= AUTO_APPLY_MIN_CONFIDENCE
    - sample_count >= AUTO_APPLY_MIN_SAMPLES
    - change magnitude <= MAX_CHANGE_PER_CYCLE_PCT of current value
    - Hard floor/ceiling per parameter
    - Skip SKIP_FILTER:* recommendations (informational only)

    Returns list of applied parameter names.
    """
    from config.settings import get_effective_param

    applied: list[str] = []
    d = db.get_db()
    now = datetime.now(timezone.utc).isoformat()

    for rec in recs:
        # Skip informational-only recommendations
        if rec.parameter.startswith("SKIP_FILTER:"):
            continue

        # Skip if not a tunable parameter we know about
        if rec.parameter not in PARAMETER_LIMITS:
            continue

        # Confidence and sample checks
        if rec.confidence < AUTO_APPLY_MIN_CONFIDENCE:
            logger.debug("Skip auto-apply %s: confidence %.2f < %.2f",
                         rec.parameter, rec.confidence, AUTO_APPLY_MIN_CONFIDENCE)
            continue
        if rec.sample_count < AUTO_APPLY_MIN_SAMPLES:
            logger.debug("Skip auto-apply %s: samples %d < %d",
                         rec.parameter, rec.sample_count, AUTO_APPLY_MIN_SAMPLES)
            continue

        # Get current effective value
        current = get_effective_param(rec.parameter, rec.current_value)
        new_val = rec.recommended_value

        # Clamp to hard limits
        floor, ceiling = PARAMETER_LIMITS[rec.parameter]
        new_val = max(floor, min(ceiling, new_val))

        # Max change per cycle
        if current > 0:
            change_pct = abs(new_val - current) / current
            if change_pct > MAX_CHANGE_PER_CYCLE_PCT:
                # Clamp to max change
                direction = 1.0 if new_val > current else -1.0
                new_val = current * (1.0 + direction * MAX_CHANGE_PER_CYCLE_PCT)
                new_val = max(floor, min(ceiling, new_val))

        # Skip if effectively no change
        if abs(new_val - current) < 1e-6:
            continue

        new_val = round(new_val, 4)

        # Deactivate previous override for this parameter
        try:
            existing = d["parameter_overrides"].get(rec.parameter)
            if existing and existing["active"] == 1:
                d["parameter_overrides"].update(rec.parameter, {"active": 0})
        except Exception:
            pass

        # Upsert new override
        d["parameter_overrides"].upsert({
            "parameter": rec.parameter,
            "original_value": rec.current_value,
            "current_value": new_val,
            "applied_at": now,
            "source_report_ts": report_timestamp,
            "confidence": rec.confidence,
            "sample_count": rec.sample_count,
            "reason": rec.reason[:500],
            "active": 1,
        }, pk="parameter")

        # Record snapshot for impact tracking
        _record_change_snapshot(rec.parameter, current, new_val, now)

        logger.info(
            "AUTO-APPLIED: %s %.4f → %.4f (conf=%.2f, n=%d) — %s",
            rec.parameter, current, new_val, rec.confidence, rec.sample_count, rec.reason[:80],
        )
        applied.append(rec.parameter)

    return applied


def revert_override(parameter: str) -> bool:
    """Deactivate the override for a parameter. Returns True if found."""
    d = db.get_db()
    try:
        row = d["parameter_overrides"].get(parameter)
        if row and row["active"] == 1:
            d["parameter_overrides"].update(parameter, {"active": 0})
            logger.info("Reverted parameter override: %s", parameter)
            return True
        return False
    except Exception as e:
        logger.warning("Failed to revert override %s: %s", parameter, e)
        return False


# ---------------------------------------------------------------------------
# Parameter impact assessment (Improvement 2)
# ---------------------------------------------------------------------------

def _record_change_snapshot(
    parameter: str,
    old_value: float,
    new_value: float,
    applied_at: str,
    window_days: int = 7,
) -> None:
    """Record a pre-change performance snapshot for later comparison."""
    d = db.get_db()

    # Compute pre-change metrics from positions closed in [applied_at - window, applied_at]
    try:
        applied_dt = datetime.fromisoformat(applied_at)
        if applied_dt.tzinfo is None:
            applied_dt = applied_dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        applied_dt = datetime.now(timezone.utc)

    from datetime import timedelta
    window_start = (applied_dt - timedelta(days=window_days)).isoformat()

    pre_metrics = _compute_window_metrics(window_start, applied_at)

    d["parameter_change_snapshots"].insert({
        "parameter": parameter,
        "old_value": old_value,
        "new_value": new_value,
        "applied_at": applied_at,
        "snapshot_window_days": window_days,
        "pre_win_rate": pre_metrics["win_rate"],
        "pre_edge_efficiency": pre_metrics["edge_efficiency"],
        "pre_profit_factor": pre_metrics["profit_factor"],
        "post_win_rate": None,
        "post_edge_efficiency": None,
        "post_profit_factor": None,
        "verdict": "pending",
    })


def _compute_window_metrics(start_iso: str, end_iso: str) -> dict[str, float]:
    """Compute win_rate, edge_efficiency, profit_factor for positions closed in [start, end]."""
    d = db.get_db()
    try:
        rows = list(d.execute(
            """
            SELECT COALESCE(p.realized_pnl, p.unrealized_pnl) as rpnl,
                   fd.edge, fd.bet_size_usd
            FROM positions p
            LEFT JOIN frontier_decisions fd ON p.market_id = fd.market_id AND fd.should_trade = 1
            WHERE p.status = 'closed'
                  AND p.last_updated >= ? AND p.last_updated < ?
                  AND COALESCE(p.realized_pnl, p.unrealized_pnl) IS NOT NULL
            """,
            [start_iso, end_iso],
        ).fetchall())
    except Exception:
        return {"win_rate": 0.0, "edge_efficiency": 0.0, "profit_factor": 0.0}

    if not rows:
        return {"win_rate": 0.0, "edge_efficiency": 0.0, "profit_factor": 0.0}

    wins = 0
    gross_wins = 0.0
    gross_losses = 0.0
    predicted_edges: list[float] = []
    realized_returns: list[float] = []

    for row in rows:
        rpnl = float(row[0])
        edge = float(row[1]) if row[1] is not None else 0.0
        bet_size = float(row[2]) if row[2] is not None else 1.0

        if rpnl > 0:
            wins += 1
            gross_wins += rpnl
        else:
            gross_losses += abs(rpnl)

        if edge > 0:
            predicted_edges.append(edge)
        ret = rpnl / bet_size if bet_size > 0 else 0.0
        realized_returns.append(ret)

    win_rate = wins / len(rows) if rows else 0.0
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")

    avg_predicted = sum(predicted_edges) / len(predicted_edges) if predicted_edges else 0.0
    avg_realized = sum(realized_returns) / len(realized_returns) if realized_returns else 0.0
    edge_efficiency = avg_realized / avg_predicted if avg_predicted > 0 else 0.0

    return {
        "win_rate": win_rate,
        "edge_efficiency": edge_efficiency,
        "profit_factor": profit_factor if profit_factor != float("inf") else 99.0,
    }


def assess_parameter_impact(days_window: int = 7) -> list[dict[str, Any]]:
    """Compare performance metrics before/after each parameter change.

    For each change older than `days_window`:
    1. Query positions closed in [applied_at - window, applied_at]
    2. Query positions closed in [applied_at, applied_at + window]
    3. Compute win_rate, edge_efficiency, profit_factor for both windows
    4. If post-window shows degradation > 20% on any metric, mark verdict="degraded"

    Returns list of change records with verdicts.
    """
    d = db.get_db()
    now = datetime.now(timezone.utc)
    results: list[dict[str, Any]] = []

    try:
        rows = list(d.execute(
            "SELECT id, parameter, old_value, new_value, applied_at, "
            "snapshot_window_days, pre_win_rate, pre_edge_efficiency, "
            "pre_profit_factor, verdict "
            "FROM parameter_change_snapshots WHERE verdict = 'pending'"
        ).fetchall())
    except Exception:
        return []

    from datetime import timedelta

    for row in rows:
        snap_id = row[0]
        parameter = row[1]
        applied_at = row[4]
        window = int(row[5]) if row[5] else days_window
        pre_wr = float(row[6]) if row[6] is not None else 0.0
        pre_ee = float(row[7]) if row[7] is not None else 0.0
        pre_pf = float(row[8]) if row[8] is not None else 0.0

        try:
            applied_dt = datetime.fromisoformat(applied_at)
            if applied_dt.tzinfo is None:
                applied_dt = applied_dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue

        # Check if enough time has passed
        window_end = applied_dt + timedelta(days=window)
        if now < window_end:
            continue  # Not enough time yet

        # Compute post-change metrics
        post_start = applied_at
        post_end = window_end.isoformat()
        post_metrics = _compute_window_metrics(post_start, post_end)

        # Determine verdict
        verdict = "insufficient_data"
        if post_metrics["win_rate"] > 0 or post_metrics["profit_factor"] > 0:
            degraded = False
            # Check for >20% degradation on any metric (only where pre > 0)
            if pre_wr > 0 and post_metrics["win_rate"] < pre_wr * 0.80:
                degraded = True
            if pre_ee > 0 and post_metrics["edge_efficiency"] < pre_ee * 0.80:
                degraded = True
            if pre_pf > 0 and pre_pf < 90 and post_metrics["profit_factor"] < pre_pf * 0.80:
                degraded = True
            verdict = "degraded" if degraded else "improved"

        # Update snapshot
        try:
            d["parameter_change_snapshots"].update(snap_id, {
                "post_win_rate": post_metrics["win_rate"],
                "post_edge_efficiency": post_metrics["edge_efficiency"],
                "post_profit_factor": post_metrics["profit_factor"],
                "verdict": verdict,
            })
        except Exception:
            pass

        results.append({
            "parameter": parameter,
            "old_value": float(row[2]),
            "new_value": float(row[3]),
            "applied_at": applied_at,
            "verdict": verdict,
            "pre": {"win_rate": pre_wr, "edge_efficiency": pre_ee, "profit_factor": pre_pf},
            "post": post_metrics,
        })

    return results


# ---------------------------------------------------------------------------
# Skipped market resolution tracking
# ---------------------------------------------------------------------------

async def update_skipped_resolutions() -> int:
    """Check Gamma API for resolved markets in the skipped_markets table.

    Uses cached Gamma numeric IDs for individual lookups, then falls back
    to batch-fetching closed markets to catch any not in cache.
    """
    import aiohttp
    from signals.calibration import _extract_resolution, _batch_resolve_closed

    try:
        d = db.get_db()
        rows = list(d.execute(
            "SELECT DISTINCT market_id FROM skipped_markets "
            "WHERE resolution_outcome IS NULL"
        ).fetchall())
    except Exception as e:
        logger.warning("Failed to query unresolved skipped markets: %s", e)
        return 0

    if not rows:
        return 0

    condition_ids = [row[0] for row in rows if row[0].startswith("0x")]
    resolved_count = 0
    resolved_cids: set[str] = set()

    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Phase 1: Individual lookups via cached Gamma numeric IDs
            for cid in condition_ids:
                gamma_id = db.get_gamma_id_for_condition(cid)
                if not gamma_id:
                    continue
                try:
                    async with session.get(
                        "https://gamma-api.polymarket.com/markets",
                        params={"id": gamma_id, "closed": "true"},
                    ) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json()

                    markets = data if isinstance(data, list) else [data]
                    for mkt in markets:
                        actual = _extract_resolution(mkt)
                        if actual is not None:
                            d.execute(
                                "UPDATE skipped_markets SET resolution_outcome = ? "
                                "WHERE market_id = ? AND resolution_outcome IS NULL",
                                [actual, cid],
                            )
                            resolved_cids.add(cid)
                            resolved_count += 1
                except Exception:
                    continue

            # Phase 2: Batch scan for markets not in cache
            remaining = set(condition_ids) - resolved_cids
            if remaining:
                batch_resolved = await _batch_resolve_closed(session, remaining)
                for cid, actual in batch_resolved.items():
                    d.execute(
                        "UPDATE skipped_markets SET resolution_outcome = ? "
                        "WHERE market_id = ? AND resolution_outcome IS NULL",
                        [actual, cid],
                    )
                    resolved_count += 1

    except Exception as e:
        logger.warning("Skipped resolution check failed: %s", e)

    if resolved_count > 0:
        logger.info("Updated %d skipped market resolutions", resolved_count)

    return resolved_count


# ---------------------------------------------------------------------------
# Persistence — save reports to DB for dashboard access
# ---------------------------------------------------------------------------

def _ensure_learning_table() -> None:
    """Create learning_reports table if it doesn't exist."""
    d = db.get_db()
    if "learning_reports" not in d.table_names():
        d["learning_reports"].create({
            "id": int,
            "timestamp": str,
            "report_json": str,
            "recommendations_json": str,
        }, pk="id", if_not_exists=True)
        logger.info("Created learning_reports table")


def save_report(report: LearningReport) -> None:
    """Persist a learning report to the database."""
    _ensure_learning_table()
    d = db.get_db()

    # Serialize report (convert dataclasses to dicts)
    import dataclasses
    report_dict = dataclasses.asdict(report)
    recs_list = [dataclasses.asdict(r) for r in report.recommendations]

    d["learning_reports"].insert({
        "timestamp": report.timestamp,
        "report_json": json.dumps(report_dict),
        "recommendations_json": json.dumps(recs_list),
    })


def get_latest_report() -> LearningReport | None:
    """Load the most recent learning report from the database."""
    _ensure_learning_table()
    try:
        d = db.get_db()
        rows = list(d.execute(
            "SELECT report_json FROM learning_reports ORDER BY timestamp DESC LIMIT 1"
        ).fetchall())
        if not rows:
            return None

        data = json.loads(rows[0][0])
        # Reconstruct dataclass from dict
        report = LearningReport(
            timestamp=data.get("timestamp", ""),
            data_sufficiency=data.get("data_sufficiency", {}),
            applied_overrides=data.get("applied_overrides", []),
            reverted_overrides=data.get("reverted_overrides", []),
            current_regime=data.get("current_regime", ""),
        )
        # Reconstruct nested reports
        if "bias" in data:
            report.bias = BiasReport(**{k: v for k, v in data["bias"].items()})
        if "skip_retro" in data:
            report.skip_retro = SkipRetroReport(**{k: v for k, v in data["skip_retro"].items()})
        if "edge_realization" in data:
            report.edge_realization = EdgeRealizationReport(**{k: v for k, v in data["edge_realization"].items()})
        if "signal_features" in data:
            report.signal_features = SignalFeatureReport(**{k: v for k, v in data["signal_features"].items()})
        if "cost_effectiveness" in data:
            report.cost_effectiveness = CostEffectivenessReport(**{k: v for k, v in data["cost_effectiveness"].items()})
        if "recommendations" in data:
            report.recommendations = [ParameterRecommendation(**r) for r in data["recommendations"]]
        return report
    except Exception as e:
        logger.warning("Failed to load learning report: %s", e)
        return None


def get_report_history(limit: int = 20) -> list[dict[str, Any]]:
    """Get summary of recent learning reports for trend analysis."""
    _ensure_learning_table()
    try:
        d = db.get_db()
        rows = list(d.execute(
            "SELECT timestamp, report_json FROM learning_reports "
            "ORDER BY timestamp DESC LIMIT ?",
            [limit],
        ).fetchall())

        summaries = []
        for row in rows:
            data = json.loads(row[1])
            summaries.append({
                "timestamp": row[0],
                "bias_mean": data.get("bias", {}).get("mean_bias", 0),
                "bias_samples": data.get("bias", {}).get("sample_count", 0),
                "total_decisions": data.get("edge_realization", {}).get("total_trades", 0),
                "resolved_decisions": data.get("bias", {}).get("sample_count", 0),
                "skip_total": data.get("skip_retro", {}).get("total_skipped", 0),
                "skip_would_profit": data.get("skip_retro", {}).get("would_have_profited", 0),
                "edge_efficiency": data.get("edge_realization", {}).get("edge_efficiency", 0),
                "win_rate": data.get("edge_realization", {}).get("win_rate", 0),
                "roi": data.get("cost_effectiveness", {}).get("roi", 0),
                "rec_count": len(data.get("recommendations", [])),
                "applied_overrides": data.get("applied_overrides", []),
                "reverted_overrides": data.get("reverted_overrides", []),
                "current_regime": data.get("current_regime", ""),
                "data_sufficiency": data.get("data_sufficiency", {}),
            })
        return summaries
    except Exception as e:
        logger.warning("Failed to load report history: %s", e)
        return []


# ---------------------------------------------------------------------------
# Main entry point — run full learning cycle
# ---------------------------------------------------------------------------

async def run_learning_cycle() -> LearningReport:
    """Execute a full learning cycle: gather data, analyze, recommend, persist.

    Call this periodically (e.g., after each aggregation cycle or daily).
    Returns the complete LearningReport.
    """
    logger.info("Starting learning cycle...")

    # Step 1: Update skipped market resolutions from Gamma API
    await update_skipped_resolutions()

    # Step 1b: Classify current market regime
    current_regime = await classify_and_store_regime()

    # Step 2: Run all analyses
    bias = analyze_frontier_bias()
    skip_retro = analyze_skipped_markets()
    edge_real = analyze_edge_realization()
    sig_features = analyze_signal_features()
    cost_eff = analyze_cost_effectiveness()

    # Step 3: Generate recommendations
    recs = compute_parameter_recommendations(bias, skip_retro, edge_real, sig_features)

    # Step 4: Assess data sufficiency
    data_sufficiency = {
        "frontier_bias": bias.sample_count >= 10,
        "skip_retrospective": skip_retro.resolved_skipped >= 5,
        "edge_realization": edge_real.total_trades >= 5,
        "signal_features": bool(sig_features.by_source),
        "cost_effectiveness": cost_eff.total_llm_cost > 0,
    }

    insufficient = [k for k, v in data_sufficiency.items() if not v]
    if insufficient:
        logger.info("Learning: insufficient data for: %s", ", ".join(insufficient))

    # Step 5: Auto-apply qualifying recommendations
    report_ts = datetime.now(timezone.utc).isoformat()
    applied = apply_recommendations(recs, report_timestamp=report_ts)

    # Step 5b: Assess impact of previous parameter changes and auto-revert degraded ones
    reverted: list[str] = []
    degraded_changes = assess_parameter_impact()
    for change in degraded_changes:
        if change["verdict"] == "degraded":
            revert_override(change["parameter"])
            reverted.append(change["parameter"])
            logger.warning(
                "Auto-reverted %s: post-change degradation detected "
                "(pre WR=%.2f → post WR=%.2f)",
                change["parameter"],
                change["pre"]["win_rate"],
                change["post"]["win_rate"],
            )

    # Step 6: Build report
    report = LearningReport(
        timestamp=report_ts,
        bias=bias,
        skip_retro=skip_retro,
        edge_realization=edge_real,
        signal_features=sig_features,
        cost_effectiveness=cost_eff,
        recommendations=recs,
        data_sufficiency=data_sufficiency,
        applied_overrides=applied,
        reverted_overrides=reverted,
        current_regime=current_regime,
    )

    # Step 7: Persist
    try:
        save_report(report)
        logger.info(
            "Learning cycle complete: %d bias samples, %d skip retros, "
            "%d edge samples, %d recommendations, %d applied, %d reverted, regime=%s",
            bias.sample_count, skip_retro.resolved_skipped,
            edge_real.total_trades, len(recs), len(applied), len(reverted),
            current_regime,
        )
    except Exception as e:
        logger.warning("Failed to save learning report: %s", e)

    # Log recommendations
    for rec in recs:
        logger.info(
            "RECOMMENDATION [%.0f%% conf, n=%d]: %s %.3f → %.3f — %s",
            rec.confidence * 100, rec.sample_count,
            rec.parameter, rec.current_value, rec.recommended_value, rec.reason,
        )

    return report
