// ---------------------------------------------------------------------------
// "The Ledger" — private-bank trading terminal.
// Warm ink surfaces, a single brass accent, emerald/crimson reserved for P&L.
// Flat panels, hairline rules, no glow, no gradients, no blur.
// ---------------------------------------------------------------------------

export const colors = {
  // Ink — warm near-black, never a pure void
  bgVoid: '#0b0c0e',
  bgPrimary: '#0e0f12',
  bgSecondary: '#121317',
  bgCard: '#15161a',
  bgCardHover: '#1a1b21',
  bgGlass: 'rgba(21, 22, 26, 0.94)',
  bgElevated: 'rgba(26, 27, 33, 0.98)',

  // Text — bone to graphite
  textPrimary: '#e9e6df',
  textSecondary: '#a3a099',
  textMuted: '#6e6c66',
  textDim: '#474642',

  // Accent — burnished brass, used sparingly for emphasis and chrome
  accent: '#c5a572',
  accentLight: '#d9bd8d',
  accentDim: 'rgba(197, 165, 114, 0.08)',
  accentMid: 'rgba(197, 165, 114, 0.18)',
  blue: '#8aa8c5',   // steel — secondary chart series
  purple: '#9d97c0', // slate violet — tertiary chart series

  // Semantic — reserved for P&L and state, never decoration
  success: '#3fb970',
  successDim: 'rgba(63, 185, 112, 0.09)',
  danger: '#e5484d',
  dangerDim: 'rgba(229, 72, 77, 0.09)',
  warning: '#d9a03f',
  warningDim: 'rgba(217, 160, 63, 0.09)',

  // Hairlines
  border: 'rgba(233, 230, 223, 0.08)',
  borderLight: 'rgba(233, 230, 223, 0.14)',
  borderHover: 'rgba(197, 165, 114, 0.45)',

  // Legacy gradient tokens — kept for callers, now flat fills
  gradientAccent: '#c5a572',
  gradientAccentHover: '#d9bd8d',
  gradientSuccess: '#3fb970',
  gradientDanger: '#e5484d',
  gradientCard: '#15161a',
  gradientMesh: 'none',
}

export const fonts = {
  display: "'Fraunces', Georgia, serif",
  body: "'Instrument Sans', -apple-system, BlinkMacSystemFont, sans-serif",
  mono: "'IBM Plex Mono', 'Menlo', monospace",
}

export const cardStyle: React.CSSProperties = {
  background: colors.bgCard,
  border: `1px solid ${colors.border}`,
  borderRadius: 4,
  padding: 20,
  transition: 'border-color 0.2s ease, background 0.2s ease',
  position: 'relative',
  overflow: 'hidden',
}

// Legacy glow helper — now renders as a hairline ring of the given color
export const glowShadow = (color: string, intensity = 0.15) =>
  `0 0 0 1px ${color}${Math.round(Math.min(intensity * 3, 0.45) * 255).toString(16).padStart(2, '0')}`

export const animDelay = (i: number) => ({
  animation: 'fadeInUp 0.3s ease forwards',
  animationDelay: `${i * 0.03}s`,
  opacity: 0,
})
