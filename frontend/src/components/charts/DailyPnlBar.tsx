import { Bar } from 'react-chartjs-2'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  Tooltip,
} from 'chart.js'
import { colors, fonts } from '../../theme'
import { PnlSnapshot } from '../../api'

ChartJS.register(CategoryScale, LinearScale, BarElement, Tooltip)

export default function DailyPnlBar({ snapshots }: { snapshots: PnlSnapshot[] }) {
  if (snapshots.length < 2) {
    return (
      <div style={{
        flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
        minHeight: 140, color: colors.textDim, fontSize: 11,
        fontFamily: fonts.mono,
        border: `1px dashed ${colors.border}`, borderRadius: 6,
        background: `linear-gradient(135deg, rgba(0, 229, 255, 0.02) 0%, rgba(0, 112, 255, 0.01) 100%)`,
      }}>
        Need more data for daily P&L
      </div>
    )
  }

  // Group snapshots by day and compute daily change
  const dailyMap = new Map<string, { first: number; last: number }>()
  for (const s of snapshots) {
    const day = new Date(s.timestamp).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    if (!dailyMap.has(day)) {
      dailyMap.set(day, { first: s.total_value, last: s.total_value })
    } else {
      dailyMap.get(day)!.last = s.total_value
    }
  }

  const days = Array.from(dailyMap.keys()).slice(-14) // Last 14 days
  const changes = days.map(d => {
    const entry = dailyMap.get(d)!
    return entry.last - entry.first
  })

  return (
    <div style={{ flex: 1, minHeight: 140 }}>
      <Bar
        data={{
          labels: days,
          datasets: [{
            label: 'Daily P&L',
            data: changes,
            backgroundColor: changes.map(v => v >= 0 ? 'rgba(0, 255, 136, 0.6)' : 'rgba(255, 51, 102, 0.6)'),
            borderColor: changes.map(v => v >= 0 ? colors.success : colors.danger),
            borderWidth: 1,
            borderRadius: 3,
            borderSkipped: false,
          }],
        }}
        options={{
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: 'index', intersect: false },
          scales: {
            x: {
              grid: { display: false },
              border: { display: false },
              ticks: {
                color: colors.textDim,
                maxRotation: 0,
                maxTicksLimit: 7,
                font: { size: 9, family: fonts.mono },
              },
            },
            y: {
              grid: { color: 'rgba(30, 45, 74, 0.3)', lineWidth: 1 },
              border: { display: false },
              ticks: {
                color: colors.textDim,
                font: { size: 9, family: fonts.mono },
                callback: v => `$${Number(v).toFixed(0)}`,
              },
            },
          },
          plugins: {
            tooltip: {
              backgroundColor: 'rgba(8, 13, 26, 0.95)',
              borderColor: colors.border,
              borderWidth: 1,
              titleFont: { size: 10, family: fonts.body },
              bodyFont: { size: 12, family: fonts.mono, weight: 600 },
              padding: 8,
              cornerRadius: 6,
              displayColors: false,
              callbacks: {
                label: ctx => {
                  const v = ctx.parsed.y ?? 0
                  return `${v >= 0 ? '+' : ''}$${v.toFixed(2)}`
                },
              },
            },
          },
        }}
      />
    </div>
  )
}
