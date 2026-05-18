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
        e.currentTarget.style.transform = 'translateY(-1px) scale(1.005)'
      }}
      onMouseLeave={e => {
        e.currentTarget.style.borderColor = colors.border
        e.currentTarget.style.boxShadow = 'none'
        e.currentTarget.style.transform = 'translateY(0) scale(1)'
      }}
    >
      {accent && (
        <div style={{
          position: 'absolute', top: 0, left: 0, right: 0, height: 2,
          background: `linear-gradient(90deg, transparent 5%, ${accent}88 30%, ${accent} 50%, ${accent}88 70%, transparent 95%)`,
          opacity: 0.7,
        }} />
      )}
      <div style={{
        position: 'absolute', top: 6, left: 6,
        width: 10, height: 10,
        borderTop: `1px solid ${accent || colors.borderLight}`,
        borderLeft: `1px solid ${accent || colors.borderLight}`,
        opacity: 0.35,
      }} />
      <h3 style={{
        margin: '0 0 16px', fontSize: 10, fontWeight: 600,
        color: colors.textMuted, textTransform: 'uppercase',
        letterSpacing: '0.12em', fontFamily: fonts.mono,
        display: 'flex', alignItems: 'center', gap: 7,
      }}>
        <span style={{
          width: 5, height: 5, borderRadius: 1,
          background: accent || colors.accent,
          boxShadow: accent ? `0 0 8px ${accent}` : `0 0 8px ${colors.accent}`,
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
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {[1, 2, 3].map(i => (
        <div key={i} style={{
          height: 12, borderRadius: 4,
          background: `linear-gradient(90deg, ${colors.border} 0%, rgba(0,229,255,0.06) 50%, ${colors.border} 100%)`,
          backgroundSize: '200% 100%',
          width: `${60 + i * 12}%`,
          animation: 'shimmer 2s ease-in-out infinite',
          animationDelay: `${i * 0.15}s`,
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

  const accentColor = rec.auto_applied ? colors.success : rec.confidence > 0.7 ? colors.accent : rec.confidence > 0.4 ? colors.warning : colors.textDim

  return (
    <div style={{
      padding: '12px 14px', borderRadius: 8,
      background: rec.auto_applied ? 'rgba(0,255,136,0.03)' : 'rgba(0,229,255,0.02)',
      border: `1px solid ${rec.auto_applied ? 'rgba(0,255,136,0.1)' : colors.border}`,
      borderLeft: `3px solid ${accentColor}`,
      marginBottom: 8,
      transition: 'background 0.25s ease, border-color 0.25s ease',
    }}
      onMouseEnter={e => { e.currentTarget.style.background = rec.auto_applied ? 'rgba(0,255,136,0.05)' : 'rgba(0,229,255,0.04)' }}
      onMouseLeave={e => { e.currentTarget.style.background = rec.auto_applied ? 'rgba(0,255,136,0.03)' : 'rgba(0,229,255,0.02)' }}>
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
      <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
        {[
          { value: `${data.mean_bias > 0 ? '+' : ''}${(data.mean_bias * 100).toFixed(1)}%`, label: 'Mean Bias', color: data.mean_bias > 0.05 ? colors.warning : colors.success },
          { value: `${(data.abs_mean_error * 100).toFixed(1)}%`, label: 'Abs Error', color: colors.textPrimary },
          { value: `${data.sample_count}`, label: 'Samples', color: colors.textPrimary },
        ].map((stat, i) => (
          <div key={stat.label} style={{
            flex: 1, padding: '10px 12px', borderRadius: 8,
            background: `${stat.color}06`,
            border: `1px solid ${stat.color}12`,
            textAlign: 'center',
          }}>
            <div style={{
              fontSize: 22, fontWeight: 700, fontFamily: fonts.mono, color: stat.color,
              textShadow: `0 0 16px ${stat.color}25`,
            }}>
              {stat.value}
            </div>
            <div style={{ fontSize: 9, color: colors.textDim, textTransform: 'uppercase', letterSpacing: '0.06em', marginTop: 3 }}>
              {stat.label}
            </div>
          </div>
        ))}
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
                  <div style={{ flex: 2, position: 'relative', height: 14, display: 'flex', alignItems: 'center' }}>
                    {/* Background track */}
                    <div style={{
                      position: 'absolute', left: 0, right: 0, height: 6, borderRadius: 3,
                      background: 'rgba(255,255,255,0.03)',
                    }} />
                    {/* Estimated bar */}
                    <div style={{
                      position: 'absolute', left: 0, height: 6, borderRadius: 3,
                      width: `${b.avg_estimated * 100}%`,
                      background: colors.accent, opacity: 0.45,
                    }} />
                    {/* Actual bar */}
                    <div style={{
                      position: 'absolute', left: 0, height: 6, borderRadius: 3,
                      width: `${b.avg_actual * 100}%`,
                      background: colors.success, opacity: 0.65,
                      boxShadow: `0 0 6px ${colors.success}20`,
                    }} />
                    {/* Estimated marker */}
                    <div style={{
                      position: 'absolute',
                      left: `${b.avg_estimated * 100}%`,
                      top: 0, width: 2, height: 14, borderRadius: 1,
                      background: colors.accent,
                      transform: 'translateX(-1px)',
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
        border: `1px dashed ${colors.borderLight}`, borderRadius: 8,
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
            padding: '10px 14px', borderRadius: 8,
            background: 'rgba(0,229,255,0.02)',
            border: `1px solid ${colors.border}`,
            borderLeft: `3px solid ${colors.warning}`,
            transition: 'background 0.25s ease',
            ...animDelay(i),
          }}
            onMouseEnter={e => { e.currentTarget.style.background = 'rgba(0,229,255,0.04)' }}
            onMouseLeave={e => { e.currentTarget.style.background = 'rgba(0,229,255,0.02)' }}>
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
        border: `1px dashed ${colors.borderLight}`, borderRadius: 8,
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
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, marginBottom: 16 }}>
        {[
          { value: data.total_skipped, label: 'Total Skipped', color: colors.textPrimary },
          { value: data.resolved_count, label: 'Resolved', color: colors.textPrimary },
          { value: data.missed_opportunities, label: 'Missed Opps', color: data.missed_opportunities > 0 ? colors.warning : colors.success },
          { value: `${missedRate}%`, label: 'Miss Rate', color: colors.textPrimary },
        ].map((stat) => (
          <div key={stat.label} style={{
            padding: '10px 8px', borderRadius: 8,
            background: `${stat.color}06`, border: `1px solid ${stat.color}12`,
            textAlign: 'center',
          }}>
            <div style={{
              fontSize: 20, fontWeight: 700, fontFamily: fonts.mono, color: stat.color,
              textShadow: `0 0 16px ${stat.color}25`,
            }}>
              {stat.value}
            </div>
            <div style={{ fontSize: 8, color: colors.textDim, textTransform: 'uppercase', letterSpacing: '0.06em', marginTop: 3 }}>
              {stat.label}
            </div>
          </div>
        ))}
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
  const [history, setHistory] = useState<LearningReport[]>([])
  const [runLoading, setRunLoading] = useState(false)
  const [runResult, setRunResult] = useState<string | null>(null)
  const [setOverrideParam, setSetOverrideParam] = useState('')
  const [setOverrideValue, setSetOverrideValue] = useState('')
  const [setOverrideReason, setSetOverrideReason] = useState('')
  const [setOverrideLoading, setSetOverrideLoading] = useState(false)

  const refresh = useCallback(() => {
    api.fetchLearningReport().then(setReport).catch(() => {})
    api.fetchLearningCalibration().then(setCalibration).catch(() => {})
    api.fetchSkipAnalysis().then(setSkipData).catch(() => {})
    api.fetchOverrides().then(setOverrides).catch(() => {})
    api.fetchLearningHistory(10).then(setHistory).catch(() => {})
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

  const handleSetOverride = async () => {
    if (!setOverrideParam || !setOverrideValue) return
    setSetOverrideLoading(true)
    try {
      await api.setOverride(setOverrideParam, parseFloat(setOverrideValue), setOverrideReason || 'manual override')
      setSetOverrideParam('')
      setSetOverrideValue('')
      setSetOverrideReason('')
      refresh()
    } catch (e) {
      console.error('Set override failed:', e)
    } finally {
      setSetOverrideLoading(false)
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
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* Header row: Data sufficiency + Run button */}
      <div style={{
        ...cardStyle, padding: '16px 24px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        background: `linear-gradient(135deg, rgba(10, 15, 30, 0.95) 0%, rgba(0, 229, 255, 0.02) 100%)`,
        borderBottom: `1px solid ${colors.borderLight}`,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 24 }}>
          <div>
            <div style={{ fontSize: 9, color: colors.textDim, fontFamily: fonts.mono, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
              Data Sufficiency
            </div>
            <div style={{
              fontSize: 18, fontWeight: 700, fontFamily: fonts.mono, color: suffColor, marginTop: 3,
              textShadow: `0 0 16px ${suffColor}33`,
            }}>
              {dataSufficiency.toUpperCase()}
            </div>
          </div>
          {report?.total_decisions != null && (
            <div style={{
              paddingLeft: 24, borderLeft: `1px solid ${colors.border}`,
            }}>
              <div style={{ fontSize: 9, color: colors.textDim, fontFamily: fonts.mono, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                Decisions
              </div>
              <div style={{ fontSize: 18, fontWeight: 700, fontFamily: fonts.mono, color: colors.textPrimary, marginTop: 3 }}>
                {report.total_decisions}
              </div>
            </div>
          )}
          {report?.resolved_decisions != null && (
            <div style={{
              paddingLeft: 24, borderLeft: `1px solid ${colors.border}`,
            }}>
              <div style={{ fontSize: 9, color: colors.textDim, fontFamily: fonts.mono, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                Resolved
              </div>
              <div style={{ fontSize: 18, fontWeight: 700, fontFamily: fonts.mono, color: colors.textPrimary, marginTop: 3 }}>
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
              padding: '10px 24px', borderRadius: 8, border: 'none', fontFamily: fonts.mono,
              background: runLoading ? colors.bgSecondary : colors.gradientAccent,
              color: runLoading ? colors.textMuted : '#000',
              cursor: runLoading ? 'wait' : 'pointer',
              fontSize: 11, fontWeight: 700,
              boxShadow: runLoading ? 'none' : '0 2px 16px rgba(0,229,255,0.25)',
              letterSpacing: '0.06em', textTransform: 'uppercase',
              transition: 'all 0.25s ease',
            }}
            onMouseEnter={e => { if (!runLoading) { e.currentTarget.style.transform = 'scale(1.03)'; e.currentTarget.style.boxShadow = '0 4px 24px rgba(0,229,255,0.35)' } }}
            onMouseLeave={e => { e.currentTarget.style.transform = 'scale(1)'; e.currentTarget.style.boxShadow = runLoading ? 'none' : '0 2px 16px rgba(0,229,255,0.25)' }}
          >
            {runLoading ? 'Running...' : 'Run Learning Cycle'}
          </button>
        </div>
      </div>

      {/* Main grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        {/* Recommendations */}
        <Card title={`Recommendations (${recommendations.length})`} accent={colors.purple} index={1}>
          {report == null ? (
            <Skeleton />
          ) : recommendations.length === 0 ? (
            <div style={{
              padding: 20, textAlign: 'center', color: colors.textDim,
              fontSize: 11, fontFamily: fonts.mono,
              border: `1px dashed ${colors.borderLight}`, borderRadius: 8,
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
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        {/* Active Overrides */}
        <Card title={`Active Overrides (${overrides.length})`} accent={colors.warning} index={3}>
          <OverridesTable overrides={overrides} onRevert={handleRevert} />

          {/* Manual override set form */}
          <div style={{
            marginTop: 12, paddingTop: 12,
            borderTop: `1px solid ${colors.border}`,
          }}>
            <div style={{
              fontSize: 9, color: colors.textDim, fontFamily: fonts.mono,
              textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8,
            }}>
              Set Manual Override
            </div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
              <select
                value={setOverrideParam}
                onChange={e => setSetOverrideParam(e.target.value)}
                style={{
                  background: colors.bgSecondary, color: colors.textPrimary,
                  border: `1px solid ${colors.border}`, borderRadius: 4,
                  padding: '6px 8px', fontSize: 11, fontFamily: fonts.mono,
                  outline: 'none', minWidth: 160,
                }}
              >
                <option value="">Select param...</option>
                {['MIN_EDGE_THRESHOLD', 'KELLY_FRACTION', 'MIN_CONFIDENCE_BLEND',
                  'MAX_SPREAD', 'MIN_MARKET_LIQUIDITY', 'MAX_POSITION_PCT',
                  'MAX_DAILY_LOSS_PCT', 'MAX_DRAWDOWN_PCT'].map(p => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
              <input
                type="number"
                placeholder="Value"
                value={setOverrideValue}
                onChange={e => setSetOverrideValue(e.target.value)}
                step="0.01"
                style={{
                  background: colors.bgSecondary, color: colors.textPrimary,
                  border: `1px solid ${colors.border}`, borderRadius: 4,
                  padding: '6px 8px', fontSize: 11, fontFamily: fonts.mono,
                  outline: 'none', width: 80,
                }}
              />
              <input
                type="text"
                placeholder="Reason"
                value={setOverrideReason}
                onChange={e => setSetOverrideReason(e.target.value)}
                style={{
                  background: colors.bgSecondary, color: colors.textPrimary,
                  border: `1px solid ${colors.border}`, borderRadius: 4,
                  padding: '6px 8px', fontSize: 11, fontFamily: fonts.body,
                  outline: 'none', flex: 1, minWidth: 100,
                }}
              />
              <button
                disabled={setOverrideLoading || !setOverrideParam || !setOverrideValue}
                onClick={handleSetOverride}
                style={{
                  padding: '6px 12px', borderRadius: 4, border: 'none',
                  background: !setOverrideParam || !setOverrideValue ? colors.bgSecondary : colors.warningDim,
                  color: !setOverrideParam || !setOverrideValue ? colors.textDim : colors.warning,
                  cursor: setOverrideLoading ? 'wait' : 'pointer',
                  fontSize: 10, fontWeight: 600, fontFamily: fonts.mono,
                  letterSpacing: '0.04em',
                }}
              >
                {setOverrideLoading ? '...' : 'SET'}
              </button>
            </div>
          </div>
        </Card>

        {/* Skip Analysis */}
        <Card title="Skip Analysis" accent={colors.danger} index={4}>
          {skipData == null ? <Skeleton /> : <SkipAnalysisPanel data={skipData} />}
        </Card>
      </div>

      {/* Learning History Trend */}
      {history.length > 0 && (
        <Card title={`Learning History (${history.length} reports)`} accent={colors.accent} index={5}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: fonts.mono }}>
              <thead>
                <tr>
                  {['Timestamp', 'Decisions', 'Resolved', 'Recommendations', 'Data Sufficiency'].map(h => (
                    <th key={h} style={{
                      padding: '6px 10px', textAlign: h === 'Timestamp' ? 'left' : 'right',
                      color: colors.textDim, fontWeight: 500, fontSize: 9,
                      textTransform: 'uppercase', letterSpacing: '0.06em',
                      borderBottom: `1px solid ${colors.border}`,
                    }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {history.map((h, i) => {
                  const rawSuff = h.data_sufficiency
                  let suffLabel = 'unknown'
                  if (typeof rawSuff === 'string') suffLabel = rawSuff
                  else if (rawSuff && typeof rawSuff === 'object') {
                    const vals = Object.values(rawSuff as Record<string, boolean>)
                    if (vals.every(Boolean)) suffLabel = 'sufficient'
                    else if (vals.some(Boolean)) suffLabel = 'partial'
                    else suffLabel = 'insufficient'
                  }
                  const suffColor = suffLabel === 'sufficient' ? colors.success
                    : suffLabel === 'partial' ? colors.warning : colors.textDim

                  return (
                    <tr key={i} style={{
                      borderBottom: `1px solid ${colors.border}`,
                      transition: 'background 0.2s ease',
                    }}
                      onMouseEnter={e => { e.currentTarget.style.background = 'rgba(0,229,255,0.02)' }}
                      onMouseLeave={e => { e.currentTarget.style.background = 'transparent' }}
                    >
                      <td style={{ padding: '6px 10px', color: colors.textMuted, fontSize: 10 }}>
                        {h.timestamp ? h.timestamp.replace('T', ' ').slice(0, 19) : '--'}
                      </td>
                      <td style={{ padding: '6px 10px', textAlign: 'right', color: colors.textSecondary }}>
                        {h.total_decisions ?? '--'}
                      </td>
                      <td style={{ padding: '6px 10px', textAlign: 'right', color: colors.textSecondary }}>
                        {h.resolved_decisions ?? '--'}
                      </td>
                      <td style={{ padding: '6px 10px', textAlign: 'right', color: (h.recommendations?.length ?? 0) > 0 ? colors.purple : colors.textDim }}>
                        {h.recommendations?.length ?? 0}
                      </td>
                      <td style={{ padding: '6px 10px', textAlign: 'right', color: suffColor, fontWeight: 600, fontSize: 10 }}>
                        {suffLabel.toUpperCase()}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}
