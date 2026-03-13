import { colors } from '../../theme'

interface KellyData {
  bankroll: number
  edge: number
  kellyPct: number
  fractionalPct: number
  betSize: number
  side: string
}

function StepRow({ label, value, highlight }: { label: string; value: string; highlight?: string }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '8px 0', borderBottom: `1px solid ${colors.border}`, fontSize: 13,
    }}>
      <span style={{ color: colors.textMuted, fontSize: 12 }}>{label}</span>
      <span style={{
        fontWeight: 600, color: highlight || colors.textPrimary,
        fontFamily: "'JetBrains Mono', monospace", fontSize: 13,
      }}>
        {value}
      </span>
    </div>
  )
}

function MiniBar({ pct, color }: { pct: number; color: string }) {
  return (
    <div style={{
      height: 4, background: colors.border, borderRadius: 2,
      overflow: 'hidden', marginTop: 4,
    }}>
      <div style={{
        height: '100%', width: `${Math.min(Math.abs(pct) * 100, 100)}%`,
        background: color, borderRadius: 2,
        transition: 'width 0.4s ease',
      }} />
    </div>
  )
}

export default function KellyBreakdown({ data }: { data: KellyData }) {
  return (
    <div>
      <StepRow label="Side" value={data.side || '\u2014'} />
      <StepRow label="Bankroll" value={`$${data.bankroll.toFixed(2)}`} />
      <div>
        <StepRow
          label="Edge"
          value={`${(data.edge * 100).toFixed(2)}%`}
          highlight={data.edge > 0 ? colors.success : colors.danger}
        />
        <MiniBar pct={data.edge} color={data.edge > 0 ? colors.success : colors.danger} />
      </div>
      <StepRow label="Full Kelly" value={`${(data.kellyPct * 100).toFixed(2)}%`} />
      <div>
        <StepRow label="Fractional Kelly (0.25x)" value={`${(data.fractionalPct * 100).toFixed(2)}%`} highlight={colors.accent} />
        <MiniBar pct={data.fractionalPct} color={colors.accent} />
      </div>
      <StepRow label="Bet Size" value={`$${data.betSize.toFixed(2)}`} highlight={colors.accentLight} />
    </div>
  )
}
