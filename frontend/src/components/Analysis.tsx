import { useState, useEffect, useCallback } from 'react'
import { colors, cardStyle, fonts, glowShadow } from '../theme'
import { api, AnalysisSummary } from '../api'
import AnalysisDetail from './AnalysisDetail'

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { bg: string; fg: string; glow?: boolean }> = {
    done: { bg: 'rgba(0,255,136,0.1)', fg: '#00ff88' },
    processing: { bg: 'rgba(0,229,255,0.1)', fg: '#00e5ff', glow: true },
    error: { bg: 'rgba(255,51,102,0.1)', fg: '#ff3366' },
    skipped: { bg: 'rgba(85,102,136,0.1)', fg: '#556688' },
    waiting: { bg: 'rgba(51,68,102,0.1)', fg: '#334466' },
  }
  const s = map[status] || map.waiting
  return (
    <span style={{
      padding: '2px 8px', borderRadius: 3, fontSize: 9, fontWeight: 600,
      background: s.bg, color: s.fg, letterSpacing: '0.06em',
      fontFamily: fonts.mono, textTransform: 'uppercase',
      border: `1px solid ${s.fg}15`,
      animation: s.glow ? 'textGlow 2s ease-in-out infinite' : 'none',
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
      padding: '2px 8px', borderRadius: 3, fontSize: 9, fontWeight: 600,
      background: isTrade ? 'rgba(0,255,136,0.1)' : 'rgba(255,170,0,0.1)',
      color: isTrade ? '#00ff88' : '#ffaa00',
      fontFamily: fonts.mono, textTransform: 'uppercase',
      letterSpacing: '0.06em',
      border: `1px solid ${isTrade ? 'rgba(0,255,136,0.15)' : 'rgba(255,170,0,0.15)'}`,
      textShadow: isTrade ? '0 0 8px rgba(0,255,136,0.3)' : 'none',
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
  const [sigTestQuestion, setSigTestQuestion] = useState('')
  const [sigTestLoading, setSigTestLoading] = useState(false)
  const [sigTestResults, setSigTestResults] = useState<Array<{ source: string; probability: number | null; confidence: number; reasoning: string }> | null>(null)

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

  const handleSignalTest = async () => {
    if (!sigTestQuestion.trim()) return
    setSigTestLoading(true)
    setSigTestResults(null)
    try {
      const result = await api.runSignalTest(sigTestQuestion.trim())
      setSigTestResults(result.signals)
    } catch (e) {
      setSigTestResults([])
    } finally {
      setSigTestLoading(false)
    }
  }

  const inputStyle: React.CSSProperties = {
    padding: '8px 12px', borderRadius: 4,
    border: `1px solid ${colors.border}`,
    background: colors.bgSecondary,
    color: colors.textPrimary,
    fontSize: 12, fontFamily: fonts.body, outline: 'none',
    transition: 'border-color 0.2s, box-shadow 0.2s',
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* Aggregate command form */}
      <div style={{
        ...cardStyle, padding: 12,
        display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap',
      }}>
        <input
          type="text"
          placeholder="Market question (e.g. Will BTC reach $100k?)"
          value={aggQuestion}
          onChange={e => setAggQuestion(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !aggLoading) handleAggregate() }}
          style={{ ...inputStyle, flex: 1, minWidth: 250 }}
        />
        <input
          type="number"
          placeholder="Price"
          value={aggPrice}
          onChange={e => setAggPrice(e.target.value)}
          min="0" max="1" step="0.01"
          style={{ ...inputStyle, width: 90, fontFamily: fonts.mono }}
        />
        <button
          disabled={aggLoading || !aggQuestion.trim()}
          onClick={handleAggregate}
          style={{
            padding: '8px 20px', borderRadius: 4, border: 'none', fontFamily: fonts.mono,
            background: aggLoading ? colors.bgSecondary : colors.gradientAccent,
            color: aggLoading ? colors.textMuted : '#000',
            cursor: aggLoading ? 'wait' : 'pointer',
            fontSize: 11, fontWeight: 600,
            boxShadow: aggLoading ? 'none' : '0 2px 12px rgba(0,229,255,0.2)',
            transition: 'all 0.3s',
            opacity: !aggQuestion.trim() ? 0.5 : 1,
            letterSpacing: '0.06em',
            textTransform: 'uppercase',
          }}
        >
          {aggLoading ? 'Running...' : 'Run Aggregate'}
        </button>
        {aggError && (
          <span style={{ color: colors.danger, fontSize: 11, fontFamily: fonts.mono }}>{aggError}</span>
        )}
      </div>

      {/* Signal Test form */}
      <div style={{
        ...cardStyle, padding: 12,
        display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap',
      }}>
        <input
          type="text"
          placeholder="Signal test: test individual providers (no frontier model)"
          value={sigTestQuestion}
          onChange={e => setSigTestQuestion(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !sigTestLoading) handleSignalTest() }}
          style={{ ...inputStyle, flex: 1, minWidth: 250 }}
        />
        <button
          disabled={sigTestLoading || !sigTestQuestion.trim()}
          onClick={handleSignalTest}
          style={{
            padding: '8px 20px', borderRadius: 4, border: `1px solid ${colors.border}`,
            background: sigTestLoading ? colors.bgSecondary : 'rgba(139,92,246,0.1)',
            color: sigTestLoading ? colors.textMuted : colors.purple,
            cursor: sigTestLoading ? 'wait' : 'pointer',
            fontSize: 11, fontWeight: 600, fontFamily: fonts.mono,
            transition: 'all 0.3s',
            opacity: !sigTestQuestion.trim() ? 0.5 : 1,
            letterSpacing: '0.06em',
            textTransform: 'uppercase',
          }}
        >
          {sigTestLoading ? 'Testing...' : 'Test Signals'}
        </button>
      </div>

      {/* Signal test results */}
      {sigTestResults && sigTestResults.length > 0 && (
        <div style={{ ...cardStyle, padding: 12 }}>
          <div style={{
            fontSize: 10, color: colors.textMuted, fontFamily: fonts.mono,
            textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8,
          }}>
            Signal Test Results
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {sigTestResults.map((s, i) => (
              <div key={i} style={{
                display: 'flex', alignItems: 'center', gap: 10, padding: '6px 8px',
                borderRadius: 6, background: 'rgba(0,0,0,0.2)',
                borderLeft: `3px solid ${s.probability != null ? colors.accent : colors.textDim}`,
              }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: colors.textPrimary, minWidth: 120 }}>
                  {s.source.replace(/_/g, ' ')}
                </span>
                <span style={{ fontFamily: fonts.mono, fontSize: 13, fontWeight: 700, color: s.probability != null ? colors.accent : colors.textDim, minWidth: 50 }}>
                  {s.probability != null ? (s.probability * 100).toFixed(1) + '%' : '--'}
                </span>
                <span style={{
                  fontSize: 10, padding: '1px 6px', borderRadius: 10, fontFamily: fonts.mono,
                  background: s.confidence > 0.5 ? colors.successDim : s.confidence > 0.25 ? colors.warningDim : colors.dangerDim,
                  color: s.confidence > 0.5 ? colors.success : s.confidence > 0.25 ? colors.warning : colors.danger,
                }}>
                  conf {(s.confidence * 100).toFixed(0)}%
                </span>
                <span style={{ fontSize: 11, color: colors.textSecondary, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {s.reasoning}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Main split view */}
      <div style={{ display: 'grid', gridTemplateColumns: '40% 60%', gap: 14, minHeight: 500 }}>
        {/* Left: List */}
        <div style={{
          ...cardStyle, padding: 0, overflow: 'auto',
          maxHeight: 'calc(100vh - 260px)',
        }}>
          {entries.length === 0 ? (
            <div style={{ padding: 32, textAlign: 'center', color: colors.textDim }}>
              <div style={{
                fontSize: 28, marginBottom: 8, opacity: 0.2,
                fontFamily: fonts.mono, animation: 'textGlow 4s ease-in-out infinite',
              }}>
                ~
              </div>
              <div style={{ fontSize: 13, marginBottom: 4 }}>No analysis data yet</div>
              <div style={{ fontSize: 11, fontFamily: fonts.mono, letterSpacing: '0.02em' }}>
                Start the bot or run a manual aggregate above.
              </div>
            </div>
          ) : (
            <div>
              {entries.map((e, i) => (
                <div
                  key={e.condition_id}
                  onClick={() => setSelectedId(e.condition_id)}
                  style={{
                    padding: '12px 14px',
                    borderBottom: `1px solid ${colors.border}`,
                    cursor: 'pointer',
                    background: selectedId === e.condition_id ? 'rgba(0, 229, 255, 0.04)' : 'transparent',
                    transition: 'all 0.2s',
                    borderLeft: selectedId === e.condition_id ? `2px solid ${colors.accent}` : '2px solid transparent',
                  }}
                  onMouseEnter={e2 => {
                    if (selectedId !== e.condition_id) e2.currentTarget.style.background = 'rgba(0, 229, 255, 0.02)'
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
                        fontSize: 10, fontWeight: 600,
                        fontFamily: fonts.mono,
                        color: e.edge > 0 ? colors.success : colors.danger,
                        textShadow: `0 0 8px ${e.edge > 0 ? colors.success : colors.danger}30`,
                      }}>
                        {e.edge > 0 ? '+' : ''}{(e.edge * 100).toFixed(1)}%
                      </span>
                    )}
                  </div>
                  <div style={{
                    fontSize: 12, overflow: 'hidden',
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
              <div style={{
                fontSize: 24, marginBottom: 8, opacity: 0.2,
                fontFamily: fonts.mono,
              }}>
                &#x2190;
              </div>
              <div style={{ fontSize: 12 }}>Select a market from the list to view analysis details</div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
