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

export default function CashDeployedArea({ snapshots }: { snapshots: PnlSnapshot[] }) {
  if (snapshots.length < 2) {
    return (
      <div style={{
        flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
        minHeight: 140, color: colors.textDim, fontSize: 11,
        fontFamily: fonts.mono,
        border: `1px dashed ${colors.border}`, borderRadius: 6,
        background: `linear-gradient(135deg, rgba(190, 190, 190, 0.02) 0%, rgba(200, 200, 200, 0.01) 100%)`,
      }}>
        Need more data for allocation view
      </div>
    )
  }

  const labels = snapshots.map(s => {
    const d = new Date(s.timestamp)
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  })

  const cashData = snapshots.map(s => s.available_cash)
  const deployedData = snapshots.map(s => s.total_value - s.available_cash)

  return (
    <div style={{ flex: 1, minHeight: 140 }}>
      <Line
        data={{
          labels,
          datasets: [
            {
              label: 'Cash',
              data: cashData,
              borderColor: colors.accent,
              backgroundColor: 'rgba(255, 255, 255, 0.08)',
              fill: true,
              pointRadius: 0,
              pointHoverRadius: 4,
              pointHoverBackgroundColor: colors.accent,
              borderWidth: 1.5,
              tension: 0.35,
              order: 2,
            },
            {
              label: 'Deployed',
              data: deployedData,
              borderColor: colors.warning,
              backgroundColor: 'rgba(255, 170, 0, 0.08)',
              fill: true,
              pointRadius: 0,
              pointHoverRadius: 4,
              pointHoverBackgroundColor: colors.warning,
              borderWidth: 1.5,
              tension: 0.35,
              order: 1,
            },
          ],
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
              stacked: true,
            },
            y: {
              grid: { color: 'rgba(30, 45, 74, 0.3)', lineWidth: 1 },
              border: { display: false },
              ticks: {
                color: colors.textDim,
                font: { size: 9, family: fonts.mono },
                callback: v => `$${Number(v).toFixed(0)}`,
              },
              stacked: true,
            },
          },
          plugins: {
            tooltip: {
              backgroundColor: 'rgba(8, 13, 26, 0.95)',
              borderColor: colors.border,
              borderWidth: 1,
              titleFont: { size: 10, family: fonts.body },
              bodyFont: { size: 11, family: fonts.mono, weight: 600 },
              padding: 8,
              cornerRadius: 6,
              callbacks: {
                label: ctx => `${ctx.dataset.label}: $${(ctx.parsed.y ?? 0).toFixed(2)}`,
              },
            },
          },
        }}
      />
    </div>
  )
}
