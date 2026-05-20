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

  // Accent — neon profit green
  accent: '#39ff14',
  accentLight: '#7fff5e',
  accentDim: 'rgba(57, 255, 20, 0.08)',
  accentMid: 'rgba(57, 255, 20, 0.18)',
  blue: '#39ff14',
  purple: '#ffcc00',

  // Semantic — neon profit palette
  success: '#39ff14',
  successDim: 'rgba(57, 255, 20, 0.10)',
  danger: '#ff2d55',
  dangerDim: 'rgba(255, 45, 85, 0.10)',
  warning: '#ffcc00',
  warningDim: 'rgba(255, 204, 0, 0.10)',

  // Borders — neon green at varying alphas
  border: 'rgba(57, 255, 20, 0.14)',
  borderLight: 'rgba(255, 204, 0, 0.22)',
  borderHover: 'rgba(57, 255, 20, 0.55)',

  // Gradients — neon profit
  gradientAccent: 'linear-gradient(135deg, #39ff14 0%, #ffcc00 100%)',
  gradientAccentHover: 'linear-gradient(135deg, #7fff5e 0%, #ffcc00 100%)',
  gradientSuccess: 'linear-gradient(135deg, #39ff14 0%, #00b86b 100%)',
  gradientDanger: 'linear-gradient(135deg, #ff2d55 0%, #b81444 100%)',
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
    'linear-gradient(135deg, rgba(57,255,20,0.05) 0%, rgba(255,204,0,0.02) 45%, rgba(0,0,0,0.55) 100%), rgba(0, 0, 0, 0.72)',
  border: '1px solid rgba(57, 255, 20, 0.22)',
  borderRadius: 18,
  padding: 20,
  backdropFilter: 'blur(28px) saturate(160%)',
  WebkitBackdropFilter: 'blur(28px) saturate(160%)',
  boxShadow:
    'inset 0 1px 0 rgba(57,255,20,0.18), inset 0 -1px 0 rgba(0,0,0,0.65), 0 18px 50px rgba(0,0,0,0.85), 0 0 24px rgba(57,255,20,0.08)',
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
