import { Bar } from 'react-chartjs-2'
import { Chart as ChartJS, CategoryScale, LinearScale, BarElement, Tooltip, Legend } from 'chart.js'
import { colors } from '../../theme'

ChartJS.register(CategoryScale, LinearScale, BarElement, Tooltip, Legend)

export default function VolComparison({ data }: { data: Record<string, number> }) {
  const labels: string[] = []
  const values: number[] = []
  const barColors: string[] = []

  const colorMap: Record<string, string> = {
    historical: colors.textMuted,
    ewm: colors.accent,
    short_term: colors.warning,
    deribit_iv: colors.success,
    selected: '#ffffff',
  }

  for (const [key, val] of Object.entries(data)) {
    if (typeof val === 'number' && val > 0) {
      labels.push(key.replace(/_/g, ' '))
      values.push(val * 100)
      barColors.push(colorMap[key] || colors.accent)
    }
  }

  if (labels.length === 0) return null

  return (
    <div style={{ maxHeight: 180 }}>
      <Bar
        data={{
          labels,
          datasets: [{
            label: 'Volatility',
            data: values,
            backgroundColor: barColors,
            borderRadius: 3,
            barThickness: 24,
          }],
        }}
        options={{
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            y: {
              grid: { color: colors.border },
              ticks: { color: colors.textDim, callback: v => v + '%' },
            },
            x: {
              grid: { display: false },
              ticks: { color: colors.textMuted, font: { size: 11 } },
            },
          },
          plugins: {
            legend: { display: false },
            tooltip: { callbacks: { label: ctx => (ctx.parsed.y ?? 0).toFixed(1) + '%' } },
          },
        }}
        height={160}
      />
    </div>
  )
}
