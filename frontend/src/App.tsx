import { useState, useEffect, useRef, useCallback } from 'react'
import { colors } from './theme'
import { BotStatus } from './api'
import TabBar from './components/TabBar'
import Dashboard from './components/Dashboard'
import Markets from './components/Markets'
import Analysis from './components/Analysis'
import Logs from './components/Logs'

type Tab = 'dashboard' | 'markets' | 'analysis' | 'logs'

function useWebSocket() {
  const [botStatus, setBotStatus] = useState<BotStatus | null>(null)
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
            phase: data.phase || 'idle',
            cycle_count: data.cycle_count ?? 0,
            paper_trading: data.paper_trading ?? true,
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

  return { botStatus }
}

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('dashboard')
  const { botStatus: wsBotStatus } = useWebSocket()

  return (
    <div style={{
      minHeight: '100vh',
      background: `${colors.bgPrimary}`,
      color: colors.textPrimary,
      fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    }}>
      {/* Background gradient orbs */}
      <div style={{
        position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
        pointerEvents: 'none', zIndex: 0, overflow: 'hidden',
      }}>
        <div style={{
          position: 'absolute', top: '-20%', right: '-10%',
          width: 600, height: 600, borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(59,130,246,0.06) 0%, transparent 70%)',
        }} />
        <div style={{
          position: 'absolute', bottom: '-10%', left: '-5%',
          width: 500, height: 500, borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(139,92,246,0.04) 0%, transparent 70%)',
        }} />
      </div>

      <div style={{ position: 'relative', zIndex: 1 }}>
        <header style={{
          background: 'rgba(11, 21, 41, 0.8)',
          backdropFilter: 'blur(16px)',
          borderBottom: `1px solid ${colors.border}`,
          padding: '14px 28px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          position: 'sticky',
          top: 0,
          zIndex: 50,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            {/* Logo icon */}
            <div style={{
              width: 32, height: 32, borderRadius: 8,
              background: colors.gradientAccent,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 16, fontWeight: 700, color: '#fff',
              boxShadow: '0 2px 8px rgba(59,130,246,0.3)',
            }}>
              P
            </div>
            <div>
              <span style={{ fontSize: 17, fontWeight: 700, letterSpacing: '-0.02em' }}>Polymarket Bot</span>
              <span style={{ color: colors.textDim, fontSize: 12, marginLeft: 10 }}>Signal-Based Trading</span>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{
              width: 8, height: 8, borderRadius: '50%',
              background: wsBotStatus?.running ? colors.success : colors.textDim,
              boxShadow: wsBotStatus?.running ? `0 0 8px ${colors.success}` : 'none',
            }} />
            <span style={{ fontSize: 12, color: colors.textMuted }}>
              {wsBotStatus?.running ? 'Live' : 'Offline'}
            </span>
          </div>
        </header>

        <TabBar active={activeTab} onChange={setActiveTab} />

        <main style={{ padding: '20px 28px', maxWidth: 1400, margin: '0 auto' }}>
          {activeTab === 'dashboard' && <Dashboard wsBotStatus={wsBotStatus} />}
          {activeTab === 'markets' && <Markets />}
          {activeTab === 'analysis' && <Analysis />}
          {activeTab === 'logs' && <Logs />}
        </main>
      </div>
    </div>
  )
}
