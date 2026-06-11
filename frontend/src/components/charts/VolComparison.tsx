import { Bar } from 'react-chartjs-2'
import { Chart as ChartJS, CategoryScale, LinearScale, BarElement, Tooltip, Legend } from 'chart.js'
import { colors, fonts } from '../../theme'

ChartJS.register(CategoryScale, LinearScale, BarElement, Tooltip, Legend)

const colorMap: Record<string, string> = {
  historical: '#8899bb',
  ewm: '#ffffff',
  short_term: '#ffaa00',
  deribit_iv: '#3fb970',
  selected: '#e4eaf6',
}

export default function VolComparison({ data }: { data: Record<string, number> }) {
  const labels: string[] = []
  const values: number[] = []
  const barColors: string[] = []

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
            backgroundColor: barColors.map(c => c + '70'),
            borderColor: barColors,
            borderWidth: 1,
            borderRadius: 4,
            barThickness: 24,
          }],
        }}
        options={{
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            y: {
              grid: { color: 'rgba(233, 230, 223, 0.05)' },
              border: { display: false },
              ticks: { color: colors.textDim, callback: v => v + '%' },
            },
            x: {
              grid: { display: false },
              border: { display: false },
              ticks: { color: colors.textMuted, font: { size: 11 } },
            },
          },
          plugins: {
            legend: { display: false },
            tooltip: {
              backgroundColor: 'rgba(21, 22, 26, 0.97)',
              borderColor: colors.border,
              borderWidth: 1,
              cornerRadius: 3,
              callbacks: { label: ctx => (ctx.parsed.y ?? 0).toFixed(1) + '%' },
            },
          },
        }}
        height={160}
      />
    </div>
  )
}
