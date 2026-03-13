import { colors } from '../../theme'

interface Props {
  totalDepth: number
  slippage: number
  bestPrice: number
  avgFillPrice: number
  maxFillable: number
  levels: number
  adjustedBet?: number
  wasAdjusted?: boolean
  thresholdSlippage: number
  thresholdMinDepth: number
}

export default function DepthLadder({
  totalDepth, slippage, bestPrice, avgFillPrice, maxFillable,
  levels, adjustedBet, wasAdjusted, thresholdSlippage, thresholdMinDepth,
}: Props) {
  const slipPct = slippage * 100
  const maxSlipPct = thresholdSlippage * 100

  // Visual bars for depth metrics
  const metrics = [
    {
      label: 'Total Depth',
      value: totalDepth,
      display: `$${totalDepth.toFixed(0)}`,
      pct: Math.min(totalDepth / 1000, 1), // scale: $1000 = full bar
      color: totalDepth >= thresholdMinDepth ? colors.success : colors.danger,
      threshold: `min $${thresholdMinDepth}`,
    },
    {
      label: 'Slippage',
      value: slipPct,
      display: `${slipPct.toFixed(2)}%`,
      pct: Math.min(slipPct / (maxSlipPct * 2), 1),
      color: slipPct <= maxSlipPct ? colors.success : slipPct <= maxSlipPct * 1.5 ? colors.warning : colors.danger,
      threshold: `max ${maxSlipPct.toFixed(1)}%`,
    },
    {
      label: 'Price Spread',
      value: avgFillPrice - bestPrice,
      display: `${bestPrice.toFixed(3)} -> ${avgFillPrice.toFixed(3)}`,
      pct: bestPrice > 0 ? Math.min((avgFillPrice - bestPrice) / bestPrice * 20, 1) : 0,
      color: colors.accent,
      threshold: '',
    },
    {
      label: 'Max Fillable',
      value: maxFillable,
      display: `$${maxFillable.toFixed(0)}`,
      pct: Math.min(maxFillable / 500, 1),
      color: colors.accentLight,
      threshold: '',
    },
  ]

  return (
    <div>
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr 1fr',
        gap: 6, marginBottom: 10,
      }}>
        <div style={{
          background: colors.bgSecondary, borderRadius: 6, padding: '8px 10px',
          border: `1px solid ${colors.border}`,
        }}>
          <div style={{ fontSize: 9, color: colors.textDim, textTransform: 'uppercase' }}>Price Levels</div>
          <div style={{ fontSize: 16, fontWeight: 700, color: colors.textPrimary, fontFamily: "'JetBrains Mono', monospace" }}>
            {levels}
          </div>
        </div>
        {wasAdjusted && adjustedBet != null && (
          <div style={{
            background: colors.warningDim, borderRadius: 6, padding: '8px 10px',
            border: `1px solid ${colors.warning}33`,
          }}>
            <div style={{ fontSize: 9, color: colors.warning, textTransform: 'uppercase' }}>Adjusted Bet</div>
            <div style={{ fontSize: 16, fontWeight: 700, color: colors.warning, fontFamily: "'JetBrains Mono', monospace" }}>
              ${adjustedBet.toFixed(2)}
            </div>
          </div>
        )}
      </div>

      {metrics.map((m, i) => (
        <div key={i} style={{ marginBottom: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
            <span style={{ fontSize: 10, color: colors.textDim }}>{m.label}</span>
            <div style={{ display: 'flex', gap: 8 }}>
              <span style={{ fontSize: 10, fontFamily: "'JetBrains Mono', monospace", color: m.color, fontWeight: 600 }}>
                {m.display}
              </span>
              {m.threshold && (
                <span style={{ fontSize: 9, color: colors.textDim }}>{m.threshold}</span>
              )}
            </div>
          </div>
          <div style={{ height: 6, background: colors.border, borderRadius: 3, overflow: 'hidden' }}>
            <div style={{
              height: '100%', width: `${Math.max(m.pct * 100, 2)}%`,
              background: m.color, borderRadius: 3,
              transition: 'width 0.4s ease',
            }} />
          </div>
        </div>
      ))}
    </div>
  )
}
