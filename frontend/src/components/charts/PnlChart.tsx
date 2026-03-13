import { Line } from 'react-chartjs-2'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Tooltip,
  Filler,
} from 'chart.js'
import { colors, fonts } from '../../theme'
import { PnlSnapshot } from '../../api'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Filler)

export default function PnlChart({ snapshots }: { snapshots: PnlSnapshot[] }) {
  if (snapshots.length === 0) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: 220, color: colors.textDim, fontSize: 13,
        background: `repeating-linear-gradient(
          -45deg, transparent, transparent 10px,
          rgba(30,45,74,0.2) 10px, rgba(30,45,74,0.2) 20px
        )`,
        borderRadius: 8,
      }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 24, marginBottom: 8, opacity: 0.4 }}>~</div>
          <div>No portfolio data yet</div>
          <div style={{ fontSize: 11, marginTop: 4 }}>P&L history will appear here once trading begins</div>
        </div>
      </div>
    )
  }

  const labels = snapshots.map(s => {
    const d = new Date(s.timestamp)
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  })

  const values = snapshots.map(s => s.total_value)
  const isPositive = values.length > 1 ? values[values.length - 1] >= values[0] : true
  const lineColor = isPositive ? colors.success : colors.danger
  const fillColor = isPositive ? 'rgba(0, 255, 136, 0.08)' : 'rgba(255, 51, 102, 0.08)'

  return (
    <div style={{ height: 220 }}>
      <Line
        data={{
          labels,
          datasets: [{
            label: 'Portfolio',
            data: values,
            borderColor: lineColor,
            backgroundColor: fillColor,
            fill: true,
            pointRadius: 0,
            pointHoverRadius: 5,
            pointHoverBackgroundColor: lineColor,
            pointHoverBorderColor: '#fff',
            pointHoverBorderWidth: 2,
            borderWidth: 2.5,
            tension: 0.35,
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
                maxTicksLimit: 8,
                font: { size: 10, family: fonts.body },
              },
            },
            y: {
              grid: { color: 'rgba(30, 45, 74, 0.4)', lineWidth: 1 },
              border: { display: false },
              ticks: {
                color: colors.textDim,
                font: { size: 10, family: fonts.mono },
                callback: v => `$${Number(v).toLocaleString()}`,
              },
            },
          },
          plugins: {
            tooltip: {
              backgroundColor: 'rgba(8, 13, 26, 0.95)',
              borderColor: colors.border,
              borderWidth: 1,
              titleFont: { size: 11, family: fonts.body },
              bodyFont: { size: 13, family: fonts.mono, weight: 600 },
              padding: 10,
              cornerRadius: 8,
              displayColors: false,
              callbacks: {
                label: ctx => `$${(ctx.parsed.y ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}`,
              },
            },
          },
        }}
      />
    </div>
  )
}
