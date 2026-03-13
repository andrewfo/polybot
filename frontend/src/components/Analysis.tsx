import { useState, useEffect, useCallback } from 'react'
import { colors } from '../theme'
import { api, AnalysisSummary } from '../api'
import AnalysisDetail from './AnalysisDetail'

function StatusBadge({ status }: { status: string }) {
  const bg =
    status === 'done' ? colors.success :
    status === 'processing' ? colors.accent :
    status === 'error' ? colors.danger :
    status === 'skipped' ? colors.textDim :
    colors.bgSecondary
  return (
    <span style={{
      padding: '1px 6px',
      borderRadius: 3,
      fontSize: 10,
      fontWeight: 600,
      background: bg,
      color: '#fff',
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
      padding: '1px 6px',
      borderRadius: 3,
      fontSize: 10,
      fontWeight: 600,
      background: isTrade ? colors.success : colors.warning,
      color: isTrade ? '#fff' : '#000',
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
      if (result.condition_id) {
        setSelectedId(result.condition_id)
      }
      setAggQuestion('')
      refresh()
    } catch (e) {
      setAggError(e instanceof Error ? e.message : 'Failed')
    } finally {
      setAggLoading(false)
    }
  }

  const isEmpty = entries.length === 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Aggregate command form */}
      <div style={{
        background: colors.bgCard,
        border: `1px solid ${colors.border}`,
        borderRadius: 8,
        padding: 12,
        display: 'flex',
        gap: 8,
        alignItems: 'center',
        flexWrap: 'wrap',
      }}>
        <input
          type="text"
          placeholder="Market question (e.g. Will BTC reach $100k?)"
          value={aggQuestion}
          onChange={e => setAggQuestion(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !aggLoading) handleAggregate() }}
          style={{
            flex: 1,
            minWidth: 250,
            padding: '6px 10px',
            borderRadius: 4,
            border: `1px solid ${colors.border}`,
            background: colors.bgSecondary,
            color: colors.textPrimary,
            fontSize: 13,
            outline: 'none',
          }}
        />
        <input
          type="number"
          placeholder="Market price"
          value={aggPrice}
          onChange={e => setAggPrice(e.target.value)}
          min="0"
          max="1"
          step="0.01"
          style={{
            width: 90,
            padding: '6px 10px',
            borderRadius: 4,
            border: `1px solid ${colors.border}`,
            background: colors.bgSecondary,
            color: colors.textPrimary,
            fontSize: 13,
            outline: 'none',
          }}
        />
        <button
          disabled={aggLoading || !aggQuestion.trim()}
          onClick={handleAggregate}
          style={{
            padding: '6px 16px',
            borderRadius: 4,
            border: `1px solid ${colors.accent}`,
            background: aggLoading ? colors.bgSecondary : colors.accent,
            color: '#fff',
            cursor: aggLoading ? 'wait' : 'pointer',
            fontSize: 13,
            fontWeight: 600,
          }}
        >
          {aggLoading ? 'Running...' : 'Run Aggregate'}
        </button>
        {aggError && (
          <span style={{ color: colors.danger, fontSize: 12 }}>{aggError}</span>
        )}
      </div>

      {/* Main split view */}
      <div style={{ display: 'grid', gridTemplateColumns: '40% 60%', gap: 16, minHeight: 500 }}>
        {/* Left: List */}
        <div style={{
          background: colors.bgCard,
          border: `1px solid ${colors.border}`,
          borderRadius: 8,
          overflow: 'auto',
          maxHeight: 'calc(100vh - 240px)',
        }}>
          {isEmpty ? (
            <div style={{ padding: 24, textAlign: 'center', color: colors.textDim }}>
              <div style={{ fontSize: 16, marginBottom: 8 }}>No analysis data yet</div>
              <div style={{ fontSize: 13 }}>Start the bot or run a manual aggregate above.</div>
            </div>
          ) : (
            <div>
              {entries.map(e => (
                <div
                  key={e.condition_id}
                  onClick={() => setSelectedId(e.condition_id)}
                  style={{
                    padding: '10px 12px',
                    borderBottom: `1px solid ${colors.border}`,
                    cursor: 'pointer',
                    background: selectedId === e.condition_id ? colors.bgSecondary : 'transparent',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                    <StatusBadge status={e.status} />
                    <DecisionBadge decision={e.decision} />
                    {e.edge != null && (
                      <span style={{ fontSize: 11, color: e.edge > 0 ? colors.success : colors.danger }}>
                        {e.edge > 0 ? '+' : ''}{(e.edge * 100).toFixed(1)}%
                      </span>
                    )}
                  </div>
                  <div style={{
                    fontSize: 13,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
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
          background: colors.bgCard,
          border: `1px solid ${colors.border}`,
          borderRadius: 8,
          overflow: 'auto',
          maxHeight: 'calc(100vh - 240px)',
          padding: 16,
        }}>
          {selectedId ? (
            <AnalysisDetail conditionId={selectedId} />
          ) : (
            <div style={{ color: colors.textDim, textAlign: 'center', marginTop: 80 }}>
              Select a market from the list to view analysis details
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
