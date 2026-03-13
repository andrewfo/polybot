import { useState, useEffect, useCallback, useRef } from 'react'
import { colors } from '../theme'
import { api, LogEntry } from '../api'

const levelColors: Record<string, string> = {
  DEBUG: colors.textDim,
  INFO: colors.accent,
  WARNING: colors.warning,
  ERROR: colors.danger,
  CRITICAL: colors.danger,
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

  return (
    <div>
      <div style={{ display: 'flex', gap: 12, marginBottom: 12, alignItems: 'center' }}>
        <label style={{ color: colors.textMuted, fontSize: 13 }}>
          Level:
          <select
            value={level}
            onChange={e => setLevel(e.target.value)}
            style={{
              marginLeft: 6,
              background: colors.bgCard,
              color: colors.textPrimary,
              border: `1px solid ${colors.border}`,
              borderRadius: 4,
              padding: '4px 8px',
              fontSize: 13,
            }}
          >
            <option value="ALL">ALL</option>
            <option value="DEBUG">DEBUG</option>
            <option value="INFO">INFO</option>
            <option value="WARNING">WARNING</option>
            <option value="ERROR">ERROR</option>
          </select>
        </label>
        <button
          onClick={refresh}
          style={{
            padding: '4px 14px',
            borderRadius: 4,
            border: `1px solid ${colors.border}`,
            background: colors.bgCard,
            color: colors.textPrimary,
            cursor: 'pointer',
            fontSize: 13,
          }}
        >
          Refresh
        </button>
        <span style={{ color: colors.textDim, fontSize: 12 }}>{logs.length} entries</span>
      </div>

      <div style={{
        background: colors.bgCard,
        border: `1px solid ${colors.border}`,
        borderRadius: 8,
        maxHeight: 'calc(100vh - 200px)',
        overflow: 'auto',
        fontFamily: 'monospace',
        fontSize: 12,
      }}>
        {logs.length === 0 ? (
          <div style={{ padding: 24, textAlign: 'center', color: colors.textDim }}>
            No logs yet
          </div>
        ) : (
          logs.map((entry, i) => (
            <div
              key={i}
              style={{
                padding: '3px 10px',
                borderBottom: `1px solid ${colors.border}`,
                display: 'flex',
                gap: 8,
              }}
            >
              <span style={{ color: colors.textDim, whiteSpace: 'nowrap', minWidth: 180 }}>
                {entry.timestamp.replace('T', ' ').slice(0, 19)}
              </span>
              <span style={{
                color: levelColors[entry.level] || colors.textMuted,
                fontWeight: 600,
                minWidth: 55,
              }}>
                {entry.level}
              </span>
              <span style={{ color: colors.textDim, minWidth: 120 }}>{entry.name}</span>
              <span style={{ color: colors.textPrimary }}>{entry.message}</span>
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
