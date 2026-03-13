import { colors } from '../theme'

type Tab = 'dashboard' | 'markets' | 'analysis' | 'logs'

const tabs: { id: Tab; label: string; icon: string }[] = [
  { id: 'dashboard', label: 'Dashboard', icon: '\u25A3' },
  { id: 'markets', label: 'Markets', icon: '\u2637' },
  { id: 'analysis', label: 'Analysis', icon: '\u2A2F' },
  { id: 'logs', label: 'Logs', icon: '\u2261' },
]

export default function TabBar({ active, onChange }: { active: Tab; onChange: (t: Tab) => void }) {
  return (
    <nav style={{
      display: 'flex',
      gap: 2,
      background: 'rgba(11, 21, 41, 0.6)',
      backdropFilter: 'blur(12px)',
      borderBottom: `1px solid ${colors.border}`,
      padding: '0 28px',
    }}>
      {tabs.map(t => {
        const isActive = active === t.id
        return (
          <button
            key={t.id}
            onClick={() => onChange(t.id)}
            style={{
              padding: '12px 20px',
              border: 'none',
              borderBottom: isActive ? `2px solid ${colors.accent}` : '2px solid transparent',
              background: isActive ? colors.accentDim : 'transparent',
              color: isActive ? colors.accentLight : colors.textMuted,
              cursor: 'pointer',
              fontSize: 13,
              fontWeight: isActive ? 600 : 500,
              transition: 'all 0.2s ease',
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              borderRadius: '6px 6px 0 0',
              fontFamily: 'inherit',
              letterSpacing: '0.01em',
            }}
            onMouseEnter={e => {
              if (!isActive) {
                e.currentTarget.style.color = colors.textSecondary
                e.currentTarget.style.background = 'rgba(59, 130, 246, 0.05)'
              }
            }}
            onMouseLeave={e => {
              if (!isActive) {
                e.currentTarget.style.color = colors.textMuted
                e.currentTarget.style.background = 'transparent'
              }
            }}
          >
            <span style={{ fontSize: 14, opacity: 0.7 }}>{t.icon}</span>
            {t.label}
          </button>
        )
      })}
    </nav>
  )
}
