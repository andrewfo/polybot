export const colors = {
  // Core backgrounds
  bgPrimary: '#060d1f',
  bgSecondary: '#0b1529',
  bgCard: '#0f1d35',
  bgCardHover: '#142544',
  bgGlass: 'rgba(15, 29, 53, 0.7)',

  // Text
  textPrimary: '#e8edf5',
  textSecondary: '#b0bdd0',
  textMuted: '#7a8ba5',
  textDim: '#556178',

  // Accents
  accent: '#3b82f6',
  accentLight: '#60a5fa',
  accentDim: 'rgba(59, 130, 246, 0.15)',

  // Semantic
  success: '#22c55e',
  successDim: 'rgba(34, 197, 94, 0.12)',
  danger: '#ef4444',
  dangerDim: 'rgba(239, 68, 68, 0.12)',
  warning: '#f59e0b',
  warningDim: 'rgba(245, 158, 11, 0.12)',

  // Borders
  border: '#1e2d4a',
  borderLight: '#2a3f65',

  // Gradients (as CSS strings)
  gradientAccent: 'linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%)',
  gradientSuccess: 'linear-gradient(135deg, #22c55e 0%, #06b6d4 100%)',
  gradientDanger: 'linear-gradient(135deg, #ef4444 0%, #f97316 100%)',
  gradientCard: 'linear-gradient(145deg, rgba(15, 29, 53, 0.9) 0%, rgba(11, 21, 41, 0.95) 100%)',
}

// Shared style snippets
export const cardStyle: React.CSSProperties = {
  background: colors.gradientCard,
  border: `1px solid ${colors.border}`,
  borderRadius: 12,
  padding: 20,
  backdropFilter: 'blur(12px)',
  transition: 'border-color 0.2s, box-shadow 0.2s',
}

export const glowShadow = (color: string) =>
  `0 0 20px ${color}22, 0 4px 12px rgba(0,0,0,0.3)`
