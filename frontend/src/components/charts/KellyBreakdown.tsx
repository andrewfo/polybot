import { colors } from '../../theme'

interface KellyData {
  bankroll: number
  edge: number
  kellyPct: number
  fractionalPct: number
  betSize: number
  side: string
}

function StepRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={{
      display: 'flex',
      justifyContent: 'space-between',
      padding: '6px 0',
      borderBottom: `1px solid ${colors.border}`,
      fontSize: 13,
    }}>
      <span style={{ color: colors.textMuted }}>{label}</span>
      <span style={{ fontWeight: 600 }}>{value}</span>
    </div>
  )
}

function MiniBar({ pct, color }: { pct: number; color: string }) {
  return (
    <div style={{
      height: 6,
      background: colors.bgSecondary,
      borderRadius: 3,
      overflow: 'hidden',
      marginTop: 2,
    }}>
      <div style={{
        height: '100%',
        width: `${Math.min(Math.abs(pct) * 100, 100)}%`,
        background: color,
        borderRadius: 3,
      }} />
    </div>
  )
}

export default function KellyBreakdown({ data }: { data: KellyData }) {
  return (
    <div>
      <StepRow label="Side" value={data.side || '—'} />
      <StepRow label="Bankroll" value={`$${data.bankroll.toFixed(2)}`} />
      <div>
        <StepRow label="Edge" value={`${(data.edge * 100).toFixed(2)}%`} />
        <MiniBar pct={data.edge} color={data.edge > 0 ? colors.success : colors.danger} />
      </div>
      <StepRow label="Full Kelly" value={`${(data.kellyPct * 100).toFixed(2)}%`} />
      <div>
        <StepRow label="Fractional Kelly (0.25x)" value={`${(data.fractionalPct * 100).toFixed(2)}%`} />
        <MiniBar pct={data.fractionalPct} color={colors.accent} />
      </div>
      <StepRow label="Bet Size" value={`$${data.betSize.toFixed(2)}`} />
    </div>
  )
}
