import { useState, useEffect } from 'react'
import { colors } from '../theme'
import { api, AnalysisDetail as AnalysisDetailType } from '../api'
import ProbabilityBars from './charts/ProbabilityBars'
import VolComparison from './charts/VolComparison'
import PriceChart from './charts/PriceChart'
import KellyBreakdown from './charts/KellyBreakdown'
import SignalWeights from './charts/SignalWeights'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function SectionHeader({ title, collapsed, onToggle }: { title: string; collapsed?: boolean; onToggle?: () => void }) {
  return (
    <h3
      onClick={onToggle}
      style={{
        margin: '20px 0 10px', fontSize: 11, fontWeight: 600,
        color: colors.textMuted, textTransform: 'uppercase',
        letterSpacing: '0.08em',
        borderBottom: `1px solid ${colors.border}`,
        paddingBottom: 8,
        cursor: onToggle ? 'pointer' : 'default',
        userSelect: 'none',
        display: 'flex', alignItems: 'center', gap: 6,
      }}
    >
      {onToggle && <span style={{ fontSize: 9, transition: 'transform 0.2s', transform: collapsed ? 'rotate(-90deg)' : 'rotate(0)' }}>&#9660;</span>}
      {title}
    </h3>
  )
}

function Badge({ text, color: fg }: { text: string; color: string }) {
  return (
    <span style={{
      padding: '3px 10px', borderRadius: 20, fontSize: 11, fontWeight: 600,
      background: fg + '22', color: fg,
    }}>
      {text}
    </span>
  )
}

function Stat({ label, value, highlight, mono }: { label: string; value: string; highlight?: string; mono?: boolean }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span style={{ fontSize: 10, color: colors.textDim, textTransform: 'uppercase', letterSpacing: '0.04em' }}>{label}</span>
      <span style={{
        fontSize: 13, fontWeight: 600, color: highlight || colors.textPrimary,
        fontFamily: mono !== false ? "'JetBrains Mono', monospace" : 'inherit',
      }}>
        {value}
      </span>
    </div>
  )
}

function MetricRow({ label, value, highlight }: { label: string; value: string; highlight?: string }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '5px 0', borderBottom: `1px solid ${colors.border}`, fontSize: 12,
    }}>
      <span style={{ color: colors.textMuted }}>{label}</span>
      <span style={{
        fontWeight: 600, color: highlight || colors.textPrimary,
        fontFamily: "'JetBrains Mono', monospace", fontSize: 12,
      }}>
        {value}
      </span>
    </div>
  )
}

function InfoBox({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      background: colors.bgSecondary, border: `1px solid ${colors.border}`,
      borderRadius: 8, padding: 12, marginBottom: 8,
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
// Sub-components for each section
// ---------------------------------------------------------------------------

function MarketMeta({ meta, marketPrice }: { meta: Record<string, unknown>; marketPrice: number }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: 10 }}>
      <Stat label="Market Price" value={fmtPct(marketPrice)} highlight={colors.accent} />
      <Stat label="Liquidity" value={fmtUsd(meta.liquidity)} />
      <Stat label="24h Volume" value={fmtUsd(meta.volume_24h)} />
      <Stat label="Spread" value={fmtPct(meta.spread)} />
      <Stat label="Best Bid" value={fmtPct(meta.best_bid)} />
      <Stat label="Best Ask" value={fmtPct(meta.best_ask)} />
      <Stat label="Days Left" value={meta.days_remaining != null ? fmt(meta.days_remaining, 1) + 'd' : '--'} highlight={
        meta.days_remaining != null && (meta.days_remaining as number) < 7 ? colors.warning : undefined
      } />
      <Stat label="Resolution Type" value={String(meta.resolution_type || '--')} mono={false} />
      {meta.model_edge != null && <Stat label="Pre-screen Edge" value={fmtPct(meta.model_edge)} highlight={
        (meta.model_edge as number) > 0 ? colors.success : colors.danger
      } />}
    </div>
  )
}

function CoinInfo({ raw }: { raw: Record<string, unknown> }) {
  const distance = raw.distance_pct as number | undefined
  const trend = raw.trend as string | undefined
  const trendColor = trend === 'upward' ? colors.success : trend === 'downward' ? colors.danger : colors.textMuted

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: 10 }}>
      <Stat label="Coin" value={String(raw.coin_id || '--').toUpperCase()} mono={false} />
      <Stat label="Current Price" value={fmtUsd(raw.current_price)} />
      <Stat label="Target Price" value={fmtUsd(raw.target_price)} />
      <Stat label="Direction" value={String(raw.target_direction || '--')} highlight={
        raw.target_direction === 'above' ? colors.success : colors.danger
      } />
      <Stat label="Distance to Target" value={fmtPct(distance != null ? distance / 100 : null)} highlight={
        distance != null && Math.abs(distance) < 5 ? colors.warning : undefined
      } />
      <Stat label="24h Change" value={fmtPct(raw.change_24h != null ? (raw.change_24h as number) / 100 : null)} highlight={
        (raw.change_24h as number) > 0 ? colors.success : colors.danger
      } />
      <Stat label="Trend" value={String(trend || '--')} highlight={trendColor} />
    </div>
  )
}

function ProbabilityModels({ raw }: { raw: Record<string, unknown> }) {
  const barrier = raw.barrier_prob as number | undefined
  const terminal = raw.terminal_prob as number | undefined
  const selected = raw.model_prob as number | undefined
  const resType = raw.resolution_type as string | undefined

  const bars: { label: string; value: number; color: string }[] = []
  if (barrier != null && barrier > 0) bars.push({ label: `Barrier (touch)${resType === 'barrier' ? ' *' : ''}`, value: barrier, color: '#f59e0b' })
  if (terminal != null && terminal > 0) bars.push({ label: `Terminal (at expiry)${resType === 'terminal' ? ' *' : ''}`, value: terminal, color: '#8b5cf6' })
  if (selected != null && selected > 0) bars.push({ label: 'Selected Model', value: selected, color: colors.accent })

  if (bars.length === 0) return null
  return (
    <div>
      <div style={{ fontSize: 10, color: colors.textDim, marginBottom: 6 }}>* = active model for this market</div>
      <ProbabilityBars bars={bars} />
    </div>
  )
}

function VolBreakdown({ raw }: { raw: Record<string, unknown> }) {
  const data: Record<string, number> = {}
  if (raw.historical_vol != null) data.historical = raw.historical_vol as number
  if (raw.ewm_vol != null) data.ewm = raw.ewm_vol as number
  if (raw.short_term_vol != null) data.short_term = raw.short_term_vol as number
  if (raw.deribit_iv != null) data.deribit_iv = raw.deribit_iv as number
  if (raw.annualized_vol != null) data.selected = raw.annualized_vol as number

  if (Object.keys(data).length === 0) return null
  return (
    <div>
      <VolComparison data={data} />
      <div style={{ display: 'flex', gap: 12, marginTop: 8, fontSize: 11, color: colors.textDim }}>
        <span>Vol Source: <span style={{ color: colors.textMuted, fontWeight: 600 }}>{String(raw.vol_source || '--')}</span></span>
        <span>Data Interval: <span style={{ color: colors.textMuted }}>{fmt(raw.avg_interval_hours, 1)}h</span></span>
      </div>
    </div>
  )
}

function DriftAnalysis({ raw }: { raw: Record<string, unknown> }) {
  const realized = raw.realized_drift as number | undefined
  const shrunk = raw.shrunk_drift as number | undefined
  const stderr = raw.drift_stderr as number | undefined

  if (realized == null && shrunk == null) return null

  const shrinkPct = realized != null && shrunk != null && realized !== 0
    ? ((1 - Math.abs(shrunk) / Math.abs(realized)) * 100)
    : null

  return (
    <InfoBox>
      <div style={{ fontSize: 11, fontWeight: 600, color: colors.textMuted, marginBottom: 8, textTransform: 'uppercase' }}>
        Drift (Bayesian Shrinkage)
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
        <Stat label="Realized Drift" value={realized != null ? (realized * 100).toFixed(2) + '%' : '--'} />
        <Stat label="Shrunk Drift" value={shrunk != null ? (shrunk * 100).toFixed(2) + '%' : '--'}
          highlight={shrunk != null && Math.abs(shrunk) < 0.01 ? colors.textDim : undefined} />
        <Stat label="Std Error" value={stderr != null ? (stderr * 100).toFixed(2) + '%' : '--'} />
      </div>
      {shrinkPct != null && (
        <div style={{ marginTop: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: colors.textDim, marginBottom: 3 }}>
            <span>Shrinkage toward zero</span>
            <span style={{ fontFamily: "'JetBrains Mono', monospace" }}>{shrinkPct.toFixed(0)}%</span>
          </div>
          <div style={{ height: 4, background: colors.border, borderRadius: 2, overflow: 'hidden' }}>
            <div style={{
              height: '100%', width: `${Math.min(Math.max(shrinkPct, 0), 100)}%`,
              background: shrinkPct > 70 ? colors.warning : colors.accent, borderRadius: 2,
            }} />
          </div>
        </div>
      )}
    </InfoBox>
  )
}

function DepthSection({ depth }: { depth: Record<string, unknown> }) {
  if (!depth || Object.keys(depth).length === 0) return null
  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: 10 }}>
        <Stat label="Total Depth" value={fmtUsd(depth.total_depth_usd)} />
        <Stat label="Slippage" value={fmtPct(depth.slippage)} highlight={
          (depth.slippage as number) > 0.02 ? colors.warning : colors.success
        } />
        <Stat label="Best Price" value={fmtPct(depth.best_price)} />
        <Stat label="Avg Fill Price" value={fmtPct(depth.avg_fill_price)} />
        <Stat label="Max Fillable" value={fmtUsd(depth.max_fillable_usd)} />
        <Stat label="Price Levels" value={String(depth.levels || '--')} />
      </div>
      {Boolean(depth.was_adjusted) && (
        <div style={{
          marginTop: 8, padding: '6px 10px', borderRadius: 6,
          background: colors.warningDim, fontSize: 11, color: colors.warning,
        }}>
          Bet size reduced from original Kelly size due to slippage. Adjusted to {fmtUsd(depth.adjusted_bet_usd)}.
        </div>
      )}
      {Boolean(depth.skip_reason) && (
        <div style={{
          marginTop: 8, padding: '6px 10px', borderRadius: 6,
          background: colors.dangerDim, fontSize: 11, color: colors.danger,
        }}>
          Depth skip: {String(depth.skip_reason)}
        </div>
      )}
    </div>
  )
}

function SignalCard({ signal, index }: { signal: Record<string, unknown>; index: number }) {
  const [expanded, setExpanded] = useState(false)
  const source = String(signal.source || '?')
  const prob = signal.probability as number | null
  const conf = signal.confidence as number | undefined
  const raw = (signal.raw_data || {}) as Record<string, unknown>
  const reasoning = String(signal.reasoning || '')
  const model = String(signal.model_used || 'none')
  const dataPoints = signal.data_points as number | undefined

  const palette = ['#22c55e', '#3b82f6', '#f59e0b', '#8b5cf6', '#06b6d4']
  const cardColor = palette[index % palette.length]

  return (
    <div style={{
      background: colors.bgSecondary, border: `1px solid ${colors.border}`,
      borderLeft: `3px solid ${cardColor}`, borderRadius: 8, marginBottom: 8, overflow: 'hidden',
    }}>
      <div
        onClick={() => setExpanded(!expanded)}
        style={{
          padding: '10px 12px', cursor: 'pointer', display: 'flex', alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: cardColor }}>{source}</span>
          {prob != null && (
            <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 13, color: colors.textPrimary }}>
              {(prob * 100).toFixed(1)}%
            </span>
          )}
          {conf != null && (
            <span style={{ fontSize: 11, color: colors.textDim }}>conf {(conf * 100).toFixed(0)}%</span>
          )}
          {model !== 'none' && (
            <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 10, background: colors.accentDim, color: colors.textDim }}>
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
              fontSize: 11, fontFamily: "'JetBrains Mono', monospace", color: colors.textSecondary,
              whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 200, overflow: 'auto',
              lineHeight: 1.4,
            }}>
              {reasoning}
            </pre>
          )}

          {/* Source-specific raw data displays */}
          {source === 'resolution_crypto' && Object.keys(raw).length > 0 && (
            <div style={{ marginTop: 10 }}>
              <CoinInfo raw={raw} />
              <div style={{ marginTop: 10 }}>
                <ProbabilityModels raw={raw} />
              </div>
              <div style={{ marginTop: 10 }}>
                <VolBreakdown raw={raw} />
              </div>
              <DriftAnalysis raw={raw} />
            </div>
          )}

          {source === 'prediction_markets' && Object.keys(raw).length > 0 && (
            <div style={{ marginTop: 10 }}>
              {Array.isArray(raw.matched_markets) && (
                <div>
                  <div style={{ fontSize: 11, fontWeight: 600, color: colors.textMuted, marginBottom: 6 }}>
                    Matched Markets ({(raw.matched_markets as unknown[]).length})
                  </div>
                  {(raw.matched_markets as Record<string, unknown>[]).map((mm, i) => (
                    <div key={i} style={{
                      padding: '6px 8px', marginBottom: 4, borderRadius: 6,
                      background: 'rgba(0,0,0,0.2)', fontSize: 11,
                    }}>
                      <div style={{ color: colors.textSecondary, marginBottom: 2 }}>{String(mm.title || mm.question || '?')}</div>
                      <div style={{ display: 'flex', gap: 10, color: colors.textDim }}>
                        <span>Platform: <span style={{ color: colors.textMuted }}>{String(mm.platform || mm.source || '?')}</span></span>
                        {mm.probability != null && (
                          <span>Prob: <span style={{ fontFamily: "'JetBrains Mono', monospace", color: colors.accent }}>{fmtPct(mm.probability)}</span></span>
                        )}
                        {mm.similarity != null && (
                          <span>Similarity: <span style={{ fontFamily: "'JetBrains Mono', monospace" }}>{fmt(mm.similarity, 2)}</span></span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {raw.platforms_checked != null && (
                <div style={{ fontSize: 10, color: colors.textDim, marginTop: 4 }}>
                  Platforms checked: {String(raw.platforms_checked)}
                </div>
              )}
            </div>
          )}

          {source === 'web_search' && Object.keys(raw).length > 0 && (
            <div style={{ marginTop: 10 }}>
              {Array.isArray(raw.evidence) && (
                <div>
                  <div style={{ fontSize: 11, fontWeight: 600, color: colors.textMuted, marginBottom: 6 }}>
                    Search Evidence ({(raw.evidence as unknown[]).length})
                  </div>
                  {(raw.evidence as Record<string, unknown>[]).map((ev, i) => (
                    <div key={i} style={{
                      padding: '6px 8px', marginBottom: 4, borderRadius: 6,
                      background: 'rgba(0,0,0,0.2)', fontSize: 11, color: colors.textSecondary,
                    }}>
                      {String(ev.title || ev.text || ev.snippet || JSON.stringify(ev))}
                    </div>
                  ))}
                </div>
              )}
              {raw.search_query != null && (
                <div style={{ fontSize: 10, color: colors.textDim, marginTop: 4 }}>
                  Query: &quot;{String(raw.search_query)}&quot;
                </div>
              )}
            </div>
          )}

          {/* Fallback: show all raw_data as JSON for any source */}
          {Object.keys(raw).length > 0 && !['resolution_crypto', 'prediction_markets', 'web_search'].includes(source) && (
            <details style={{ marginTop: 8 }}>
              <summary style={{ fontSize: 10, color: colors.textDim, cursor: 'pointer' }}>Raw Data</summary>
              <pre style={{
                background: 'rgba(0,0,0,0.2)', borderRadius: 6, padding: 8, marginTop: 4,
                fontSize: 10, fontFamily: "'JetBrains Mono', monospace", color: colors.textDim,
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

function KellyMath({ kelly }: { kelly: Record<string, unknown> }) {
  const est = kelly.estimated_prob as number | undefined
  const mkt = kelly.market_price as number | undefined
  const eff = kelly.effective_prob as number | undefined
  const conf = kelly.confidence as number | undefined
  const rawK = kelly.raw_kelly as number | undefined
  const fracK = kelly.fractional_kelly as number | undefined
  const bet = kelly.bet_size as number | undefined
  const bank = kelly.bankroll as number | undefined
  const edge = kelly.edge as number | undefined
  const ev = kelly.expected_value as number | undefined
  const side = String(kelly.side || '--')
  const shouldTrade = kelly.should_trade as boolean | undefined
  const skipReason = kelly.skip_reason as string | undefined

  return (
    <div>
      {/* Step-by-step math */}
      <InfoBox>
        <div style={{ fontSize: 11, fontWeight: 600, color: colors.textMuted, marginBottom: 8, textTransform: 'uppercase' }}>
          Kelly Formula Steps
        </div>
        <MetricRow label="1. Frontier estimate (p)" value={fmtPct(est)} />
        <MetricRow label="2. Market price (m)" value={fmtPct(mkt)} />
        <MetricRow label="3. Confidence" value={fmtPct(conf)} />
        <MetricRow label="4. Blend: eff = conf*p + (1-conf)*m" value={fmtPct(eff)} highlight={colors.accent} />
        <MetricRow label="5. Edge = eff - m" value={fmtPct(edge)} highlight={
          edge != null && edge > 0 ? colors.success : colors.danger
        } />
        <MetricRow label="6. Full Kelly f* = edge/odds" value={fmtPct(rawK)} />
        <MetricRow label="7. Fractional (0.25x)" value={fmtPct(fracK)} highlight={colors.accent} />
        <MetricRow label="8. Bankroll" value={fmtUsd(bank)} />
        <MetricRow label="9. Bet = bankroll * f(0.25)" value={fmtUsd(bet)} highlight={colors.accentLight} />
        {ev != null && <MetricRow label="10. Expected Value" value={fmtUsd(ev)} highlight={ev > 0 ? colors.success : colors.danger} />}
      </InfoBox>

      <div style={{ display: 'flex', gap: 8, marginTop: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <Badge text={side} color={side.includes('YES') ? colors.success : colors.danger} />
        <Badge text={shouldTrade ? 'TRADE' : 'SKIP'} color={shouldTrade ? colors.success : colors.warning} />
        {skipReason && <span style={{ fontSize: 11, color: colors.textDim }}>{skipReason}</span>}
      </div>
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
  const [showProbs, setShowProbs] = useState(true)
  const [showSignals, setShowSignals] = useState(true)
  const [showKelly, setShowKelly] = useState(true)
  const [showDepth, setShowDepth] = useState(true)
  const [showFrontier, setShowFrontier] = useState(true)

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
  const market = (data.market_data || data.market || {}) as Record<string, unknown>
  const meta = (data.market_meta || {}) as Record<string, unknown>
  const agg = (data.aggregation || {}) as Record<string, unknown>
  const kelly = (data.kelly || data.decision || {}) as Record<string, unknown>
  const exec = (data.execution || {}) as Record<string, unknown>
  const depth = (data.depth || {}) as Record<string, unknown>
  const signals = (agg.signals || data.signals || []) as Record<string, unknown>[]

  const question = (market.question || data.question || conditionId) as string
  const marketPrice = parseFloat(String(agg.market_price || market.market_price || market.yes_price || 0))
  const estimate = parseFloat(String(agg.final_probability || agg.estimated_prob || 0))
  const preliminary = parseFloat(String(agg.preliminary_probability || 0))
  const effective = parseFloat(String(kelly.effective_prob || agg.effective_prob || estimate))
  const confidence = parseFloat(String(agg.confidence || 0))
  const agreement = String(agg.signals_agreement || '--')
  const efficiency = String(agg.market_efficiency || '--')

  // Build probability comparison bars
  const probBars: { label: string; value: number; color: string }[] = [
    { label: 'Market', value: marketPrice, color: colors.textMuted },
  ]
  if (preliminary > 0) probBars.push({ label: 'Preliminary', value: preliminary, color: '#8b5cf6' })
  probBars.push({ label: 'Frontier', value: estimate, color: colors.accent })
  probBars.push({ label: 'Effective (blended)', value: effective, color: colors.success })
  if (Array.isArray(signals)) {
    for (const s of signals) {
      const p = parseFloat(String(s.probability || 0))
      if (p > 0) {
        probBars.push({ label: String(s.source || '?'), value: p, color: colors.warning })
      }
    }
  }

  // Build weight data
  const weightData: { label: string; weight: number }[] = []
  if (Array.isArray(signals)) {
    for (const s of signals) {
      const w = parseFloat(String(s.effective_weight || s.weight || s.confidence || 0))
      if (w > 0) {
        weightData.push({ label: String(s.source || '?'), weight: w })
      }
    }
  }

  // Crypto raw data (from resolution_crypto signal)
  const cryptoSignal = signals.find(s => s.source === 'resolution_crypto')
  const cryptoRaw = (cryptoSignal?.raw_data || {}) as Record<string, unknown>
  const hasCryptoData = Object.keys(cryptoRaw).length > 0

  // Price history (from crypto raw data)
  const priceHistory = (cryptoRaw.price_history || data.price_history || []) as { date: string; price: number }[]
  const targetPrice = parseFloat(String(cryptoRaw.target_price || market._target_price || 0))

  const reasoning = String(agg.reasoning || agg.frontier_reasoning || data.reasoning || '')
  const divergence = Math.abs(estimate - marketPrice)
  const divColor = divergence < 0.1 ? colors.success : divergence < 0.2 ? colors.warning : colors.danger

  return (
    <div>
      {/* Header: Question + quick stats */}
      <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 10, lineHeight: 1.4 }}>{question}</div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6, flexWrap: 'wrap' }}>
        <span style={{
          fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: colors.textDim,
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
        {agg.total_data_points != null && (
          <span style={{ fontSize: 10, color: colors.textDim }}>{String(agg.total_data_points)} data points</span>
        )}
      </div>

      {/* 1. Market Metadata */}
      {Object.keys(meta).length > 0 && (
        <>
          <SectionHeader title="Market Overview" collapsed={!showMeta} onToggle={() => setShowMeta(!showMeta)} />
          {showMeta && <MarketMeta meta={meta} marketPrice={marketPrice} />}
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

      {/* 2. Probability Comparison */}
      <SectionHeader title="Probability Comparison" collapsed={!showProbs} onToggle={() => setShowProbs(!showProbs)} />
      {showProbs && (
        <>
          <ProbabilityBars bars={probBars} />
          {weightData.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <div style={{ fontSize: 10, color: colors.textDim, marginBottom: 4, textTransform: 'uppercase' }}>Signal Weights</div>
              <SignalWeights data={weightData} />
            </div>
          )}
        </>
      )}

      {/* 3. Individual Signal Details */}
      <SectionHeader title={`Signal Providers (${signals.length})`} collapsed={!showSignals} onToggle={() => setShowSignals(!showSignals)} />
      {showSignals && (
        <div>
          {signals.map((s, i) => (
            <SignalCard key={String(s.source || i)} signal={s} index={i} />
          ))}
        </div>
      )}

      {/* 4. Crypto Model Deep Dive (only if resolution_crypto data available) */}
      {hasCryptoData && (
        <>
          <SectionHeader title="Crypto Model Data" />
          {priceHistory.length > 0 && <PriceChart data={priceHistory} target={targetPrice} />}
          {/* Coin Info is already shown inside resolution_crypto signal card */}
        </>
      )}

      {/* 5. Kelly Sizing (full math) */}
      {Object.keys(kelly).length > 0 && (
        <>
          <SectionHeader title="Kelly Criterion Sizing" collapsed={!showKelly} onToggle={() => setShowKelly(!showKelly)} />
          {showKelly && <KellyMath kelly={kelly} />}
        </>
      )}

      {/* 6. Depth Analysis */}
      {Object.keys(depth).length > 0 && (
        <>
          <SectionHeader title="Order Book Depth" collapsed={!showDepth} onToggle={() => setShowDepth(!showDepth)} />
          {showDepth && <DepthSection depth={depth} />}
        </>
      )}

      {/* 7. Execution */}
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
              <span style={{ color: colors.textDim, fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
                ID: {String(exec.trade_id).slice(0, 8)}
              </span>
            )}
            {exec.price != null && <span style={{ fontSize: 12 }}>Price: {fmtPct(exec.price)}</span>}
            {exec.size != null && <span style={{ fontSize: 12 }}>Size: {fmtUsd(exec.size)}</span>}
          </div>
        </>
      )}

      {/* 8. Frontier Reasoning */}
      {(reasoning || confidence > 0) && (
        <>
          <SectionHeader title="Frontier Model Reasoning" collapsed={!showFrontier} onToggle={() => setShowFrontier(!showFrontier)} />
          {showFrontier && (
            <>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 12, color: colors.textMuted }}>
                  Final: <span style={{ color: colors.textPrimary, fontWeight: 600, fontFamily: "'JetBrains Mono', monospace" }}>{fmtPct(estimate)}</span>
                </span>
                <span style={{ fontSize: 12, color: colors.textMuted }}>
                  Confidence: <span style={{ color: colors.textPrimary, fontWeight: 600, fontFamily: "'JetBrains Mono', monospace" }}>{fmtPct(confidence)}</span>
                </span>
                {preliminary > 0 && (
                  <span style={{ fontSize: 12, color: colors.textMuted }}>
                    Pre-frontier: <span style={{ fontFamily: "'JetBrains Mono', monospace" }}>{fmtPct(preliminary)}</span>
                  </span>
                )}
              </div>

              {reasoning && (
                <pre style={{
                  background: colors.bgSecondary,
                  border: `1px solid ${colors.border}`,
                  borderRadius: 8,
                  padding: 14,
                  fontSize: 12,
                  fontFamily: "'JetBrains Mono', monospace",
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

      {/* Raw JSON fallback for debugging */}
      <details style={{ marginTop: 20 }}>
        <summary style={{ fontSize: 10, color: colors.textDim, cursor: 'pointer' }}>Raw API Response</summary>
        <pre style={{
          background: 'rgba(0,0,0,0.3)', borderRadius: 6, padding: 10, marginTop: 4,
          fontSize: 10, fontFamily: "'JetBrains Mono', monospace", color: colors.textDim,
          whiteSpace: 'pre-wrap', maxHeight: 400, overflow: 'auto',
        }}>
          {JSON.stringify(data, null, 2)}
        </pre>
      </details>
    </div>
  )
}
