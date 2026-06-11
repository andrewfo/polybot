import { Bar } from 'react-chartjs-2'
import { Chart as ChartJS, CategoryScale, LinearScale, BarElement, Tooltip } from 'chart.js'
import { colors, fonts } from '../../theme'

ChartJS.register(CategoryScale, LinearScale, BarElement, Tooltip)

interface WeightEntry {
  label: string
  weight: number
}

const barPalette = ['#3fb970', '#ffffff', '#ffaa00', '#b8b8b8', '#06b6d4']

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
              grid: { color: 'rgba(233, 230, 223, 0.05)' },
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
              backgroundColor: 'rgba(21, 22, 26, 0.97)',
              borderColor: colors.border,
              borderWidth: 1,
              cornerRadius: 3,
              callbacks: { label: ctx => (ctx.parsed.x ?? 0).toFixed(2) },
            },
          },
        }}
        height={data.length * 28 + 30}
      />
    </div>
  )
}
