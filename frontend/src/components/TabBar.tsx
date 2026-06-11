import { useState } from 'react'
import { colors, fonts } from '../theme'

type Tab = 'dashboard' | 'markets' | 'analysis' | 'trades' | 'learning' | 'database' | 'logs'

const tabs: { id: Tab; label: string }[] = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'markets', label: 'Markets' },
  { id: 'analysis', label: 'Analysis' },
  { id: 'trades', label: 'Trades' },
  { id: 'learning', label: 'Learning' },
  { id: 'database', label: 'Database' },
  { id: 'logs', label: 'Logs' },
]

export default function TabBar({ active, onChange }: { active: Tab; onChange: (t: Tab) => void }) {
  const [hovered, setHovered] = useState<Tab | null>(null)

  return (
    <nav style={{
      display: 'flex',
      gap: 4,
      background: colors.bgPrimary,
      borderBottom: `1px solid ${colors.border}`,
      padding: '0 28px',
      position: 'relative',
    }}>
      {tabs.map((t) => {
        const isActive = active === t.id
        const isHovered = hovered === t.id
        return (
          <button
            key={t.id}
            onClick={() => onChange(t.id)}
            onMouseEnter={() => setHovered(t.id)}
            onMouseLeave={() => setHovered(null)}
            style={{
              padding: '12px 14px 11px',
              border: 'none',
              borderBottom: `1px solid ${isActive ? colors.accent : 'transparent'}`,
              marginBottom: -1,
              background: 'transparent',
              color: isActive ? colors.textPrimary : isHovered ? colors.textSecondary : colors.textMuted,
              cursor: 'pointer',
              fontSize: 11,
              fontWeight: isActive ? 600 : 500,
              transition: 'color 0.15s ease, border-color 0.15s ease',
              fontFamily: fonts.mono,
              letterSpacing: '0.1em',
              textTransform: 'uppercase',
            }}
          >
            {t.label}
          </button>
        )
      })}
    </nav>
  )
}
