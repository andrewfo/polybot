import { colors } from '../../theme'

interface Gate {
  name: string
  status: 'pass' | 'fail' | 'warn' | 'skip'
  value?: string
  threshold?: string
  detail?: string
}

export default function DecisionPipeline({ gates }: { gates: Gate[] }) {
  if (gates.length === 0) return null

  const statusColors = {
    pass: colors.success,
    fail: colors.danger,
    warn: colors.warning,
    skip: colors.textDim,
  }

  const statusIcons = {
    pass: '\u2713',
    fail: '\u2717',
    warn: '!',
    skip: '-',
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
      {gates.map((g, i) => {
        const c = statusColors[g.status]
        return (
          <div key={i}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '6px 10px',
              background: c + '0a',
              borderLeft: `3px solid ${c}`,
              borderRadius: i === 0 ? '6px 6px 0 0' : i === gates.length - 1 ? '0 0 6px 6px' : 0,
            }}>
              {/* Status icon */}
              <span style={{
                width: 20, height: 20, borderRadius: '50%',
                background: c + '22', color: c,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 11, fontWeight: 700, flexShrink: 0,
              }}>
                {statusIcons[g.status]}
              </span>

              {/* Gate name */}
              <span style={{ fontSize: 11, color: colors.textSecondary, flex: 1, fontWeight: 500 }}>
                {g.name}
              </span>

              {/* Value vs threshold */}
              {g.value && (
                <span style={{
                  fontSize: 10, fontFamily: "'JetBrains Mono', monospace",
                  color: c,
                }}>
                  {g.value}
                  {g.threshold && (
                    <span style={{ color: colors.textDim }}> / {g.threshold}</span>
                  )}
                </span>
              )}
            </div>

            {/* Connector line between gates */}
            {i < gates.length - 1 && (
              <div style={{
                marginLeft: 19, width: 2, height: 4,
                background: colors.border,
              }} />
            )}
          </div>
        )
      })}
    </div>
  )
}
