import { useState } from 'react'
import { colors, fonts } from '../theme'

type Tab = 'dashboard' | 'markets' | 'analysis' | 'trades' | 'learning' | 'database' | 'logs'

const tabs: { id: Tab; label: string; icon: string }[] = [
  { id: 'dashboard', label: 'Dashboard', icon: '\u25A3' },
  { id: 'markets', label: 'Markets', icon: '\u2637' },
  { id: 'analysis', label: 'Analysis', icon: '\u2A2F' },
  { id: 'trades', label: 'Trades', icon: '\u2194' },
  { id: 'learning', label: 'Learning', icon: '\u2318' },
  { id: 'database', label: 'Database', icon: '\u2505' },
  { id: 'logs', label: 'Logs', icon: '\u2261' },
]

export default function TabBar({ active, onChange }: { active: Tab; onChange: (t: Tab) => void }) {
  const [hovered, setHovered] = useState<Tab | null>(null)

  return (
    <nav style={{
      display: 'flex',
      gap: 0,
      background: 'rgba(6, 10, 20, 0.6)',
      backdropFilter: 'blur(12px)',
      borderBottom: `1px solid ${colors.border}`,
      padding: '0 28px',
      position: 'relative',
    }}>
      {tabs.map((t, i) => {
        const isActive = active === t.id
        const isHovered = hovered === t.id
        return (
          <button
            key={t.id}
            onClick={() => onChange(t.id)}
            onMouseEnter={() => setHovered(t.id)}
            onMouseLeave={() => setHovered(null)}
            style={{
              padding: '11px 22px',
              border: 'none',
              borderBottom: `2px solid ${isActive ? colors.accent : 'transparent'}`,
              background: isActive
                ? 'rgba(0, 229, 255, 0.05)'
                : isHovered
                  ? 'rgba(0, 229, 255, 0.02)'
                  : 'transparent',
              color: isActive ? colors.accent : isHovered ? colors.textSecondary : colors.textMuted,
              cursor: 'pointer',
              fontSize: 12,
              fontWeight: isActive ? 600 : 500,
              transition: 'all 0.25s ease',
              display: 'flex',
              alignItems: 'center',
              gap: 7,
              borderRadius: '6px 6px 0 0',
              fontFamily: fonts.body,
              letterSpacing: '0.03em',
              position: 'relative',
              animation: 'fadeInUp 0.3s ease forwards',
              animationDelay: `${i * 0.05}s`,
              opacity: 0,
              textTransform: 'uppercase',
            }}
          >
            <span style={{
              fontSize: 13,
              opacity: isActive ? 1 : 0.5,
              transition: 'opacity 0.2s',
              filter: isActive ? `drop-shadow(0 0 4px ${colors.accent})` : 'none',
            }}>
              {t.icon}
            </span>
            {t.label}
            {/* Active glow underline */}
            {isActive && (
              <div style={{
                position: 'absolute',
                bottom: -1, left: '10%', right: '10%',
                height: 2,
                background: colors.accent,
                boxShadow: `0 0 12px ${colors.accent}, 0 0 24px rgba(0, 229, 255, 0.3)`,
                borderRadius: 1,
              }} />
            )}
          </button>
        )
      })}
    </nav>
  )
}
