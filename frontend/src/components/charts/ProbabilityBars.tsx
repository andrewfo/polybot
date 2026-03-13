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
  return (
    <div style={{ maxHeight: 200 }}>
      <Bar
        data={{
          labels: bars.map(b => b.label),
          datasets: [{
            data: bars.map(b => b.value * 100),
            backgroundColor: bars.map(b => b.color + '80'),
            borderColor: bars.map(b => b.color),
            borderWidth: 1,
            borderRadius: 4,
            barThickness: 22,
          }],
        }}
        options={{
          indexAxis: 'y',
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            x: {
              min: 0, max: 100,
              grid: { color: 'rgba(30, 45, 74, 0.4)' },
              border: { display: false },
              ticks: { color: colors.textDim, callback: v => v + '%', font: { size: 10 } },
            },
            y: {
              grid: { display: false },
              border: { display: false },
              ticks: { color: colors.textMuted, font: { size: 11, family: 'Inter' } },
            },
          },
          plugins: {
            tooltip: {
              backgroundColor: 'rgba(11, 21, 41, 0.95)',
              borderColor: colors.border,
              borderWidth: 1,
              cornerRadius: 8,
              callbacks: { label: ctx => (ctx.parsed.x ?? 0).toFixed(1) + '%' },
            },
          },
        }}
        height={bars.length * 32 + 40}
      />
    </div>
  )
}
