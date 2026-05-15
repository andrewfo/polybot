import { useState, useEffect, useCallback } from 'react'
import { colors, cardStyle, fonts, glowShadow, animDelay } from '../theme'
import {
  api, LearningReport, LearningRecommendation, CalibrationResponse,
  SkipAnalysis, ParameterOverride,
} from '../api'

// ---------------------------------------------------------------------------
// Shared atoms
// ---------------------------------------------------------------------------

function Card({ title, children, accent, style, index = 0 }: {
  title: string; children: React.ReactNode; accent?: string; style?: React.CSSProperties; index?: number
}) {
  return (
    <div style={{
      ...cardStyle,
      ...animDelay(index),
      ...style,
    }}
      onMouseEnter={e => {
        e.currentTarget.style.borderColor = accent || colors.borderHover
        e.currentTarget.style.boxShadow = glowShadow(accent || colors.accent, 0.08)
      }}
      onMouseLeave={e => {
        e.currentTarget.style.borderColor = colors.border
        e.currentTarget.style.boxShadow = 'none'
      }}
    >
      {accent && (
        <div style={{
          position: 'absolute', top: 0, left: 0, right: 0, height: 1,
          background: `linear-gradient(90deg, transparent, ${accent}, transparent)`,
          opacity: 0.6,
        }} />
      )}
      <div style={{
        position: 'absolute', top: 6, left: 6,
        width: 8, height: 8,
        borderTop: `1px solid ${accent || colors.borderLight}`,
        borderLeft: `1px solid ${accent || colors.borderLight}`,
        opacity: 0.4,
      }} />
      <h3 style={{
        margin: '0 0 14px', fontSize: 10, fontWeight: 600,
        color: colors.textMuted, textTransform: 'uppercase',
        letterSpacing: '0.1em', fontFamily: fonts.mono,
        display: 'flex', alignItems: 'center', gap: 6,
      }}>
        <span style={{
          width: 4, height: 4, borderRadius: 1,
          background: accent || colors.accent,
          boxShadow: accent ? `0 0 6px ${accent}` : `0 0 6px ${colors.accent}`,
        }} />
        {title}
      </h3>
      {children}
    </div>
  )
}

function PillBadge({ text, bg, fg }: { text: string; bg: string; fg?: string }) {
  return (
    <span style={{
      padding: '3px 10px', borderRadius: 20, fontSize: 10, fontWeight: 600,
      background: bg, color: fg || '#fff', letterSpacing: '0.04em',
      fontFamily: fonts.mono, border: `1px solid ${fg || '#fff'}15`,
    }}>
      {text}
    </span>
  )
}

function Skeleton() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {[1, 2, 3].map(i => (
        <div key={i} style={{
          height: 12, borderRadius: 3,
          background: `linear-gradient(90deg, ${colors.border} 0%, rgba(0,229,255,0.04) 50%, ${colors.border} 100%)`,
          backgroundSize: '200% 100%',
          width: `${60 + i * 12}%`,
          animation: 'shimmer 2s ease-in-out infinite',
        }} />
      ))}
    </div>
  )
}

const fmtPct = (v: number | null | undefined): string => {
  if (v == null) return '--'
  return (v * 100).toFixed(1) + '%'
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function RecommendationRow({ rec, onRevert }: { rec: LearningRecommendation; onRevert?: () => void }) {
  const delta = rec.recommended_value - rec.current_value
  const deltaColor = delta > 0 ? colors.success : delta < 0 ? colors.danger : colors.textMuted
  const isPercent = rec.current_value > 0 && rec.current_value < 1

  const formatVal = (v: number) => isPercent ? fmtPct(v) : v.toFixed(4)

  return (
    <div style={{
      padding: '10px 12px', borderRadius: 6,
      background: rec.auto_applied ? 'rgba(0,255,136,0.03)' : 'rgba(0,229,255,0.02)',
      border: `1px solid ${rec.auto_applied ? 'rgba(0,255,136,0.1)' : colors.border}`,
      marginBottom: 6,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{
            fontSize: 12, fontWeight: 600, color: colors.textPrimary,
            fontFamily: fonts.mono,
          }}>
            {rec.parameter.replace(/_/g, ' ')}
          </span>
          {rec.auto_applied && (
            <PillBadge text="AUTO-APPLIED" bg={colors.successDim} fg={colors.success} />
          )}
          <PillBadge
            text={`${rec.sample_count} samples`}
            bg={colors.accentDim}
            fg={colors.textDim}
          />
        </div>
        {onRevert && rec.auto_applied && (
          <button
            onClick={onRevert}
            style={{
              padding: '3px 10px', borderRadius: 4, border: `1px solid ${colors.danger}30`,
              background: colors.dangerDim, color: colors.danger,
              cursor: 'pointer', fontSize: 10, fontFamily: fonts.mono,
              fontWeight: 600, letterSpacing: '0.04em',
            }}
          >
            REVERT
          </button>
        )}
      </div>
      <div style={{ display: 'flex', gap: 16, alignItems: 'center', fontSize: 12, fontFamily: fonts.mono }}>
        <span style={{ color: colors.textDim }}>
          Current: <span style={{ color: colors.textSecondary }}>{formatVal(rec.current_value)}</span>
        </span>
        <span style={{ color: deltaColor, fontWeight: 600 }}>
          {delta > 0 ? '+' : ''}{isPercent ? (delta * 100).toFixed(2) + '%' : delta.toFixed(4)}
        </span>
        <span style={{ color: colors.textDim }}>
          Suggested: <span style={{ color: colors.accent, fontWeight: 600 }}>{formatVal(rec.recommended_value)}</span>
        </span>
        <span style={{ color: colors.textDim }}>
          conf: <span style={{ color: rec.confidence > 0.7 ? colors.success : rec.confidence > 0.4 ? colors.warning : colors.textDim }}>
            {fmtPct(rec.confidence)}
          </span>
        </span>
      </div>
      <div style={{
        fontSize: 11, color: colors.textSecondary, marginTop: 6,
        lineHeight: 1.4,
      }}>
        {rec.reason}
      </div>
    </div>
  )
}

function CalibrationChart({ data }: { data: CalibrationResponse }) {
  if (data.sample_count === 0) {
    return (
      <div style={{ padding: 20, textAlign: 'center', color: colors.textDim, fontSize: 12, fontFamily: fonts.mono }}>
        No calibration data yet (need resolved markets)
      </div>
    )
  }

  const buckets = data.calibration_curve || []
  const maxCount = Math.max(1, ...buckets.map(b => b.count))

  return (
    <div>
      {/* Summary stats */}
      <div style={{ display: 'flex', gap: 20, marginBottom: 14 }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 600, fontFamily: fonts.mono, color: data.mean_bias > 0.05 ? colors.warning : colors.success }}>
            {data.mean_bias > 0 ? '+' : ''}{(data.mean_bias * 100).toFixed(1)}%
          </div>
          <div style={{ fontSize: 9, color: colors.textDim, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Mean Bias</div>
        </div>
        <div>
          <div style={{ fontSize: 20, fontWeight: 600, fontFamily: fonts.mono, color: colors.textPrimary }}>
            {(data.abs_mean_error * 100).toFixed(1)}%
          </div>
          <div style={{ fontSize: 9, color: colors.textDim, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Abs Error</div>
        </div>
        <div>
          <div style={{ fontSize: 20, fontWeight: 600, fontFamily: fonts.mono, color: colors.textPrimary }}>
            {data.sample_count}
          </div>
          <div style={{ fontSize: 9, color: colors.textDim, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Samples</div>
        </div>
      </div>

      {/* Calibration curve — estimated vs actual */}
      {buckets.length > 0 && (
        <div>
          <div style={{
            fontSize: 9, color: colors.textDim, fontFamily: fonts.mono,
            textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8,
          }}>
            Calibration Curve (estimated vs actual)
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            {buckets.map((b, i) => {
              const biasColor = Math.abs(b.bias) < 0.05 ? colors.success
                : Math.abs(b.bias) < 0.1 ? colors.warning : colors.danger
              return (
                <div key={b.bucket} style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '4px 0',
                  ...animDelay(i),
                }}>
                  <span style={{
                    width: 70, fontSize: 10, fontFamily: fonts.mono,
                    color: colors.textMuted, textAlign: 'right',
                  }}>
                    {b.bucket}
                  </span>
                  <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 4 }}>
                    {/* Estimated bar */}
                    <div style={{
                      height: 6, borderRadius: 3,
                      width: `${b.avg_estimated * 100}%`,
                      background: colors.accent, opacity: 0.5,
                    }} />
                  </div>
                  <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 4 }}>
                    {/* Actual bar */}
                    <div style={{
                      height: 6, borderRadius: 3,
                      width: `${b.avg_actual * 100}%`,
                      background: colors.success, opacity: 0.7,
                    }} />
                  </div>
                  <span style={{
                    width: 50, fontSize: 10, fontFamily: fonts.mono,
                    color: biasColor, fontWeight: 600, textAlign: 'right',
                  }}>
                    {b.bias > 0 ? '+' : ''}{(b.bias * 100).toFixed(1)}%
                  </span>
                  <span style={{
                    width: 30, fontSize: 9, fontFamily: fonts.mono,
                    color: colors.textDim, textAlign: 'right',
                  }}>
                    n={b.count}
                  </span>
                </div>
              )
            })}
          </div>
          <div style={{
            display: 'flex', gap: 16, marginTop: 6, fontSize: 9, color: colors.textDim,
          }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ width: 12, height: 4, background: colors.accent, opacity: 0.5, borderRadius: 2 }} />
              Estimated
            </span>
            <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ width: 12, height: 4, background: colors.success, opacity: 0.7, borderRadius: 2 }} />
              Actual
            </span>
          </div>
        </div>
      )}

      {/* Bias by confidence band */}
      {data.bias_by_confidence && Object.keys(data.bias_by_confidence).length > 0 && (
        <div style={{ marginTop: 14 }}>
          <div style={{
            fontSize: 9, color: colors.textDim, fontFamily: fonts.mono,
            textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6,
          }}>
            Bias by Confidence Band
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {Object.entries(data.bias_by_confidence).map(([band, info]) => {
              const biasColor = Math.abs(info.mean_bias) < 0.05 ? colors.success
                : Math.abs(info.mean_bias) < 0.1 ? colors.warning : colors.danger
              return (
                <div key={band} style={{
                  padding: '6px 10px', borderRadius: 6,
                  background: 'rgba(0,0,0,0.2)', border: `1px solid ${colors.border}`,
                  textAlign: 'center',
                }}>
                  <div style={{ fontSize: 9, color: colors.textDim, marginBottom: 2 }}>{band}</div>
                  <div style={{ fontSize: 14, fontWeight: 600, fontFamily: fonts.mono, color: biasColor }}>
                    {info.mean_bias > 0 ? '+' : ''}{(info.mean_bias * 100).toFixed(1)}%
                  </div>
                  <div style={{ fontSize: 8, color: colors.textDim }}>n={info.count}</div>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

function OverridesTable({ overrides, onRevert }: {
  overrides: ParameterOverride[]
  onRevert: (param: string) => void
}) {
  if (overrides.length === 0) {
    return (
      <div style={{
        padding: 20, textAlign: 'center', color: colors.textDim,
        fontSize: 11, fontFamily: fonts.mono,
        border: `1px dashed ${colors.border}`, borderRadius: 6,
      }}>
        No active parameter overrides
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {overrides.map((o, i) => {
        const delta = o.current_value - o.original_value
        const isPercent = o.original_value > 0 && o.original_value < 1
        const formatVal = (v: number) => isPercent ? fmtPct(v) : v.toFixed(4)
        const deltaColor = delta > 0 ? colors.success : delta < 0 ? colors.danger : colors.textMuted

        return (
          <div key={o.parameter} style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '8px 12px', borderRadius: 6,
            background: 'rgba(0,229,255,0.02)',
            border: `1px solid ${colors.border}`,
            ...animDelay(i),
          }}>
            <div style={{ flex: 1 }}>
              <div style={{
                fontSize: 12, fontWeight: 600, fontFamily: fonts.mono,
                color: colors.textPrimary, marginBottom: 2,
              }}>
                {o.parameter.replace(/_/g, ' ')}
              </div>
              <div style={{ display: 'flex', gap: 12, fontSize: 10, fontFamily: fonts.mono, color: colors.textDim }}>
                <span>Default: {formatVal(o.original_value)}</span>
                <span style={{ color: deltaColor, fontWeight: 600 }}>
                  Active: {formatVal(o.current_value)}
                </span>
                <span>conf: {fmtPct(o.confidence)}</span>
              </div>
              <div style={{ fontSize: 10, color: colors.textDim, marginTop: 2 }}>
                {o.reason}
              </div>
            </div>
            <button
              onClick={() => onRevert(o.parameter)}
              style={{
                padding: '4px 12px', borderRadius: 4, border: `1px solid ${colors.danger}30`,
                background: colors.dangerDim, color: colors.danger,
                cursor: 'pointer', fontSize: 10, fontFamily: fonts.mono,
                fontWeight: 600, flexShrink: 0, marginLeft: 12,
              }}
            >
              REVERT
            </button>
          </div>
        )
      })}
    </div>
  )
}

function SkipAnalysisPanel({ data }: { data: SkipAnalysis }) {
  if (data.total_skipped === 0) {
    return (
      <div style={{
        padding: 20, textAlign: 'center', color: colors.textDim,
        fontSize: 11, fontFamily: fonts.mono,
        border: `1px dashed ${colors.border}`, borderRadius: 6,
      }}>
        No skipped markets to analyze yet
      </div>
    )
  }

  const missedRate = data.resolved_count > 0
    ? (data.missed_opportunities / data.resolved_count * 100).toFixed(0)
    : '0'

  return (
    <div>
      {/* Summary stats */}
      <div style={{ display: 'flex', gap: 20, marginBottom: 14 }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 600, fontFamily: fonts.mono, color: colors.textPrimary }}>
            {data.total_skipped}
          </div>
          <div style={{ fontSize: 9, color: colors.textDim, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Total Skipped</div>
        </div>
        <div>
          <div style={{ fontSize: 20, fontWeight: 600, fontFamily: fonts.mono, color: colors.textPrimary }}>
            {data.resolved_count}
          </div>
          <div style={{ fontSize: 9, color: colors.textDim, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Resolved</div>
        </div>
        <div>
          <div style={{
            fontSize: 20, fontWeight: 600, fontFamily: fonts.mono,
            color: data.missed_opportunities > 0 ? colors.warning : colors.success,
          }}>
            {data.missed_opportunities}
          </div>
          <div style={{ fontSize: 9, color: colors.textDim, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Missed Opps</div>
        </div>
        <div>
          <div style={{ fontSize: 20, fontWeight: 600, fontFamily: fonts.mono, color: colors.textPrimary }}>
            {missedRate}%
          </div>
          <div style={{ fontSize: 9, color: colors.textDim, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Miss Rate</div>
        </div>
      </div>

      {/* Avg missed edge */}
      {data.avg_missed_edge > 0 && (
        <div style={{
          padding: '8px 12px', borderRadius: 6, marginBottom: 10,
          background: 'rgba(255,170,0,0.04)', border: `1px solid rgba(255,170,0,0.1)`,
          fontSize: 12, fontFamily: fonts.mono, color: colors.warning,
        }}>
          Avg missed edge: {fmtPct(data.avg_missed_edge)}
        </div>
      )}

      {/* Top skip reasons */}
      {data.top_missed_reasons && Object.keys(data.top_missed_reasons).length > 0 && (
        <div>
          <div style={{
            fontSize: 9, color: colors.textDim, fontFamily: fonts.mono,
            textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6,
          }}>
            Top Skip Reasons (missed opportunities)
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            {Object.entries(data.top_missed_reasons)
              .sort(([, a], [, b]) => b - a)
              .slice(0, 8)
              .map(([reason, count], i) => (
                <div key={reason} style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '4px 8px', borderRadius: 4,
                  background: 'rgba(0,0,0,0.2)',
                  ...animDelay(i),
                }}>
                  <span style={{
                    width: 24, fontSize: 11, fontFamily: fonts.mono,
                    color: colors.warning, fontWeight: 600, textAlign: 'right',
                  }}>
                    {count}
                  </span>
                  <span style={{
                    fontSize: 11, color: colors.textSecondary,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    {reason}
                  </span>
                </div>
              ))}
          </div>
        </div>
      )}

      {/* Recommendation */}
      {data.recommendation && (
        <div style={{
          marginTop: 10, padding: '8px 12px', borderRadius: 6,
          background: colors.accentDim, border: `1px solid ${colors.border}`,
          fontSize: 11, color: colors.textSecondary, lineHeight: 1.4,
        }}>
          {data.recommendation}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function Learning() {
  const [report, setReport] = useState<LearningReport | null>(null)
  const [calibration, setCalibration] = useState<CalibrationResponse | null>(null)
  const [skipData, setSkipData] = useState<SkipAnalysis | null>(null)
  const [overrides, setOverrides] = useState<ParameterOverride[]>([])
  const [runLoading, setRunLoading] = useState(false)
  const [runResult, setRunResult] = useState<string | null>(null)

  const refresh = useCallback(() => {
    api.fetchLearningReport().then(setReport).catch(() => {})
    api.fetchLearningCalibration().then(setCalibration).catch(() => {})
    api.fetchSkipAnalysis().then(setSkipData).catch(() => {})
    api.fetchOverrides().then(setOverrides).catch(() => {})
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 30000)
    return () => clearInterval(id)
  }, [refresh])

  const handleRunCycle = async () => {
    setRunLoading(true)
    setRunResult(null)
    try {
      const result = await api.runLearningCycle()
      setRunResult(`Learning cycle complete: ${result.recommendations} recommendations`)
      refresh()
    } catch (e) {
      setRunResult(`Error: ${e instanceof Error ? e.message : 'Unknown'}`)
    } finally {
      setRunLoading(false)
    }
  }

  const handleRevert = async (param: string) => {
    try {
      await api.revertOverride(param)
      refresh()
    } catch (e) {
      console.error('Revert failed:', e)
    }
  }

  const recommendations = report?.recommendations ?? []

  // data_sufficiency is a dict {analysis_name: bool} from the backend, or a string for the no_data stub
  const rawSuff = report?.data_sufficiency
  let dataSufficiency = 'unknown'
  if (typeof rawSuff === 'string') {
    dataSufficiency = rawSuff
  } else if (rawSuff && typeof rawSuff === 'object') {
    const vals = Object.values(rawSuff as Record<string, boolean>)
    if (vals.length === 0) dataSufficiency = 'unknown'
    else if (vals.every(Boolean)) dataSufficiency = 'sufficient'
    else if (vals.some(Boolean)) dataSufficiency = 'partial'
    else dataSufficiency = 'insufficient'
  }
  const suffColor = dataSufficiency === 'sufficient' ? colors.success
    : dataSufficiency === 'partial' ? colors.warning : colors.textDim

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* Header row: Data sufficiency + Run button */}
      <div style={{
        ...cardStyle, padding: '12px 20px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div>
            <div style={{ fontSize: 10, color: colors.textDim, fontFamily: fonts.mono, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
              Data Sufficiency
            </div>
            <div style={{ fontSize: 16, fontWeight: 600, fontFamily: fonts.mono, color: suffColor, marginTop: 2 }}>
              {dataSufficiency.toUpperCase()}
            </div>
          </div>
          {report?.total_decisions != null && (
            <div>
              <div style={{ fontSize: 10, color: colors.textDim, fontFamily: fonts.mono, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                Decisions
              </div>
              <div style={{ fontSize: 16, fontWeight: 600, fontFamily: fonts.mono, color: colors.textPrimary, marginTop: 2 }}>
                {report.total_decisions}
              </div>
            </div>
          )}
          {report?.resolved_decisions != null && (
            <div>
              <div style={{ fontSize: 10, color: colors.textDim, fontFamily: fonts.mono, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                Resolved
              </div>
              <div style={{ fontSize: 16, fontWeight: 600, fontFamily: fonts.mono, color: colors.textPrimary, marginTop: 2 }}>
                {report.resolved_decisions}
              </div>
            </div>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {runResult && (
            <span style={{
              fontSize: 10, fontFamily: fonts.mono,
              color: runResult.startsWith('Error') ? colors.danger : colors.success,
            }}>
              {runResult}
            </span>
          )}
          <button
            disabled={runLoading}
            onClick={handleRunCycle}
            style={{
              padding: '8px 20px', borderRadius: 6, border: 'none', fontFamily: fonts.mono,
              background: runLoading ? colors.bgSecondary : colors.gradientAccent,
              color: runLoading ? colors.textMuted : '#000',
              cursor: runLoading ? 'wait' : 'pointer',
              fontSize: 11, fontWeight: 600,
              boxShadow: runLoading ? 'none' : '0 2px 12px rgba(0,229,255,0.2)',
              letterSpacing: '0.06em', textTransform: 'uppercase',
            }}
          >
            {runLoading ? 'Running...' : 'Run Learning Cycle'}
          </button>
        </div>
      </div>

      {/* Main grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        {/* Recommendations */}
        <Card title={`Recommendations (${recommendations.length})`} accent={colors.purple} index={1}>
          {report == null ? (
            <Skeleton />
          ) : recommendations.length === 0 ? (
            <div style={{
              padding: 20, textAlign: 'center', color: colors.textDim,
              fontSize: 11, fontFamily: fonts.mono,
              border: `1px dashed ${colors.border}`, borderRadius: 6,
            }}>
              {report.status === 'no_data'
                ? 'No learning data yet. Run a learning cycle after some trades resolve.'
                : 'No recommendations at this time'}
            </div>
          ) : (
            <div>
              {recommendations.map((rec, i) => (
                <RecommendationRow
                  key={rec.parameter}
                  rec={rec}
                  onRevert={() => handleRevert(rec.parameter)}
                />
              ))}
            </div>
          )}
        </Card>

        {/* Calibration */}
        <Card title="Frontier Calibration" accent={colors.accent} index={2}>
          {calibration == null ? <Skeleton /> : <CalibrationChart data={calibration} />}
        </Card>
      </div>

      {/* Bottom row */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        {/* Active Overrides */}
        <Card title={`Active Overrides (${overrides.length})`} accent={colors.warning} index={3}>
          <OverridesTable overrides={overrides} onRevert={handleRevert} />
        </Card>

        {/* Skip Analysis */}
        <Card title="Skip Analysis" accent={colors.danger} index={4}>
          {skipData == null ? <Skeleton /> : <SkipAnalysisPanel data={skipData} />}
        </Card>
      </div>
    </div>
  )
}
