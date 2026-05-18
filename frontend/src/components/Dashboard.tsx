import { useState, useEffect, useCallback, useRef } from 'react'
import { colors, cardStyle, glowShadow, fonts, animDelay } from '../theme'
import {
  api, HealthResponse, WalletResponse, CostResponse, BotStatus,
  Position, PnlResponse, PaperBalance, CyclesResponse, ActivityEvent, Trade, TradeDetail,
  CalibrationResponse, SkipAnalysis,
} from '../api'
import CostBreakdown from './charts/CostBreakdown'
import PnlChart from './charts/PnlChart'
import DailyPnlBar from './charts/DailyPnlBar'
import CashDeployedArea from './charts/CashDeployedArea'

// ---------------------------------------------------------------------------
// Shared UI atoms — Quantum Terminal style
// ---------------------------------------------------------------------------

function Card({ title, children, accent, style, index = 0 }: {
  title: string; children: React.ReactNode; accent?: string; style?: React.CSSProperties; index?: number
}) {
  return (
    <div style={{
      ...cardStyle,
      ...animDelay(index),
      display: 'flex',
      flexDirection: 'column' as const,
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
      {/* Top accent line */}
      {accent && (
        <div style={{
          position: 'absolute', top: 0, left: 0, right: 0, height: 2,
          background: `linear-gradient(90deg, transparent 5%, ${accent}88 30%, ${accent} 50%, ${accent}88 70%, transparent 95%)`,
          opacity: 0.7,
        }} />
      )}
      {/* Corner tick marks */}
      <div style={{
        position: 'absolute', top: 6, left: 6,
        width: 10, height: 10,
        borderTop: `1px solid ${accent || colors.borderLight}`,
        borderLeft: `1px solid ${accent || colors.borderLight}`,
        opacity: 0.35,
      }} />
      <div style={{
        position: 'absolute', top: 6, right: 6,
        width: 10, height: 10,
        borderTop: `1px solid ${accent || colors.borderLight}`,
        borderRight: `1px solid ${accent || colors.borderLight}`,
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
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        {children}
      </div>
    </div>
  )
}

function StatValue({ value, label, color, size = 'md' }: {
  value: string; label: string; color?: string; size?: 'sm' | 'md' | 'lg'
}) {
  const fontSize = size === 'lg' ? 28 : size === 'md' ? 20 : 15
  return (
    <div>
      <div style={{
        fontSize, fontWeight: 600, color: color || colors.textPrimary,
        letterSpacing: '-0.02em', fontFamily: fonts.mono,
        textShadow: color ? `0 0 20px ${color}33` : 'none',
      }}>
        {value}
      </div>
      <div style={{
        fontSize: 10, color: colors.textDim, marginTop: 3, fontWeight: 500,
        letterSpacing: '0.06em', textTransform: 'uppercase', fontFamily: fonts.mono,
      }}>
        {label}
      </div>
    </div>
  )
}

function StatusDot({ ok, pulse }: { ok: boolean; pulse?: boolean }) {
  return (
    <span style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
      {pulse && ok && (
        <span style={{
          position: 'absolute', width: 16, height: 16, borderRadius: '50%',
          background: colors.success, opacity: 0.2,
          animation: 'pulse 2s ease-in-out infinite',
        }} />
      )}
      <span style={{
        display: 'inline-block', width: 7, height: 7, borderRadius: '50%',
        background: ok ? colors.success : colors.danger,
        boxShadow: ok ? `0 0 8px ${colors.success}` : `0 0 6px ${colors.danger}`,
        position: 'relative', zIndex: 1,
      }} />
    </span>
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

// ---------------------------------------------------------------------------
// Countdown Ring — circular progress for cycle timers
// ---------------------------------------------------------------------------

function CountdownRing({ secondsRemaining, totalSeconds, label, accent, sublabel }: {
  secondsRemaining: number | null
  totalSeconds: number
  label: string
  accent: string
  sublabel?: string
}) {
  const size = 78
  const stroke = 3
  const radius = (size - stroke) / 2
  const circumference = 2 * Math.PI * radius
  const progress = secondsRemaining != null ? Math.max(0, 1 - secondsRemaining / totalSeconds) : 0
  const offset = circumference * (1 - progress)

  const formatTime = (s: number | null) => {
    if (s == null) return '--:--'
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    const sec = s % 60
    if (h > 0) return `${h}h ${m}m`
    if (m > 0) return `${m}m ${sec}s`
    return `${sec}s`
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
      <div style={{ position: 'relative', width: size, height: size }}>
        <svg width={size} height={size} style={{ transform: 'rotate(-90deg)' }}>
          {/* Background ring */}
          <circle
            cx={size / 2} cy={size / 2} r={radius}
            fill="none"
            stroke="rgba(255,255,255,0.04)"
            strokeWidth={stroke}
          />
          {/* Progress ring */}
          <circle
            cx={size / 2} cy={size / 2} r={radius}
            fill="none"
            stroke={accent}
            strokeWidth={stroke}
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            strokeLinecap="round"
            style={{
              transition: 'stroke-dashoffset 1s linear',
              filter: `drop-shadow(0 0 4px ${accent})`,
            }}
          />
        </svg>
        {/* Center text */}
        <div style={{
          position: 'absolute', inset: 0,
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        }}>
          <span style={{
            fontSize: 13, fontWeight: 600, fontFamily: fonts.mono,
            color: secondsRemaining != null ? colors.textPrimary : colors.textDim,
            letterSpacing: '-0.02em',
          }}>
            {formatTime(secondsRemaining)}
          </span>
        </div>
      </div>
      <span style={{
        fontSize: 9, color: colors.textMuted, fontFamily: fonts.mono,
        letterSpacing: '0.06em', textTransform: 'uppercase',
      }}>
        {label}
      </span>
      {sublabel && (
        <span style={{
          fontSize: 8, color: colors.textDim, fontFamily: fonts.mono,
          letterSpacing: '0.04em', marginTop: -2,
        }}>
          {sublabel}
        </span>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Uptime display
// ---------------------------------------------------------------------------

function formatUptime(seconds: number | null): string {
  if (seconds == null) return 'Offline'
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  if (d > 0) return `${d}d ${h}h ${m}m`
  if (h > 0) return `${h}h ${m}m ${s}s`
  return `${m}m ${s}s`
}

// ---------------------------------------------------------------------------
// Activity event icon/color mapping
// ---------------------------------------------------------------------------

function activityMeta(type: string): { icon: string; color: string } {
  switch (type) {
    case 'trade': return { icon: '$', color: colors.success }
    case 'discovery': return { icon: '~', color: colors.accent }
    case 'aggregation': return { icon: '#', color: colors.purple }
    case 'monitor': return { icon: '>', color: colors.warning }
    case 'error': return { icon: '!', color: colors.danger }
    default: return { icon: '*', color: colors.textMuted }
  }
}

function timeAgo(isoStr: string): string {
  const diff = (Date.now() - new Date(isoStr).getTime()) / 1000
  if (diff < 60) return `${Math.round(diff)}s ago`
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`
  return `${Math.round(diff / 86400)}d ago`
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------

interface DashboardProps {
  wsBotStatus?: BotStatus | null
  wsDiscovery?: { discovered: number; filtered: number } | null
  wsBatchProgress?: { current_index: number; total: number; condition_id: string; status: string } | null
}

export default function Dashboard({ wsBotStatus, wsDiscovery, wsBatchProgress }: DashboardProps) {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [wallet, setWallet] = useState<WalletResponse | null>(null)
  const [costData, setCostData] = useState<CostResponse | null>(null)
  const [botStatus, setBotStatus] = useState<BotStatus | null>(null)
  const [positions, setPositions] = useState<Position[]>([])
  const [pnlData, setPnlData] = useState<PnlResponse | null>(null)
  const [paperBal, setPaperBal] = useState<PaperBalance | null>(null)
  const [cycles, setCycles] = useState<CyclesResponse | null>(null)
  const [trades, setTrades] = useState<Trade[]>([])
  const [actionLoading, setActionLoading] = useState(false)
  const [selectedTrade, setSelectedTrade] = useState<TradeDetail | null>(null)
  const [tradeModalLoading, setTradeModalLoading] = useState(false)
  const [calibration, setCalibration] = useState<CalibrationResponse | null>(null)
  const [skipAnalysis, setSkipAnalysis] = useState<SkipAnalysis | null>(null)

  // Live countdown state — ticks every second
  const [liveCountdowns, setLiveCountdowns] = useState<{
    discovery: number | null; aggregation: number | null; position: number | null
  }>({ discovery: null, aggregation: null, position: null })
  const [liveUptime, setLiveUptime] = useState<number | null>(null)
  const cyclesRef = useRef(cycles)
  cyclesRef.current = cycles

  const displayStatus = wsBotStatus || botStatus

  const refresh = useCallback(() => {
    api.fetchHealth().then(setHealth).catch(() => {})
    api.fetchWallet().then(setWallet).catch(() => {})
    api.fetchCosts().then(setCostData).catch(() => {})
    api.fetchBotStatus().then(setBotStatus).catch(() => {})
    api.fetchPositions().then(setPositions).catch(() => {})
    api.fetchPnl().then(setPnlData).catch(() => {})
    api.fetchPaperBalance().then(setPaperBal).catch(() => {})
    api.fetchCycles().then(setCycles).catch(() => {})
    api.fetchTrades().then(setTrades).catch(() => {})
    api.fetchLearningCalibration().then(setCalibration).catch(() => {})
    api.fetchSkipAnalysis().then(setSkipAnalysis).catch(() => {})
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 30000)
    return () => clearInterval(id)
  }, [refresh])

  // Faster cycle refresh (every 10s) to keep countdowns accurate
  useEffect(() => {
    const id = setInterval(() => {
      api.fetchCycles().then(setCycles).catch(() => {})
    }, 10000)
    return () => clearInterval(id)
  }, [])

  // 1-second tick for live countdowns
  useEffect(() => {
    const id = setInterval(() => {
      const c = cyclesRef.current
      if (!c) return
      setLiveCountdowns({
        discovery: c.discovery.seconds_remaining != null ? Math.max(0, c.discovery.seconds_remaining - 1) : null,
        aggregation: c.aggregation.seconds_remaining != null ? Math.max(0, c.aggregation.seconds_remaining - 1) : null,
        position: c.position_monitor.seconds_remaining != null ? Math.max(0, c.position_monitor.seconds_remaining - 1) : null,
      })
      setLiveUptime(c.uptime_seconds != null ? c.uptime_seconds + 1 : null)
    }, 1000)
    return () => clearInterval(id)
  }, [])

  // Sync live countdowns when cycles data refreshes
  useEffect(() => {
    if (cycles) {
      setLiveCountdowns({
        discovery: cycles.discovery.seconds_remaining,
        aggregation: cycles.aggregation.seconds_remaining,
        position: cycles.position_monitor.seconds_remaining,
      })
      setLiveUptime(cycles.uptime_seconds)
    }
  }, [cycles])

  const handleStart = async () => {
    setActionLoading(true)
    try {
      await api.startBot()
      await api.fetchBotStatus().then(setBotStatus)
    } catch (e) { console.error('Failed to start bot:', e) }
    finally { setActionLoading(false) }
  }

  const handleStop = async () => {
    setActionLoading(true)
    try {
      await api.stopBot()
      await api.fetchBotStatus().then(setBotStatus)
    } catch (e) { console.error('Failed to stop bot:', e) }
    finally { setActionLoading(false) }
  }

  const handlePauseResume = async () => {
    setActionLoading(true)
    try {
      if (displayStatus?.paused) {
        await api.resumeBot()
      } else {
        await api.pauseBot()
      }
      await api.fetchBotStatus().then(setBotStatus)
    } catch (e) { console.error('Failed to pause/resume bot:', e) }
    finally { setActionLoading(false) }
  }

  const handleTradeClick = async (tradeId: string) => {
    setTradeModalLoading(true)
    try {
      const detail = await api.fetchTradeDetail(tradeId)
      setSelectedTrade(detail)
    } catch (e) {
      console.error('Failed to fetch trade detail:', e)
    } finally {
      setTradeModalLoading(false)
    }
  }

  const totalPnl = pnlData?.total_pnl ?? 0
  const dailyPnl = pnlData?.daily_pnl ?? 0
  const unrealizedPnl = positions.reduce((s, p) => s + p.unrealized_pnl, 0)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* Row 1: Key metrics strip */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
        {/* Bot Status */}
        <Card title="Bot Status" accent={displayStatus?.paused ? colors.warning : displayStatus?.running ? colors.success : colors.textDim} index={0}>
          {displayStatus ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <PillBadge
                  text={displayStatus.paused ? 'PAUSED' : displayStatus.running ? 'RUNNING' : 'STOPPED'}
                  bg={displayStatus.paused ? colors.warningDim : displayStatus.running ? colors.successDim : 'rgba(85,102,136,0.1)'}
                  fg={displayStatus.paused ? colors.warning : displayStatus.running ? colors.success : colors.textDim}
                />
                {displayStatus.paper_trading && (
                  <PillBadge text="PAPER" bg={colors.warningDim} fg={colors.warning} />
                )}
              </div>
              <div style={{ fontSize: 11, color: colors.textMuted, fontFamily: fonts.mono }}>
                Phase: <span style={{ color: colors.textSecondary, fontWeight: 500 }}>{displayStatus.phase}</span>
                <span style={{ margin: '0 8px', color: colors.border }}>|</span>
                Cycles: <span style={{ color: colors.textSecondary, fontWeight: 500 }}>{displayStatus.cycle_count}</span>
              </div>
              {/* Uptime */}
              {displayStatus.running && liveUptime != null && (
                <div style={{
                  fontSize: 10, color: colors.textDim, fontFamily: fonts.mono,
                  display: 'flex', alignItems: 'center', gap: 6,
                }}>
                  <span style={{
                    width: 5, height: 5, borderRadius: '50%',
                    background: colors.success, display: 'inline-block',
                    animation: 'pulse 2s ease-in-out infinite',
                  }} />
                  Uptime: <span style={{ color: colors.textMuted }}>{formatUptime(liveUptime)}</span>
                </div>
              )}
              <div style={{ display: 'flex', gap: 6 }}>
                {displayStatus.running && (
                  <button
                    disabled={actionLoading}
                    onClick={handlePauseResume}
                    style={{
                      flex: 1, padding: '9px 0', borderRadius: 6, border: 'none', fontFamily: fonts.mono,
                      background: displayStatus.paused ? colors.successDim : colors.warningDim,
                      color: displayStatus.paused ? colors.success : colors.warning,
                      cursor: actionLoading ? 'wait' : 'pointer',
                      fontSize: 11, fontWeight: 600,
                      transition: 'all 0.25s ease',
                      letterSpacing: '0.06em',
                      textTransform: 'uppercase',
                    }}
                    onMouseEnter={e => { if (!actionLoading) e.currentTarget.style.transform = 'scale(1.02)' }}
                    onMouseLeave={e => { e.currentTarget.style.transform = 'scale(1)' }}
                  >
                    {actionLoading ? '...' : displayStatus.paused ? 'Resume' : 'Pause'}
                  </button>
                )}
                <button
                  disabled={actionLoading}
                  onClick={displayStatus.running ? handleStop : handleStart}
                  style={{
                    flex: 1, padding: '9px 0', borderRadius: 6, border: 'none', fontFamily: fonts.mono,
                    background: displayStatus.running
                      ? colors.dangerDim
                      : colors.gradientAccent,
                    color: displayStatus.running ? colors.danger : '#000',
                    cursor: actionLoading ? 'wait' : 'pointer',
                    fontSize: 11, fontWeight: 600,
                    transition: 'all 0.25s ease',
                    boxShadow: displayStatus.running ? 'none' : `0 2px 16px rgba(0,229,255,0.3)`,
                    letterSpacing: '0.06em',
                    textTransform: 'uppercase',
                  }}
                  onMouseEnter={e => { if (!actionLoading) e.currentTarget.style.transform = 'scale(1.02)' }}
                  onMouseLeave={e => { e.currentTarget.style.transform = 'scale(1)' }}
                >
                  {actionLoading ? '...' : displayStatus.running ? 'Stop' : 'Start Bot'}
                </button>
              </div>
            </div>
          ) : (
            <Skeleton />
          )}
        </Card>

        {/* Paper Balance / Wallet */}
        <Card title={displayStatus?.paper_trading !== false ? "Paper Balance" : "Wallet"} accent={colors.accent} index={1}>
          {displayStatus?.paper_trading !== false && paperBal ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <StatValue
                value={`$${paperBal.total_value.toFixed(2)}`}
                label="Total Value"
                color={paperBal.total_value >= paperBal.starting_balance ? colors.success : colors.danger}
                size="lg"
              />
              <div style={{ display: 'flex', gap: 16 }}>
                <StatValue value={`$${paperBal.available_cash.toFixed(2)}`} label="Available" size="sm" />
                <StatValue value={`$${paperBal.deployed_capital.toFixed(2)}`} label="Deployed" size="sm" />
              </div>
              {/* Balance bar */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                <div style={{
                  width: '100%', height: 4, background: 'rgba(255,255,255,0.04)', borderRadius: 2,
                  overflow: 'hidden', display: 'flex',
                }}>
                  <div style={{
                    height: '100%',
                    width: `${paperBal.total_value > 0 ? (paperBal.available_cash / paperBal.total_value) * 100 : 100}%`,
                    background: colors.gradientAccent,
                    transition: 'width 0.8s cubic-bezier(0.22, 1, 0.36, 1)',
                  }} />
                  <div style={{
                    height: '100%',
                    width: `${paperBal.total_value > 0 ? (paperBal.deployed_capital / paperBal.total_value) * 100 : 0}%`,
                    background: colors.warning,
                    opacity: 0.6,
                    transition: 'width 0.8s cubic-bezier(0.22, 1, 0.36, 1)',
                  }} />
                </div>
                <div style={{
                  fontSize: 10, color: colors.textDim, fontFamily: fonts.mono,
                  display: 'flex', justifyContent: 'space-between',
                }}>
                  <span>{paperBal.open_positions} positions</span>
                  <span>Start: ${paperBal.starting_balance.toFixed(0)}</span>
                </div>
              </div>
            </div>
          ) : wallet ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div style={{
                fontSize: 10, color: colors.textDim, fontFamily: fonts.mono,
                background: colors.accentDim, padding: '3px 8px', borderRadius: 4,
                display: 'inline-block', width: 'fit-content',
                border: `1px solid ${colors.border}`,
                letterSpacing: '0.02em',
              }}>
                {wallet.address.slice(0, 6)}...{wallet.address.slice(-4)}
              </div>
              <div style={{ display: 'flex', gap: 20 }}>
                <StatValue value={`$${wallet.usdc.toFixed(2)}`} label="USDC" size="md" />
                <StatValue value={wallet.matic.toFixed(4)} label="MATIC" size="sm" />
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }}>
                <StatusDot ok={wallet.has_gas} />
                <span style={{
                  color: wallet.has_gas ? colors.textMuted : colors.danger,
                  fontFamily: fonts.mono, fontSize: 10,
                }}>
                  {wallet.has_gas ? 'Gas OK' : 'Low gas!'}
                </span>
                <span style={{
                  marginLeft: 'auto', color: colors.textDim,
                  fontFamily: fonts.mono, fontSize: 10,
                }}>
                  {wallet.positions_count} pos
                </span>
              </div>
            </div>
          ) : (
            <Skeleton />
          )}
        </Card>

        {/* Daily P&L */}
        <Card title="Today's P&L" accent={dailyPnl >= 0 ? colors.success : colors.danger} index={2}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <StatValue
              value={`${dailyPnl >= 0 ? '+' : ''}$${dailyPnl.toFixed(2)}`}
              label="Realized"
              color={dailyPnl >= 0 ? colors.success : colors.danger}
              size="lg"
            />
            <div style={{ display: 'flex', gap: 16 }}>
              <StatValue
                value={`${unrealizedPnl >= 0 ? '+' : ''}$${unrealizedPnl.toFixed(2)}`}
                label="Unrealized"
                color={unrealizedPnl >= 0 ? colors.success : colors.danger}
                size="sm"
              />
              <StatValue
                value={`${pnlData?.trade_count ?? 0}`}
                label="Trades"
                size="sm"
              />
            </div>
          </div>
        </Card>

        {/* Performance */}
        <Card title="Performance" accent={colors.purple} index={3}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <StatValue
              value={`${totalPnl >= 0 ? '+' : ''}$${totalPnl.toFixed(2)}`}
              label="Total P&L"
              color={totalPnl >= 0 ? colors.success : colors.danger}
              size="lg"
            />
            <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
              <StatValue
                value={`${((pnlData?.win_rate ?? 0) * 100).toFixed(0)}%`}
                label="Win Rate"
                size="sm"
              />
              <div>
                {/* Animated win rate bar */}
                <div style={{
                  width: 80, height: 4, background: 'rgba(255,51,102,0.15)', borderRadius: 2, overflow: 'hidden',
                  position: 'relative',
                }}>
                  <div style={{
                    height: '100%', borderRadius: 2,
                    width: `${(pnlData?.win_rate ?? 0) * 100}%`,
                    background: colors.gradientSuccess,
                    transition: 'width 0.8s cubic-bezier(0.22, 1, 0.36, 1)',
                    boxShadow: `0 0 8px ${colors.success}40`,
                    transformOrigin: 'left',
                  }} />
                </div>
                <div style={{
                  fontSize: 10, color: colors.textDim, marginTop: 3,
                  fontFamily: fonts.mono, letterSpacing: '0.04em',
                }}>
                  of closed trades
                </div>
              </div>
            </div>
          </div>
        </Card>
      </div>

      {/* Row 2: Cycle Timers + Session Stats */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        {/* Cycle Countdown Timers */}
        <Card title="Pipeline Cycles" accent={colors.accent} index={4}>
          <div style={{
            display: 'flex', justifyContent: 'space-around', alignItems: 'center',
            padding: '4px 0',
          }}>
            <CountdownRing
              secondsRemaining={displayStatus?.running ? liveCountdowns.discovery : null}
              totalSeconds={(cycles?.discovery.interval_minutes ?? 120) * 60}
              label="Discovery"
              accent={colors.accent}
              sublabel={cycles?.discovery.markets_ranked ? `${cycles.discovery.markets_ranked} ranked` : undefined}
            />
            <CountdownRing
              secondsRemaining={displayStatus?.running ? liveCountdowns.aggregation : null}
              totalSeconds={(cycles?.aggregation.interval_minutes ?? 120) * 60}
              label="Aggregation"
              accent={colors.purple}
              sublabel={cycles?.aggregation.batch_size ? `batch: ${cycles.aggregation.batch_size}` : undefined}
            />
            <CountdownRing
              secondsRemaining={displayStatus?.running ? liveCountdowns.position : null}
              totalSeconds={(cycles?.position_monitor.interval_minutes ?? 30) * 60}
              label="Positions"
              accent={colors.warning}
              sublabel={positions.length > 0 ? `${positions.length} open` : undefined}
            />
          </div>
          {/* Phase indicator bar */}
          {displayStatus?.running && displayStatus.phase !== 'idle' && displayStatus.phase !== 'waiting' && (
            <div style={{
              marginTop: 10, padding: '6px 10px', borderRadius: 6,
              background: 'rgba(0,229,255,0.04)',
              border: `1px solid ${colors.border}`,
              display: 'flex', alignItems: 'center', gap: 8,
            }}>
              <div style={{
                width: 6, height: 6, borderRadius: '50%',
                background: colors.accent,
                animation: 'pulse 1s ease-in-out infinite',
              }} />
              <span style={{
                fontSize: 10, fontFamily: fonts.mono, color: colors.textSecondary,
                letterSpacing: '0.04em', textTransform: 'uppercase',
              }}>
                {displayStatus.phase === 'filtering' && (
                  wsDiscovery ? `Discovery: ${wsDiscovery.filtered} ranked from ${wsDiscovery.discovered} markets` : 'Discovering & filtering markets...'
                )}
                {displayStatus.phase === 'aggregating' && (
                  wsBatchProgress ? `Aggregating ${wsBatchProgress.current_index + 1}/${wsBatchProgress.total}...` : 'Running signal aggregation...'
                )}
                {displayStatus.phase === 'learning' && 'Analyzing performance...'}
                {displayStatus.phase === 'monitoring' && 'Checking positions & orders...'}
              </span>
            </div>
          )}
        </Card>

        {/* Session Statistics */}
        <Card title="Session Statistics" accent={colors.success} index={5}>
          {cycles?.session_stats ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
                <SessionStat
                  value={cycles.session_stats.markets_discovered}
                  label="Discovered"
                  color={colors.accent}
                />
                <SessionStat
                  value={cycles.session_stats.markets_analyzed}
                  label="Analyzed"
                  color={colors.purple}
                />
                <SessionStat
                  value={cycles.session_stats.trades_executed}
                  label="Traded"
                  color={colors.success}
                />
                <SessionStat
                  value={cycles.session_stats.markets_skipped}
                  label="Skipped"
                  color={colors.textMuted}
                />
              </div>
              {/* Hit rate bar */}
              {cycles.session_stats.markets_analyzed > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                  <div style={{
                    display: 'flex', justifyContent: 'space-between',
                    fontSize: 9, fontFamily: fonts.mono, color: colors.textDim,
                    textTransform: 'uppercase', letterSpacing: '0.06em',
                  }}>
                    <span>Trade Hit Rate</span>
                    <span style={{ color: colors.textMuted }}>
                      {((cycles.session_stats.trades_executed / cycles.session_stats.markets_analyzed) * 100).toFixed(0)}%
                    </span>
                  </div>
                  <div style={{
                    width: '100%', height: 4, background: 'rgba(255,255,255,0.04)',
                    borderRadius: 2, overflow: 'hidden',
                  }}>
                    <div style={{
                      height: '100%',
                      width: `${(cycles.session_stats.trades_executed / cycles.session_stats.markets_analyzed) * 100}%`,
                      background: colors.gradientSuccess,
                      transition: 'width 0.8s cubic-bezier(0.22, 1, 0.36, 1)',
                      borderRadius: 2,
                    }} />
                  </div>
                </div>
              )}
              {/* Funnel: discovered → analyzed → traded */}
              {cycles.session_stats.markets_discovered > 0 && (
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 4,
                  fontSize: 9, fontFamily: fonts.mono, color: colors.textDim,
                }}>
                  <span style={{ color: colors.accent }}>{cycles.session_stats.markets_discovered}</span>
                  <span style={{ opacity: 0.4 }}>{'>'}</span>
                  <span style={{ color: colors.purple }}>{cycles.session_stats.markets_analyzed}</span>
                  <span style={{ opacity: 0.4 }}>{'>'}</span>
                  <span style={{ color: colors.success }}>{cycles.session_stats.trades_executed}</span>
                  <span style={{ marginLeft: 4, letterSpacing: '0.04em' }}>FUNNEL</span>
                </div>
              )}
            </div>
          ) : (
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              padding: 20, color: colors.textDim, fontSize: 11, fontFamily: fonts.mono,
            }}>
              Start the bot to see session stats
            </div>
          )}
        </Card>
      </div>

      {/* Row 3: PnL Chart + Signal Accuracy + Right column */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
        {/* PnL Chart */}
        <Card title="Portfolio Value" accent={colors.accent} style={{ minHeight: 280, display: 'flex', flexDirection: 'column' }} index={6}>
          <PnlChart snapshots={pnlData?.snapshots ?? []} />
        </Card>

        {/* Signal Accuracy */}
        <Card title="Signal Accuracy" accent={colors.purple} style={{ minHeight: 280 }} index={7}>
          <SignalAccuracyCard calibration={calibration} skipAnalysis={skipAnalysis} />
        </Card>

        {/* Right column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Connections + Health Checks */}
          <Card title="Connections" index={8}>
            {health ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {health.services.map((s, si) => (
                  <div key={s.name} style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    padding: '6px 10px', borderRadius: 6,
                    background: s.healthy ? 'rgba(0,255,136,0.03)' : 'rgba(255,51,102,0.05)',
                    border: `1px solid ${s.healthy ? 'rgba(0,255,136,0.08)' : 'rgba(255,51,102,0.1)'}`,
                    transition: 'all 0.3s',
                    ...animDelay(si + 8),
                  }}>
                    <span style={{
                      display: 'flex', alignItems: 'center', gap: 8, fontSize: 12,
                      fontFamily: fonts.body,
                    }}>
                      <StatusDot ok={s.healthy} pulse={s.healthy} />
                      {s.name}
                    </span>
                    <span style={{
                      fontSize: 10, fontFamily: fonts.mono,
                      color: s.healthy ? colors.textMuted : colors.danger,
                    }}>
                      {s.healthy ? `${s.latency_ms}ms` : (s.error || 'Error')}
                    </span>
                  </div>
                ))}
                {/* Bot Health Checks */}
                {health.health_checks && health.health_checks.length > 0 && (
                  <>
                    <div style={{
                      fontSize: 9, color: colors.textDim, fontFamily: fonts.mono,
                      letterSpacing: '0.08em', textTransform: 'uppercase',
                      marginTop: 6, paddingTop: 6,
                      borderTop: `1px solid ${colors.border}`,
                    }}>
                      Bot Health Checks
                    </div>
                    {health.health_checks.map((hc, hi) => {
                      const statusColor = hc.status === 'ok' ? colors.success
                        : hc.status === 'warning' ? colors.warning : colors.danger
                      const statusBg = hc.status === 'ok' ? 'rgba(0,255,136,0.03)'
                        : hc.status === 'warning' ? 'rgba(255,170,0,0.05)' : 'rgba(255,51,102,0.05)'
                      const statusBorder = hc.status === 'ok' ? 'rgba(0,255,136,0.08)'
                        : hc.status === 'warning' ? 'rgba(255,170,0,0.1)' : 'rgba(255,51,102,0.1)'
                      return (
                        <div key={hc.check_name} style={{
                          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                          padding: '5px 10px', borderRadius: 6,
                          background: statusBg,
                          border: `1px solid ${statusBorder}`,
                          ...animDelay(hi + health.services.length + 8),
                        }}>
                          <span style={{
                            display: 'flex', alignItems: 'center', gap: 8, fontSize: 11,
                            fontFamily: fonts.body,
                          }}>
                            <StatusDot ok={hc.status === 'ok'} />
                            {hc.check_name}
                          </span>
                          <span style={{
                            fontSize: 9, fontFamily: fonts.mono,
                            color: statusColor, fontWeight: 600,
                            letterSpacing: '0.04em',
                            textTransform: 'uppercase',
                          }}>
                            {hc.status}
                          </span>
                        </div>
                      )
                    })}
                  </>
                )}
              </div>
            ) : (
              <Skeleton />
            )}
          </Card>

          {/* LLM Costs */}
          <Card title="LLM Costs" index={9}>
            {costData ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div style={{ display: 'flex', gap: 16 }}>
                  <StatValue value={`$${costData.daily.toFixed(4)}`} label="Today" size="sm" />
                  <StatValue value={`$${costData.monthly.toFixed(4)}`} label="Month" size="sm" />
                  <StatValue value={`${costData.total_calls}`} label="Calls" size="sm" />
                </div>
                {costData.model_breakdown.length > 0 && (
                  <div style={{ maxWidth: 200, margin: '0 auto' }}>
                    <CostBreakdown data={costData.model_breakdown} />
                  </div>
                )}
                {/* Task-type cost breakdown */}
                {costData.task_breakdown.length > 0 && (
                  <div style={{
                    marginTop: 8, paddingTop: 8,
                    borderTop: `1px solid ${colors.border}`,
                  }}>
                    <div style={{
                      fontSize: 9, color: colors.textDim, fontFamily: fonts.mono,
                      letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 6,
                    }}>
                      By Task Type
                    </div>
                    {costData.task_breakdown.slice(0, 5).map((t) => (
                      <div key={t.task_type} style={{
                        display: 'flex', justifyContent: 'space-between',
                        fontSize: 10, fontFamily: fonts.mono, padding: '2px 0',
                        color: colors.textMuted,
                      }}>
                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 110 }}>
                          {t.task_type || 'unknown'}
                        </span>
                        <span style={{ color: colors.textSecondary }}>
                          ${t.cost.toFixed(4)} ({t.calls})
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <Skeleton />
            )}
          </Card>
        </div>
      </div>

      {/* Row 4: Daily P&L + Capital Allocation charts */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <Card title="Daily P&L" accent={dailyPnl >= 0 ? colors.success : colors.danger} style={{ minHeight: 240, display: 'flex', flexDirection: 'column' }} index={9}>
          <DailyPnlBar snapshots={pnlData?.snapshots ?? []} />
        </Card>
        <Card title="Capital Allocation" accent={colors.purple} style={{ minHeight: 240, display: 'flex', flexDirection: 'column' }} index={10}>
          <CashDeployedArea snapshots={pnlData?.snapshots ?? []} />
        </Card>
      </div>

      {/* Row 5: Activity Feed + Positions */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 16, alignItems: 'stretch' }}>
        {/* Pipeline Activity Feed */}
        <Card title="Pipeline Activity" accent={colors.accent} index={11} style={{ minHeight: 260 }}>
          <ActivityFeed events={cycles?.activity_feed ?? []} />
        </Card>

        {/* Positions table */}
        <Card title={`Open Positions (${positions.length})`} accent={positions.length > 0 ? colors.warning : undefined} index={12}>
          {positions.length === 0 ? (
            <div style={{
              padding: 40, textAlign: 'center', color: colors.textDim,
              background: `linear-gradient(135deg, rgba(0, 229, 255, 0.02) 0%, rgba(0, 112, 255, 0.01) 100%)`,
              border: `1px dashed ${colors.borderLight}`,
              borderRadius: 10,
              position: 'relative', overflow: 'hidden',
            }}>
              <div style={{
                position: 'absolute', inset: 0, opacity: 0.03,
                backgroundImage: `radial-gradient(${colors.accent} 1px, transparent 1px)`,
                backgroundSize: '20px 20px',
              }} />
              <div style={{ fontSize: 13, marginBottom: 6, fontFamily: fonts.body, color: colors.textSecondary, position: 'relative' }}>No open positions</div>
              <div style={{ fontSize: 11, fontFamily: fonts.mono, letterSpacing: '0.02em', position: 'relative' }}>
                Start the bot to begin trading
              </div>
            </div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: '0 3px', fontSize: 12 }}>
                <thead>
                  <tr>
                    {['Market', 'Side', 'Size', 'Entry', 'Current', 'Cost', 'Max Gain', 'P&L', 'Opened'].map(h => (
                      <th key={h} style={{
                        padding: '8px 12px', textAlign: h === 'Market' || h === 'Side' || h === 'Opened' ? 'left' : 'right',
                        color: colors.textDim, fontWeight: 500, fontSize: 10,
                        textTransform: 'uppercase', letterSpacing: '0.08em',
                        fontFamily: fonts.mono,
                      }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {positions.map((p, pi) => {
                    const pnlColor = p.unrealized_pnl >= 0 ? colors.success : colors.danger
                    const pnlBg = p.unrealized_pnl >= 0 ? colors.successDim : colors.dangerDim
                    const pnlPct = p.avg_entry > 0 ? ((p.current_price - p.avg_entry) / p.avg_entry * 100) : 0
                    const cost = p.size * p.avg_entry
                    // Max gain: payout on win ($1/token) minus cost
                    const maxGain = p.size - cost
                    // Time since opened
                    const openedAgo = p.opened_at ? (() => {
                      const ms = Date.now() - new Date(p.opened_at).getTime()
                      const mins = Math.floor(ms / 60000)
                      if (mins < 60) return `${mins}m ago`
                      const hrs = Math.floor(mins / 60)
                      if (hrs < 24) return `${hrs}h ago`
                      const days = Math.floor(hrs / 24)
                      return `${days}d ago`
                    })() : '--'
                    return (
                      <tr key={p.token_id} style={{
                        background: colors.bgCard,
                        borderRadius: 6,
                        transition: 'background 0.25s ease, box-shadow 0.25s ease',
                        ...animDelay(pi + 11),
                      }}
                      onMouseEnter={e => {
                        e.currentTarget.style.background = colors.bgCardHover
                        e.currentTarget.style.boxShadow = `inset 2px 0 0 ${pnlColor}`
                      }}
                      onMouseLeave={e => {
                        e.currentTarget.style.background = colors.bgCard
                        e.currentTarget.style.boxShadow = 'none'
                      }}>
                        <td style={{
                          padding: '10px 12px', maxWidth: 260, overflow: 'hidden',
                          textOverflow: 'ellipsis', whiteSpace: 'nowrap', borderRadius: '6px 0 0 6px',
                          fontSize: 12,
                        }}>
                          {p.market_question || p.market_id}
                          {p.paper === 1 && (
                            <> <PillBadge text="PAPER" bg={colors.warningDim} fg={colors.warning} /></>
                          )}
                        </td>
                        <td style={{ padding: '10px 12px' }}>
                          <PillBadge
                            text={p.side === 'BUY_YES' ? 'YES' : p.side === 'BUY_NO' ? 'NO' : p.side}
                            bg={p.side === 'BUY_NO' ? colors.dangerDim : colors.successDim}
                            fg={p.side === 'BUY_NO' ? colors.danger : colors.success}
                          />
                        </td>
                        <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: fonts.mono, fontSize: 11, color: colors.textSecondary }}>
                          {p.size.toFixed(1)}
                        </td>
                        <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: fonts.mono, fontSize: 11, color: colors.textSecondary }}>
                          ${p.avg_entry.toFixed(3)}
                        </td>
                        <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: fonts.mono, fontSize: 11, color: colors.textSecondary }}>
                          ${p.current_price.toFixed(3)}
                        </td>
                        <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: fonts.mono, fontSize: 11, color: colors.textSecondary }}>
                          ${cost.toFixed(2)}
                        </td>
                        <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: fonts.mono, fontSize: 11 }}>
                          <span style={{
                            color: colors.success, fontWeight: 500,
                          }}>
                            +${maxGain.toFixed(2)}
                          </span>
                          <span style={{ color: colors.textDim, fontSize: 9, marginLeft: 3 }}>
                            ({((maxGain / cost) * 100).toFixed(0)}%)
                          </span>
                        </td>
                        <td style={{
                          padding: '10px 12px', textAlign: 'right',
                          fontFamily: fonts.mono, fontSize: 11,
                        }}>
                          <span style={{
                            padding: '3px 8px', borderRadius: 4,
                            background: pnlBg, color: pnlColor, fontWeight: 600,
                            border: `1px solid ${pnlColor}15`,
                            textShadow: `0 0 10px ${pnlColor}30`,
                          }}>
                            {p.unrealized_pnl >= 0 ? '+' : ''}${p.unrealized_pnl.toFixed(2)}
                            <span style={{ opacity: 0.7, fontSize: 9, marginLeft: 4 }}>
                              {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(1)}%
                            </span>
                          </span>
                        </td>
                        <td style={{
                          padding: '10px 12px', whiteSpace: 'nowrap', borderRadius: '0 6px 6px 0',
                          fontFamily: fonts.mono, fontSize: 10, color: colors.textDim,
                        }}>
                          {openedAgo}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      {/* Row 6: Trade History */}
      <Card title={`Trade History (${trades.length})`} accent={trades.length > 0 ? colors.purple : undefined} index={13}>
        {trades.length === 0 ? (
          <div style={{
            padding: 40, textAlign: 'center', color: colors.textDim,
            background: `linear-gradient(135deg, rgba(139, 92, 246, 0.02) 0%, rgba(0, 112, 255, 0.01) 100%)`,
            border: `1px dashed ${colors.borderLight}`,
            borderRadius: 10,
            position: 'relative', overflow: 'hidden',
          }}>
            <div style={{
              position: 'absolute', inset: 0, opacity: 0.03,
              backgroundImage: `radial-gradient(${colors.purple} 1px, transparent 1px)`,
              backgroundSize: '20px 20px',
            }} />
            <div style={{ fontSize: 13, marginBottom: 6, fontFamily: fonts.body, color: colors.textSecondary, position: 'relative' }}>No trades yet</div>
            <div style={{ fontSize: 11, fontFamily: fonts.mono, letterSpacing: '0.02em', position: 'relative' }}>
              Trades will appear here as the bot executes orders
            </div>
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: '0 3px', fontSize: 12 }}>
              <thead>
                <tr>
                  {['Time', 'Market', 'Side', 'Size', 'Price', 'Fill', 'Cost', 'Max Gain', 'Status', 'P&L'].map(h => (
                    <th key={h} style={{
                      padding: '8px 12px', textAlign: h === 'Market' || h === 'Time' ? 'left' : 'right',
                      color: colors.textDim, fontWeight: 500, fontSize: 10,
                      textTransform: 'uppercase', letterSpacing: '0.08em',
                      fontFamily: fonts.mono,
                    }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades.map((t, ti) => {
                  const tradePnl = t.pnl
                  const hasPnl = tradePnl != null && tradePnl !== 0
                  const fillOrPrice = t.fill_price ?? t.price
                  const tradeCost = t.size * fillOrPrice
                  // Max gain: payout on win ($1/token) minus cost
                  const tradeMaxGain = t.size - tradeCost
                  return (
                    <tr
                      key={t.id || ti}
                      onClick={() => handleTradeClick(t.id)}
                      style={{
                        background: colors.bgCard,
                        borderRadius: 6,
                        cursor: 'pointer',
                        transition: 'background 0.25s ease, box-shadow 0.25s ease',
                      }}
                      onMouseEnter={e => {
                        e.currentTarget.style.background = colors.bgCardHover
                        e.currentTarget.style.boxShadow = `inset 2px 0 0 ${colors.purple}`
                      }}
                      onMouseLeave={e => {
                        e.currentTarget.style.background = colors.bgCard
                        e.currentTarget.style.boxShadow = 'none'
                      }}
                    >
                      <td style={{
                        padding: '8px 12px', fontSize: 10, fontFamily: fonts.mono,
                        color: colors.textDim, whiteSpace: 'nowrap', borderRadius: '6px 0 0 6px',
                      }}>
                        {t.timestamp ? t.timestamp.replace('T', ' ').slice(0, 19) : '--'}
                      </td>
                      <td style={{
                        padding: '8px 12px', maxWidth: 220, overflow: 'hidden',
                        textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 12,
                      }}>
                        {t.market_question || t.market_id}
                        {t.paper === 1 && (
                          <> <PillBadge text="PAPER" bg={colors.warningDim} fg={colors.warning} /></>
                        )}
                      </td>
                      <td style={{ padding: '8px 12px', textAlign: 'right' }}>
                        <PillBadge
                          text={t.side === 'BUY_YES' ? 'YES' : t.side === 'BUY_NO' ? 'NO' : t.side}
                          bg={t.side === 'BUY_NO' ? colors.dangerDim : colors.successDim}
                          fg={t.side === 'BUY_NO' ? colors.danger : colors.success}
                        />
                      </td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', fontFamily: fonts.mono, fontSize: 11, color: colors.textSecondary }}>
                        {t.size.toFixed(1)}
                      </td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', fontFamily: fonts.mono, fontSize: 11, color: colors.textSecondary }}>
                        ${t.price.toFixed(3)}
                      </td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', fontFamily: fonts.mono, fontSize: 11, color: colors.textSecondary }}>
                        {t.fill_price != null ? `$${t.fill_price.toFixed(3)}` : '--'}
                      </td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', fontFamily: fonts.mono, fontSize: 11, color: colors.textSecondary }}>
                        ${tradeCost.toFixed(2)}
                      </td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', fontFamily: fonts.mono, fontSize: 11 }}>
                        <span style={{ color: colors.success, fontWeight: 500 }}>
                          +${tradeMaxGain.toFixed(2)}
                        </span>
                        <span style={{ color: colors.textDim, fontSize: 9, marginLeft: 3 }}>
                          ({tradeCost > 0 ? ((tradeMaxGain / tradeCost) * 100).toFixed(0) : '0'}%)
                        </span>
                      </td>
                      <td style={{ padding: '8px 12px', textAlign: 'right' }}>
                        <PillBadge
                          text={t.status.toUpperCase()}
                          bg={t.status === 'FILLED' || t.status === 'filled' ? colors.successDim : t.status === 'PENDING' || t.status === 'pending' ? colors.warningDim : colors.accentDim}
                          fg={t.status === 'FILLED' || t.status === 'filled' ? colors.success : t.status === 'PENDING' || t.status === 'pending' ? colors.warning : colors.textMuted}
                        />
                      </td>
                      <td style={{
                        padding: '8px 12px', textAlign: 'right', fontFamily: fonts.mono,
                        fontSize: 11, borderRadius: '0 6px 6px 0',
                      }}>
                        {hasPnl ? (
                          <span style={{
                            padding: '2px 6px', borderRadius: 4,
                            background: tradePnl >= 0 ? colors.successDim : colors.dangerDim,
                            color: tradePnl >= 0 ? colors.success : colors.danger,
                            fontWeight: 600,
                          }}>
                            {tradePnl >= 0 ? '+' : ''}${tradePnl.toFixed(2)}
                          </span>
                        ) : (
                          <span style={{ color: colors.textDim }}>--</span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Trade Detail Modal */}
      {(selectedTrade || tradeModalLoading) && (
        <TradeDetailModal
          detail={selectedTrade}
          loading={tradeModalLoading}
          onClose={() => setSelectedTrade(null)}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Signal Accuracy card
// ---------------------------------------------------------------------------

function SignalAccuracyCard({ calibration, skipAnalysis }: {
  calibration: CalibrationResponse | null
  skipAnalysis: SkipAnalysis | null
}) {
  if (!calibration && !skipAnalysis) {
    return (
      <div style={{
        flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: colors.textDim, fontSize: 11, fontFamily: fonts.mono,
      }}>
        No calibration data yet
      </div>
    )
  }

  const curve = calibration?.calibration_curve ?? []
  const maxCount = Math.max(...curve.map(b => b.count), 1)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Top stats */}
      {calibration && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
          <div style={{
            padding: '8px 6px', borderRadius: 6, textAlign: 'center',
            background: 'rgba(139,92,246,0.06)', border: `1px solid rgba(139,92,246,0.1)`,
          }}>
            <div style={{
              fontSize: 16, fontWeight: 700, fontFamily: fonts.mono,
              color: Math.abs(calibration.mean_bias) < 0.05 ? colors.success : colors.warning,
              letterSpacing: '-0.02em',
            }}>
              {calibration.mean_bias >= 0 ? '+' : ''}{(calibration.mean_bias * 100).toFixed(1)}%
            </div>
            <div style={{
              fontSize: 8, color: colors.textDim, fontFamily: fonts.mono,
              textTransform: 'uppercase', letterSpacing: '0.06em', marginTop: 2,
            }}>
              Bias
            </div>
          </div>
          <div style={{
            padding: '8px 6px', borderRadius: 6, textAlign: 'center',
            background: 'rgba(0,229,255,0.04)', border: `1px solid ${colors.border}`,
          }}>
            <div style={{
              fontSize: 16, fontWeight: 700, fontFamily: fonts.mono,
              color: calibration.abs_mean_error < 0.15 ? colors.success : colors.warning,
              letterSpacing: '-0.02em',
            }}>
              {(calibration.abs_mean_error * 100).toFixed(1)}%
            </div>
            <div style={{
              fontSize: 8, color: colors.textDim, fontFamily: fonts.mono,
              textTransform: 'uppercase', letterSpacing: '0.06em', marginTop: 2,
            }}>
              Avg Error
            </div>
          </div>
          <div style={{
            padding: '8px 6px', borderRadius: 6, textAlign: 'center',
            background: 'rgba(0,229,255,0.04)', border: `1px solid ${colors.border}`,
          }}>
            <div style={{
              fontSize: 16, fontWeight: 700, fontFamily: fonts.mono,
              color: colors.textPrimary, letterSpacing: '-0.02em',
            }}>
              {calibration.sample_count}
            </div>
            <div style={{
              fontSize: 8, color: colors.textDim, fontFamily: fonts.mono,
              textTransform: 'uppercase', letterSpacing: '0.06em', marginTop: 2,
            }}>
              Samples
            </div>
          </div>
        </div>
      )}

      {/* Mini calibration curve */}
      {curve.length > 0 && (
        <div>
          <div style={{
            fontSize: 9, color: colors.textDim, fontFamily: fonts.mono,
            letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 6,
          }}>
            Calibration Curve
          </div>
          <div style={{
            display: 'flex', alignItems: 'flex-end', gap: 3, height: 48,
          }}>
            {curve.map((bucket, i) => {
              const estimated = bucket.avg_estimated
              const actual = bucket.avg_actual
              const barH = Math.max(4, (bucket.count / maxCount) * 48)
              const isGood = Math.abs(estimated - actual) < 0.1
              return (
                <div key={i} style={{
                  flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2,
                  position: 'relative',
                }}>
                  {/* Actual vs expected dot */}
                  <div style={{ position: 'relative', width: '100%', height: barH }}>
                    <div style={{
                      width: '100%', height: '100%', borderRadius: 2,
                      background: isGood
                        ? `linear-gradient(180deg, ${colors.success}40, ${colors.success}15)`
                        : `linear-gradient(180deg, ${colors.warning}40, ${colors.warning}15)`,
                      border: `1px solid ${isGood ? colors.success : colors.warning}25`,
                    }} />
                  </div>
                </div>
              )
            })}
          </div>
          {/* X-axis labels */}
          <div style={{
            display: 'flex', justifyContent: 'space-between',
            fontSize: 8, color: colors.textDim, fontFamily: fonts.mono,
            marginTop: 3, padding: '0 2px',
          }}>
            <span>0%</span>
            <span>50%</span>
            <span>100%</span>
          </div>
        </div>
      )}

      {/* Skip analysis */}
      {skipAnalysis && skipAnalysis.total_skipped > 0 && (
        <div style={{
          padding: '8px 10px', borderRadius: 6,
          background: 'rgba(255,170,0,0.04)', border: `1px solid rgba(255,170,0,0.08)`,
        }}>
          <div style={{
            fontSize: 9, color: colors.textDim, fontFamily: fonts.mono,
            textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6,
          }}>
            Missed Opportunities
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <span style={{
                fontSize: 18, fontWeight: 700, fontFamily: fonts.mono,
                color: skipAnalysis.missed_opportunities > 0 ? colors.warning : colors.success,
              }}>
                {skipAnalysis.missed_opportunities}
              </span>
              <span style={{
                fontSize: 10, color: colors.textDim, fontFamily: fonts.mono, marginLeft: 4,
              }}>
                / {skipAnalysis.resolved_count} resolved
              </span>
            </div>
            {skipAnalysis.avg_missed_edge > 0 && (
              <div style={{
                fontSize: 10, fontFamily: fonts.mono, color: colors.warning,
                padding: '2px 8px', borderRadius: 4,
                background: colors.warningDim,
              }}>
                avg edge: {(skipAnalysis.avg_missed_edge * 100).toFixed(1)}%
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Session stat mini component
// ---------------------------------------------------------------------------

function SessionStat({ value, label, color }: { value: number; label: string; color: string }) {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3,
      padding: '10px 6px', borderRadius: 8,
      background: `${color}08`,
      border: `1px solid ${color}12`,
      transition: 'border-color 0.3s ease, background 0.3s ease',
    }}
      onMouseEnter={e => {
        e.currentTarget.style.borderColor = `${color}30`
        e.currentTarget.style.background = `${color}10`
      }}
      onMouseLeave={e => {
        e.currentTarget.style.borderColor = `${color}12`
        e.currentTarget.style.background = `${color}08`
      }}
    >
      <span style={{
        fontSize: 22, fontWeight: 700, fontFamily: fonts.mono,
        color, letterSpacing: '-0.02em',
        textShadow: `0 0 20px ${color}33`,
      }}>
        {value}
      </span>
      <span style={{
        fontSize: 8, color: colors.textDim, fontFamily: fonts.mono,
        letterSpacing: '0.06em', textTransform: 'uppercase',
      }}>
        {label}
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Activity Feed component
// ---------------------------------------------------------------------------

function ActivityFeed({ events }: { events: ActivityEvent[] }) {
  if (events.length === 0) {
    return (
      <div style={{
        flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
        textAlign: 'center', color: colors.textDim,
        fontSize: 11, fontFamily: fonts.mono,
        border: `1px dashed ${colors.border}`, borderRadius: 6,
        background: `linear-gradient(135deg, rgba(0, 229, 255, 0.02) 0%, rgba(0, 112, 255, 0.01) 100%)`,
      }}>
        No pipeline activity yet
      </div>
    )
  }

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 1,
      flex: 1, overflowY: 'auto',
      /* Custom scrollbar */
      scrollbarWidth: 'thin',
      scrollbarColor: `${colors.border} transparent`,
    }}>
      {events.slice(0, 20).map((evt, i) => {
        const meta = activityMeta(evt.type)
        return (
          <div key={`${evt.timestamp}-${i}`} style={{
            display: 'flex', alignItems: 'flex-start', gap: 10,
            padding: '7px 10px', borderRadius: 6,
            background: i === 0 ? `${meta.color}08` : 'transparent',
            borderLeft: i === 0 ? `2px solid ${meta.color}` : '2px solid transparent',
            transition: 'background 0.25s ease, border-color 0.25s ease',
            ...animDelay(i),
          }}
            onMouseEnter={e => {
              if (i !== 0) {
                e.currentTarget.style.background = `${meta.color}06`
                e.currentTarget.style.borderLeft = `2px solid ${meta.color}40`
              }
            }}
            onMouseLeave={e => {
              if (i !== 0) {
                e.currentTarget.style.background = 'transparent'
                e.currentTarget.style.borderLeft = '2px solid transparent'
              }
            }}>
            {/* Icon */}
            <span style={{
              width: 18, height: 18, borderRadius: 4, flexShrink: 0,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              background: `${meta.color}12`,
              color: meta.color, fontSize: 10, fontWeight: 700,
              fontFamily: fonts.mono, marginTop: 1,
            }}>
              {meta.icon}
            </span>
            {/* Content */}
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{
                fontSize: 11, color: colors.textSecondary,
                fontFamily: fonts.body, lineHeight: 1.3,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {evt.message}
              </div>
              {evt.detail && (
                <div style={{
                  fontSize: 9, color: colors.textDim, fontFamily: fonts.mono,
                  marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {evt.detail}
                </div>
              )}
            </div>
            {/* Timestamp */}
            <span style={{
              fontSize: 9, color: colors.textDim, fontFamily: fonts.mono,
              whiteSpace: 'nowrap', flexShrink: 0,
            }}>
              {timeAgo(evt.timestamp)}
            </span>
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Trade Detail Modal
// ---------------------------------------------------------------------------

function TradeDetailModal({ detail, loading, onClose }: {
  detail: TradeDetail | null; loading: boolean; onClose: () => void
}) {
  const trade = detail?.trade
  const fd = detail?.frontier_decision
  const signals = detail?.signals ?? []

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 1000,
        background: 'rgba(3, 5, 9, 0.88)',
        backdropFilter: 'blur(12px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        animation: 'fadeInUp 0.25s ease forwards',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: colors.gradientCard,
          border: `1px solid ${colors.borderLight}`,
          borderRadius: 16,
          padding: 32,
          width: '90%',
          maxWidth: 720,
          maxHeight: '85vh',
          overflowY: 'auto',
          position: 'relative',
          boxShadow: glowShadow(colors.accent, 0.06),
          scrollbarWidth: 'thin',
          scrollbarColor: `${colors.border} transparent`,
        }}
      >
        {/* Close button */}
        <button
          onClick={onClose}
          style={{
            position: 'absolute', top: 14, right: 14,
            background: 'rgba(255,255,255,0.05)', border: `1px solid ${colors.border}`,
            borderRadius: 6, width: 28, height: 28,
            color: colors.textMuted, cursor: 'pointer', fontSize: 14,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontFamily: fonts.mono,
          }}
        >
          x
        </button>

        {loading || !trade ? (
          <div style={{
            padding: 40, textAlign: 'center', color: colors.textDim,
            fontFamily: fonts.mono, fontSize: 12,
          }}>
            Loading trade details...
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
            {/* Header */}
            <div>
              <div style={{
                fontSize: 9, color: colors.textDim, fontFamily: fonts.mono,
                textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 6,
              }}>
                Trade Detail
              </div>
              <div style={{
                fontSize: 16, color: colors.textPrimary, fontFamily: fonts.body,
                fontWeight: 600, lineHeight: 1.3,
              }}>
                {trade.market_question || trade.market_id}
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                <PillBadge
                  text={trade.side === 'BUY_YES' || trade.side === 'BUY' ? 'BUY' : trade.side === 'BUY_NO' ? 'NO' : trade.side === 'SELL' ? 'SELL' : trade.side}
                  bg={trade.side === 'BUY_NO' || trade.side === 'SELL' ? colors.dangerDim : colors.successDim}
                  fg={trade.side === 'BUY_NO' || trade.side === 'SELL' ? colors.danger : colors.success}
                />
                <PillBadge
                  text={trade.status.toUpperCase()}
                  bg={trade.status === 'FILLED' || trade.status === 'filled' ? colors.successDim : colors.warningDim}
                  fg={trade.status === 'FILLED' || trade.status === 'filled' ? colors.success : colors.warning}
                />
                {trade.paper === 1 && <PillBadge text="PAPER" bg={colors.warningDim} fg={colors.warning} />}
              </div>
            </div>

            {/* Trade Info Grid */}
            <div style={{
              display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12,
              background: 'rgba(0,229,255,0.02)', borderRadius: 8,
              padding: 14, border: `1px solid ${colors.border}`,
            }}>
              <ModalStat label="Limit Price" value={`$${trade.price.toFixed(4)}`} />
              <ModalStat label="Fill Price" value={trade.fill_price != null ? `$${trade.fill_price.toFixed(4)}` : (trade.status?.toUpperCase() === 'FILLED' ? `$${trade.price.toFixed(4)}` : '--')} />
              <ModalStat label="Cost" value={`$${(trade.size * trade.price).toFixed(2)}`} />
              <ModalStat
                label="P&L"
                value={trade.pnl != null ? `${trade.pnl >= 0 ? '+' : ''}$${trade.pnl.toFixed(2)}` : (trade.status?.toUpperCase() === 'PENDING' ? 'Pending' : '$0.00')}
                color={trade.pnl != null ? (trade.pnl >= 0 ? colors.success : colors.danger) : undefined}
              />
              <ModalStat label="Placed At" value={(trade.placed_at || trade.timestamp) ? (trade.placed_at || trade.timestamp).replace('T', ' ').slice(0, 19) : '--'} />
              <ModalStat label="Timestamp" value={trade.timestamp ? trade.timestamp.replace('T', ' ').slice(0, 19) : '--'} />
              <ModalStat label="Order ID" value={trade.order_id ? trade.order_id.slice(0, 16) : (trade.paper ? 'Paper Trade' : '--')} />
              <ModalStat label="Trade ID" value={trade.id.slice(0, 16)} />
            </div>

            {/* Frontier Decision — Why This Trade Was Placed */}
            {fd && (
              <div>
                <div style={{
                  fontSize: 10, color: colors.textMuted, fontFamily: fonts.mono,
                  textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10,
                  display: 'flex', alignItems: 'center', gap: 6,
                }}>
                  <span style={{
                    width: 4, height: 4, borderRadius: 1,
                    background: colors.purple, boxShadow: `0 0 6px ${colors.purple}`,
                  }} />
                  Why This Trade Was Placed
                </div>
                <div style={{
                  display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12,
                  background: 'rgba(139,92,246,0.03)', borderRadius: 8,
                  padding: 14, border: `1px solid rgba(139,92,246,0.1)`,
                }}>
                  <ModalStat label="Estimated Prob" value={`${(fd.estimated_prob * 100).toFixed(1)}%`} color={colors.accent} />
                  <ModalStat label="Market Price" value={`${(fd.market_price * 100).toFixed(1)}%`} />
                  <ModalStat label="Edge" value={`${(fd.edge * 100).toFixed(1)}%`} color={fd.edge > 0 ? colors.success : colors.danger} />
                  <ModalStat label="Confidence" value={`${(fd.confidence * 100).toFixed(0)}%`} />
                  <ModalStat label="Kelly Fraction" value={`${(fd.kelly_fraction * 100).toFixed(2)}%`} />
                  <ModalStat label="Bet Size" value={`$${fd.bet_size_usd.toFixed(2)}`} color={colors.warning} />
                  <ModalStat label="Effective Prob" value={`${(fd.effective_prob * 100).toFixed(1)}%`} />
                  <ModalStat label="Decision" value={fd.should_trade ? 'TRADE' : 'SKIP'} color={fd.should_trade ? colors.success : colors.danger} />
                </div>

                {/* Edge visual bar */}
                <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ fontSize: 9, color: colors.textDim, fontFamily: fonts.mono, minWidth: 64, textTransform: 'uppercase' }}>
                    Edge
                  </span>
                  <div style={{
                    flex: 1, height: 6, background: 'rgba(255,255,255,0.04)',
                    borderRadius: 3, overflow: 'hidden', position: 'relative',
                  }}>
                    {/* Center line (0 edge) */}
                    <div style={{
                      position: 'absolute', left: '50%', top: 0, bottom: 0,
                      width: 1, background: colors.border,
                    }} />
                    <div style={{
                      position: 'absolute',
                      left: fd.edge >= 0 ? '50%' : `${50 + fd.edge * 500}%`,
                      width: `${Math.abs(fd.edge) * 500}%`,
                      maxWidth: '50%',
                      height: '100%', borderRadius: 3,
                      background: fd.edge > 0 ? colors.gradientSuccess : colors.gradientDanger,
                      boxShadow: `0 0 8px ${fd.edge > 0 ? colors.success : colors.danger}40`,
                    }} />
                  </div>
                  <span style={{
                    fontSize: 11, fontFamily: fonts.mono, fontWeight: 600,
                    color: fd.edge > 0 ? colors.success : colors.danger, minWidth: 50, textAlign: 'right',
                  }}>
                    {fd.edge > 0 ? '+' : ''}{(fd.edge * 100).toFixed(1)}%
                  </span>
                </div>
              </div>
            )}

            {/* Signals */}
            {signals.length > 0 && (
              <div>
                <div style={{
                  fontSize: 10, color: colors.textMuted, fontFamily: fonts.mono,
                  textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10,
                  display: 'flex', alignItems: 'center', gap: 6,
                }}>
                  <span style={{
                    width: 4, height: 4, borderRadius: 1,
                    background: colors.accent, boxShadow: `0 0 6px ${colors.accent}`,
                  }} />
                  Signal Breakdown ({signals.length} signals)
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {signals.map((sig, si) => (
                    <div key={sig.id || si} style={{
                      padding: '10px 14px', borderRadius: 8,
                      background: 'rgba(0,229,255,0.02)',
                      border: `1px solid ${colors.border}`,
                    }}>
                      <div style={{
                        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                        marginBottom: 6,
                      }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <span style={{
                            fontSize: 11, fontWeight: 600, color: colors.textPrimary,
                            fontFamily: fonts.mono,
                          }}>
                            {sig.signal_source}
                          </span>
                          <span style={{
                            fontSize: 9, color: colors.textDim, fontFamily: fonts.mono,
                          }}>
                            {sig.model_used}
                          </span>
                        </div>
                        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                          <span style={{
                            fontSize: 12, fontWeight: 600, fontFamily: fonts.mono,
                            color: colors.accent,
                          }}>
                            {(sig.probability * 100).toFixed(1)}%
                          </span>
                          <span style={{
                            fontSize: 10, fontFamily: fonts.mono, color: colors.textMuted,
                          }}>
                            conf: {(sig.confidence * 100).toFixed(0)}%
                          </span>
                        </div>
                      </div>
                      {/* Probability bar */}
                      <div style={{
                        width: '100%', height: 3, background: 'rgba(255,255,255,0.04)',
                        borderRadius: 2, overflow: 'hidden', marginBottom: 6,
                      }}>
                        <div style={{
                          height: '100%', width: `${sig.probability * 100}%`,
                          background: colors.gradientAccent, borderRadius: 2,
                        }} />
                      </div>
                      {sig.reasoning && (
                        <div style={{
                          fontSize: 10, color: colors.textMuted, fontFamily: fonts.body,
                          lineHeight: 1.4, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                        }}>
                          {sig.reasoning.length > 300 ? sig.reasoning.slice(0, 300) + '...' : sig.reasoning}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Raw IDs */}
            <div style={{
              fontSize: 9, color: colors.textDim, fontFamily: fonts.mono,
              display: 'flex', flexDirection: 'column', gap: 3,
              padding: '10px 12px', borderRadius: 6,
              background: 'rgba(0,0,0,0.2)', border: `1px solid ${colors.border}`,
            }}>
              <span>Market ID: {trade.market_id}</span>
              <span>Token ID: {trade.token_id}</span>
              <span>Trade ID: {trade.id}</span>
              {trade.order_id && <span>Order ID: {trade.order_id}</span>}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function ModalStat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <div style={{
        fontSize: 9, color: colors.textDim, fontFamily: fonts.mono,
        letterSpacing: '0.04em', textTransform: 'uppercase', marginBottom: 3,
      }}>
        {label}
      </div>
      <div style={{
        fontSize: 13, fontWeight: 600, fontFamily: fonts.mono,
        color: color || colors.textPrimary, letterSpacing: '-0.02em',
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>
        {value}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Skeleton
// ---------------------------------------------------------------------------

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
