export const colors = {
  // Void & backgrounds
  bgVoid: '#030509',
  bgPrimary: '#060a14',
  bgSecondary: '#080d1a',
  bgCard: '#0a0f1e',
  bgCardHover: '#0e1428',
  bgGlass: 'rgba(10, 15, 30, 0.75)',
  bgElevated: 'rgba(14, 20, 40, 0.9)',

  // Text
  textPrimary: '#e4eaf6',
  textSecondary: '#8899bb',
  textMuted: '#556688',
  textDim: '#334466',

  // Accents
  accent: '#00e5ff',
  accentLight: '#66f0ff',
  accentDim: 'rgba(0, 229, 255, 0.07)',
  accentMid: 'rgba(0, 229, 255, 0.12)',
  blue: '#0070ff',
  purple: '#8b5cf6',

  // Semantic
  success: '#00ff88',
  successDim: 'rgba(0, 255, 136, 0.08)',
  danger: '#ff3366',
  dangerDim: 'rgba(255, 51, 102, 0.08)',
  warning: '#ffaa00',
  warningDim: 'rgba(255, 170, 0, 0.08)',

  // Borders
  border: 'rgba(0, 229, 255, 0.06)',
  borderLight: 'rgba(0, 229, 255, 0.12)',
  borderHover: 'rgba(0, 229, 255, 0.2)',

  // Gradients
  gradientAccent: 'linear-gradient(135deg, #00e5ff 0%, #0070ff 100%)',
  gradientAccentHover: 'linear-gradient(135deg, #33eeff 0%, #2288ff 100%)',
  gradientSuccess: 'linear-gradient(135deg, #00ff88 0%, #00cc99 100%)',
  gradientDanger: 'linear-gradient(135deg, #ff3366 0%, #ff6644 100%)',
  gradientCard: 'linear-gradient(145deg, rgba(10, 15, 30, 0.95) 0%, rgba(6, 10, 20, 0.98) 100%)',
  gradientMesh: 'radial-gradient(ellipse at 20% 50%, rgba(0, 229, 255, 0.03) 0%, transparent 50%), radial-gradient(ellipse at 80% 20%, rgba(0, 112, 255, 0.02) 0%, transparent 50%), radial-gradient(ellipse at 50% 80%, rgba(139, 92, 246, 0.02) 0%, transparent 50%)',
}

export const fonts = {
  display: "'Syne', sans-serif",
  body: "'Outfit', -apple-system, BlinkMacSystemFont, sans-serif",
  mono: "'IBM Plex Mono', 'Menlo', monospace",
}

export const cardStyle: React.CSSProperties = {
  background:
    'linear-gradient(135deg, rgba(255,255,255,0.06) 0%, rgba(255,255,255,0.015) 40%, rgba(0,0,0,0.20) 100%), rgba(10, 18, 36, 0.42)',
  border: '1px solid rgba(255, 255, 255, 0.08)',
  borderRadius: 14,
  padding: 20,
  backdropFilter: 'blur(28px) saturate(180%)',
  WebkitBackdropFilter: 'blur(28px) saturate(180%)',
  boxShadow:
    'inset 0 1px 0 rgba(255,255,255,0.12), inset 0 -1px 0 rgba(0,0,0,0.35), 0 12px 40px rgba(0,0,0,0.45), 0 2px 10px rgba(0,229,255,0.05)',
  transition: 'border-color 0.3s ease, box-shadow 0.3s ease, transform 0.25s ease',
  position: 'relative',
  overflow: 'hidden',
}

export const glowShadow = (color: string, intensity = 0.15) =>
  `0 0 24px ${color}${Math.round(intensity * 255).toString(16).padStart(2, '0')}, 0 0 48px ${color}${Math.round(intensity * 0.5 * 255).toString(16).padStart(2, '0')}, 0 4px 16px rgba(0,0,0,0.4)`

export const animDelay = (i: number) => ({
  animation: 'fadeInUp 0.4s ease forwards',
  animationDelay: `${i * 0.06}s`,
  opacity: 0,
})
