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

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Filler)

interface PricePoint {
  date: string
  price: number
}

export default function PriceChart({ data, target }: { data: PricePoint[]; target?: number }) {
  if (data.length === 0) return null

  return (
    <div style={{ marginBottom: 12 }}>
      <Line
        data={{
          labels: data.map(d => d.date),
          datasets: [
            {
              label: 'Price',
              data: data.map(d => d.price),
              borderColor: colors.accent,
              backgroundColor: 'rgba(0, 229, 255, 0.08)',
              fill: true,
              pointRadius: 0,
              pointHoverRadius: 4,
              pointHoverBackgroundColor: colors.accent,
              borderWidth: 2,
              tension: 0.3,
            },
            ...(target && target > 0 ? [{
              label: 'Target',
              data: data.map(() => target),
              borderColor: colors.danger,
              borderDash: [6, 3],
              pointRadius: 0,
              borderWidth: 1.5,
              fill: false,
            }] : []),
          ],
        }}
        options={{
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: 'index' as const, intersect: false },
          scales: {
            x: {
              grid: { display: false },
              border: { display: false },
              ticks: { color: colors.textDim, maxRotation: 0, maxTicksLimit: 8, font: { size: 10 } },
            },
            y: {
              grid: { color: 'rgba(30, 45, 74, 0.4)' },
              border: { display: false },
              ticks: { color: colors.textDim },
            },
          },
          plugins: {
            tooltip: {
              backgroundColor: 'rgba(8, 13, 26, 0.95)',
              borderColor: colors.border,
              borderWidth: 1,
              cornerRadius: 8,
              callbacks: { label: ctx => `$${(ctx.parsed.y ?? 0).toLocaleString()}` },
            },
          },
        }}
        height={200}
      />
    </div>
  )
}
