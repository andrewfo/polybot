import { useState, useEffect, useCallback } from 'react'
import { colors, cardStyle, fonts } from '../theme'
import { api, Trade, TradeDetail, FrontierDecision, Signal, PaperBalance } from '../api'
import AnalysisDetail from './AnalysisDetail'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

function formatTs(ts: string | null | undefined): string {
  if (!ts) return '--'
  try {
    const d = new Date(ts)
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' ' +
      d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })
  } catch { return ts }
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SectionHeader({ title, badge }: { title: string; badge?: string }) {
  return (
    <h3 style={{
      margin: '20px 0 10px', fontSize: 10, fontWeight: 600,
      color: colors.textMuted, textTransform: 'uppercase',
      letterSpacing: '0.1em',
      borderBottom: `1px solid ${colors.border}`,
      paddingBottom: 8,
      display: 'flex', alignItems: 'center', gap: 6,
      fontFamily: fonts.mono,
    }}>
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

function PnlBadge({ pnl }: { pnl: number | null | undefined }) {
  if (pnl == null) return <span style={{ fontSize: 11, color: colors.textDim, fontFamily: fonts.mono }}>--</span>
  const positive = pnl > 0
  const fg = positive ? colors.success : pnl < 0 ? colors.danger : colors.textMuted
  return (
    <span style={{
      fontSize: 11, fontWeight: 600, fontFamily: fonts.mono, color: fg,
      textShadow: 'none',
    }}>
      {positive ? '+' : ''}{fmtUsd(pnl)}
    </span>
  )
}

function StatusBadge({ status }: { status: string }) {
  const upper = status.toUpperCase()
  const fg = upper === 'FILLED' ? colors.success
    : upper === 'PENDING' ? colors.accent
    : upper === 'CANCELLED' ? colors.danger
    : colors.textMuted
  const bg = fg + '15'
  return (
    <span style={{
      padding: '2px 8px', borderRadius: 3, fontSize: 9, fontWeight: 600,
      background: bg, color: fg, letterSpacing: '0.06em',
      fontFamily: fonts.mono, textTransform: 'uppercase',
      border: `1px solid ${fg}20`,
    }}>
      {upper}
    </span>
  )
}

function SideBadge({ side }: { side: string }) {
  const isYes = side.toUpperCase().includes('YES')
  const fg = isYes ? colors.success : colors.danger
  return (
    <span style={{
      padding: '2px 6px', borderRadius: 3, fontSize: 9, fontWeight: 600,
      background: fg + '12', color: fg, fontFamily: fonts.mono,
      border: `1px solid ${fg}20`, letterSpacing: '0.04em',
    }}>
      {side.toUpperCase()}
    </span>
  )
}

function PaperBadge() {
  return (
    <span style={{
      padding: '2px 6px', borderRadius: 3, fontSize: 8, fontWeight: 600,
      background: colors.warningDim, color: colors.warning, fontFamily: fonts.mono,
      border: `1px solid rgba(217, 160, 63,0.15)`, letterSpacing: '0.04em',
    }}>
      PAPER
    </span>
  )
}

const RESOLUTION_CONFIG: Record<string, { label: string; fg: string }> = {
  pending_fill: { label: 'AWAITING FILL', fg: '#8899bb' },
  open_winning: { label: 'WINNING', fg: '#3fb970' },
  open_losing:  { label: 'LOSING', fg: '#e5484d' },
  open_flat:    { label: 'FLAT', fg: '#8899bb' },
  won:          { label: 'WON', fg: '#3fb970' },
  lost:         { label: 'LOST', fg: '#e5484d' },
  closed_profit:{ label: 'CLOSED +', fg: '#3fb970' },
  closed_loss:  { label: 'CLOSED -', fg: '#e5484d' },
  expired:      { label: 'EXPIRED', fg: '#556688' },
}

function ResolutionBadge({ status }: { status: string | undefined }) {
  if (!status) return null
  const cfg = RESOLUTION_CONFIG[status] || { label: status.toUpperCase(), fg: colors.textMuted }
  return (
    <span style={{
      padding: '2px 6px', borderRadius: 3, fontSize: 8, fontWeight: 600,
      background: cfg.fg + '12', color: cfg.fg, fontFamily: fonts.mono,
      border: `1px solid ${cfg.fg}20`, letterSpacing: '0.04em',
    }}>
      {cfg.label}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Signal card inside detail panel
// ---------------------------------------------------------------------------

function SignalCard({ signal }: { signal: Signal }) {
  const [expanded, setExpanded] = useState(false)
  const prob = signal.probability
  const conf = signal.confidence
  const probColor = prob > 0.6 ? colors.success : prob < 0.4 ? colors.danger : colors.warning

  let rawData: Record<string, unknown> | null = null
  if (signal.raw_data) {
    try { rawData = JSON.parse(signal.raw_data) } catch { /* ignore */ }
  }

  return (
    <div style={{
      background: 'rgba(11, 12, 14, 0.65)', border: `1px solid ${colors.border}`,
      borderRadius: 3, padding: 12, marginBottom: 8,
      backdropFilter: 'blur(8px)',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontSize: 11, fontWeight: 600, color: colors.textPrimary, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
          {signal.signal_source.replace(/_/g, ' ')}
        </span>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <span style={{ fontSize: 10, color: colors.textDim, fontFamily: fonts.mono }}>
            conf {(conf * 100).toFixed(0)}%
          </span>
          <span style={{ fontSize: 14, fontWeight: 700, fontFamily: fonts.mono, color: probColor }}>
            {(prob * 100).toFixed(1)}%
          </span>
        </div>
      </div>

      {/* Probability bar */}
      <div style={{ height: 4, background: 'rgba(255,255,255,0.04)', borderRadius: 2, marginBottom: 8, overflow: 'hidden' }}>
        <div style={{
          width: `${prob * 100}%`, height: '100%', borderRadius: 2,
          background: probColor, boxShadow: `0 0 8px ${probColor}40`,
          transition: 'width 0.3s ease',
        }} />
      </div>

      {/* Reasoning */}
      {signal.reasoning && (
        <div style={{
          fontSize: 11, color: colors.textSecondary, lineHeight: 1.5,
          maxHeight: expanded ? 'none' : 60, overflow: 'hidden',
          position: 'relative',
        }}>
          {signal.reasoning}
          {!expanded && signal.reasoning.length > 200 && (
            <div style={{
              position: 'absolute', bottom: 0, left: 0, right: 0, height: 24,
              background: 'linear-gradient(transparent, rgba(14,15,18,0.92))',
            }} />
          )}
        </div>
      )}
      {signal.reasoning && signal.reasoning.length > 200 && (
        <button
          onClick={() => setExpanded(!expanded)}
          style={{
            background: 'none', border: 'none', color: colors.accent, fontSize: 10,
            cursor: 'pointer', padding: '4px 0', fontFamily: fonts.mono,
          }}
        >
          {expanded ? 'show less' : 'show more'}
        </button>
      )}

      {/* Model + timestamp */}
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6, fontSize: 9, color: colors.textDim, fontFamily: fonts.mono }}>
        <span>{signal.model_used || '--'}</span>
        <span>{formatTs(signal.timestamp)}</span>
      </div>

      {/* Raw data (collapsed) */}
      {rawData && (
        <RawDataSection data={rawData} />
      )}
    </div>
  )
}

function RawDataSection({ data }: { data: Record<string, unknown> }) {
  const [show, setShow] = useState(false)
  return (
    <div style={{ marginTop: 6 }}>
      <button
        onClick={() => setShow(!show)}
        style={{
          background: 'none', border: 'none', color: colors.textDim, fontSize: 9,
          cursor: 'pointer', padding: '2px 0', fontFamily: fonts.mono,
        }}
      >
        {show ? '- hide raw data' : '+ raw data'}
      </button>
      {show && (
        <pre style={{
          fontSize: 10, color: colors.textMuted, fontFamily: fonts.mono,
          background: 'rgba(0,0,0,0.3)', borderRadius: 4, padding: 8,
          marginTop: 4, overflow: 'auto', maxHeight: 200,
          border: `1px solid ${colors.border}`,
        }}>
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Frontier Decision detail
// ---------------------------------------------------------------------------

function FrontierDecisionPanel({ fd }: { fd: FrontierDecision }) {
  const edgeColor = fd.edge > 0.05 ? colors.success : fd.edge > 0 ? colors.warning : colors.danger
  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: 8, marginBottom: 12 }}>
        {[
          { label: 'Estimated Prob', value: fmtPct(fd.estimated_prob), color: colors.accent },
          { label: 'Effective Prob', value: fmtPct(fd.effective_prob), color: colors.accentLight },
          { label: 'Market Price', value: fmtPct(fd.market_price) },
          { label: 'Edge', value: fmtPct(fd.edge), color: edgeColor },
          { label: 'Kelly Fraction', value: fmtPct(fd.kelly_fraction) },
          { label: 'Bet Size', value: fmtUsd(fd.bet_size_usd) },
          { label: 'Confidence', value: fmtPct(fd.confidence) },
          { label: 'Decision', value: fd.should_trade ? 'TRADE' : 'SKIP', color: fd.should_trade ? colors.success : colors.warning },
        ].map((s, i) => (
          <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <span style={{ fontSize: 9, color: colors.textDim, textTransform: 'uppercase', letterSpacing: '0.06em', fontFamily: fonts.mono }}>
              {s.label}
            </span>
            <span style={{
              fontSize: 13, fontWeight: 600, fontFamily: fonts.mono,
              color: s.color || colors.textPrimary,
              textShadow: 'none',
            }}>
              {s.value}
            </span>
          </div>
        ))}
      </div>

      {/* Edge bar */}
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: 9, color: colors.textDim, marginBottom: 4, fontFamily: fonts.mono }}>EDGE VS MARKET</div>
        <div style={{ position: 'relative', height: 20, background: 'rgba(255,255,255,0.03)', borderRadius: 4, overflow: 'hidden' }}>
          {/* Market price marker */}
          <div style={{
            position: 'absolute', left: `${fd.market_price * 100}%`, top: 0, bottom: 0,
            width: 2, background: colors.textDim, zIndex: 2,
          }} />
          {/* Estimate bar */}
          <div style={{
            position: 'absolute',
            left: `${Math.min(fd.market_price, fd.effective_prob) * 100}%`,
            width: `${Math.abs(fd.effective_prob - fd.market_price) * 100}%`,
            top: 2, bottom: 2, borderRadius: 3,
            background: edgeColor, opacity: 0.5,
          }} />
          {/* Labels */}
          <span style={{
            position: 'absolute', left: `${fd.market_price * 100}%`, top: -1,
            transform: 'translateX(-50%)', fontSize: 8, color: colors.textDim, fontFamily: fonts.mono,
          }}>mkt</span>
        </div>
      </div>

      {fd.skip_reason && (
        <div style={{
          marginTop: 8, padding: '6px 10px', borderRadius: 4,
          background: colors.warningDim, border: `1px solid rgba(217, 160, 63,0.15)`,
          fontSize: 11, color: colors.warning,
        }}>
          Skip reason: {fd.skip_reason}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Trade Detail Panel (right side)
// ---------------------------------------------------------------------------

function TradeDetailPanel({ tradeId }: { tradeId: string }) {
  const [detail, setDetail] = useState<TradeDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [hasAnalysis, setHasAnalysis] = useState<boolean | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setHasAnalysis(null)
    api.fetchTradeDetail(tradeId)
      .then(d => {
        if (cancelled) return
        setDetail(d)
        // Probe whether full analysis exists for this market
        api.fetchAnalysisDetail(d.trade.market_id)
          .then(() => { if (!cancelled) setHasAnalysis(true) })
          .catch(() => { if (!cancelled) setHasAnalysis(false) })
      })
      .catch(e => { if (!cancelled) setError(e.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [tradeId])

  if (loading) return (
    <div style={{ padding: 40, textAlign: 'center', color: colors.textDim }}>
      <div style={{ fontSize: 12, fontFamily: fonts.mono, animation: 'textGlow 2s ease-in-out infinite' }}>Loading analysis...</div>
    </div>
  )
  if (error) return (
    <div style={{ padding: 20, color: colors.danger, fontSize: 12 }}>Error: {error}</div>
  )
  if (!detail) return null

  const { trade, frontier_decision, signals } = detail

  return (
    <div style={{ padding: '4px 0' }}>
      {/* Trade execution header */}
      <div style={{
        marginBottom: 16, padding: 16,
        background: 'rgba(11, 12, 14, 0.65)', border: `1px solid ${colors.border}`,
        borderRadius: 3, backdropFilter: 'blur(8px)',
      }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: colors.textPrimary, lineHeight: 1.4, marginBottom: 10 }}>
          {trade.market_question || 'Unknown Market'}
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12 }}>
          <SideBadge side={trade.side} />
          <StatusBadge status={trade.status} />
          <ResolutionBadge status={trade.resolution_status} />
          {trade.paper === 1 && <PaperBadge />}
          <span style={{ fontSize: 10, color: colors.textDim, fontFamily: fonts.mono, marginLeft: 'auto' }}>
            {formatTs(trade.timestamp)}
          </span>
        </div>

        {(() => {
          const ep = trade.fill_price != null ? trade.fill_price : trade.price
          const cost = trade.size * ep
          const maxGain = trade.size - cost
          const pnlColor = trade.pnl != null ? (trade.pnl > 0 ? colors.success : trade.pnl < 0 ? colors.danger : undefined) : undefined
          const cp = trade.current_price
          const upnl = trade.unrealized_pnl
          const rs = trade.resolution_status || ''
          const isClosed = rs === 'won' || rs === 'lost' || rs === 'expired'
            || rs === 'closed_profit' || rs === 'closed_loss'
          const liveOpen = !isClosed && cp != null && upnl != null
          const showThenNow = liveOpen || (isClosed && trade.pnl != null)
          const exitValue = isClosed && trade.pnl != null
            ? cost + trade.pnl
            : liveOpen ? trade.size * (cp as number) : null
          // Exit price (odds when sold). Only fall back to the 0/1 rail when
          // we're certain it was a real market resolution — never invent it
          // for take-profit / stop-loss closes.
          const exitPrice: number | null = isClosed
            ? (trade.exit_price != null ? trade.exit_price
                : (rs === 'won' ? 1 : rs === 'lost' ? 0 : null))
            : (liveOpen ? (cp as number) : null)
          const exitLabel = isClosed
            ? (rs === 'won' ? 'Worth At Win'
                : rs === 'lost' ? 'Worth At Loss'
                : rs === 'expired' ? 'Worth At Expiry'
                : 'Worth At Close')
            : 'Worth Now'
          const fromLabel = 'Worth When Bought'
          const entryTs = trade.timestamp
          const exitTs = isClosed ? (trade.closed_at || null) : (liveOpen ? new Date().toISOString() : null)
          const fmtDuration = (a: string | null | undefined, b: string | null | undefined): string | null => {
            if (!a || !b) return null
            const ms = new Date(b).getTime() - new Date(a).getTime()
            if (!isFinite(ms) || ms < 0) return null
            const s = Math.floor(ms / 1000)
            if (s < 60) return `${s}s`
            const m = Math.floor(s / 60)
            if (m < 60) return `${m}m`
            const h = Math.floor(m / 60)
            if (h < 24) return `${h}h ${m % 60}m`
            const d = Math.floor(h / 24)
            return `${d}d ${h % 24}h`
          }
          const heldFor = fmtDuration(entryTs, exitTs)
          const valueDelta = exitValue != null ? exitValue - cost : null
          const valuePct = valueDelta != null && cost > 0 ? (valueDelta / cost) * 100 : null
          const deltaColor = valueDelta == null ? colors.textMuted
            : valueDelta > 0 ? colors.success
            : valueDelta < 0 ? colors.danger
            : colors.textMuted
          return (
          <>
            {/* Cost hero — the main "how much money was placed" number */}
            <div style={{
              display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 12,
              padding: '10px 12px', borderRadius: 3,
              background: 'rgba(68,136,204,0.06)', border: `1px solid ${colors.accent}20`,
            }}>
              <span style={{ fontSize: 9, color: colors.textDim, textTransform: 'uppercase', fontFamily: fonts.mono, letterSpacing: '0.06em' }}>
                Amount Placed
              </span>
              <span style={{ fontSize: 22, fontWeight: 700, fontFamily: fonts.mono, color: colors.textPrimary }}>
                {fmtUsd(cost)}
              </span>
              {ep > 0 && ep < 1 && (
                <span style={{ fontSize: 12, color: colors.success, fontFamily: fonts.mono }}>
                  +{fmtUsd(maxGain)} potential ({cost > 0 ? ((maxGain / cost) * 100).toFixed(0) : '0'}%)
                </span>
              )}
            </div>

            {/* Then vs Now — entry value vs current/exit value */}
            {showThenNow && (
              <div style={{
                display: 'grid', gridTemplateColumns: '1fr auto 1fr', gap: 12, alignItems: 'center',
                marginBottom: 12, padding: '10px 12px', borderRadius: 3,
                background: 'rgba(11,12,14,0.5)', border: `1px solid ${deltaColor}25`,
              }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                  <span style={{ fontSize: 9, color: colors.textDim, textTransform: 'uppercase', fontFamily: fonts.mono, letterSpacing: '0.06em' }}>
                    {fromLabel}
                  </span>
                  <span style={{ fontSize: 18, fontWeight: 700, fontFamily: fonts.mono, color: colors.textPrimary }}>
                    {fmtUsd(cost)}
                  </span>
                  <span style={{ fontSize: 10, color: colors.textDim, fontFamily: fonts.mono }}>
                    @ {fmtPct(ep)} odds
                  </span>
                  <span style={{ fontSize: 9, color: colors.textDim, fontFamily: fonts.mono }}>
                    {formatTs(entryTs)}
                  </span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
                  <span style={{ fontSize: 20, color: deltaColor, fontFamily: fonts.mono }}>&rarr;</span>
                  {heldFor && (
                    <span style={{ fontSize: 9, color: colors.textDim, fontFamily: fonts.mono, letterSpacing: '0.04em' }}>
                      {heldFor}
                    </span>
                  )}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 2, alignItems: 'flex-end' }}>
                  <span style={{ fontSize: 9, color: colors.textDim, textTransform: 'uppercase', fontFamily: fonts.mono, letterSpacing: '0.06em' }}>
                    {exitLabel}
                  </span>
                  <span style={{
                    fontSize: 18, fontWeight: 700, fontFamily: fonts.mono, color: deltaColor,
                    textShadow: 'none',
                  }}>
                    {fmtUsd(exitValue)}
                  </span>
                  {exitPrice != null && (
                    <span style={{ fontSize: 10, color: colors.textDim, fontFamily: fonts.mono }}>
                      @ {fmtPct(exitPrice)} odds
                      {!isClosed && <span style={{ marginLeft: 4, color: colors.textDim }}>(now)</span>}
                    </span>
                  )}
                  <span style={{ fontSize: 10, color: deltaColor, fontFamily: fonts.mono, fontWeight: 600 }}>
                    {valueDelta != null && valueDelta >= 0 ? '+' : ''}{fmtUsd(valueDelta)}
                    {valuePct != null && (
                      <span style={{ color: colors.textDim, marginLeft: 4 }}>
                        ({valuePct >= 0 ? '+' : ''}{valuePct.toFixed(1)}%)
                      </span>
                    )}
                  </span>
                  <span style={{ fontSize: 9, color: colors.textDim, fontFamily: fonts.mono }}>
                    {isClosed ? (exitTs ? formatTs(exitTs) : '--') : 'now'}
                  </span>
                </div>
              </div>
            )}

            {/* Price bar visual */}
            <div style={{ marginBottom: 14 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9, color: colors.textDim, fontFamily: fonts.mono, marginBottom: 4 }}>
                <span>$0.00</span>
                <span>ENTRY @ {fmt(ep, 3)}</span>
                <span>$1.00</span>
              </div>
              <div style={{ position: 'relative', height: 12, background: 'rgba(255,255,255,0.04)', borderRadius: 4, overflow: 'hidden' }}>
                {/* Fill level */}
                <div style={{
                  width: `${ep * 100}%`, height: '100%', borderRadius: 4,
                  background: `linear-gradient(90deg, ${trade.side.toUpperCase().includes('YES') ? colors.success : colors.danger}40, ${trade.side.toUpperCase().includes('YES') ? colors.success : colors.danger}80)`,
                }} />
                {/* Entry marker */}
                <div style={{
                  position: 'absolute', left: `${ep * 100}%`, top: 0, bottom: 0,
                  width: 2, background: colors.textPrimary, transform: 'translateX(-1px)',
                }} />
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))', gap: 8 }}>
              {[
                { label: 'Cost', value: fmtUsd(cost) },
                { label: 'Odds When Placed', value: fmtPct(ep) },
                {
                  label: isClosed ? 'Odds At Close' : 'Odds Now',
                  value: exitPrice != null ? fmtPct(exitPrice) : '--',
                  color: exitPrice != null
                    ? (exitPrice > ep ? colors.success : exitPrice < ep ? colors.danger : undefined)
                    : undefined,
                },
                { label: 'Size (tokens)', value: fmt(trade.size, 1) },
                { label: 'P&L', value: trade.pnl != null ? fmtUsd(trade.pnl) : '--', color: pnlColor },
                { label: 'Max Gain', value: ep > 0 && ep < 1 ? fmtUsd(maxGain) : '--', color: colors.success },
                { label: 'Payout If Won', value: ep > 0 && ep < 1 ? fmtUsd(trade.size) : '--' },
                { label: 'Order ID', value: trade.order_id ? trade.order_id.slice(0, 12) + '...' : '--' },
              ].map((s, i) => (
                <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                  <span style={{ fontSize: 9, color: colors.textDim, textTransform: 'uppercase', letterSpacing: '0.06em', fontFamily: fonts.mono }}>
                    {s.label}
                  </span>
                  <span style={{
                    fontSize: 13, fontWeight: 600, fontFamily: fonts.mono,
                    color: s.color || colors.textPrimary,
                    textShadow: 'none',
                  }}>
                    {s.value}
                  </span>
                </div>
              ))}
            </div>
          </>
          )
        })()}
      </div>

      {/* Full analysis from AnalysisDetail (same as Analysis tab) */}
      {hasAnalysis && (
        <AnalysisDetail conditionId={trade.market_id} />
      )}

      {/* DB-backed fallback for older trades without in-memory analysis */}
      {hasAnalysis === false && (
        <>
          {/* Frontier Decision from DB */}
          {frontier_decision && (
            <>
              <SectionHeader title="Frontier Decision (from history)" />
              <FrontierDecisionPanel fd={frontier_decision} />
            </>
          )}

          {/* Signals from DB */}
          {signals.length > 0 && (
            <>
              <SectionHeader title="Signals (from history)" badge={`${signals.length}`} />
              {signals.map(sig => (
                <SignalCard key={sig.id} signal={sig} />
              ))}
            </>
          )}

          {/* No data at all */}
          {!frontier_decision && signals.length === 0 && (
            <div style={{
              marginTop: 20, padding: 16, textAlign: 'center',
              background: 'rgba(85,102,136,0.05)', borderRadius: 3,
              border: `1px solid ${colors.border}`,
            }}>
              <div style={{ fontSize: 12, color: colors.textDim }}>No analysis data linked to this trade</div>
              <div style={{ fontSize: 10, color: colors.textDim, marginTop: 4 }}>
                Decision and signal data may not have been recorded for older trades
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Summary stats bar
// ---------------------------------------------------------------------------

function TradeStats({ trades, paperBal }: { trades: Trade[]; paperBal: PaperBalance | null }) {
  const total = trades.length
  const filled = trades.filter(t => t.status.toUpperCase() === 'FILLED').length
  const withPnl = trades.filter(t => t.pnl != null)
  const wins = withPnl.filter(t => (t.pnl as number) > 0).length
  const winRate = withPnl.length > 0 ? wins / withPnl.length : null
  const totalPnl = withPnl.reduce((s, t) => s + (t.pnl as number), 0)

  const stats: { label: string; value: string; color?: string }[] = [
    { label: 'Total Trades', value: String(total) },
    { label: 'Filled', value: String(filled) },
    { label: 'Win Rate', value: winRate != null ? (winRate * 100).toFixed(1) + '%' : '--', color: winRate != null ? (winRate >= 0.5 ? colors.success : colors.danger) : undefined },
    { label: 'Realized P&L', value: fmtUsd(totalPnl), color: totalPnl > 0 ? colors.success : totalPnl < 0 ? colors.danger : undefined },
  ]

  if (paperBal) {
    const balColor = paperBal.total_value >= paperBal.starting_balance ? colors.success : colors.danger
    stats.push(
      { label: 'Total Value', value: fmtUsd(paperBal.total_value), color: balColor },
      { label: 'Available', value: fmtUsd(paperBal.available_cash) },
      { label: 'Deployed', value: fmtUsd(paperBal.deployed_capital) },
      { label: 'Unrealized P&L', value: fmtUsd(paperBal.unrealized_pnl), color: paperBal.unrealized_pnl > 0 ? colors.success : paperBal.unrealized_pnl < 0 ? colors.danger : undefined },
      { label: 'Open Positions', value: String(paperBal.open_positions) },
    )
  }

  return (
    <div style={{
      display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: 12,
      ...cardStyle, padding: 16, marginBottom: 16,
    }}>
      {stats.map((s, i) => (
        <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span style={{ fontSize: 9, color: colors.textDim, textTransform: 'uppercase', letterSpacing: '0.06em', fontFamily: fonts.mono }}>
            {s.label}
          </span>
          <span style={{
            fontSize: 16, fontWeight: 700, fontFamily: fonts.mono,
            color: s.color || colors.textPrimary,
            textShadow: 'none',
          }}>
            {s.value}
          </span>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Filter bar
// ---------------------------------------------------------------------------

type FilterStatus = 'all' | 'filled' | 'pending'
type FilterPnl = 'all' | 'winners' | 'losers' | 'open'
type FilterType = 'all' | 'paper' | 'live'
type FilterResolution = 'all' | 'open' | 'closed' | 'won' | 'lost' | 'expired'

function FilterBar({
  status, onStatus, pnl, onPnl, tradeType, onTradeType, resolution, onResolution,
}: {
  status: FilterStatus; onStatus: (v: FilterStatus) => void
  pnl: FilterPnl; onPnl: (v: FilterPnl) => void
  tradeType: FilterType; onTradeType: (v: FilterType) => void
  resolution: FilterResolution; onResolution: (v: FilterResolution) => void
}) {
  const btnStyle = (active: boolean): React.CSSProperties => ({
    padding: '4px 10px', borderRadius: 4, fontSize: 10, fontWeight: 600,
    fontFamily: fonts.mono, letterSpacing: '0.04em', cursor: 'pointer',
    border: `1px solid ${active ? colors.accent + '40' : colors.border}`,
    background: active ? colors.accentDim : 'transparent',
    color: active ? colors.accent : colors.textMuted,
    transition: 'all 0.2s',
    textTransform: 'uppercase' as const,
  })

  return (
    <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 12, alignItems: 'center' }}>
      <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
        <span style={{ fontSize: 9, color: colors.textDim, fontFamily: fonts.mono, marginRight: 4 }}>STATUS</span>
        {(['all', 'filled', 'pending'] as FilterStatus[]).map(v => (
          <button key={v} style={btnStyle(status === v)} onClick={() => onStatus(v)}>{v}</button>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
        <span style={{ fontSize: 9, color: colors.textDim, fontFamily: fonts.mono, marginRight: 4 }}>P&L</span>
        {(['all', 'winners', 'losers', 'open'] as FilterPnl[]).map(v => (
          <button key={v} style={btnStyle(pnl === v)} onClick={() => onPnl(v)}>{v}</button>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
        <span style={{ fontSize: 9, color: colors.textDim, fontFamily: fonts.mono, marginRight: 4 }}>TYPE</span>
        {(['all', 'paper', 'live'] as FilterType[]).map(v => (
          <button key={v} style={btnStyle(tradeType === v)} onClick={() => onTradeType(v)}>{v}</button>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
        <span style={{ fontSize: 9, color: colors.textDim, fontFamily: fonts.mono, marginRight: 4 }}>OUTCOME</span>
        {(['all', 'open', 'closed', 'won', 'lost', 'expired'] as FilterResolution[]).map(v => (
          <button key={v} style={btnStyle(resolution === v)} onClick={() => onResolution(v)}>{v}</button>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Trades component
// ---------------------------------------------------------------------------

export default function Trades() {
  const [trades, setTrades] = useState<Trade[]>([])
  const [paperBal, setPaperBal] = useState<PaperBalance | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [filterStatus, setFilterStatus] = useState<FilterStatus>('all')
  const [filterPnl, setFilterPnl] = useState<FilterPnl>('all')
  const [filterType, setFilterType] = useState<FilterType>('all')
  const [filterResolution, setFilterResolution] = useState<FilterResolution>('all')

  const load = useCallback(() => {
    api.fetchTrades()
      .then(setTrades)
      .catch(() => {})
      .finally(() => setLoading(false))
    api.fetchPaperBalance().then(setPaperBal).catch(() => {})
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 30000)
    return () => clearInterval(id)
  }, [load])

  // Apply filters
  const filtered = trades.filter(t => {
    if (filterStatus === 'filled' && t.status.toUpperCase() !== 'FILLED') return false
    if (filterStatus === 'pending' && t.status.toUpperCase() !== 'PENDING') return false
    if (filterPnl === 'winners' && !(t.pnl != null && t.pnl > 0)) return false
    if (filterPnl === 'losers' && !(t.pnl != null && t.pnl < 0)) return false
    if (filterPnl === 'open' && t.pnl != null) return false
    if (filterType === 'paper' && t.paper !== 1) return false
    if (filterType === 'live' && t.paper !== 0) return false
    const rs = t.resolution_status || ''
    if (filterResolution === 'open' && !rs.startsWith('open_') && rs !== 'pending_fill') return false
    if (filterResolution === 'closed' && rs !== 'closed_profit' && rs !== 'closed_loss') return false
    if (filterResolution === 'won' && rs !== 'won') return false
    if (filterResolution === 'lost' && rs !== 'lost') return false
    if (filterResolution === 'expired' && rs !== 'expired') return false
    return true
  })

  if (loading) return (
    <div style={{ padding: 40, textAlign: 'center', color: colors.textDim, fontFamily: fonts.mono }}>
      Loading trades...
    </div>
  )

  return (
    <div>
      {/* Summary stats */}
      <TradeStats trades={trades} paperBal={paperBal} />

      {/* Filters */}
      <FilterBar
        status={filterStatus} onStatus={setFilterStatus}
        pnl={filterPnl} onPnl={setFilterPnl}
        tradeType={filterType} onTradeType={setFilterType}
        resolution={filterResolution} onResolution={setFilterResolution}
      />

      {/* Split layout */}
      <div style={{ display: 'flex', gap: 16, minHeight: 500 }}>
        {/* Trade list */}
        <div style={{
          ...cardStyle, flex: '0 0 480px', padding: 0,
          maxHeight: 'calc(100vh - 320px)', overflowY: 'auto',
        }}>
          {filtered.length === 0 ? (
            <div style={{ padding: 40, textAlign: 'center', color: colors.textDim, fontSize: 12 }}>
              No trades match filters
            </div>
          ) : (
            filtered.map((t, i) => {
              const isSelected = selectedId === t.id
              return (
                <div
                  key={t.id}
                  onClick={() => setSelectedId(isSelected ? null : t.id)}
                  style={{
                    padding: '12px 16px',
                    borderBottom: `1px solid ${colors.border}`,
                    cursor: 'pointer',
                    background: isSelected ? colors.accentDim : 'transparent',
                    borderLeft: isSelected ? `2px solid ${colors.accent}` : '2px solid transparent',
                    transition: 'all 0.15s ease',
                  }}
                  onMouseEnter={e => {
                    if (!isSelected) e.currentTarget.style.background = 'rgba(255, 255, 255,0.02)'
                  }}
                  onMouseLeave={e => {
                    if (!isSelected) e.currentTarget.style.background = 'transparent'
                  }}
                >
                  {/* Row 1: question + badges */}
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 6 }}>
                    <div style={{
                      fontSize: 12, fontWeight: 500, color: colors.textPrimary,
                      lineHeight: 1.4, flex: 1, marginRight: 10,
                      overflow: 'hidden', textOverflow: 'ellipsis',
                      display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
                    }}>
                      {t.market_question || t.market_id.slice(0, 16) + '...'}
                    </div>
                    <PnlBadge pnl={t.pnl} />
                  </div>

                  {/* Row 2: badges */}
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', marginBottom: 6 }}>
                    <SideBadge side={t.side} />
                    <StatusBadge status={t.status} />
                    <ResolutionBadge status={t.resolution_status} />
                    {t.paper === 1 && <PaperBadge />}
                    <span style={{ fontSize: 9, color: colors.textDim, fontFamily: fonts.mono, marginLeft: 'auto' }}>
                      {formatTs(t.timestamp)}
                    </span>
                  </div>

                  {/* Row 3: money line — matches Dashboard trade history */}
                  {(() => {
                    const ep = t.fill_price ?? t.price
                    const cost = t.size * ep
                    const maxGain = t.size - cost
                    const cp = t.current_price
                    const upnl = t.unrealized_pnl
                    const liveOpen = cp != null && upnl != null
                    const liveColor = liveOpen
                      ? (upnl > 0 ? colors.success : upnl < 0 ? colors.danger : colors.textMuted)
                      : colors.textDim
                    const livePct = liveOpen && ep > 0 ? ((cp - ep) / ep) * 100 : 0
                    return (
                      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                        <span style={{ fontSize: 11, fontWeight: 600, color: colors.textPrimary, fontFamily: fonts.mono }}>
                          {fmtUsd(cost)}
                        </span>
                        <span style={{ fontSize: 9, color: colors.textDim, fontFamily: fonts.mono }}>
                          {fmtPct(ep)}
                          {liveOpen && (
                            <>
                              <span style={{ margin: '0 4px', color: liveColor }}>→</span>
                              <span style={{ color: liveColor, fontWeight: 600 }}>{fmtPct(cp)}</span>
                            </>
                          )}
                        </span>
                        {liveOpen ? (
                          <span style={{ fontSize: 10, color: liveColor, fontFamily: fonts.mono, fontWeight: 600 }}>
                            {upnl >= 0 ? '+' : ''}{fmtUsd(upnl)}
                            <span style={{ color: colors.textDim, fontSize: 9, marginLeft: 3 }}>
                              ({livePct >= 0 ? '+' : ''}{livePct.toFixed(1)}%)
                            </span>
                          </span>
                        ) : ep > 0 && ep < 1 && (
                          <span style={{ fontSize: 10, color: colors.success, fontFamily: fonts.mono }}>
                            +{fmtUsd(maxGain)}
                            <span style={{ color: colors.textDim, fontSize: 9, marginLeft: 3 }}>
                              ({cost > 0 ? ((maxGain / cost) * 100).toFixed(0) : '0'}%)
                            </span>
                          </span>
                        )}
                        {/* Mini price bar with entry + current markers */}
                        <div style={{
                          flex: 1, minWidth: 60, height: 4,
                          background: 'rgba(255,255,255,0.04)', borderRadius: 2,
                          overflow: 'hidden', position: 'relative',
                        }}>
                          <div style={{
                            width: `${ep * 100}%`, height: '100%', borderRadius: 2,
                            background: t.side.toUpperCase().includes('YES') ? colors.success : colors.danger,
                            opacity: 0.5,
                          }} />
                          {liveOpen && (
                            <div style={{
                              position: 'absolute', left: `${cp * 100}%`, top: -1, bottom: -1,
                              width: 2, background: liveColor,
                              boxShadow: `0 0 4px ${liveColor}`,
                            }} />
                          )}
                        </div>
                      </div>
                    )
                  })()}
                </div>
              )
            })
          )}
        </div>

        {/* Detail panel */}
        <div style={{
          ...cardStyle, flex: 1, padding: 20,
          maxHeight: 'calc(100vh - 320px)', overflowY: 'auto',
        }}>
          {selectedId ? (
            <TradeDetailPanel tradeId={selectedId} />
          ) : (
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              height: '100%', minHeight: 300,
            }}>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 32, marginBottom: 12, opacity: 0.15 }}>&#9783;</div>
                <div style={{ fontSize: 13, color: colors.textDim }}>Select a trade to view full analysis</div>
                <div style={{ fontSize: 11, color: colors.textDim, marginTop: 4 }}>
                  Includes frontier decision, signal breakdown, and execution details
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
