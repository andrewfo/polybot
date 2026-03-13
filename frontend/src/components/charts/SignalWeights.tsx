import { Bar } from 'react-chartjs-2'
import { Chart as ChartJS, CategoryScale, LinearScale, BarElement, Tooltip } from 'chart.js'
import { colors } from '../../theme'

ChartJS.register(CategoryScale, LinearScale, BarElement, Tooltip)

interface WeightEntry {
  label: string
  weight: number
}

export default function SignalWeights({ data }: { data: WeightEntry[] }) {
  if (data.length === 0) return null

  return (
    <div style={{ maxHeight: 140, marginBottom: 8 }}>
      <Bar
        data={{
          labels: data.map(d => d.label),
          datasets: [{
            label: 'Weight',
            data: data.map(d => d.weight),
            backgroundColor: [colors.success, colors.accent, colors.warning, colors.textMuted].slice(0, data.length),
            borderRadius: 3,
            barThickness: 18,
          }],
        }}
        options={{
          indexAxis: 'y',
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            x: {
              grid: { color: colors.border },
              ticks: { color: colors.textDim },
            },
            y: {
              grid: { display: false },
              ticks: { color: colors.textMuted, font: { size: 11 } },
            },
          },
          plugins: {
            tooltip: { callbacks: { label: ctx => (ctx.parsed.x ?? 0).toFixed(2) } },
          },
        }}
        height={data.length * 28 + 30}
      />
    </div>
  )
}
