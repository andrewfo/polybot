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

/* Floating ambient orbs — liquid metaball drift */
function AmbientBackground() {
  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
      pointerEvents: 'none', zIndex: 0, overflow: 'hidden',
      filter: 'blur(2px)',
    }}>
      {/* Cyan plasma — top-right */}
      <div style={{
        position: 'absolute', top: '-18%', right: '-10%',
        width: 780, height: 780, borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(0,229,255,0.22) 0%, rgba(0,112,255,0.10) 35%, transparent 70%)',
        animation: 'orbFloat 22s ease-in-out infinite',
        mixBlendMode: 'screen',
      }} />
      {/* Violet plasma — bottom-left */}
      <div style={{
        position: 'absolute', bottom: '-12%', left: '-8%',
        width: 720, height: 720, borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(139,92,246,0.18) 0%, rgba(0,112,255,0.08) 40%, transparent 72%)',
        animation: 'orbFloat 30s ease-in-out infinite reverse',
        mixBlendMode: 'screen',
      }} />
      {/* Mint plasma — drifting center */}
      <div style={{
        position: 'absolute', top: '38%', left: '42%',
        width: 520, height: 520, borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(0,255,170,0.10) 0%, rgba(0,229,255,0.05) 45%, transparent 70%)',
        animation: 'orbFloat 36s ease-in-out infinite',
        animationDelay: '-12s',
        mixBlendMode: 'screen',
      }} />
      {/* Magenta whisper — wandering */}
      <div style={{
        position: 'absolute', top: '60%', right: '15%',
        width: 440, height: 440, borderRadius: '50%',
        background: 'radial-gradient(circle, rgba(255,51,130,0.08) 0%, transparent 65%)',
        animation: 'orbFloat 40s ease-in-out infinite reverse',
        animationDelay: '-6s',
        mixBlendMode: 'screen',
      }} />
      {/* Void vignette so cards still pop */}
      <div style={{
        position: 'absolute', inset: 0,
        background: 'radial-gradient(ellipse at center, transparent 0%, rgba(3,5,9,0.40) 92%)',
      }} />
    </div>
  )
}

/* Drifting starfield — tiny twinkling specks */
function Starfield() {
  const stars = Array.from({ length: 60 }, (_, i) => {
    const seed = (i * 9301 + 49297) % 233280
    const left = (seed / 233280) * 100
    const top = ((seed * 7) % 233280) / 233280 * 100
    const size = 1 + (i % 3)
    const dur = 3 + (i % 7)
    const delay = (i % 9) * 0.4
    return (
      <div key={i} style={{
        position: 'absolute',
        left: `${left}%`, top: `${top}%`,
        width: size, height: size,
        borderRadius: '50%',
        background: i % 5 === 0 ? '#00e5ff' : '#e4eaf6',
        boxShadow: i % 5 === 0 ? '0 0 6px #00e5ff' : '0 0 4px rgba(255,255,255,0.6)',
        opacity: 0,
        animation: `twinkle ${dur}s ease-in-out ${delay}s infinite`,
      }} />
    )
  })
  return (
    <div style={{
      position: 'fixed', inset: 0, pointerEvents: 'none',
      zIndex: 0, overflow: 'hidden',
    }}>
      {stars}
    </div>
  )
}

/* Cursor-following soft glow */
function CursorGlow() {
  const ref = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (ref.current) {
        ref.current.style.transform = `translate(${e.clientX - 200}px, ${e.clientY - 200}px)`
      }
    }
    window.addEventListener('mousemove', onMove)
    return () => window.removeEventListener('mousemove', onMove)
  }, [])
  return (
    <div ref={ref} style={{
      position: 'fixed', top: 0, left: 0,
      width: 400, height: 400, pointerEvents: 'none',
      zIndex: 0,
      background: 'radial-gradient(circle, rgba(0,229,255,0.10) 0%, rgba(0,229,255,0.04) 35%, transparent 70%)',
      mixBlendMode: 'screen',
      transition: 'transform 0.18s cubic-bezier(0.2, 0.8, 0.2, 1)',
      willChange: 'transform',
    }} />
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
      <span>POLYBOT v1.0</span>
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
      background: 'transparent',
      color: colors.textPrimary,
      fontFamily: fonts.body,
      position: 'relative',
    }}>
      <AmbientBackground />
      <Starfield />
      <CursorGlow />

      <div style={{ position: 'relative', zIndex: 1 }}>
        {/* Micro ticker bar */}
        <TickerBar />

        {/* Header — liquid glass */}
        <header style={{
          background: 'linear-gradient(180deg, rgba(255,255,255,0.05) 0%, rgba(255,255,255,0) 60%), rgba(6, 10, 20, 0.55)',
          backdropFilter: 'blur(28px) saturate(180%)',
          WebkitBackdropFilter: 'blur(28px) saturate(180%)',
          borderBottom: '1px solid rgba(255,255,255,0.08)',
          boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.10), 0 8px 24px rgba(0,0,0,0.35)',
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
                Polybot
              </span>
              <span style={{
                color: colors.textDim, fontSize: 11, marginLeft: 12,
                fontFamily: fonts.mono, letterSpacing: '0.04em',
              }}>
                POLYBOT
              </span>
            </div>
          </div>

          {/* Status indicator */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{
              padding: '4px 12px', borderRadius: 20,
              background: wsBotStatus?.paused ? colors.warningDim
                : wsBotStatus?.running ? colors.successDim : 'rgba(85,102,136,0.1)',
              border: `1px solid ${wsBotStatus?.paused ? 'rgba(255,180,0,0.2)'
                : wsBotStatus?.running ? 'rgba(0,255,136,0.2)' : 'rgba(85,102,136,0.15)'}`,
              display: 'flex', alignItems: 'center', gap: 6,
            }}>
              <div style={{
                width: 6, height: 6, borderRadius: '50%',
                background: wsBotStatus?.paused ? colors.warning
                  : wsBotStatus?.running ? colors.success : colors.textDim,
                boxShadow: wsBotStatus?.paused ? `0 0 8px ${colors.warning}`
                  : wsBotStatus?.running ? `0 0 8px ${colors.success}` : 'none',
                animation: wsBotStatus?.running ? 'pulse 2s ease-in-out infinite' : 'none',
              }} />
              <span style={{
                fontSize: 11, fontWeight: 600,
                fontFamily: fonts.mono,
                color: wsBotStatus?.paused ? colors.warning
                  : wsBotStatus?.running ? colors.success : colors.textMuted,
                letterSpacing: '0.04em',
              }}>
                {wsBotStatus?.paused ? 'PAUSED' : wsBotStatus?.running ? 'LIVE' : 'OFFLINE'}
              </span>
            </div>
          </div>
        </header>

        <TabBar active={activeTab} onChange={setActiveTab} />

        <main key={activeTab} style={{
          padding: '20px 28px',
          maxWidth: 1440,
          margin: '0 auto',
          animation: 'tabSwoop 0.45s cubic-bezier(0.2, 0.8, 0.2, 1) forwards',
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

function glowShadowCSS(color: string, intensity = 0.15) {
  return `0 0 24px ${color}${Math.round(intensity * 255).toString(16).padStart(2, '0')}, 0 4px 16px rgba(0,0,0,0.4)`
}
