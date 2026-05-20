export const colors = {
  // Void & backgrounds — pure black
  bgVoid: '#000000',
  bgPrimary: '#050505',
  bgSecondary: '#0a0a0a',
  bgCard: '#0c0c0c',
  bgCardHover: '#141414',
  bgGlass: 'rgba(8, 8, 10, 0.55)',
  bgElevated: 'rgba(14, 14, 16, 0.78)',

  // Text — pearl to graphite
  textPrimary: '#f2f2f2',
  textSecondary: '#9a9a9a',
  textMuted: '#666666',
  textDim: '#3a3a3a',

  // Accent — white/silver only (no color)
  accent: '#ffffff',
  accentLight: '#ffffff',
  accentDim: 'rgba(255, 255, 255, 0.06)',
  accentMid: 'rgba(255, 255, 255, 0.12)',
  blue: '#c8c8c8',
  purple: '#a8a8a8',

  // Semantic — desaturated near-monochrome
  success: '#cfeed3',
  successDim: 'rgba(207, 238, 211, 0.08)',
  danger: '#ff8898',
  dangerDim: 'rgba(255, 136, 152, 0.08)',
  warning: '#ffd28a',
  warningDim: 'rgba(255, 210, 138, 0.08)',

  // Borders — white at varying alphas
  border: 'rgba(255, 255, 255, 0.06)',
  borderLight: 'rgba(255, 255, 255, 0.12)',
  borderHover: 'rgba(255, 255, 255, 0.22)',

  // Gradients — silver/pearl, no hue
  gradientAccent: 'linear-gradient(135deg, #ffffff 0%, #9a9a9a 100%)',
  gradientAccentHover: 'linear-gradient(135deg, #ffffff 0%, #c8c8c8 100%)',
  gradientSuccess: 'linear-gradient(135deg, #e6f5e8 0%, #b8d4bc 100%)',
  gradientDanger: 'linear-gradient(135deg, #ff8898 0%, #c66c78 100%)',
  gradientCard: 'linear-gradient(145deg, rgba(12, 12, 14, 0.92) 0%, rgba(4, 4, 5, 0.96) 100%)',
  gradientMesh: 'radial-gradient(ellipse at 20% 50%, rgba(255, 255, 255, 0.025) 0%, transparent 50%), radial-gradient(ellipse at 80% 20%, rgba(255, 255, 255, 0.018) 0%, transparent 50%), radial-gradient(ellipse at 50% 80%, rgba(255, 255, 255, 0.012) 0%, transparent 50%)',
}

export const fonts = {
  display: "'Syne', sans-serif",
  body: "'Outfit', -apple-system, BlinkMacSystemFont, sans-serif",
  mono: "'IBM Plex Mono', 'Menlo', monospace",
}

export const cardStyle: React.CSSProperties = {
  background:
    'linear-gradient(135deg, rgba(255,255,255,0.07) 0%, rgba(255,255,255,0.02) 38%, rgba(0,0,0,0.40) 100%), rgba(8, 8, 10, 0.55)',
  border: '1px solid rgba(255, 255, 255, 0.09)',
  borderRadius: 18,
  padding: 20,
  backdropFilter: 'blur(36px) saturate(140%)',
  WebkitBackdropFilter: 'blur(36px) saturate(140%)',
  boxShadow:
    'inset 0 1px 0 rgba(255,255,255,0.14), inset 0 -1px 0 rgba(0,0,0,0.55), 0 18px 50px rgba(0,0,0,0.65), 0 2px 12px rgba(0,0,0,0.5)',
  transition: 'border-color 0.3s ease, box-shadow 0.3s ease, transform 0.25s ease',
  position: 'relative',
  overflow: 'hidden',
}

export const glowShadow = (color: string, intensity = 0.15) =>
  `0 0 24px ${color}${Math.round(intensity * 255).toString(16).padStart(2, '0')}, 0 0 48px ${color}${Math.round(intensity * 0.5 * 255).toString(16).padStart(2, '0')}, 0 4px 16px rgba(0,0,0,0.55)`

export const animDelay = (i: number) => ({
  animation: 'fadeInUp 0.4s ease forwards',
  animationDelay: `${i * 0.06}s`,
  opacity: 0,
})
