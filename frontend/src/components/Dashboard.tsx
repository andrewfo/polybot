import { useState, useEffect, useCallback } from 'react'
import { colors } from '../theme'
import { api, HealthResponse, WalletResponse, CostResponse, BotStatus, Position } from '../api'
import CostBreakdown from './charts/CostBreakdown'

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{
      background: colors.bgCard,
      border: `1px solid ${colors.border}`,
      borderRadius: 8,
      padding: 16,
    }}>
      <h3 style={{ margin: '0 0 12px', fontSize: 14, color: colors.textMuted, textTransform: 'uppercase', letterSpacing: 1 }}>
        {title}
      </h3>
      {children}
    </div>
  )
}

function StatusDot({ ok }: { ok: boolean }) {
  return <span style={{
    display: 'inline-block',
    width: 8,
    height: 8,
    borderRadius: '50%',
    background: ok ? colors.success : colors.danger,
    marginRight: 8,
  }} />
}

interface DashboardProps {
  wsBotStatus?: BotStatus | null
}

export default function Dashboard({ wsBotStatus }: DashboardProps) {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [wallet, setWallet] = useState<WalletResponse | null>(null)
  const [costData, setCostData] = useState<CostResponse | null>(null)
  const [botStatus, setBotStatus] = useState<BotStatus | null>(null)
  const [positions, setPositions] = useState<Position[]>([])
  const [actionLoading, setActionLoading] = useState(false)

  // Use WebSocket status if available, fall back to polled status
  const displayStatus = wsBotStatus || botStatus

  const refresh = useCallback(() => {
    api.fetchHealth().then(setHealth).catch(() => {})
    api.fetchWallet().then(setWallet).catch(() => {})
    api.fetchCosts().then(setCostData).catch(() => {})
    api.fetchBotStatus().then(setBotStatus).catch(() => {})
    api.fetchPositions().then(setPositions).catch(() => {})
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
    } catch (e) {
      console.error('Failed to start bot:', e)
    } finally {
      setActionLoading(false)
    }
  }

  const handleStop = async () => {
    setActionLoading(true)
    try {
      await api.stopBot()
      await api.fetchBotStatus().then(setBotStatus)
    } catch (e) {
      console.error('Failed to stop bot:', e)
    } finally {
      setActionLoading(false)
    }
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
      {/* Bot Status */}
      <Card title="Bot Status">
        {displayStatus ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{
                padding: '2px 10px',
                borderRadius: 4,
                fontSize: 12,
                fontWeight: 600,
                background: displayStatus.running ? colors.success : colors.textDim,
                color: '#fff',
              }}>
                {displayStatus.running ? 'RUNNING' : 'STOPPED'}
              </span>
              <span style={{ color: colors.textMuted, fontSize: 13 }}>
                Phase: {displayStatus.phase}
              </span>
              {displayStatus.paper_trading && (
                <span style={{
                  padding: '2px 8px',
                  borderRadius: 4,
                  fontSize: 11,
                  background: colors.warning,
                  color: '#000',
                  fontWeight: 600,
                }}>
                  PAPER
                </span>
              )}
            </div>
            <div style={{ color: colors.textDim, fontSize: 13 }}>
              Cycles: {displayStatus.cycle_count}
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
              {!displayStatus.running ? (
                <button
                  disabled={actionLoading}
                  onClick={handleStart}
                  style={{
                    padding: '6px 16px',
                    borderRadius: 4,
                    border: `1px solid ${colors.success}`,
                    background: actionLoading ? colors.bgSecondary : colors.success,
                    color: '#fff',
                    cursor: actionLoading ? 'wait' : 'pointer',
                    fontSize: 13,
                    fontWeight: 600,
                  }}
                >
                  {actionLoading ? 'Starting...' : 'Start'}
                </button>
              ) : (
                <button
                  disabled={actionLoading}
                  onClick={handleStop}
                  style={{
                    padding: '6px 16px',
                    borderRadius: 4,
                    border: `1px solid ${colors.danger}`,
                    background: actionLoading ? colors.bgSecondary : colors.danger,
                    color: '#fff',
                    cursor: actionLoading ? 'wait' : 'pointer',
                    fontSize: 13,
                    fontWeight: 600,
                  }}
                >
                  {actionLoading ? 'Stopping...' : 'Stop'}
                </button>
              )}
            </div>
          </div>
        ) : (
          <span style={{ color: colors.textDim }}>Loading...</span>
        )}
      </Card>

      {/* Connections */}
      <Card title="Connections">
        {health ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {health.services.map(s => (
              <div key={s.name} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <span><StatusDot ok={s.healthy} />{s.name}</span>
                <span style={{ color: colors.textDim, fontSize: 12 }}>
                  {s.healthy ? `${s.latency_ms}ms` : s.error}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <span style={{ color: colors.textDim }}>Checking...</span>
        )}
      </Card>

      {/* Wallet */}
      <Card title="Wallet">
        {wallet ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={{ fontSize: 12, color: colors.textDim, fontFamily: 'monospace' }}>
              {wallet.address.slice(0, 6)}...{wallet.address.slice(-4)}
            </div>
            <div style={{ display: 'flex', gap: 24 }}>
              <div>
                <div style={{ fontSize: 22, fontWeight: 700 }}>${wallet.usdc.toFixed(2)}</div>
                <div style={{ fontSize: 11, color: colors.textDim }}>USDC</div>
              </div>
              <div>
                <div style={{ fontSize: 22, fontWeight: 700 }}>{wallet.matic.toFixed(4)}</div>
                <div style={{ fontSize: 11, color: colors.textDim }}>MATIC</div>
              </div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
              <StatusDot ok={wallet.has_gas} />
              <span>{wallet.has_gas ? 'Gas OK' : 'Low gas'}</span>
              <span style={{ color: colors.textDim, marginLeft: 'auto' }}>
                {wallet.positions_count} position{wallet.positions_count !== 1 ? 's' : ''}
              </span>
            </div>
          </div>
        ) : (
          <span style={{ color: colors.textDim }}>Loading...</span>
        )}
      </Card>

      {/* LLM Costs */}
      <Card title="LLM Costs">
        {costData ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', gap: 24 }}>
              <div>
                <div style={{ fontSize: 18, fontWeight: 600 }}>${costData.daily.toFixed(4)}</div>
                <div style={{ fontSize: 11, color: colors.textDim }}>Today</div>
              </div>
              <div>
                <div style={{ fontSize: 18, fontWeight: 600 }}>${costData.monthly.toFixed(4)}</div>
                <div style={{ fontSize: 11, color: colors.textDim }}>This Month</div>
              </div>
              <div>
                <div style={{ fontSize: 18, fontWeight: 600 }}>{costData.total_calls}</div>
                <div style={{ fontSize: 11, color: colors.textDim }}>Total Calls</div>
              </div>
            </div>
            {costData.model_breakdown.length > 0 && (
              <div style={{ maxWidth: 220, margin: '0 auto' }}>
                <CostBreakdown data={costData.model_breakdown} />
              </div>
            )}
          </div>
        ) : (
          <span style={{ color: colors.textDim }}>Loading...</span>
        )}
      </Card>

      {/* Positions — full width */}
      <div style={{ gridColumn: '1 / -1' }}>
        <Card title="Open Positions">
          {positions.length === 0 ? (
            <div style={{ color: colors.textDim, fontSize: 13 }}>No open positions</div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ color: colors.textMuted, textAlign: 'left' }}>
                  <th style={{ padding: '6px 8px' }}>Market</th>
                  <th style={{ padding: '6px 8px' }}>Side</th>
                  <th style={{ padding: '6px 8px', textAlign: 'right' }}>Entry</th>
                  <th style={{ padding: '6px 8px', textAlign: 'right' }}>Current</th>
                  <th style={{ padding: '6px 8px', textAlign: 'right' }}>Size</th>
                  <th style={{ padding: '6px 8px', textAlign: 'right' }}>PnL</th>
                </tr>
              </thead>
              <tbody>
                {positions.map(p => {
                  const pnlColor = p.unrealized_pnl >= 0 ? colors.success : colors.danger
                  const pnlPct = p.avg_entry > 0 ? ((p.current_price - p.avg_entry) / p.avg_entry * 100) : 0
                  return (
                    <tr key={p.token_id} style={{ borderTop: `1px solid ${colors.border}` }}>
                      <td style={{ padding: '6px 8px', maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {p.market_question || p.market_id}
                      </td>
                      <td style={{ padding: '6px 8px' }}>{p.side}</td>
                      <td style={{ padding: '6px 8px', textAlign: 'right' }}>${p.avg_entry.toFixed(3)}</td>
                      <td style={{ padding: '6px 8px', textAlign: 'right' }}>${p.current_price.toFixed(3)}</td>
                      <td style={{ padding: '6px 8px', textAlign: 'right' }}>${p.size.toFixed(2)}</td>
                      <td style={{ padding: '6px 8px', textAlign: 'right', color: pnlColor }}>
                        ${p.unrealized_pnl.toFixed(2)} ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(1)}%)
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </Card>
      </div>
    </div>
  )
}
