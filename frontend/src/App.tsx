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
    // In dev mode (Vite on :5173), connect to backend on :8080
    const port = window.location.port === '5173' ? '8080' : (window.location.port || '8080')
    const url = `${protocol}//${host}:${port}/ws`

    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      retryRef.current = 0
    }

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
      } catch {
        // ignore malformed messages
      }
    }

    ws.onclose = () => {
      wsRef.current = null
      // Reconnect with exponential backoff (max 30s)
      const delay = Math.min(1000 * 2 ** retryRef.current, 30000)
      retryRef.current++
      setTimeout(connect, delay)
    }

    ws.onerror = () => {
      ws.close()
    }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      wsRef.current?.close()
    }
  }, [connect])

  return { botStatus }
}

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('dashboard')
  const { botStatus: wsBotStatus } = useWebSocket()

  return (
    <div style={{
      minHeight: '100vh',
      background: colors.bgPrimary,
      color: colors.textPrimary,
      fontFamily: "'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif",
    }}>
      <header style={{
        background: colors.bgSecondary,
        borderBottom: `1px solid ${colors.border}`,
        padding: '12px 24px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 20, fontWeight: 700 }}>Polymarket Bot</span>
          <span style={{ color: colors.textDim, fontSize: 13 }}>Signal-Based Trading Dashboard</span>
        </div>
      </header>

      <TabBar active={activeTab} onChange={setActiveTab} />

      <main style={{ padding: '16px 24px' }}>
        {activeTab === 'dashboard' && <Dashboard wsBotStatus={wsBotStatus} />}
        {activeTab === 'markets' && <Markets />}
        {activeTab === 'analysis' && <Analysis />}
        {activeTab === 'logs' && <Logs />}
      </main>
    </div>
  )
}
