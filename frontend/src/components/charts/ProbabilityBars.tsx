import { Bar } from 'react-chartjs-2'
import { Chart as ChartJS, CategoryScale, LinearScale, BarElement, Tooltip } from 'chart.js'
import { colors } from '../../theme'

ChartJS.register(CategoryScale, LinearScale, BarElement, Tooltip)

interface ProbBar {
  label: string
  value: number
  color: string
}

export default function ProbabilityBars({ bars }: { bars: ProbBar[] }) {
  const data = {
    labels: bars.map(b => b.label),
    datasets: [{
      data: bars.map(b => b.value * 100),
      backgroundColor: bars.map(b => b.color),
      borderRadius: 3,
      barThickness: 20,
    }],
  }

  return (
    <div style={{ maxHeight: 200 }}>
      <Bar
        data={data}
        options={{
          indexAxis: 'y',
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            x: {
              min: 0,
              max: 100,
              grid: { color: colors.border },
              ticks: { color: colors.textDim, callback: v => v + '%' },
            },
            y: {
              grid: { display: false },
              ticks: { color: colors.textMuted, font: { size: 11 } },
            },
          },
          plugins: {
            tooltip: {
              callbacks: { label: ctx => (ctx.parsed.x ?? 0).toFixed(1) + '%' },
            },
          },
        }}
        height={bars.length * 30 + 40}
      />
    </div>
  )
}
