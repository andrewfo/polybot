import { colors } from '../theme'

type Tab = 'dashboard' | 'markets' | 'analysis' | 'logs'

const tabs: { id: Tab; label: string }[] = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'markets', label: 'Markets' },
  { id: 'analysis', label: 'Analysis' },
  { id: 'logs', label: 'Logs' },
]

export default function TabBar({ active, onChange }: { active: Tab; onChange: (t: Tab) => void }) {
  return (
    <nav style={{
      display: 'flex',
      gap: 0,
      background: colors.bgSecondary,
      borderBottom: `1px solid ${colors.border}`,
    }}>
      {tabs.map(t => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          style={{
            padding: '10px 24px',
            border: 'none',
            borderBottom: active === t.id ? `2px solid ${colors.accent}` : '2px solid transparent',
            background: active === t.id ? colors.bgCard : 'transparent',
            color: active === t.id ? colors.textPrimary : colors.textMuted,
            cursor: 'pointer',
            fontSize: 14,
            fontWeight: active === t.id ? 600 : 400,
            transition: 'all 0.15s',
          }}
        >
          {t.label}
        </button>
      ))}
    </nav>
  )
}
