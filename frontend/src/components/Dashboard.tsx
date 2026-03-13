import { useState, useEffect, useCallback } from 'react'
import { colors, cardStyle, glowShadow } from '../theme'
import { api, HealthResponse, WalletResponse, CostResponse, BotStatus, Position, PnlResponse } from '../api'
import CostBreakdown from './charts/CostBreakdown'
import PnlChart from './charts/PnlChart'

// ---------------------------------------------------------------------------
// Shared UI atoms
// ---------------------------------------------------------------------------

function Card({ title, children, accent, style }: {
  title: string; children: React.ReactNode; accent?: string; style?: React.CSSProperties
}) {
  return (
    <div style={{
      ...cardStyle,
      position: 'relative',
      overflow: 'hidden',
      ...style,
    }}>
      {accent && (
        <div style={{
          position: 'absolute', top: 0, left: 0, right: 0, height: 2,
          background: accent,
        }} />
      )}
      <h3 style={{
        margin: '0 0 14px', fontSize: 11, fontWeight: 600,
        color: colors.textMuted, textTransform: 'uppercase',
        letterSpacing: '0.08em',
      }}>
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
        fontSize, fontWeight: 700, color: color || colors.textPrimary,
        letterSpacing: '-0.02em', fontFamily: "'JetBrains Mono', monospace",
      }}>
        {value}
      </div>
      <div style={{ fontSize: 11, color: colors.textDim, marginTop: 2, fontWeight: 500 }}>{label}</div>
    </div>
  )
}

function StatusDot({ ok, pulse }: { ok: boolean; pulse?: boolean }) {
  return (
    <span style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
      {pulse && ok && (
        <span style={{
          position: 'absolute', width: 14, height: 14, borderRadius: '50%',
          background: colors.success, opacity: 0.3,
          animation: 'pulse 2s ease-in-out infinite',
        }} />
      )}
      <span style={{
        display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
        background: ok ? colors.success : colors.danger,
        boxShadow: ok ? `0 0 6px ${colors.success}40` : 'none',
        position: 'relative', zIndex: 1,
      }} />
      <style>{`@keyframes pulse { 0%, 100% { transform: scale(1); opacity: 0.3; } 50% { transform: scale(1.8); opacity: 0; } }`}</style>
    </span>
  )
}

function PillBadge({ text, bg, fg }: { text: string; bg: string; fg?: string }) {
  return (
    <span style={{
      padding: '3px 10px', borderRadius: 20, fontSize: 11, fontWeight: 600,
      background: bg, color: fg || '#fff', letterSpacing: '0.02em',
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
  const [actionLoading, setActionLoading] = useState(false)

  const displayStatus = wsBotStatus || botStatus

  const refresh = useCallback(() => {
    api.fetchHealth().then(setHealth).catch(() => {})
    api.fetchWallet().then(setWallet).catch(() => {})
    api.fetchCosts().then(setCostData).catch(() => {})
    api.fetchBotStatus().then(setBotStatus).catch(() => {})
    api.fetchPositions().then(setPositions).catch(() => {})
    api.fetchPnl().then(setPnlData).catch(() => {})
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
        <Card title="Bot Status" accent={displayStatus?.running ? colors.success : colors.textDim}>
          {displayStatus ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <PillBadge
                  text={displayStatus.running ? 'RUNNING' : 'STOPPED'}
                  bg={displayStatus.running ? colors.successDim : 'rgba(85,97,120,0.2)'}
                  fg={displayStatus.running ? colors.success : colors.textDim}
                />
                {displayStatus.paper_trading && (
                  <PillBadge text="PAPER" bg={colors.warningDim} fg={colors.warning} />
                )}
              </div>
              <div style={{ fontSize: 12, color: colors.textMuted }}>
                Phase: <span style={{ color: colors.textSecondary, fontWeight: 500 }}>{displayStatus.phase}</span>
                <span style={{ margin: '0 8px', color: colors.border }}>|</span>
                Cycles: <span style={{ color: colors.textSecondary, fontWeight: 500 }}>{displayStatus.cycle_count}</span>
              </div>
              <button
                disabled={actionLoading}
                onClick={displayStatus.running ? handleStop : handleStart}
                style={{
                  padding: '8px 0', borderRadius: 8, border: 'none', fontFamily: 'inherit',
                  background: displayStatus.running
                    ? `${colors.dangerDim}`
                    : colors.gradientAccent,
                  color: displayStatus.running ? colors.danger : '#fff',
                  cursor: actionLoading ? 'wait' : 'pointer',
                  fontSize: 13, fontWeight: 600, transition: 'all 0.2s',
                  boxShadow: displayStatus.running ? 'none' : '0 2px 8px rgba(59,130,246,0.3)',
                }}
              >
                {actionLoading ? '...' : displayStatus.running ? 'Stop Bot' : 'Start Bot'}
              </button>
            </div>
          ) : (
            <Skeleton />
          )}
        </Card>

        {/* Wallet */}
        <Card title="Wallet" accent={colors.accent}>
          {wallet ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div style={{
                fontSize: 11, color: colors.textDim, fontFamily: "'JetBrains Mono', monospace",
                background: colors.accentDim, padding: '3px 8px', borderRadius: 6, display: 'inline-block',
                width: 'fit-content',
              }}>
                {wallet.address.slice(0, 6)}...{wallet.address.slice(-4)}
              </div>
              <div style={{ display: 'flex', gap: 20 }}>
                <StatValue value={`$${wallet.usdc.toFixed(2)}`} label="USDC" size="md" />
                <StatValue value={wallet.matic.toFixed(4)} label="MATIC" size="sm" />
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
                <StatusDot ok={wallet.has_gas} />
                <span style={{ color: wallet.has_gas ? colors.textMuted : colors.danger }}>
                  {wallet.has_gas ? 'Gas OK' : 'Low gas!'}
                </span>
                <span style={{ marginLeft: 'auto', color: colors.textDim }}>
                  {wallet.positions_count} pos
                </span>
              </div>
            </div>
          ) : (
            <Skeleton />
          )}
        </Card>

        {/* Daily P&L */}
        <Card title="Today's P&L" accent={dailyPnl >= 0 ? colors.success : colors.danger}>
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

        {/* Win Rate / Total P&L */}
        <Card title="Performance" accent={colors.accentLight}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <StatValue
              value={`${totalPnl >= 0 ? '+' : ''}$${totalPnl.toFixed(2)}`}
              label="Total P&L"
              color={totalPnl >= 0 ? colors.success : colors.danger}
              size="lg"
            />
            <div style={{ display: 'flex', gap: 16 }}>
              <StatValue
                value={`${((pnlData?.win_rate ?? 0) * 100).toFixed(0)}%`}
                label="Win Rate"
                size="sm"
              />
              <div>
                {/* Mini win rate bar */}
                <div style={{
                  width: 80, height: 6, background: colors.dangerDim, borderRadius: 3, overflow: 'hidden',
                }}>
                  <div style={{
                    height: '100%', borderRadius: 3,
                    width: `${(pnlData?.win_rate ?? 0) * 100}%`,
                    background: colors.gradientSuccess,
                    transition: 'width 0.5s ease',
                  }} />
                </div>
                <div style={{ fontSize: 11, color: colors.textDim, marginTop: 3 }}>of closed trades</div>
              </div>
            </div>
          </div>
        </Card>
      </div>

      {/* Row 2: PnL Chart + Connections + Costs */}
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 14 }}>
        {/* PnL Chart */}
        <Card title="Portfolio Value" accent={colors.accent} style={{ minHeight: 280 }}>
          <PnlChart snapshots={pnlData?.snapshots ?? []} />
        </Card>

        {/* Right column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {/* Connections */}
          <Card title="Connections">
            {health ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {health.services.map(s => (
                  <div key={s.name} style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    padding: '6px 10px', borderRadius: 8,
                    background: s.healthy ? colors.successDim : colors.dangerDim,
                    transition: 'all 0.2s',
                  }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
                      <StatusDot ok={s.healthy} pulse={s.healthy} />
                      {s.name}
                    </span>
                    <span style={{
                      fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
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
          <Card title="LLM Costs">
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
      <Card title={`Open Positions (${positions.length})`} accent={positions.length > 0 ? colors.warning : undefined}>
        {positions.length === 0 ? (
          <div style={{
            padding: 32, textAlign: 'center', color: colors.textDim,
            background: `repeating-linear-gradient(
              -45deg, transparent, transparent 10px,
              rgba(30,45,74,0.3) 10px, rgba(30,45,74,0.3) 20px
            )`,
            borderRadius: 8,
          }}>
            <div style={{ fontSize: 14, marginBottom: 4 }}>No open positions</div>
            <div style={{ fontSize: 12 }}>Start the bot to begin trading</div>
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: '0 4px', fontSize: 13 }}>
              <thead>
                <tr>
                  {['Market', 'Side', 'Entry', 'Current', 'Size', 'P&L'].map(h => (
                    <th key={h} style={{
                      padding: '8px 12px', textAlign: h === 'Market' || h === 'Side' ? 'left' : 'right',
                      color: colors.textDim, fontWeight: 500, fontSize: 11,
                      textTransform: 'uppercase', letterSpacing: '0.06em',
                    }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {positions.map(p => {
                  const pnlColor = p.unrealized_pnl >= 0 ? colors.success : colors.danger
                  const pnlBg = p.unrealized_pnl >= 0 ? colors.successDim : colors.dangerDim
                  const pnlPct = p.avg_entry > 0 ? ((p.current_price - p.avg_entry) / p.avg_entry * 100) : 0
                  return (
                    <tr key={p.token_id} style={{
                      background: colors.bgCard,
                      borderRadius: 8,
                      transition: 'background 0.15s',
                    }}>
                      <td style={{
                        padding: '10px 12px', maxWidth: 300, overflow: 'hidden',
                        textOverflow: 'ellipsis', whiteSpace: 'nowrap', borderRadius: '8px 0 0 8px',
                      }}>
                        {p.market_question || p.market_id}
                        {p.paper === 1 && (
                          <PillBadge text="PAPER" bg={colors.warningDim} fg={colors.warning} />
                        )}
                      </td>
                      <td style={{ padding: '10px 12px' }}>
                        <PillBadge
                          text={p.side}
                          bg={p.side === 'BUY_YES' ? colors.successDim : colors.dangerDim}
                          fg={p.side === 'BUY_YES' ? colors.success : colors.danger}
                        />
                      </td>
                      <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: "'JetBrains Mono', monospace", fontSize: 12 }}>
                        ${p.avg_entry.toFixed(3)}
                      </td>
                      <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: "'JetBrains Mono', monospace", fontSize: 12 }}>
                        ${p.current_price.toFixed(3)}
                      </td>
                      <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: "'JetBrains Mono', monospace", fontSize: 12 }}>
                        ${p.size.toFixed(2)}
                      </td>
                      <td style={{
                        padding: '10px 12px', textAlign: 'right', borderRadius: '0 8px 8px 0',
                        fontFamily: "'JetBrains Mono', monospace", fontSize: 12,
                      }}>
                        <span style={{
                          padding: '3px 8px', borderRadius: 6,
                          background: pnlBg, color: pnlColor, fontWeight: 600,
                        }}>
                          {p.unrealized_pnl >= 0 ? '+' : ''}${p.unrealized_pnl.toFixed(2)}
                          <span style={{ opacity: 0.7, fontSize: 10, marginLeft: 4 }}>
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
          height: 14, borderRadius: 4,
          background: `linear-gradient(90deg, ${colors.border} 0%, ${colors.bgCard} 50%, ${colors.border} 100%)`,
          width: `${70 + i * 10}%`,
          animation: 'shimmer 1.5s ease-in-out infinite',
        }} />
      ))}
      <style>{`@keyframes shimmer { 0% { opacity: 0.5; } 50% { opacity: 1; } 100% { opacity: 0.5; } }`}</style>
    </div>
  )
}
