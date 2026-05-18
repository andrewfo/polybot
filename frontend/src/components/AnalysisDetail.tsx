import { useState, useEffect } from 'react'
import { colors, fonts } from '../theme'
import { api, AnalysisDetail as AnalysisDetailType } from '../api'
import ProbabilityBars from './charts/ProbabilityBars'
import VolComparison from './charts/VolComparison'
import PriceChart from './charts/PriceChart'
import SignalWeights from './charts/SignalWeights'
import EdgeWaterfall from './charts/EdgeWaterfall'
import ConfidenceGauge from './charts/ConfidenceGauge'
import SignalRadar from './charts/SignalRadar'
import DecisionPipeline from './charts/DecisionPipeline'
import DepthLadder from './charts/DepthLadder'
import CrossPlatformBars from './charts/CrossPlatformBars'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function SectionHeader({ title, collapsed, onToggle, badge }: { title: string; collapsed?: boolean; onToggle?: () => void; badge?: string }) {
  return (
    <h3
      onClick={onToggle}
      style={{
        margin: '20px 0 10px', fontSize: 10, fontWeight: 600,
        color: colors.textMuted, textTransform: 'uppercase',
        letterSpacing: '0.1em',
        borderBottom: `1px solid ${colors.border}`,
        paddingBottom: 8,
        cursor: onToggle ? 'pointer' : 'default',
        userSelect: 'none',
        display: 'flex', alignItems: 'center', gap: 6,
        fontFamily: fonts.mono,
      }}
    >
      {onToggle && <span style={{ fontSize: 9, transition: 'transform 0.2s', transform: collapsed ? 'rotate(-90deg)' : 'rotate(0)' }}>&#9660;</span>}
      <span style={{ width: 3, height: 3, borderRadius: 1, background: colors.accent, boxShadow: `0 0 4px ${colors.accent}` }} />
      {title}
      {badge && (
        <span style={{
          fontSize: 9, padding: '1px 6px', borderRadius: 3,
          background: colors.accentDim, color: colors.textDim, fontWeight: 500,
          marginLeft: 4, fontFamily: fonts.mono, border: `1px solid ${colors.border}`,
        }}>{badge}</span>
      )}
    </h3>
  )
}

function Badge({ text, color: fg }: { text: string; color: string }) {
  return (
    <span style={{
      padding: '3px 10px', borderRadius: 3, fontSize: 10, fontWeight: 600,
      background: fg + '15', color: fg, fontFamily: fonts.mono,
      letterSpacing: '0.04em', border: `1px solid ${fg}20`,
      textShadow: `0 0 8px ${fg}30`,
    }}>
      {text}
    </span>
  )
}

function Stat({ label, value, highlight, mono, small }: { label: string; value: string; highlight?: string; mono?: boolean; small?: boolean }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span style={{
        fontSize: small ? 9 : 10, color: colors.textDim, textTransform: 'uppercase',
        letterSpacing: '0.06em', fontFamily: fonts.mono,
      }}>
        {label}
      </span>
      <span style={{
        fontSize: small ? 11 : 13, fontWeight: 600, color: highlight || colors.textPrimary,
        fontFamily: mono !== false ? fonts.mono : fonts.body,
        textShadow: highlight ? `0 0 12px ${highlight}25` : 'none',
      }}>
        {value}
      </span>
    </div>
  )
}

function MetricRow({ label, value, highlight, detail }: { label: string; value: string; highlight?: string; detail?: string }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '5px 0', borderBottom: `1px solid ${colors.border}`, fontSize: 12,
    }}>
      <span style={{ color: colors.textMuted }}>{label}</span>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        {detail && <span style={{ fontSize: 10, color: colors.textDim }}>{detail}</span>}
        <span style={{
          fontWeight: 600, color: highlight || colors.textPrimary,
          fontFamily: fonts.mono, fontSize: 12,
        }}>
          {value}
        </span>
      </div>
    </div>
  )
}

function InfoBox({ children, accent }: { children: React.ReactNode; accent?: string }) {
  return (
    <div style={{
      background: 'rgba(6, 10, 20, 0.6)', border: `1px solid ${accent ? accent + '20' : colors.border}`,
      borderRadius: 6, padding: 12, marginBottom: 8,
      backdropFilter: 'blur(8px)',
    }}>
      {children}
    </div>
  )
}

const fmt = (v: unknown, decimals = 2): string => {
  if (v == null) return '--'
  const n = typeof v === 'number' ? v : parseFloat(String(v))
  return isNaN(n) ? '--' : n.toFixed(decimals)
}

const fmtPct = (v: unknown): string => {
  if (v == null) return '--'
  const n = typeof v === 'number' ? v : parseFloat(String(v))
  return isNaN(n) ? '--' : (n * 100).toFixed(1) + '%'
}

const fmtUsd = (v: unknown): string => {
  if (v == null) return '--'
  const n = typeof v === 'number' ? v : parseFloat(String(v))
  return isNaN(n) ? '--' : '$' + n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

// ---------------------------------------------------------------------------
// Sub-components for each signal source
// ---------------------------------------------------------------------------

function CryptoSignalDetail({ raw }: { raw: Record<string, unknown> }) {
  const distance = raw.distance_pct as number | undefined
  const trend = raw.trend as string | undefined
  const trendColor = trend === 'upward' ? colors.success : trend === 'downward' ? colors.danger : colors.textMuted

  const barrier = raw.barrier_prob as number | undefined
  const terminal = raw.terminal_prob as number | undefined
  const selected = raw.model_prob as number | undefined
  const resType = raw.resolution_type as string | undefined

  // Volatility data for chart
  const volData: Record<string, number> = {}
  if (raw.historical_vol != null) volData.historical = raw.historical_vol as number
  if (raw.ewm_vol != null) volData.ewm = raw.ewm_vol as number
  if (raw.short_term_vol != null) volData.short_term = raw.short_term_vol as number
  if (raw.deribit_iv != null) volData.deribit_iv = raw.deribit_iv as number
  if (raw.annualized_vol != null) volData.selected = raw.annualized_vol as number

  // Drift
  const realized = raw.realized_drift as number | undefined
  const shrunk = raw.shrunk_drift as number | undefined
  const stderr = raw.drift_stderr as number | undefined
  const shrinkPct = realized != null && shrunk != null && realized !== 0
    ? ((1 - Math.abs(shrunk) / Math.abs(realized)) * 100) : null

  return (
    <div>
      {/* Coin Overview */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))', gap: 8, marginBottom: 10 }}>
        <Stat label="Coin" value={String(raw.coin_id || '--').toUpperCase()} mono={false} />
        <Stat label="Current" value={fmtUsd(raw.current_price)} />
        <Stat label="Target" value={fmtUsd(raw.target_price)} />
        <Stat label="Direction" value={String(raw.target_direction || '--')} highlight={
          raw.target_direction === 'above' ? colors.success : colors.danger
        } />
        <Stat label="Distance" value={fmtPct(distance != null ? distance / 100 : null)} highlight={
          distance != null && Math.abs(distance) < 5 ? colors.warning : undefined
        } />
        <Stat label="24h Change" value={fmtPct(raw.change_24h)} highlight={
          (raw.change_24h as number) > 0 ? colors.success : colors.danger
        } />
        <Stat label="Trend" value={String(trend || '--')} highlight={trendColor} />
        <Stat label="Days Left" value={raw.days_remaining != null ? fmt(raw.days_remaining, 1) + 'd' : '--'} />
      </div>

      {/* Probability Models — side by side comparison */}
      {(barrier != null || terminal != null) && (
        <InfoBox>
          <div style={{ fontSize: 10, fontWeight: 600, color: colors.textMuted, marginBottom: 8, textTransform: 'uppercase' }}>
            Probability Models
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
            <div style={{
              padding: 8, borderRadius: 6, textAlign: 'center',
              background: resType === 'barrier' ? '#f59e0b11' : 'transparent',
              border: resType === 'barrier' ? '1px solid #f59e0b33' : `1px solid ${colors.border}`,
            }}>
              <div style={{ fontSize: 9, color: colors.textDim, marginBottom: 4 }}>BARRIER (touch)</div>
              <div style={{
                fontSize: 20, fontWeight: 700, fontFamily: fonts.mono,
                color: resType === 'barrier' ? '#f59e0b' : colors.textMuted,
              }}>
                {barrier != null ? (barrier * 100).toFixed(1) + '%' : '--'}
              </div>
              {resType === 'barrier' && <div style={{ fontSize: 8, color: '#f59e0b', marginTop: 2 }}>ACTIVE</div>}
            </div>
            <div style={{
              padding: 8, borderRadius: 6, textAlign: 'center',
              background: resType === 'terminal' ? '#8b5cf611' : 'transparent',
              border: resType === 'terminal' ? '1px solid #8b5cf633' : `1px solid ${colors.border}`,
            }}>
              <div style={{ fontSize: 9, color: colors.textDim, marginBottom: 4 }}>TERMINAL (expiry)</div>
              <div style={{
                fontSize: 20, fontWeight: 700, fontFamily: fonts.mono,
                color: resType === 'terminal' ? '#8b5cf6' : colors.textMuted,
              }}>
                {terminal != null ? (terminal * 100).toFixed(1) + '%' : '--'}
              </div>
              {resType === 'terminal' && <div style={{ fontSize: 8, color: '#8b5cf6', marginTop: 2 }}>ACTIVE</div>}
            </div>
            <div style={{
              padding: 8, borderRadius: 6, textAlign: 'center',
              border: `1px solid ${colors.accent}33`,
              background: colors.accentDim,
            }}>
              <div style={{ fontSize: 9, color: colors.textDim, marginBottom: 4 }}>SELECTED</div>
              <div style={{
                fontSize: 20, fontWeight: 700, fontFamily: fonts.mono,
                color: colors.accent,
              }}>
                {selected != null ? (selected * 100).toFixed(1) + '%' : '--'}
              </div>
            </div>
          </div>
        </InfoBox>
      )}

      {/* Volatility Comparison */}
      {Object.keys(volData).length > 0 && (
        <InfoBox>
          <div style={{ fontSize: 10, fontWeight: 600, color: colors.textMuted, marginBottom: 8, textTransform: 'uppercase' }}>
            Volatility Comparison
          </div>
          <VolComparison data={volData} />
          <div style={{ display: 'flex', gap: 12, marginTop: 8, fontSize: 10, color: colors.textDim, flexWrap: 'wrap' }}>
            <span>Source: <span style={{ color: colors.textMuted, fontWeight: 600 }}>{String(raw.vol_source || '--')}</span></span>
            <span>Interval: <span style={{ color: colors.textMuted }}>{fmt(raw.avg_interval_hours, 1)}h avg</span></span>
            <span>Selected: <span style={{ color: colors.accent, fontWeight: 600 }}>{fmtPct(raw.annualized_vol)}</span></span>
          </div>
        </InfoBox>
      )}

      {/* Drift Analysis with visual shrinkage bar */}
      {(realized != null || shrunk != null) && (
        <InfoBox>
          <div style={{ fontSize: 10, fontWeight: 600, color: colors.textMuted, marginBottom: 8, textTransform: 'uppercase' }}>
            Drift Analysis (Bayesian Shrinkage)
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10, marginBottom: 8 }}>
            <Stat label="Realized" value={realized != null ? (realized * 100).toFixed(2) + '%/yr' : '--'} small />
            <Stat label="Shrunk" value={shrunk != null ? (shrunk * 100).toFixed(2) + '%/yr' : '--'}
              highlight={shrunk != null && Math.abs(shrunk) < 0.01 ? colors.textDim : undefined} small />
            <Stat label="Std Error" value={stderr != null ? (stderr * 100).toFixed(2) + '%' : '--'} small />
          </div>
          {shrinkPct != null && (
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, color: colors.textDim, marginBottom: 3 }}>
                <span>Shrinkage toward zero</span>
                <span style={{ fontFamily: fonts.mono, color: shrinkPct > 70 ? colors.warning : colors.accent }}>{shrinkPct.toFixed(0)}%</span>
              </div>
              <div style={{ height: 6, background: colors.border, borderRadius: 3, overflow: 'hidden' }}>
                <div style={{
                  height: '100%', width: `${Math.min(Math.max(shrinkPct, 0), 100)}%`,
                  background: shrinkPct > 70 ? colors.warning : colors.accent, borderRadius: 3,
                  transition: 'width 0.4s ease',
                }} />
              </div>
              <div style={{ fontSize: 9, color: colors.textDim, marginTop: 4 }}>
                {shrinkPct > 70 ? 'High shrinkage — drift estimate is noisy, model relies mostly on volatility' :
                 shrinkPct > 40 ? 'Moderate shrinkage — drift partially discounted' :
                 'Low shrinkage — drift signal is statistically significant'}
              </div>
            </div>
          )}
        </InfoBox>
      )}
    </div>
  )
}

function PredictionMarketsDetail({ raw, consensusProb }: { raw: Record<string, unknown>; consensusProb?: number }) {
  const matched = (raw.matched_markets || []) as Record<string, unknown>[]
  const allCandidates = raw.all_candidates as number | undefined
  const platforms = raw.platforms_searched as string[] | undefined

  return (
    <div>
      {/* Cross-platform probability chart */}
      {matched.length > 0 && (
        <CrossPlatformBars
          markets={matched.map(m => ({
            platform: String(m.platform || '?'),
            title: String(m.title || m.question || '?'),
            probability: parseFloat(String(m.probability || 0)),
            similarity: m.similarity != null ? parseFloat(String(m.similarity)) : undefined,
            forecasters: m.forecasters != null ? Number(m.forecasters) : undefined,
            volume: m.volume != null ? Number(m.volume) : undefined,
          }))}
          consensusProb={consensusProb}
        />
      )}

      {/* Match details table */}
      {matched.length > 0 && (
        <div style={{ marginTop: 8 }}>
          {matched.map((mm, i) => (
            <div key={i} style={{
              padding: '6px 8px', marginBottom: 4, borderRadius: 6,
              background: 'rgba(0,0,0,0.2)', fontSize: 11,
            }}>
              <div style={{ color: colors.textSecondary, marginBottom: 3, lineHeight: 1.3 }}>
                {String(mm.title || mm.question || '?')}
              </div>
              <div style={{ display: 'flex', gap: 10, color: colors.textDim, flexWrap: 'wrap', fontSize: 10 }}>
                <span>Platform: <span style={{ color: colors.textMuted, fontWeight: 600 }}>{String(mm.platform || '?')}</span></span>
                {mm.probability != null && (
                  <span>Prob: <span style={{ fontFamily: fonts.mono, color: colors.accent }}>{fmtPct(mm.probability)}</span></span>
                )}
                {mm.similarity != null && (
                  <span>Similarity: <span style={{
                    fontFamily: fonts.mono,
                    color: (mm.similarity as number) > 0.6 ? colors.success : (mm.similarity as number) > 0.4 ? colors.warning : colors.textDim,
                  }}>{fmt(mm.similarity, 3)}</span></span>
                )}
                {mm.forecasters != null && (
                  <span>Forecasters: <span style={{ color: colors.textMuted }}>{String(mm.forecasters)}</span></span>
                )}
                {mm.volume != null && (
                  <span>Volume: <span style={{ color: colors.textMuted }}>${Number(mm.volume).toLocaleString()}</span></span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Search metadata */}
      <div style={{ display: 'flex', gap: 12, marginTop: 6, fontSize: 10, color: colors.textDim, flexWrap: 'wrap' }}>
        {allCandidates != null && <span>Total candidates found: <span style={{ color: colors.textMuted }}>{allCandidates}</span></span>}
        {platforms && <span>Platforms: <span style={{ color: colors.textMuted }}>{platforms.join(', ')}</span></span>}
        {matched.length > 0 && allCandidates != null && (
          <span>Match rate: <span style={{ color: colors.textMuted }}>{((matched.length / Math.max(allCandidates, 1)) * 100).toFixed(0)}%</span></span>
        )}
      </div>
    </div>
  )
}

function OnchainFlowDetail({ raw }: { raw: Record<string, unknown> }) {
  const pressure = raw.pressure_score as number | undefined
  const sourcesAvailable = raw.sources_available as number | undefined
  const agreement = raw.source_agreement as number | undefined
  const sourcePressures = raw.source_pressures as Record<string, number> | undefined

  // Determine sentiment from composite pressure
  const sentiment = pressure != null
    ? (pressure > 0.05 ? 'bullish' : pressure < -0.05 ? 'bearish' : 'neutral')
    : undefined
  const sentimentColor = sentiment === 'bullish' ? colors.success
    : sentiment === 'bearish' ? colors.danger : colors.warning

  const sourceLabels: Record<string, string> = {
    stablecoin_flow: 'Stablecoin Flow',
    tvl_trend: 'DeFi TVL',
    fear_greed: 'Fear & Greed',
    global_market: 'Global Market',
  }

  return (
    <div>
      {/* Composite Pressure */}
      {pressure != null && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, flexWrap: 'wrap',
        }}>
          <span style={{ fontSize: 10, color: colors.textDim, textTransform: 'uppercase' }}>Flow:</span>
          <span style={{
            fontSize: 14, fontWeight: 700, fontFamily: fonts.mono, color: sentimentColor,
            textTransform: 'uppercase',
          }}>
            {sentiment} ({pressure > 0 ? '+' : ''}{pressure.toFixed(3)})
          </span>
          {sourcesAvailable != null && (
            <span style={{ fontSize: 10, color: colors.textDim, fontFamily: fonts.mono }}>
              {sourcesAvailable} source{sourcesAvailable !== 1 ? 's' : ''}
            </span>
          )}
          {agreement != null && (
            <span style={{
              padding: '2px 8px', borderRadius: 10, fontSize: 10,
              background: agreement >= 0.7 ? 'rgba(0,200,100,0.1)' : 'rgba(255,170,0,0.1)',
              color: agreement >= 0.7 ? colors.success : colors.warning,
              fontFamily: fonts.mono,
            }}>
              Agreement: {(agreement * 100).toFixed(0)}%
            </span>
          )}
          {raw.fear_greed_value != null && (
            <span style={{
              padding: '2px 8px', borderRadius: 10, fontSize: 10,
              background: 'rgba(255,170,0,0.1)', color: colors.warning,
              fontFamily: fonts.mono,
            }}>
              Fear/Greed: {String(raw.fear_greed_value)}
              {raw.fear_greed_label ? ` (${String(raw.fear_greed_label)})` : ''}
            </span>
          )}
        </div>
      )}

      {/* Key Metrics Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: 8, marginBottom: 10 }}>
        {raw.total_stablecoin_supply != null && Number(raw.total_stablecoin_supply) > 0 && (
          <Stat label="Stablecoin Supply" value={`$${(Number(raw.total_stablecoin_supply) / 1e9).toFixed(1)}B`} small />
        )}
        {raw.weekly_change_pct != null && (
          <Stat label="Stablecoin 7d" value={`${Number(raw.weekly_change_pct) > 0 ? '+' : ''}${Number(raw.weekly_change_pct).toFixed(2)}%`}
            highlight={Number(raw.weekly_change_pct) > 0 ? colors.success : colors.danger} small />
        )}
        {raw.monthly_change_pct != null && (
          <Stat label="Stablecoin 30d" value={`${Number(raw.monthly_change_pct) > 0 ? '+' : ''}${Number(raw.monthly_change_pct).toFixed(2)}%`}
            highlight={Number(raw.monthly_change_pct) > 0 ? colors.success : colors.danger} small />
        )}
        {raw.current_tvl != null && (
          <Stat label="DeFi TVL" value={`$${(Number(raw.current_tvl) / 1e9).toFixed(1)}B`} small />
        )}
        {raw.tvl_weekly_change_pct != null && (
          <Stat label="TVL 7d" value={`${Number(raw.tvl_weekly_change_pct) > 0 ? '+' : ''}${Number(raw.tvl_weekly_change_pct).toFixed(2)}%`}
            highlight={Number(raw.tvl_weekly_change_pct) > 0 ? colors.success : colors.danger} small />
        )}
        {raw.btc_dominance != null && (
          <Stat label="BTC Dominance" value={`${Number(raw.btc_dominance).toFixed(1)}%`} small />
        )}
        {raw.total_market_cap != null && (
          <Stat label="Total MCap" value={`$${(Number(raw.total_market_cap) / 1e12).toFixed(2)}T`} small />
        )}
        {raw.market_cap_change_24h_pct != null && (
          <Stat label="MCap 24h" value={`${Number(raw.market_cap_change_24h_pct) > 0 ? '+' : ''}${Number(raw.market_cap_change_24h_pct).toFixed(2)}%`}
            highlight={Number(raw.market_cap_change_24h_pct) > 0 ? colors.success : colors.danger} small />
        )}
      </div>

      {/* Source Pressure Breakdown */}
      {sourcePressures && Object.keys(sourcePressures).length > 0 && (
        <div>
          <div style={{ fontSize: 10, fontWeight: 600, color: colors.textMuted, marginBottom: 6, textTransform: 'uppercase' }}>
            Source Pressures ({Object.keys(sourcePressures).length})
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {Object.entries(sourcePressures).map(([name, p]) => {
              const srcColor = p > 0.05 ? colors.success : p < -0.05 ? colors.danger : colors.warning
              const barWidth = Math.abs(p) * 100
              return (
                <div key={name} style={{
                  padding: '6px 8px', borderRadius: 6,
                  background: 'rgba(0,0,0,0.2)', fontSize: 11,
                  borderLeft: `3px solid ${srcColor}`,
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{ fontWeight: 600, color: colors.textSecondary }}>
                      {sourceLabels[name] || name}
                    </span>
                    <span style={{ fontSize: 11, fontFamily: fonts.mono, color: srcColor, fontWeight: 600 }}>
                      {p > 0 ? '+' : ''}{p.toFixed(3)}
                    </span>
                  </div>
                  <div style={{ marginTop: 4, height: 4, borderRadius: 2, background: 'rgba(255,255,255,0.05)', position: 'relative' }}>
                    <div style={{
                      position: 'absolute',
                      left: p < 0 ? `${50 - barWidth / 2}%` : '50%',
                      width: `${barWidth / 2}%`,
                      height: '100%',
                      borderRadius: 2,
                      background: srcColor,
                      opacity: 0.6,
                    }} />
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

function WebSearchDetail({ raw }: { raw: Record<string, unknown> }) {
  const evidence = (raw.key_evidence || raw.evidence || []) as (string | Record<string, unknown>)[]
  const sourcesFound = raw.sources_found as number | undefined
  const searchQuery = raw.search_query as string | undefined

  return (
    <div>
      {evidence.length > 0 && (
        <div>
          <div style={{ fontSize: 10, fontWeight: 600, color: colors.textMuted, marginBottom: 6, textTransform: 'uppercase' }}>
            Evidence ({evidence.length} items{sourcesFound != null ? `, ${sourcesFound} sources` : ''})
          </div>
          {evidence.map((ev, i) => {
            const text = typeof ev === 'string' ? ev : String((ev as Record<string, unknown>).title || (ev as Record<string, unknown>).text || (ev as Record<string, unknown>).snippet || JSON.stringify(ev))
            return (
              <div key={i} style={{
                padding: '6px 8px', marginBottom: 4, borderRadius: 6,
                background: 'rgba(0,0,0,0.2)', fontSize: 11, color: colors.textSecondary,
                borderLeft: `2px solid ${colors.warning}44`,
              }}>
                {text}
              </div>
            )
          })}
        </div>
      )}
      {searchQuery && (
        <div style={{ fontSize: 10, color: colors.textDim, marginTop: 6 }}>
          Query: <span style={{ color: colors.textMuted, fontStyle: 'italic' }}>"{searchQuery}"</span>
        </div>
      )}
    </div>
  )
}

function SignalCard({ signal, index }: { signal: Record<string, unknown>; index: number }) {
  const [expanded, setExpanded] = useState(true) // Default expanded for more visibility
  const source = String(signal.source || '?')
  const prob = signal.probability as number | null
  const conf = signal.confidence as number | undefined
  const raw = (signal.raw_data || {}) as Record<string, unknown>
  const reasoning = String(signal.reasoning || '')
  const model = String(signal.model_used || 'none')
  const dataPoints = signal.data_points as number | undefined
  const effectiveWeight = signal.effective_weight as number | undefined
  const baseMultiplier = signal.base_multiplier as number | undefined
  const usable = signal.usable !== false && prob != null && (conf ?? 0) > 0

  const palette = ['#22c55e', '#3b82f6', '#f59e0b', '#8b5cf6', '#06b6d4']
  const cardColor = usable ? palette[index % palette.length] : colors.textDim

  return (
    <div style={{
      background: colors.bgSecondary, border: `1px solid ${colors.border}`,
      borderLeft: `3px solid ${cardColor}`, borderRadius: 8, marginBottom: 8, overflow: 'hidden',
      opacity: usable ? 1 : 0.6,
    }}>
      <div
        onClick={() => setExpanded(!expanded)}
        style={{
          padding: '10px 12px', cursor: 'pointer', display: 'flex', alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: cardColor }}>{source.replace(/_/g, ' ')}</span>
          {!usable && (
            <span style={{
              fontSize: 9, padding: '1px 6px', borderRadius: 10,
              background: colors.dangerDim, color: colors.danger,
            }}>
              {prob == null ? 'NO DATA' : 'UNUSABLE'}
            </span>
          )}
          {prob != null && (
            <span style={{ fontFamily: fonts.mono, fontSize: 14, fontWeight: 700, color: colors.textPrimary }}>
              {(prob * 100).toFixed(1)}%
            </span>
          )}
          {conf != null && (
            <span style={{
              fontSize: 10, padding: '1px 6px', borderRadius: 10,
              background: conf > 0.5 ? colors.successDim : conf > 0.25 ? colors.warningDim : colors.dangerDim,
              color: conf > 0.5 ? colors.success : conf > 0.25 ? colors.warning : colors.danger,
            }}>
              conf {(conf * 100).toFixed(0)}%
            </span>
          )}
          {effectiveWeight != null && effectiveWeight > 0 && (
            <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 10, background: colors.accentDim, color: colors.textDim }}>
              wt {effectiveWeight.toFixed(2)}
            </span>
          )}
          {baseMultiplier != null && (
            <span style={{ fontSize: 9, color: colors.textDim }}>{baseMultiplier}x base</span>
          )}
          {model !== 'none' && (
            <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 10, background: 'rgba(139,92,246,0.15)', color: '#8b5cf6' }}>
              {model}
            </span>
          )}
          {dataPoints != null && (
            <span style={{ fontSize: 10, color: colors.textDim }}>{dataPoints} pts</span>
          )}
        </div>
        <span style={{ fontSize: 9, color: colors.textDim, transition: 'transform 0.2s', transform: expanded ? 'rotate(0)' : 'rotate(-90deg)' }}>
          &#9660;
        </span>
      </div>

      {expanded && (
        <div style={{ padding: '0 12px 12px', borderTop: `1px solid ${colors.border}` }}>
          {/* Signal reasoning */}
          {reasoning && (
            <pre style={{
              background: 'rgba(0,0,0,0.2)', borderRadius: 6, padding: 10, marginTop: 8,
              fontSize: 11, fontFamily: fonts.mono, color: colors.textSecondary,
              whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 150, overflow: 'auto',
              lineHeight: 1.4,
            }}>
              {reasoning}
            </pre>
          )}

          {/* Source-specific displays */}
          {source === 'resolution_crypto' && Object.keys(raw).length > 0 && (
            <div style={{ marginTop: 10 }}>
              <CryptoSignalDetail raw={raw} />
            </div>
          )}

          {source === 'prediction_markets' && Object.keys(raw).length > 0 && (
            <div style={{ marginTop: 10 }}>
              <PredictionMarketsDetail raw={raw} consensusProb={prob ?? undefined} />
            </div>
          )}

          {source === 'web_search' && Object.keys(raw).length > 0 && (
            <div style={{ marginTop: 10 }}>
              <WebSearchDetail raw={raw} />
            </div>
          )}

          {source === 'onchain_flow' && Object.keys(raw).length > 0 && (
            <div style={{ marginTop: 10 }}>
              <OnchainFlowDetail raw={raw} />
            </div>
          )}

          {/* Fallback: show all raw_data as JSON for any other source */}
          {Object.keys(raw).length > 0 && !['resolution_crypto', 'prediction_markets', 'web_search', 'onchain_flow'].includes(source) && (
            <details style={{ marginTop: 8 }}>
              <summary style={{ fontSize: 10, color: colors.textDim, cursor: 'pointer' }}>Raw Data</summary>
              <pre style={{
                background: 'rgba(0,0,0,0.2)', borderRadius: 6, padding: 8, marginTop: 4,
                fontSize: 10, fontFamily: fonts.mono, color: colors.textDim,
                whiteSpace: 'pre-wrap', maxHeight: 200, overflow: 'auto',
              }}>
                {JSON.stringify(raw, null, 2)}
              </pre>
            </details>
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function AnalysisDetail({ conditionId }: { conditionId: string }) {
  const [data, setData] = useState<AnalysisDetailType | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Collapsible sections
  const [showMeta, setShowMeta] = useState(true)
  const [showPipeline, setShowPipeline] = useState(true)
  const [showProbs, setShowProbs] = useState(true)
  const [showRadar, setShowRadar] = useState(true)
  const [showSignals, setShowSignals] = useState(true)
  const [showCrypto, setShowCrypto] = useState(true)
  const [showKelly, setShowKelly] = useState(true)
  const [showDepth, setShowDepth] = useState(true)
  const [showFrontier, setShowFrontier] = useState(true)
  const [showThresholds, setShowThresholds] = useState(false) // collapsed by default

  useEffect(() => {
    setData(null)
    setError(null)
    api.fetchAnalysisDetail(conditionId)
      .then(setData)
      .catch(e => setError(e.message))
  }, [conditionId])

  if (error) return <div style={{ color: colors.danger }}>Error: {error}</div>
  if (!data) return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: 20 }}>
      {[1,2,3].map(i => (
        <div key={i} style={{
          height: 14, borderRadius: 4, width: `${60 + i * 12}%`,
          background: colors.border, animation: 'shimmer 1.5s ease-in-out infinite',
        }} />
      ))}
      <style>{`@keyframes shimmer { 0% { opacity: 0.5; } 50% { opacity: 1; } 100% { opacity: 0.5; } }`}</style>
    </div>
  )

  // Extract data layers
  const meta = (data.market_meta || {}) as Record<string, unknown>
  const agg = (data.aggregation || {}) as Record<string, unknown>
  const kelly = (data.kelly || data.decision || {}) as Record<string, unknown>
  const exec = (data.execution || {}) as Record<string, unknown>
  const depth = (data.depth || {}) as Record<string, unknown>
  const signals = (agg.signals || data.signals || []) as Record<string, unknown>[]
  const thresholds = (data.thresholds || {}) as Record<string, unknown>

  const question = (data.question || conditionId) as string
  const marketPrice = parseFloat(String(agg.market_price || 0))
  const estimate = parseFloat(String(agg.final_probability || 0))
  const preliminary = parseFloat(String(agg.preliminary_probability || 0))
  const effective = parseFloat(String(kelly.effective_prob || estimate))
  const confidence = parseFloat(String(agg.confidence || 0))
  const agreement = String(agg.signals_agreement || '--')
  const efficiency = String(agg.market_efficiency || '--')
  const signalsStdev = parseFloat(String(agg.signals_stdev || 0))

  const divergence = Math.abs(estimate - marketPrice)
  const divColor = divergence < 0.1 ? colors.success : divergence < 0.2 ? colors.warning : colors.danger
  const edge = parseFloat(String(kelly.edge || 0))

  // Crypto raw data
  const cryptoSignal = signals.find(s => s.source === 'resolution_crypto')
  const cryptoRaw = (cryptoSignal?.raw_data || {}) as Record<string, unknown>
  const priceHistory = (cryptoRaw.price_history || data.price_history || []) as { date: string; price: number }[]
  const targetPrice = parseFloat(String(cryptoRaw.target_price || 0))

  const reasoning = String(agg.reasoning || data.reasoning || '')

  // Build probability waterfall steps
  const waterfallSteps = [
    { label: 'Market', value: marketPrice, color: colors.textMuted },
  ]
  if (preliminary > 0) waterfallSteps.push({ label: 'Preliminary', value: preliminary, color: '#8b5cf6' })
  waterfallSteps.push({ label: 'Frontier', value: estimate, color: colors.accent })
  waterfallSteps.push({ label: 'Blended', value: effective, color: colors.success })

  // Build probability comparison bars (including individual signals)
  const probBars: { label: string; value: number; color: string }[] = [
    { label: 'Market', value: marketPrice, color: colors.textMuted },
  ]
  if (preliminary > 0) probBars.push({ label: 'Preliminary', value: preliminary, color: '#8b5cf6' })
  probBars.push({ label: 'Frontier', value: estimate, color: colors.accent })
  probBars.push({ label: 'Effective (blended)', value: effective, color: colors.success })
  const signalPalette = ['#22c55e', '#3b82f6', '#f59e0b']
  for (let i = 0; i < signals.length; i++) {
    const s = signals[i]
    const p = parseFloat(String(s.probability || 0))
    if (p > 0) {
      probBars.push({ label: String(s.source || '?').replace(/_/g, ' '), value: p, color: signalPalette[i % signalPalette.length] })
    }
  }

  // Build weight data
  const weightData: { label: string; weight: number }[] = []
  for (const s of signals) {
    const w = parseFloat(String(s.effective_weight || 0))
    if (w > 0) {
      weightData.push({ label: String(s.source || '?').replace(/_/g, ' '), weight: w })
    }
  }

  // Build decision pipeline gates
  const pipelineGates: { name: string; status: 'pass' | 'fail' | 'warn' | 'skip'; value?: string; threshold?: string }[] = []

  // Signal collection
  const usableSignals = signals.filter(s => s.probability != null && (s.confidence as number) > 0)
  pipelineGates.push({
    name: 'Signals Collected',
    status: usableSignals.length > 0 ? 'pass' : 'fail',
    value: `${usableSignals.length}/${signals.length} usable`,
  })

  // Signal agreement
  pipelineGates.push({
    name: 'Signal Agreement',
    status: agreement === 'agree' ? 'pass' : agreement === 'mixed' ? 'warn' : 'fail',
    value: `${agreement} (stdev ${(signalsStdev * 100).toFixed(1)}%)`,
  })

  // Frontier confidence
  const minConf = 0.25
  pipelineGates.push({
    name: 'Frontier Confidence',
    status: confidence >= minConf ? (confidence >= 0.5 ? 'pass' : 'warn') : 'fail',
    value: fmtPct(confidence),
    threshold: `min ${fmtPct(minConf)}`,
  })

  // Divergence check
  const maxDivLow = parseFloat(String(thresholds.max_divergence_low_conf || 0.40))
  const maxDivAny = parseFloat(String(thresholds.max_divergence_any_conf || 0.50))
  const divThreshold = confidence < 0.7 ? maxDivLow : maxDivAny
  pipelineGates.push({
    name: 'Divergence Check',
    status: divergence <= divThreshold ? 'pass' : 'fail',
    value: fmtPct(divergence),
    threshold: `max ${fmtPct(divThreshold)}`,
  })

  // Edge threshold
  const minEdge = parseFloat(String(thresholds.min_edge || kelly.min_edge_threshold || 0.03))
  pipelineGates.push({
    name: 'Edge Threshold',
    status: Math.abs(edge) >= minEdge ? 'pass' : 'fail',
    value: fmtPct(edge),
    threshold: `min ${fmtPct(minEdge)}`,
  })

  // Kelly sizing
  const betSize = parseFloat(String(kelly.bet_size || 0))
  pipelineGates.push({
    name: 'Kelly Bet Size',
    status: betSize >= 1 ? 'pass' : 'fail',
    value: fmtUsd(betSize),
    threshold: 'min $1.00',
  })

  // Depth check
  const totalDepth = parseFloat(String(depth.total_depth_usd || 0))
  const minDepth = parseFloat(String(thresholds.min_depth_usd || 50))
  if (Object.keys(depth).length > 0) {
    pipelineGates.push({
      name: 'Order Book Depth',
      status: totalDepth >= minDepth ? 'pass' : 'fail',
      value: fmtUsd(totalDepth),
      threshold: `min ${fmtUsd(minDepth)}`,
    })

    const slippage = parseFloat(String(depth.slippage || 0))
    const maxSlip = parseFloat(String(thresholds.max_slippage || 0.03))
    pipelineGates.push({
      name: 'Slippage',
      status: slippage <= maxSlip ? 'pass' : 'warn',
      value: fmtPct(slippage),
      threshold: `max ${fmtPct(maxSlip)}`,
    })
  }

  // Final decision
  const shouldTrade = kelly.should_trade as boolean | undefined
  pipelineGates.push({
    name: 'Final Decision',
    status: shouldTrade ? 'pass' : 'fail',
    value: shouldTrade ? 'TRADE' : 'SKIP',
    threshold: kelly.skip_reason ? String(kelly.skip_reason) : undefined,
  })

  return (
    <div>
      {/* Header: Question + quick stats */}
      <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 10, lineHeight: 1.4 }}>{question}</div>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 6, flexWrap: 'wrap' }}>
        <span style={{
          fontFamily: fonts.mono, fontSize: 10, color: colors.textDim,
          background: colors.accentDim, padding: '2px 8px', borderRadius: 10,
        }}>
          {conditionId.slice(0, 20)}...
        </span>
        <Badge text={agreement.toUpperCase()} color={
          agreement === 'agree' ? colors.success : agreement === 'mixed' ? colors.warning : colors.danger
        } />
        <Badge text={efficiency.toUpperCase()} color={
          efficiency === 'underpriced' ? colors.success : efficiency === 'overpriced' ? colors.danger : colors.textMuted
        } />
        <Badge text={`DIV ${(divergence * 100).toFixed(1)}%`} color={divColor} />
        <Badge text={`EDGE ${edge > 0 ? '+' : ''}${(edge * 100).toFixed(1)}%`} color={edge > 0 ? colors.success : colors.danger} />
        {agg.total_data_points != null && (
          <span style={{ fontSize: 10, color: colors.textDim }}>{String(agg.total_data_points)} data points</span>
        )}
      </div>

      {/* Quick summary row: gauges */}
      <div style={{
        display: 'flex', gap: 16, alignItems: 'center', justifyContent: 'space-around',
        padding: '12px 0', marginBottom: 4, flexWrap: 'wrap',
      }}>
        <ConfidenceGauge value={confidence} label="Confidence" thresholds={{ low: 0.25, medium: 0.5 }} />
        <ConfidenceGauge value={marketPrice} label="Market Price" thresholds={{ low: 0.2, medium: 0.5 }} />
        <ConfidenceGauge value={estimate} label="Frontier Est." thresholds={{ low: 0.2, medium: 0.5 }} />
        <ConfidenceGauge value={effective} label="Effective" thresholds={{ low: 0.2, medium: 0.5 }} />
      </div>

      {/* 1. Decision Pipeline — the full pass/fail gate visualization */}
      <SectionHeader title="Decision Pipeline" collapsed={!showPipeline} onToggle={() => setShowPipeline(!showPipeline)} badge={shouldTrade ? 'TRADE' : 'SKIP'} />
      {showPipeline && <DecisionPipeline gates={pipelineGates} />}

      {/* 2. Market Metadata */}
      {Object.keys(meta).length > 0 && (
        <>
          <SectionHeader title="Market Overview" collapsed={!showMeta} onToggle={() => setShowMeta(!showMeta)} />
          {showMeta && (
            <div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))', gap: 8 }}>
                <Stat label="Market Price" value={fmtPct(marketPrice)} highlight={colors.accent} />
                <Stat label="Liquidity" value={fmtUsd(meta.liquidity)} />
                <Stat label="24h Volume" value={fmtUsd(meta.volume_24h)} />
                <Stat label="Spread" value={fmtPct(meta.spread)} highlight={
                  (meta.spread as number) > 0.08 ? colors.warning : undefined
                } />
                <Stat label="Best Bid" value={fmtPct(meta.best_bid)} />
                <Stat label="Best Ask" value={fmtPct(meta.best_ask)} />
                <Stat label="Days Left" value={meta.days_remaining != null ? fmt(meta.days_remaining, 1) + 'd' : '--'} highlight={
                  meta.days_remaining != null && (meta.days_remaining as number) < 7 ? colors.warning : undefined
                } />
                <Stat label="Resolution Type" value={String(meta.resolution_type || '--')} mono={false} />
                {meta.model_edge != null && <Stat label="Pre-screen Edge" value={fmtPct(meta.model_edge)} highlight={
                  (meta.model_edge as number) > 0 ? colors.success : colors.danger
                } />}
                {meta.time_score != null && <Stat label="Time Score" value={String(meta.time_score)} />}
                {meta.total_score != null && <Stat label="Total Score" value={fmt(meta.total_score, 1)} />}
              </div>

              {/* Resolution params detail */}
              {meta.resolution_params != null && typeof meta.resolution_params === 'object' && (
                <details style={{ marginTop: 8 }}>
                  <summary style={{ fontSize: 10, color: colors.textDim, cursor: 'pointer' }}>Resolution Parameters</summary>
                  <div style={{
                    display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))',
                    gap: 6, marginTop: 6,
                  }}>
                    {Object.entries(meta.resolution_params as Record<string, unknown>).map(([k, v]) => (
                      <Stat key={k} label={k.replace(/_/g, ' ')} value={String(v ?? '--')} small />
                    ))}
                  </div>
                </details>
              )}
            </div>
          )}
        </>
      )}

      {/* Fallback for manual aggregates without meta */}
      {Object.keys(meta).length === 0 && (
        <div style={{
          display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginBottom: 8,
        }}>
          <div style={{ background: colors.accentDim, padding: '6px 10px', borderRadius: 6 }}>
            <Stat label="Market Price" value={fmtPct(marketPrice)} highlight={colors.accent} />
          </div>
          <div style={{ background: colors.accentDim, padding: '6px 10px', borderRadius: 6 }}>
            <Stat label="Frontier Est." value={fmtPct(estimate)} />
          </div>
          <div style={{ background: colors.accentDim, padding: '6px 10px', borderRadius: 6 }}>
            <Stat label="Confidence" value={fmtPct(confidence)} />
          </div>
        </div>
      )}

      {/* 3. Probability Journey — waterfall + bars */}
      <SectionHeader title="Probability Journey" collapsed={!showProbs} onToggle={() => setShowProbs(!showProbs)} />
      {showProbs && (
        <>
          <div style={{ fontSize: 10, color: colors.textDim, marginBottom: 6, textTransform: 'uppercase' }}>
            Edge Decomposition Waterfall
          </div>
          <EdgeWaterfall steps={waterfallSteps} />
          <div style={{ marginTop: 12, fontSize: 10, color: colors.textDim, marginBottom: 6, textTransform: 'uppercase' }}>
            All Probability Estimates
          </div>
          <ProbabilityBars bars={probBars} />
          {weightData.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div style={{ fontSize: 10, color: colors.textDim, marginBottom: 4, textTransform: 'uppercase' }}>
                Signal Effective Weights (confidence x multiplier)
              </div>
              <SignalWeights data={weightData} />
              {agg.signal_weight_multipliers != null && typeof agg.signal_weight_multipliers === 'object' && (
                <div style={{ display: 'flex', gap: 10, marginTop: 4, fontSize: 9, color: colors.textDim, flexWrap: 'wrap' }}>
                  {Object.entries(agg.signal_weight_multipliers as Record<string, number>).map(([k, v]) => (
                    <span key={k}>{k.replace(/_/g, ' ')}: <span style={{ color: colors.textMuted }}>{v}x</span></span>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}

      {/* 4. Signal Radar Comparison */}
      {signals.length >= 2 && (
        <>
          <SectionHeader title="Signal Radar Comparison" collapsed={!showRadar} onToggle={() => setShowRadar(!showRadar)} />
          {showRadar && (
            <SignalRadar
              signals={signals.map(s => ({
                source: String(s.source || '?'),
                probability: s.probability as number | null,
                confidence: parseFloat(String(s.confidence || 0)),
                data_points: parseInt(String(s.data_points || 0)),
                effective_weight: parseFloat(String(s.effective_weight || 0)),
              }))}
              marketPrice={marketPrice}
            />
          )}
        </>
      )}

      {/* 5. Individual Signal Details */}
      <SectionHeader title={`Signal Providers (${signals.length})`} collapsed={!showSignals} onToggle={() => setShowSignals(!showSignals)} badge={`${usableSignals.length} usable`} />
      {showSignals && (
        <div>
          {signals.map((s, i) => (
            <SignalCard key={String(s.source || i)} signal={s} index={i} />
          ))}
        </div>
      )}

      {/* 6. Crypto Price Chart (if available) */}
      {priceHistory.length > 0 && (
        <>
          <SectionHeader title="Price History" collapsed={!showCrypto} onToggle={() => setShowCrypto(!showCrypto)} />
          {showCrypto && <PriceChart data={priceHistory} target={targetPrice} />}
        </>
      )}

      {/* 7. Kelly Sizing (full math + visual) */}
      {Object.keys(kelly).length > 0 && (
        <>
          <SectionHeader title="Kelly Criterion Sizing" collapsed={!showKelly} onToggle={() => setShowKelly(!showKelly)} badge={String(kelly.side || '')} />
          {showKelly && (
            <div>
              <InfoBox>
                <div style={{ fontSize: 10, fontWeight: 600, color: colors.textMuted, marginBottom: 8, textTransform: 'uppercase' }}>
                  Kelly Formula Steps
                </div>
                <MetricRow label="1. Frontier estimate (p)" value={fmtPct(kelly.estimated_prob)} />
                <MetricRow label="2. Market price (m)" value={fmtPct(kelly.market_price)} />
                <MetricRow label="3. Confidence (c)" value={fmtPct(kelly.confidence)} />
                <MetricRow
                  label="4. Confidence blend floor"
                  value={fmtPct(kelly.confidence_blend_floor)}
                  detail={`blend_wt = max(c, floor) = ${fmtPct(Math.max(confidence, parseFloat(String(kelly.confidence_blend_floor || 0.5))))}`}
                />
                <MetricRow label="5. Effective prob = blend*p + (1-blend)*m" value={fmtPct(kelly.effective_prob)} highlight={colors.accent} />
                <MetricRow label="6. Edge = effective - market" value={fmtPct(kelly.edge)} highlight={
                  edge > 0 ? colors.success : colors.danger
                } />
                <MetricRow label="7. Fee rate" value={fmtPct(kelly.fee_rate)} detail="reduces effective odds" />
                <MetricRow label="8. Full Kelly f*" value={fmtPct(kelly.raw_kelly)} />
                <MetricRow
                  label={`9. Fractional (${kelly.kelly_fraction_multiplier || 0.25}x)`}
                  value={fmtPct(kelly.fractional_kelly)}
                  highlight={colors.accent}
                />
                <MetricRow label="10. Bankroll" value={fmtUsd(kelly.bankroll)} />
                <MetricRow
                  label="11. Reserve"
                  value={fmtUsd(kelly.min_bankroll_reserve)}
                  detail={`available: ${fmtUsd(parseFloat(String(kelly.bankroll || 0)) - parseFloat(String(kelly.min_bankroll_reserve || 20)))}`}
                />
                <MetricRow
                  label="12. Max position"
                  value={fmtPct(kelly.max_position_pct)}
                  detail={`cap: ${fmtUsd(parseFloat(String(kelly.bankroll || 0)) * parseFloat(String(kelly.max_position_pct || 0.1)))}`}
                />
                <MetricRow label="13. Bet size" value={fmtUsd(kelly.bet_size)} highlight={colors.accentLight} />
                {kelly.expected_value != null && (
                  <MetricRow label="14. Expected Value" value={fmtUsd(kelly.expected_value)} highlight={
                    (kelly.expected_value as number) > 0 ? colors.success : colors.danger
                  } />
                )}
              </InfoBox>

              {/* Visual Kelly fraction bar */}
              <div style={{ marginTop: 8 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: colors.textDim, marginBottom: 3 }}>
                  <span>Kelly fraction of bankroll</span>
                  <span style={{ fontFamily: fonts.mono }}>{fmtPct(kelly.fractional_kelly)}</span>
                </div>
                <div style={{ height: 8, background: colors.border, borderRadius: 4, overflow: 'hidden', position: 'relative' }}>
                  {/* Full Kelly indicator */}
                  <div style={{
                    position: 'absolute', left: `${Math.min(parseFloat(String(kelly.raw_kelly || 0)) * 100, 100)}%`,
                    top: 0, bottom: 0, width: 2, background: colors.textDim, zIndex: 1,
                  }} />
                  {/* Fractional Kelly bar */}
                  <div style={{
                    height: '100%', width: `${Math.min(parseFloat(String(kelly.fractional_kelly || 0)) * 100, 100)}%`,
                    background: colors.accent, borderRadius: 4,
                    transition: 'width 0.4s ease',
                  }} />
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 8, color: colors.textDim, marginTop: 2 }}>
                  <span>0%</span>
                  <span>Full Kelly: {fmtPct(kelly.raw_kelly)}</span>
                  <span>100%</span>
                </div>
              </div>

              <div style={{ display: 'flex', gap: 8, marginTop: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                <Badge text={String(kelly.side || '--')} color={String(kelly.side || '').includes('YES') ? colors.success : colors.danger} />
                <Badge text={shouldTrade ? 'TRADE' : 'SKIP'} color={shouldTrade ? colors.success : colors.warning} />
                {kelly.skip_reason != null && <span style={{ fontSize: 11, color: colors.textDim }}>{String(kelly.skip_reason)}</span>}
              </div>
            </div>
          )}
        </>
      )}

      {/* 8. Depth Analysis (enhanced) */}
      {Object.keys(depth).length > 0 && (
        <>
          <SectionHeader title="Order Book Depth" collapsed={!showDepth} onToggle={() => setShowDepth(!showDepth)} />
          {showDepth && (
            <DepthLadder
              totalDepth={parseFloat(String(depth.total_depth_usd || 0))}
              slippage={parseFloat(String(depth.slippage || 0))}
              bestPrice={parseFloat(String(depth.best_price || 0))}
              avgFillPrice={parseFloat(String(depth.avg_fill_price || 0))}
              maxFillable={parseFloat(String(depth.max_fillable_usd || 0))}
              levels={parseInt(String(depth.levels || 0))}
              adjustedBet={depth.adjusted_bet_usd != null ? parseFloat(String(depth.adjusted_bet_usd)) : undefined}
              wasAdjusted={Boolean(depth.was_adjusted)}
              thresholdSlippage={parseFloat(String(thresholds.max_slippage || 0.03))}
              thresholdMinDepth={parseFloat(String(thresholds.min_depth_usd || 50))}
            />
          )}
        </>
      )}

      {/* 9. Execution */}
      {exec.status != null && (
        <>
          <SectionHeader title="Execution" />
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, flexWrap: 'wrap' }}>
            <Badge
              text={String(exec.status).toUpperCase()}
              color={String(exec.status) === 'filled' ? colors.success : String(exec.status) === 'error' ? colors.danger : colors.accent}
            />
            {exec.paper != null && <Badge text="PAPER" color={colors.warning} />}
            {exec.trade_id != null && (
              <span style={{ color: colors.textDim, fontFamily: fonts.mono, fontSize: 11 }}>
                ID: {String(exec.trade_id).slice(0, 8)}
              </span>
            )}
            {exec.price != null && <span style={{ fontSize: 12 }}>Price: {fmtPct(exec.price)}</span>}
            {exec.size != null && <span style={{ fontSize: 12 }}>Size: {fmtUsd(exec.size)}</span>}
          </div>
        </>
      )}

      {/* 10. Frontier Reasoning */}
      {(reasoning || confidence > 0) && (
        <>
          <SectionHeader title="Frontier Model Reasoning" collapsed={!showFrontier} onToggle={() => setShowFrontier(!showFrontier)} />
          {showFrontier && (
            <>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 12, color: colors.textMuted }}>
                  Final: <span style={{ color: colors.textPrimary, fontWeight: 600, fontFamily: fonts.mono }}>{fmtPct(estimate)}</span>
                </span>
                <span style={{ fontSize: 12, color: colors.textMuted }}>
                  Confidence: <span style={{ color: colors.textPrimary, fontWeight: 600, fontFamily: fonts.mono }}>{fmtPct(confidence)}</span>
                </span>
                {preliminary > 0 && (
                  <span style={{ fontSize: 12, color: colors.textMuted }}>
                    Pre-frontier: <span style={{ fontFamily: fonts.mono }}>{fmtPct(preliminary)}</span>
                  </span>
                )}
                <span style={{ fontSize: 12, color: colors.textMuted }}>
                  Shift: <span style={{
                    fontFamily: fonts.mono,
                    color: Math.abs(estimate - preliminary) > 0.1 ? colors.warning : colors.textDim,
                  }}>
                    {preliminary > 0 ? (estimate > preliminary ? '+' : '') + ((estimate - preliminary) * 100).toFixed(1) + '%' : '--'}
                  </span>
                </span>
              </div>

              {reasoning && (
                <pre style={{
                  background: colors.bgSecondary,
                  border: `1px solid ${colors.border}`,
                  borderRadius: 8,
                  padding: 14,
                  fontSize: 12,
                  fontFamily: fonts.mono,
                  color: colors.textSecondary,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  marginTop: 6,
                  maxHeight: 400,
                  overflow: 'auto',
                  lineHeight: 1.5,
                }}>
                  {reasoning}
                </pre>
              )}
            </>
          )}
        </>
      )}

      {/* 11. Thresholds & Settings (collapsed by default) */}
      {Object.keys(thresholds).length > 0 && (
        <>
          <SectionHeader title="Active Thresholds & Settings" collapsed={!showThresholds} onToggle={() => setShowThresholds(!showThresholds)} badge={`${Object.keys(thresholds).length} params`} />
          {showThresholds && (
            <InfoBox>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 }}>
                {Object.entries(thresholds).map(([k, v]) => (
                  <MetricRow
                    key={k}
                    label={k.replace(/_/g, ' ')}
                    value={typeof v === 'number' ? (v < 1 && v > 0 ? fmtPct(v) : String(v)) : String(v)}
                  />
                ))}
              </div>
            </InfoBox>
          )}
        </>
      )}

      {/* Raw JSON fallback for debugging */}
      <details style={{ marginTop: 20 }}>
        <summary style={{ fontSize: 10, color: colors.textDim, cursor: 'pointer' }}>Raw API Response</summary>
        <pre style={{
          background: 'rgba(0,0,0,0.3)', borderRadius: 6, padding: 10, marginTop: 4,
          fontSize: 10, fontFamily: fonts.mono, color: colors.textDim,
          whiteSpace: 'pre-wrap', maxHeight: 400, overflow: 'auto',
        }}>
          {JSON.stringify(data, null, 2)}
        </pre>
      </details>
    </div>
  )
}
