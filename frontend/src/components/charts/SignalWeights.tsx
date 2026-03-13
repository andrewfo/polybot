import { Bar } from 'react-chartjs-2'
import { Chart as ChartJS, CategoryScale, LinearScale, BarElement, Tooltip } from 'chart.js'
import { colors } from '../../theme'

ChartJS.register(CategoryScale, LinearScale, BarElement, Tooltip)

interface WeightEntry {
  label: string
  weight: number
}

const barPalette = ['#22c55e', '#3b82f6', '#f59e0b', '#8b5cf6', '#06b6d4']

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
            backgroundColor: data.map((_, i) => barPalette[i % barPalette.length] + '70'),
            borderColor: data.map((_, i) => barPalette[i % barPalette.length]),
            borderWidth: 1,
            borderRadius: 4,
            barThickness: 20,
          }],
        }}
        options={{
          indexAxis: 'y',
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            x: {
              grid: { color: 'rgba(30, 45, 74, 0.4)' },
              border: { display: false },
              ticks: { color: colors.textDim, font: { size: 10 } },
            },
            y: {
              grid: { display: false },
              border: { display: false },
              ticks: { color: colors.textMuted, font: { size: 11 } },
            },
          },
          plugins: {
            tooltip: {
              backgroundColor: 'rgba(11, 21, 41, 0.95)',
              borderColor: colors.border,
              borderWidth: 1,
              cornerRadius: 8,
              callbacks: { label: ctx => (ctx.parsed.x ?? 0).toFixed(2) },
            },
          },
        }}
        height={data.length * 28 + 30}
      />
    </div>
  )
}
