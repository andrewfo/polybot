import { useState, useEffect, useRef, useCallback } from 'react'
import { colors, fonts } from './theme'
import { BotStatus } from './api'
import TabBar from './components/TabBar'
import Dashboard from './components/Dashboard'
import Markets from './components/Markets'
import Analysis from './components/Analysis'
import Learning from './components/Learning'
import Database from './components/Database'
import Trades from './components/Trades'
import Logs from './components/Logs'

type Tab = 'dashboard' | 'markets' | 'analysis' | 'trades' | 'learning' | 'database' | 'logs'

interface DiscoveryEvent {
  discovered: number
  filtered: number
}

interface BatchEvent {
  current_index: number
  total: number
  condition_id: string
  status: string
}

function useWebSocket() {
  const [botStatus, setBotStatus] = useState<BotStatus | null>(null)
  const [lastDiscovery, setLastDiscovery] = useState<DiscoveryEvent | null>(null)
  const [batchProgress, setBatchProgress] = useState<BatchEvent | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const retryRef = useRef(0)

  const connect = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.hostname || 'localhost'
    const port = window.location.port === '5173' ? '8080' : (window.location.port || '8080')
    const url = `${protocol}//${host}:${port}/ws`

    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => { retryRef.current = 0 }

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.type === 'bot_status') {
          setBotStatus({
            running: data.running,
            paused: data.paused ?? false,
            phase: data.phase || 'idle',
            cycle_count: data.cycle_count ?? 0,
            paper_trading: data.paper_trading ?? true,
          })
        } else if (data.type === 'discovery_complete') {
          setLastDiscovery({ discovered: data.discovered, filtered: data.filtered })
        } else if (data.type === 'batch_update') {
          setBatchProgress({
            current_index: data.current_index,
            total: data.total,
            condition_id: data.condition_id,
            status: data.status,
          })
        }
      } catch { /* ignore */ }
    }

    ws.onclose = () => {
      wsRef.current = null
      const delay = Math.min(1000 * 2 ** retryRef.current, 30000)
      retryRef.current++
      setTimeout(connect, delay)
    }

    ws.onerror = () => { ws.close() }
  }, [])

  useEffect(() => {
    connect()
    return () => { wsRef.current?.close() }
  }, [connect])

  return { botStatus, lastDiscovery, batchProgress }
}

/* Top rule bar — UTC clock and session marker */
function TickerBar() {
  const [time, setTime] = useState(new Date())
  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  return (
    <div style={{
      background: colors.bgVoid,
      borderBottom: `1px solid ${colors.border}`,
      padding: '5px 28px',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      fontFamily: fonts.mono,
      fontSize: 10,
      color: colors.textMuted,
      letterSpacing: '0.08em',
    }}>
      <span>POLYBOT · NO. 001</span>
      <div style={{ display: 'flex', gap: 24, alignItems: 'center' }}>
        <span>{time.toISOString().slice(0, 19).replace('T', ' ')} UTC</span>
        <span style={{ color: colors.accent }}>ACTIVE</span>
      </div>
    </div>
  )
}

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('dashboard')
  const { botStatus: wsBotStatus, lastDiscovery, batchProgress } = useWebSocket()

  return (
    <div style={{
      minHeight: '100vh',
      background: colors.bgPrimary,
      color: colors.textPrimary,
      fontFamily: fonts.body,
      position: 'relative',
    }}>
      <div style={{ position: 'relative', zIndex: 1 }}>
        {/* Micro ticker bar */}
        <TickerBar />

        {/* Header — masthead */}
        <header style={{
          background: colors.bgPrimary,
          borderBottom: `1px solid ${colors.borderLight}`,
          padding: '14px 28px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          position: 'sticky',
          top: 0,
          zIndex: 50,
        }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 14 }}>
            {/* Brass monogram */}
            <div style={{
              width: 30, height: 30, borderRadius: 2,
              border: `1px solid ${colors.accent}`,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 17, fontWeight: 600, color: colors.accent,
              fontFamily: fonts.display, fontStyle: 'italic',
              alignSelf: 'center', paddingBottom: 2,
            }}>
              P
            </div>
            <span style={{
              fontSize: 21, fontWeight: 550, letterSpacing: '-0.01em',
              fontFamily: fonts.display,
              color: colors.textPrimary,
            }}>
              Polybot
            </span>
            <span style={{
              color: colors.textMuted, fontSize: 10,
              fontFamily: fonts.mono, letterSpacing: '0.14em',
            }}>
              CRYPTO PREDICTION DESK
            </span>
          </div>

          {/* Status indicator */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{
              padding: '4px 12px', borderRadius: 2,
              background: wsBotStatus?.paused ? colors.warningDim
                : wsBotStatus?.running ? colors.successDim : 'transparent',
              border: `1px solid ${wsBotStatus?.paused ? 'rgba(217,160,63,0.3)'
                : wsBotStatus?.running ? 'rgba(63,185,112,0.3)' : colors.border}`,
              display: 'flex', alignItems: 'center', gap: 7,
            }}>
              <div style={{
                width: 5, height: 5, borderRadius: '50%',
                background: wsBotStatus?.paused ? colors.warning
                  : wsBotStatus?.running ? colors.success : colors.textDim,
                animation: wsBotStatus?.running ? 'pulse 2s ease-in-out infinite' : 'none',
              }} />
              <span style={{
                fontSize: 10, fontWeight: 600,
                fontFamily: fonts.mono,
                color: wsBotStatus?.paused ? colors.warning
                  : wsBotStatus?.running ? colors.success : colors.textMuted,
                letterSpacing: '0.12em',
              }}>
                {wsBotStatus?.paused ? 'PAUSED' : wsBotStatus?.running ? 'LIVE' : 'OFFLINE'}
              </span>
            </div>
          </div>
        </header>

        <TabBar active={activeTab} onChange={setActiveTab} />

        <main key={activeTab} style={{
          padding: '24px 28px 48px',
          maxWidth: 1440,
          margin: '0 auto',
          animation: 'tabSwoop 0.3s ease forwards',
        }}>
          {activeTab === 'dashboard' && <Dashboard wsBotStatus={wsBotStatus} wsDiscovery={lastDiscovery} wsBatchProgress={batchProgress} />}
          {activeTab === 'markets' && <Markets />}
          {activeTab === 'analysis' && <Analysis />}
          {activeTab === 'trades' && <Trades />}
          {activeTab === 'learning' && <Learning />}
          {activeTab === 'database' && <Database />}
          {activeTab === 'logs' && <Logs />}
        </main>
      </div>
    </div>
  )
}
