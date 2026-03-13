import { useState, useEffect, useCallback } from 'react'
import { colors, cardStyle } from '../theme'
import { api, AnalysisSummary } from '../api'
import AnalysisDetail from './AnalysisDetail'

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { bg: string; fg: string }> = {
    done: { bg: 'rgba(34,197,94,0.15)', fg: '#22c55e' },
    processing: { bg: 'rgba(59,130,246,0.15)', fg: '#60a5fa' },
    error: { bg: 'rgba(239,68,68,0.15)', fg: '#ef4444' },
    skipped: { bg: 'rgba(122,139,165,0.15)', fg: '#7a8ba5' },
    waiting: { bg: 'rgba(30,45,74,0.5)', fg: '#556178' },
  }
  const s = map[status] || map.waiting
  return (
    <span style={{
      padding: '2px 8px', borderRadius: 20, fontSize: 10, fontWeight: 600,
      background: s.bg, color: s.fg, letterSpacing: '0.02em',
    }}>
      {status.toUpperCase()}
    </span>
  )
}

function DecisionBadge({ decision }: { decision: string | null }) {
  if (!decision) return null
  const isTrade = decision.toUpperCase().includes('TRADE') && !decision.toUpperCase().includes('SKIP')
  return (
    <span style={{
      padding: '2px 8px', borderRadius: 20, fontSize: 10, fontWeight: 600,
      background: isTrade ? 'rgba(34,197,94,0.15)' : 'rgba(245,158,11,0.15)',
      color: isTrade ? '#22c55e' : '#f59e0b',
    }}>
      {decision.toUpperCase()}
    </span>
  )
}

export default function Analysis() {
  const [entries, setEntries] = useState<AnalysisSummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [aggQuestion, setAggQuestion] = useState('')
  const [aggPrice, setAggPrice] = useState('0.5')
  const [aggLoading, setAggLoading] = useState(false)
  const [aggError, setAggError] = useState<string | null>(null)

  const refresh = useCallback(() => {
    api.fetchAnalysisList().then(setEntries).catch(() => {})
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 15000)
    return () => clearInterval(id)
  }, [refresh])

  const handleAggregate = async () => {
    if (!aggQuestion.trim()) return
    setAggLoading(true)
    setAggError(null)
    try {
      const result = await api.runAggregate(aggQuestion.trim(), parseFloat(aggPrice) || 0.5)
      if (result.condition_id) setSelectedId(result.condition_id)
      setAggQuestion('')
      refresh()
    } catch (e) {
      setAggError(e instanceof Error ? e.message : 'Failed')
    } finally {
      setAggLoading(false)
    }
  }

  const inputStyle: React.CSSProperties = {
    padding: '8px 12px', borderRadius: 8,
    border: `1px solid ${colors.border}`,
    background: colors.bgSecondary,
    color: colors.textPrimary,
    fontSize: 13, fontFamily: 'inherit', outline: 'none',
    transition: 'border-color 0.15s',
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* Aggregate command form */}
      <div style={{
        ...cardStyle, padding: 14,
        display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap',
      }}>
        <input
          type="text"
          placeholder="Market question (e.g. Will BTC reach $100k?)"
          value={aggQuestion}
          onChange={e => setAggQuestion(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !aggLoading) handleAggregate() }}
          onFocus={e => { e.currentTarget.style.borderColor = colors.accent }}
          onBlur={e => { e.currentTarget.style.borderColor = colors.border }}
          style={{ ...inputStyle, flex: 1, minWidth: 250 }}
        />
        <input
          type="number"
          placeholder="Price"
          value={aggPrice}
          onChange={e => setAggPrice(e.target.value)}
          min="0" max="1" step="0.01"
          onFocus={e => { e.currentTarget.style.borderColor = colors.accent }}
          onBlur={e => { e.currentTarget.style.borderColor = colors.border }}
          style={{ ...inputStyle, width: 90 }}
        />
        <button
          disabled={aggLoading || !aggQuestion.trim()}
          onClick={handleAggregate}
          style={{
            padding: '8px 20px', borderRadius: 8, border: 'none', fontFamily: 'inherit',
            background: aggLoading ? colors.bgSecondary : colors.gradientAccent,
            color: '#fff', cursor: aggLoading ? 'wait' : 'pointer',
            fontSize: 13, fontWeight: 600,
            boxShadow: aggLoading ? 'none' : '0 2px 8px rgba(59,130,246,0.3)',
            transition: 'all 0.2s',
            opacity: !aggQuestion.trim() ? 0.5 : 1,
          }}
        >
          {aggLoading ? 'Running...' : 'Run Aggregate'}
        </button>
        {aggError && (
          <span style={{ color: colors.danger, fontSize: 12 }}>{aggError}</span>
        )}
      </div>

      {/* Main split view */}
      <div style={{ display: 'grid', gridTemplateColumns: '40% 60%', gap: 14, minHeight: 500 }}>
        {/* Left: List */}
        <div style={{
          ...cardStyle, padding: 0, overflow: 'auto',
          maxHeight: 'calc(100vh - 260px)',
        }}>
          {entries.length === 0 ? (
            <div style={{ padding: 32, textAlign: 'center', color: colors.textDim }}>
              <div style={{ fontSize: 20, marginBottom: 8, opacity: 0.4 }}>~</div>
              <div style={{ fontSize: 14, marginBottom: 4 }}>No analysis data yet</div>
              <div style={{ fontSize: 12 }}>Start the bot or run a manual aggregate above.</div>
            </div>
          ) : (
            <div>
              {entries.map(e => (
                <div
                  key={e.condition_id}
                  onClick={() => setSelectedId(e.condition_id)}
                  style={{
                    padding: '12px 14px',
                    borderBottom: `1px solid ${colors.border}`,
                    cursor: 'pointer',
                    background: selectedId === e.condition_id ? colors.accentDim : 'transparent',
                    transition: 'all 0.15s',
                    borderLeft: selectedId === e.condition_id ? `3px solid ${colors.accent}` : '3px solid transparent',
                  }}
                  onMouseEnter={e2 => {
                    if (selectedId !== e.condition_id) e2.currentTarget.style.background = 'rgba(59,130,246,0.05)'
                  }}
                  onMouseLeave={e2 => {
                    if (selectedId !== e.condition_id) e2.currentTarget.style.background = 'transparent'
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 5 }}>
                    <StatusBadge status={e.status} />
                    <DecisionBadge decision={e.decision} />
                    {e.edge != null && (
                      <span style={{
                        fontSize: 11, fontWeight: 600,
                        fontFamily: "'JetBrains Mono', monospace",
                        color: e.edge > 0 ? colors.success : colors.danger,
                      }}>
                        {e.edge > 0 ? '+' : ''}{(e.edge * 100).toFixed(1)}%
                      </span>
                    )}
                  </div>
                  <div style={{
                    fontSize: 13, overflow: 'hidden',
                    textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    color: colors.textSecondary,
                  }}>
                    {e.question || e.condition_id}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Right: Detail */}
        <div style={{
          ...cardStyle, overflow: 'auto',
          maxHeight: 'calc(100vh - 260px)',
        }}>
          {selectedId ? (
            <AnalysisDetail conditionId={selectedId} />
          ) : (
            <div style={{ color: colors.textDim, textAlign: 'center', marginTop: 80 }}>
              <div style={{ fontSize: 24, marginBottom: 8, opacity: 0.3 }}>&#x2190;</div>
              Select a market from the list to view analysis details
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
