import { useState, useEffect, useCallback } from 'react'
import { colors, cardStyle, glowShadow, fonts, animDelay } from '../theme'
import { api, HealthResponse, WalletResponse, CostResponse, BotStatus, Position, PnlResponse, PaperBalance } from '../api'
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
  const [actionLoading, setActionLoading] = useState(false)

  const displayStatus = wsBotStatus || botStatus

  const refresh = useCallback(() => {
    api.fetchHealth().then(setHealth).catch(() => {})
    api.fetchWallet().then(setWallet).catch(() => {})
    api.fetchCosts().then(setCostData).catch(() => {})
    api.fetchBotStatus().then(setBotStatus).catch(() => {})
    api.fetchPositions().then(setPositions).catch(() => {})
    api.fetchPnl().then(setPnlData).catch(() => {})
    api.fetchPaperBalance().then(setPaperBal).catch(() => {})
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 30000)
    return () => clearInterval(id)
  }, [refresh])

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

      {/* Row 2: PnL Chart + Connections + Costs */}
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 14 }}>
        {/* PnL Chart */}
        <Card title="Portfolio Value" accent={colors.accent} style={{ minHeight: 280 }} index={4}>
          <PnlChart snapshots={pnlData?.snapshots ?? []} />
        </Card>

        {/* Right column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {/* Connections */}
          <Card title="Connections" index={5}>
            {health ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {health.services.map((s, si) => (
                  <div key={s.name} style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    padding: '6px 10px', borderRadius: 6,
                    background: s.healthy ? 'rgba(0,255,136,0.03)' : 'rgba(255,51,102,0.05)',
                    border: `1px solid ${s.healthy ? 'rgba(0,255,136,0.08)' : 'rgba(255,51,102,0.1)'}`,
                    transition: 'all 0.3s',
                    ...animDelay(si + 6),
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
          <Card title="LLM Costs" index={6}>
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

      {/* Row 3: Positions table */}
      <Card title={`Open Positions (${positions.length})`} accent={positions.length > 0 ? colors.warning : undefined} index={7}>
        {positions.length === 0 ? (
          <div style={{
            padding: 32, textAlign: 'center', color: colors.textDim,
            background: 'rgba(0, 229, 255, 0.01)',
            border: `1px dashed ${colors.border}`,
            borderRadius: 8,
            position: 'relative', overflow: 'hidden',
          }}>
            {/* Animated dashed border overlay */}
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
                      ...animDelay(pi + 8),
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
