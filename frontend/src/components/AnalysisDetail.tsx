import { useState, useEffect } from 'react'
import { colors } from '../theme'
import { api, AnalysisDetail as AnalysisDetailType } from '../api'
import ProbabilityBars from './charts/ProbabilityBars'
import VolComparison from './charts/VolComparison'
import PriceChart from './charts/PriceChart'
import KellyBreakdown from './charts/KellyBreakdown'
import SignalWeights from './charts/SignalWeights'

function SectionHeader({ title }: { title: string }) {
  return (
    <h3 style={{
      margin: '20px 0 10px',
      fontSize: 13,
      color: colors.textMuted,
      textTransform: 'uppercase',
      letterSpacing: 1,
      borderBottom: `1px solid ${colors.border}`,
      paddingBottom: 6,
    }}>
      {title}
    </h3>
  )
}

function Badge({ text, color }: { text: string; color: string }) {
  return (
    <span style={{
      padding: '2px 8px',
      borderRadius: 4,
      fontSize: 11,
      fontWeight: 600,
      background: color,
      color: '#fff',
    }}>
      {text}
    </span>
  )
}

export default function AnalysisDetail({ conditionId }: { conditionId: string }) {
  const [data, setData] = useState<AnalysisDetailType | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setData(null)
    setError(null)
    api.fetchAnalysisDetail(conditionId)
      .then(setData)
      .catch(e => setError(e.message))
  }, [conditionId])

  if (error) return <div style={{ color: colors.danger }}>Error: {error}</div>
  if (!data) return <div style={{ color: colors.textDim }}>Loading...</div>

  const market = (data.market_data || data.market || {}) as Record<string, unknown>
  const agg = (data.aggregation || {}) as Record<string, unknown>
  const kelly = (data.kelly || data.decision || {}) as Record<string, unknown>
  const exec = (data.execution || {}) as Record<string, unknown>
  const signals = (agg.signals || data.signals || []) as Record<string, unknown>[]

  const question = (market.question || data.question || conditionId) as string
  const marketPrice = parseFloat(String(market.market_price || market.yes_price || agg.market_price || 0))
  const estimate = parseFloat(String(agg.final_probability || agg.estimated_prob || 0))
  const effective = parseFloat(String(kelly.effective_prob || agg.effective_prob || estimate))
  const confidence = parseFloat(String(agg.confidence || 0))

  // Probability bars data
  const probBars: { label: string; value: number; color: string }[] = [
    { label: 'Market', value: marketPrice, color: colors.textMuted },
    { label: 'Estimate', value: estimate, color: colors.accent },
    { label: 'Effective', value: effective, color: colors.success },
  ]
  if (Array.isArray(signals)) {
    for (const s of signals) {
      const p = parseFloat(String(s.probability || 0))
      if (p > 0) {
        probBars.push({ label: String(s.source || s.signal_source || '?'), value: p, color: colors.warning })
      }
    }
  }

  // Vol data
  const volData = (agg.vol_data || data.vol_data || market._vol_data || null) as Record<string, number> | null
  const priceHistory = (data.price_history || []) as { date: string; price: number }[]
  const targetPrice = parseFloat(String(market._target_price || data.target_price || 0))

  // Kelly data
  const kellyData = {
    bankroll: parseFloat(String(kelly.bankroll || 0)),
    edge: parseFloat(String(kelly.edge || 0)),
    kellyPct: parseFloat(String(kelly.kelly_fraction_raw || kelly.raw_kelly || 0)),
    fractionalPct: parseFloat(String(kelly.kelly_fraction || kelly.fractional_kelly || 0)),
    betSize: parseFloat(String(kelly.bet_size || 0)),
    side: String(kelly.side || ''),
  }

  // Signal weights data
  const weightData: { label: string; weight: number }[] = []
  if (Array.isArray(signals)) {
    for (const s of signals) {
      const w = parseFloat(String(s.effective_weight || s.weight || 0))
      if (w > 0) {
        weightData.push({ label: String(s.source || s.signal_source || '?'), weight: w })
      }
    }
  }

  const reasoning = String(agg.reasoning || agg.frontier_reasoning || data.reasoning || '')
  const divergence = Math.abs(estimate - marketPrice)
  const divColor = divergence < 0.1 ? colors.success : divergence < 0.2 ? colors.warning : colors.danger

  return (
    <div>
      {/* 1. Market Info */}
      <SectionHeader title="Market Info" />
      <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>{question}</div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, fontSize: 13, color: colors.textMuted }}>
        <div>Condition: <span style={{ fontFamily: 'monospace', fontSize: 11 }}>{conditionId.slice(0, 16)}...</span></div>
        <div>Market Price: <span style={{ color: colors.textPrimary }}>{(marketPrice * 100).toFixed(1)}%</span></div>
      </div>

      {/* 2. Probability Comparison */}
      <SectionHeader title="Probability Comparison" />
      <ProbabilityBars bars={probBars} />

      {/* 3. Crypto Model Data */}
      {volData != null && (
        <>
          <SectionHeader title="Crypto Model Data" />
          {priceHistory.length > 0 && <PriceChart data={priceHistory} target={targetPrice} />}
          <VolComparison data={volData} />
        </>
      )}

      {/* 4. Kelly Sizing */}
      {kellyData.betSize > 0 && (
        <>
          <SectionHeader title="Kelly Sizing" />
          <KellyBreakdown data={kellyData} />
        </>
      )}

      {/* 5. Execution */}
      {exec.status != null && (
        <>
          <SectionHeader title="Execution" />
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}>
            <Badge
              text={String(exec.status).toUpperCase()}
              color={String(exec.status) === 'filled' ? colors.success : String(exec.status) === 'error' ? colors.danger : colors.accent}
            />
            {exec.paper != null && <Badge text="PAPER" color={colors.warning} />}
            {exec.trade_id != null && <span style={{ color: colors.textDim }}>ID: {String(exec.trade_id).slice(0, 8)}</span>}
            {exec.price != null && <span>Price: {String(exec.price)}</span>}
            {exec.size != null && <span>Size: ${String(exec.size)}</span>}
          </div>
        </>
      )}

      {/* 6. Frontier Reasoning */}
      {(reasoning || confidence > 0) && (
        <>
          <SectionHeader title="Frontier Reasoning" />
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
            <span style={{ fontSize: 13, color: colors.textMuted }}>
              Final: <span style={{ color: colors.textPrimary }}>{(estimate * 100).toFixed(1)}%</span>
            </span>
            <span style={{ fontSize: 13, color: colors.textMuted }}>
              Confidence: <span style={{ color: colors.textPrimary }}>{(confidence * 100).toFixed(0)}%</span>
            </span>
            <Badge text={`DIV ${(divergence * 100).toFixed(1)}%`} color={divColor} />
          </div>

          {/* Signal weights */}
          {weightData.length > 0 && <SignalWeights data={weightData} />}

          {reasoning && (
            <pre style={{
              background: colors.bgSecondary,
              border: `1px solid ${colors.border}`,
              borderRadius: 6,
              padding: 12,
              fontSize: 12,
              color: colors.textPrimary,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              marginTop: 8,
              maxHeight: 300,
              overflow: 'auto',
            }}>
              {reasoning}
            </pre>
          )}
        </>
      )}
    </div>
  )
}
