import { useState, useEffect, useCallback, useRef } from 'react'
import { colors, cardStyle, glowShadow, fonts, animDelay } from '../theme'
import {
  api, HealthResponse, WalletResponse, CostResponse, BotStatus,
  Position, PnlResponse, PaperBalance, CyclesResponse, ActivityEvent,
} from '../api'
import CostBreakdown from './charts/CostBreakdown'
import PnlChart from './charts/PnlChart'

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
      {/* Top accent line */}
      {accent && (
        <div style={{
          position: 'absolute', top: 0, left: 0, right: 0, height: 1,
          background: `linear-gradient(90deg, transparent, ${accent}, transparent)`,
          opacity: 0.6,
        }} />
      )}
      {/* Corner tick marks */}
      <div style={{
        position: 'absolute', top: 6, left: 6,
        width: 8, height: 8,
        borderTop: `1px solid ${accent || colors.borderLight}`,
        borderLeft: `1px solid ${accent || colors.borderLight}`,
        opacity: 0.4,
      }} />
      <div style={{
        position: 'absolute', top: 6, right: 6,
        width: 8, height: 8,
        borderTop: `1px solid ${accent || colors.borderLight}`,
        borderRight: `1px solid ${accent || colors.borderLight}`,
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
  const size = 72
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
}

export default function Dashboard({ wsBotStatus }: DashboardProps) {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [wallet, setWallet] = useState<WalletResponse | null>(null)
  const [costData, setCostData] = useState<CostResponse | null>(null)
  const [botStatus, setBotStatus] = useState<BotStatus | null>(null)
  const [positions, setPositions] = useState<Position[]>([])
  const [pnlData, setPnlData] = useState<PnlResponse | null>(null)
  const [paperBal, setPaperBal] = useState<PaperBalance | null>(null)
  const [cycles, setCycles] = useState<CyclesResponse | null>(null)
  const [actionLoading, setActionLoading] = useState(false)

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

  const totalPnl = pnlData?.total_pnl ?? 0
  const dailyPnl = pnlData?.daily_pnl ?? 0
  const unrealizedPnl = positions.reduce((s, p) => s + p.unrealized_pnl, 0)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Row 1: Key metrics strip */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14 }}>
        {/* Bot Status */}
        <Card title="Bot Status" accent={displayStatus?.running ? colors.success : colors.textDim} index={0}>
          {displayStatus ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <PillBadge
                  text={displayStatus.running ? 'RUNNING' : 'STOPPED'}
                  bg={displayStatus.running ? colors.successDim : 'rgba(85,102,136,0.1)'}
                  fg={displayStatus.running ? colors.success : colors.textDim}
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
              <button
                disabled={actionLoading}
                onClick={displayStatus.running ? handleStop : handleStart}
                style={{
                  padding: '8px 0', borderRadius: 6, border: 'none', fontFamily: fonts.mono,
                  background: displayStatus.running
                    ? colors.dangerDim
                    : colors.gradientAccent,
                  color: displayStatus.running ? colors.danger : '#000',
                  cursor: actionLoading ? 'wait' : 'pointer',
                  fontSize: 11, fontWeight: 600, transition: 'all 0.3s',
                  boxShadow: displayStatus.running ? 'none' : `0 2px 12px rgba(0,229,255,0.25)`,
                  letterSpacing: '0.06em',
                  textTransform: 'uppercase',
                }}
              >
                {actionLoading ? '...' : displayStatus.running ? 'Stop Bot' : 'Start Bot'}
              </button>
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
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
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
                {displayStatus.phase === 'filtering' && 'Discovering & filtering markets...'}
                {displayStatus.phase === 'aggregating' && 'Running signal aggregation...'}
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

      {/* Row 3: PnL Chart + Right column */}
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 14 }}>
        {/* PnL Chart */}
        <Card title="Portfolio Value" accent={colors.accent} style={{ minHeight: 280 }} index={6}>
          <PnlChart snapshots={pnlData?.snapshots ?? []} />
        </Card>

        {/* Right column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {/* Connections */}
          <Card title="Connections" index={7}>
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
              </div>
            ) : (
              <Skeleton />
            )}
          </Card>

          {/* LLM Costs */}
          <Card title="LLM Costs" index={8}>
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
              </div>
            ) : (
              <Skeleton />
            )}
          </Card>
        </div>
      </div>

      {/* Row 4: Activity Feed + Positions */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 14 }}>
        {/* Pipeline Activity Feed */}
        <Card title="Pipeline Activity" accent={colors.accent} index={9}>
          <ActivityFeed events={cycles?.activity_feed ?? []} />
        </Card>

        {/* Positions table */}
        <Card title={`Open Positions (${positions.length})`} accent={positions.length > 0 ? colors.warning : undefined} index={10}>
          {positions.length === 0 ? (
            <div style={{
              padding: 32, textAlign: 'center', color: colors.textDim,
              background: 'rgba(0, 229, 255, 0.01)',
              border: `1px dashed ${colors.border}`,
              borderRadius: 8,
              position: 'relative', overflow: 'hidden',
            }}>
              <div style={{ fontSize: 13, marginBottom: 4, fontFamily: fonts.body }}>No open positions</div>
              <div style={{ fontSize: 11, fontFamily: fonts.mono, letterSpacing: '0.02em' }}>
                Start the bot to begin trading
              </div>
            </div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: '0 3px', fontSize: 12 }}>
                <thead>
                  <tr>
                    {['Market', 'Side', 'Entry', 'Current', 'Size', 'P&L'].map(h => (
                      <th key={h} style={{
                        padding: '8px 12px', textAlign: h === 'Market' || h === 'Side' ? 'left' : 'right',
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
                    return (
                      <tr key={p.token_id} style={{
                        background: colors.bgCard,
                        borderRadius: 6,
                        transition: 'background 0.2s',
                        ...animDelay(pi + 11),
                      }}>
                        <td style={{
                          padding: '10px 12px', maxWidth: 300, overflow: 'hidden',
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
                          ${p.avg_entry.toFixed(3)}
                        </td>
                        <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: fonts.mono, fontSize: 11, color: colors.textSecondary }}>
                          ${p.current_price.toFixed(3)}
                        </td>
                        <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: fonts.mono, fontSize: 11, color: colors.textSecondary }}>
                          ${p.size.toFixed(2)}
                        </td>
                        <td style={{
                          padding: '10px 12px', textAlign: 'right', borderRadius: '0 6px 6px 0',
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
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Session stat mini component
// ---------------------------------------------------------------------------

function SessionStat({ value, label, color }: { value: number; label: string; color: string }) {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2,
      padding: '8px 4px', borderRadius: 6,
      background: `${color}08`,
      border: `1px solid ${color}15`,
    }}>
      <span style={{
        fontSize: 20, fontWeight: 700, fontFamily: fonts.mono,
        color, letterSpacing: '-0.02em',
        textShadow: `0 0 16px ${color}33`,
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
        padding: 24, textAlign: 'center', color: colors.textDim,
        fontSize: 11, fontFamily: fonts.mono,
        border: `1px dashed ${colors.border}`, borderRadius: 6,
      }}>
        No pipeline activity yet
      </div>
    )
  }

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 1,
      maxHeight: 240, overflowY: 'auto',
      /* Custom scrollbar */
      scrollbarWidth: 'thin',
      scrollbarColor: `${colors.border} transparent`,
    }}>
      {events.slice(0, 20).map((evt, i) => {
        const meta = activityMeta(evt.type)
        return (
          <div key={`${evt.timestamp}-${i}`} style={{
            display: 'flex', alignItems: 'flex-start', gap: 8,
            padding: '6px 8px', borderRadius: 4,
            background: i === 0 ? `${meta.color}06` : 'transparent',
            borderLeft: i === 0 ? `2px solid ${meta.color}` : '2px solid transparent',
            transition: 'all 0.3s',
            ...animDelay(i),
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
// Skeleton
// ---------------------------------------------------------------------------

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
