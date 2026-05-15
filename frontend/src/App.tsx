import { useState, useEffect, useRef, useCallback } from 'react'
import { colors, fonts } from './theme'
import { BotStatus } from './api'
import TabBar from './components/TabBar'
import Dashboard from './components/Dashboard'
import Markets from './components/Markets'
import Analysis from './components/Analysis'
import Learning from './components/Learning'
import Database from './components/Database'
import Logs from './components/Logs'

type Tab = 'dashboard' | 'markets' | 'analysis' | 'learning' | 'database' | 'logs'

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

/* Floating ambient orbs */
function AmbientBackground() {
  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
      pointerEvents: 'none', zIndex: 0, overflow: 'hidden',
    }}>
      {/* Cyan orb top-right */}
      <div style={{
        position: 'absolute', top: '-15%', right: '-8%',
        width: 700, height: 700, borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(0,229,255,0.04) 0%, rgba(0,112,255,0.02) 40%, transparent 70%)',
        animation: 'orbFloat 25s ease-in-out infinite',
      }} />
      {/* Purple orb bottom-left */}
      <div style={{
        position: 'absolute', bottom: '-10%', left: '-5%',
        width: 600, height: 600, borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(139,92,246,0.03) 0%, rgba(0,112,255,0.01) 40%, transparent 70%)',
        animation: 'orbFloat 30s ease-in-out infinite reverse',
      }} />
      {/* Green orb center-bottom */}
      <div style={{
        position: 'absolute', bottom: '20%', right: '30%',
        width: 400, height: 400, borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(0,255,136,0.015) 0%, transparent 60%)',
        animation: 'orbFloat 35s ease-in-out infinite',
        animationDelay: '-10s',
      }} />
      {/* Gradient mesh overlay */}
      <div style={{
        position: 'absolute', inset: 0,
        background: colors.gradientMesh,
      }} />
    </div>
  )
}

/* Top ticker bar */
function TickerBar() {
  const [time, setTime] = useState(new Date())
  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  return (
    <div style={{
      background: 'rgba(0, 229, 255, 0.02)',
      borderBottom: `1px solid ${colors.border}`,
      padding: '4px 28px',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      fontFamily: fonts.mono,
      fontSize: 10,
      color: colors.textMuted,
      letterSpacing: '0.05em',
    }}>
      <span>CRYPTO AI SLAVE 3000 v1.0</span>
      <div style={{ display: 'flex', gap: 20, alignItems: 'center' }}>
        <span>{time.toISOString().slice(0, 19).replace('T', ' ')} UTC</span>
        <span style={{ color: colors.accent, animation: 'textGlow 3s ease-in-out infinite' }}>ACTIVE</span>
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
      background: colors.bgVoid,
      color: colors.textPrimary,
      fontFamily: fonts.body,
      position: 'relative',
    }}>
      <AmbientBackground />

      <div style={{ position: 'relative', zIndex: 1 }}>
        {/* Micro ticker bar */}
        <TickerBar />

        {/* Header */}
        <header style={{
          background: 'rgba(6, 10, 20, 0.85)',
          backdropFilter: 'blur(20px)',
          borderBottom: `1px solid ${colors.border}`,
          padding: '12px 28px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          position: 'sticky',
          top: 0,
          zIndex: 50,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            {/* Animated logo */}
            <div style={{
              width: 36, height: 36, borderRadius: 8,
              background: colors.gradientAccent,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 16, fontWeight: 800, color: '#000',
              fontFamily: fonts.display,
              boxShadow: glowShadowCSS(colors.accent, 0.25),
              position: 'relative',
              overflow: 'hidden',
            }}>
              <span style={{ position: 'relative', zIndex: 1 }}>P</span>
              {/* Shine sweep */}
              <div style={{
                position: 'absolute', inset: 0,
                background: 'linear-gradient(105deg, transparent 40%, rgba(255,255,255,0.2) 50%, transparent 60%)',
                animation: 'shimmer 3s ease-in-out infinite',
                backgroundSize: '200% 100%',
              }} />
            </div>
            <div>
              <span style={{
                fontSize: 18, fontWeight: 700, letterSpacing: '-0.02em',
                fontFamily: fonts.display,
                background: colors.gradientAccent,
                WebkitBackgroundClip: 'text',
                WebkitTextFillColor: 'transparent',
              }}>
                Crypto AI Slave 3000
              </span>
              <span style={{
                color: colors.textDim, fontSize: 11, marginLeft: 12,
                fontFamily: fonts.mono, letterSpacing: '0.04em',
              }}>
                CRYPTO AI SLAVE 3000
              </span>
            </div>
          </div>

          {/* Status indicator */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{
              padding: '4px 12px', borderRadius: 20,
              background: wsBotStatus?.running ? colors.successDim : 'rgba(85,102,136,0.1)',
              border: `1px solid ${wsBotStatus?.running ? 'rgba(0,255,136,0.2)' : 'rgba(85,102,136,0.15)'}`,
              display: 'flex', alignItems: 'center', gap: 6,
            }}>
              <div style={{
                width: 6, height: 6, borderRadius: '50%',
                background: wsBotStatus?.running ? colors.success : colors.textDim,
                boxShadow: wsBotStatus?.running ? `0 0 8px ${colors.success}` : 'none',
                animation: wsBotStatus?.running ? 'pulse 2s ease-in-out infinite' : 'none',
              }} />
              <span style={{
                fontSize: 11, fontWeight: 600,
                fontFamily: fonts.mono,
                color: wsBotStatus?.running ? colors.success : colors.textMuted,
                letterSpacing: '0.04em',
              }}>
                {wsBotStatus?.running ? 'LIVE' : 'OFFLINE'}
              </span>
            </div>
          </div>
        </header>

        <TabBar active={activeTab} onChange={setActiveTab} />

        <main style={{
          padding: '20px 28px',
          maxWidth: 1440,
          margin: '0 auto',
          animation: 'fadeInUp 0.3s ease forwards',
        }}>
          {activeTab === 'dashboard' && <Dashboard wsBotStatus={wsBotStatus} wsDiscovery={lastDiscovery} wsBatchProgress={batchProgress} />}
          {activeTab === 'markets' && <Markets />}
          {activeTab === 'analysis' && <Analysis />}
          {activeTab === 'learning' && <Learning />}
          {activeTab === 'database' && <Database />}
          {activeTab === 'logs' && <Logs />}
        </main>
      </div>
    </div>
  )
}

function glowShadowCSS(color: string, intensity = 0.15) {
  return `0 0 24px ${color}${Math.round(intensity * 255).toString(16).padStart(2, '0')}, 0 4px 16px rgba(0,0,0,0.4)`
}
