import { useState, useEffect, useCallback, useRef } from 'react'
import { colors, cardStyle, fonts } from '../theme'
import { api, LogEntry } from '../api'

const levelColors: Record<string, string> = {
  DEBUG: colors.textDim,
  INFO: '#ffffff',
  WARNING: '#ffaa00',
  ERROR: '#e5484d',
  CRITICAL: '#e5484d',
}

const levelBg: Record<string, string> = {
  DEBUG: 'transparent',
  INFO: 'rgba(255, 255, 255,0.03)',
  WARNING: 'rgba(217, 160, 63,0.03)',
  ERROR: 'rgba(229, 72, 77,0.05)',
  CRITICAL: 'rgba(229, 72, 77,0.08)',
}

export default function Logs() {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [level, setLevel] = useState('ALL')
  const bottomRef = useRef<HTMLDivElement>(null)

  const refresh = useCallback(() => {
    api.fetchLogs(level, 200).then(setLogs).catch(() => {})
  }, [level])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 5000)
    return () => clearInterval(id)
  }, [refresh])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs])

  const selectStyle: React.CSSProperties = {
    background: colors.bgCard,
    color: colors.textPrimary,
    border: `1px solid ${colors.border}`,
    borderRadius: 4,
    padding: '6px 10px',
    fontSize: 11,
    fontFamily: fonts.mono,
    cursor: 'pointer',
    outline: 'none',
    transition: 'border-color 0.2s',
    letterSpacing: '0.02em',
  }

  return (
    <div>
      {/* Controls */}
      <div style={{
        ...cardStyle, padding: '10px 16px', marginBottom: 14,
        display: 'flex', gap: 10, alignItems: 'center',
      }}>
        <label style={{
          color: colors.textMuted, fontSize: 10, display: 'flex', alignItems: 'center', gap: 6,
          fontFamily: fonts.mono, letterSpacing: '0.06em', textTransform: 'uppercase',
        }}>
          Level
          <select value={level} onChange={e => setLevel(e.target.value)} style={selectStyle}>
            <option value="ALL">ALL</option>
            <option value="DEBUG">DEBUG</option>
            <option value="INFO">INFO</option>
            <option value="WARNING">WARNING</option>
            <option value="ERROR">ERROR</option>
          </select>
        </label>
        <button onClick={refresh} style={selectStyle}
          onMouseEnter={e => {
            e.currentTarget.style.borderColor = colors.accent
            e.currentTarget.style.boxShadow = '0 0 8px rgba(255, 255, 255,0.15)'
          }}
          onMouseLeave={e => {
            e.currentTarget.style.borderColor = colors.border
            e.currentTarget.style.boxShadow = 'none'
          }}
        >
          Refresh
        </button>
        <span style={{
          color: colors.textDim, fontSize: 10, marginLeft: 'auto',
          fontFamily: fonts.mono, letterSpacing: '0.04em',
        }}>
          {logs.length} ENTRIES
        </span>
        {/* Pulsing live indicator */}
        <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
          <div style={{
            width: 6, height: 6, borderRadius: '50%',
            background: colors.success,
            boxShadow: `0 0 8px ${colors.success}`,
            animation: 'pulse 2s ease-in-out infinite',
          }} />
        </div>
      </div>

      {/* Log viewer — terminal style */}
      <div style={{
        ...cardStyle, padding: 0,
        maxHeight: 'calc(100vh - 220px)',
        overflow: 'auto',
        fontFamily: fonts.mono,
        fontSize: 11,
        position: 'relative',
      }}>
        {/* Scanline effect */}
        <div style={{
          position: 'absolute', left: 0, right: 0,
          height: 4,
          background: 'linear-gradient(180deg, rgba(255, 255, 255,0.03) 0%, transparent 100%)',
          animation: 'scanline 8s linear infinite',
          pointerEvents: 'none',
          zIndex: 1,
        }} />

        {logs.length === 0 ? (
          <div style={{
            padding: 32, textAlign: 'center', color: colors.textDim,
            fontFamily: fonts.body,
          }}>
            <div style={{ fontFamily: fonts.mono, fontSize: 12, letterSpacing: '0.02em' }}>
              No logs yet
            </div>
          </div>
        ) : (
          logs.map((entry, i) => (
            <div
              key={i}
              style={{
                padding: '4px 12px',
                borderBottom: `1px solid rgba(255, 255, 255, 0.02)`,
                display: 'flex',
                gap: 8,
                background: levelBg[entry.level] || 'transparent',
                transition: 'background 0.15s',
              }}
              onMouseEnter={e => {
                e.currentTarget.style.background = 'rgba(255, 255, 255, 0.03)'
              }}
              onMouseLeave={e => {
                e.currentTarget.style.background = levelBg[entry.level] || 'transparent'
              }}
            >
              <span style={{
                color: colors.textDim, whiteSpace: 'nowrap', minWidth: 150, fontSize: 10,
                opacity: 0.6,
              }}>
                {entry.timestamp.replace('T', ' ').slice(0, 19)}
              </span>
              <span style={{
                color: levelColors[entry.level] || colors.textMuted,
                fontWeight: 600, minWidth: 55, fontSize: 10,
                textShadow: 'none',
              }}>
                {entry.level}
              </span>
              <span style={{
                color: colors.textDim, minWidth: 90, fontSize: 10,
                overflow: 'hidden', textOverflow: 'ellipsis',
                opacity: 0.5,
              }}>
                {entry.name}
              </span>
              <span style={{
                color: colors.textSecondary, fontSize: 11,
              }}>
                {entry.message}
              </span>
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
