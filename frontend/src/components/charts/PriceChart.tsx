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
import { colors } from '../../theme'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Filler)

interface PricePoint {
  date: string
  price: number
}

export default function PriceChart({ data, target }: { data: PricePoint[]; target?: number }) {
  if (data.length === 0) return null

  const chartData = {
    labels: data.map(d => d.date),
    datasets: [
      {
        label: 'Price',
        data: data.map(d => d.price),
        borderColor: colors.accent,
        backgroundColor: colors.accent + '20',
        fill: true,
        pointRadius: 0,
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
  }

  return (
    <div style={{ marginBottom: 12 }}>
      <Line
        data={chartData}
        options={{
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            x: {
              grid: { display: false },
              ticks: {
                color: colors.textDim,
                maxRotation: 0,
                maxTicksLimit: 8,
                font: { size: 10 },
              },
            },
            y: {
              grid: { color: colors.border },
              ticks: { color: colors.textDim },
            },
          },
          plugins: {
            tooltip: {
              callbacks: {
                label: ctx => `$${(ctx.parsed.y ?? 0).toLocaleString()}`,
              },
            },
          },
        }}
        height={200}
      />
    </div>
  )
}
