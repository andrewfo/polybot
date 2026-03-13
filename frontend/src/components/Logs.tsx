import { useState, useEffect, useCallback, useRef } from 'react'
import { colors, cardStyle } from '../theme'
import { api, LogEntry } from '../api'

const levelColors: Record<string, string> = {
  DEBUG: colors.textDim,
  INFO: '#3b82f6',
  WARNING: '#f59e0b',
  ERROR: '#ef4444',
  CRITICAL: '#ef4444',
}

const levelBg: Record<string, string> = {
  DEBUG: 'transparent',
  INFO: 'rgba(59,130,246,0.06)',
  WARNING: 'rgba(245,158,11,0.06)',
  ERROR: 'rgba(239,68,68,0.08)',
  CRITICAL: 'rgba(239,68,68,0.12)',
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
    borderRadius: 8,
    padding: '6px 10px',
    fontSize: 13,
    fontFamily: 'inherit',
    cursor: 'pointer',
    outline: 'none',
  }

  return (
    <div>
      <div style={{
        ...cardStyle, padding: '12px 16px', marginBottom: 14,
        display: 'flex', gap: 10, alignItems: 'center',
      }}>
        <label style={{ color: colors.textMuted, fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
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
          onMouseEnter={e => { e.currentTarget.style.borderColor = colors.accent }}
          onMouseLeave={e => { e.currentTarget.style.borderColor = colors.border }}
        >
          Refresh
        </button>
        <span style={{ color: colors.textDim, fontSize: 12, marginLeft: 'auto' }}>
          {logs.length} entries
        </span>
        <div style={{
          width: 8, height: 8, borderRadius: '50%',
          background: colors.success, animation: 'pulse 2s ease-in-out infinite',
        }} />
        <style>{`@keyframes pulse { 0%, 100% { opacity: 0.4; } 50% { opacity: 1; } }`}</style>
      </div>

      <div style={{
        ...cardStyle, padding: 0,
        maxHeight: 'calc(100vh - 220px)',
        overflow: 'auto',
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 12,
      }}>
        {logs.length === 0 ? (
          <div style={{ padding: 32, textAlign: 'center', color: colors.textDim, fontFamily: 'Inter' }}>
            No logs yet
          </div>
        ) : (
          logs.map((entry, i) => (
            <div
              key={i}
              style={{
                padding: '5px 12px',
                borderBottom: `1px solid ${colors.border}`,
                display: 'flex',
                gap: 10,
                background: levelBg[entry.level] || 'transparent',
                transition: 'background 0.1s',
              }}
            >
              <span style={{ color: colors.textDim, whiteSpace: 'nowrap', minWidth: 170, fontSize: 11 }}>
                {entry.timestamp.replace('T', ' ').slice(0, 19)}
              </span>
              <span style={{
                color: levelColors[entry.level] || colors.textMuted,
                fontWeight: 600, minWidth: 55, fontSize: 11,
              }}>
                {entry.level}
              </span>
              <span style={{ color: colors.textDim, minWidth: 100, fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {entry.name}
              </span>
              <span style={{ color: colors.textSecondary, fontSize: 12 }}>{entry.message}</span>
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
