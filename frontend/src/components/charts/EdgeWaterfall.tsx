import { Bar } from 'react-chartjs-2'
import { Chart as ChartJS, CategoryScale, LinearScale, BarElement, Tooltip } from 'chart.js'
import { colors, fonts } from '../../theme'

ChartJS.register(CategoryScale, LinearScale, BarElement, Tooltip)

interface WaterfallStep {
  label: string
  value: number
  color?: string
}

export default function EdgeWaterfall({ steps }: { steps: WaterfallStep[] }) {
  if (steps.length === 0) return null

  // Build floating bars: each bar starts where the previous ended
  const bases: number[] = []
  const tops: number[] = []
  const barColors: string[] = []
  const borderColors: string[] = []

  for (let i = 0; i < steps.length; i++) {
    const v = steps[i].value * 100
    if (i === 0) {
      bases.push(0)
      tops.push(v)
    } else {
      const prev = tops[i - 1]
      bases.push(Math.min(prev, v))
      tops.push(Math.max(prev, v))
    }
    const delta = i > 0 ? steps[i].value - steps[i - 1].value : steps[i].value
    const c = steps[i].color || (delta >= 0 ? colors.success : colors.danger)
    barColors.push(c + '70')
    borderColors.push(c)
  }

  return (
    <div style={{ maxHeight: 220 }}>
      <Bar
        data={{
          labels: steps.map(s => s.label),
          datasets: [
            {
              label: 'Base',
              data: bases,
              backgroundColor: 'transparent',
              borderWidth: 0,
              barThickness: 28,
            },
            {
              label: 'Value',
              data: tops.map((t, i) => t - bases[i]),
              backgroundColor: barColors,
              borderColor: borderColors,
              borderWidth: 1,
              borderRadius: 4,
              barThickness: 28,
            },
          ],
        }}
        options={{
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            x: {
              stacked: true,
              grid: { display: false },
              border: { display: false },
              ticks: { color: colors.textMuted, font: { size: 10 } },
            },
            y: {
              stacked: true,
              min: 0,
              max: 100,
              grid: { color: 'rgba(233, 230, 223, 0.05)' },
              border: { display: false },
              ticks: { color: colors.textDim, callback: v => v + '%', font: { size: 10 } },
            },
          },
          plugins: {
            tooltip: {
              backgroundColor: 'rgba(21, 22, 26, 0.97)',
              borderColor: colors.border,
              borderWidth: 1,
              cornerRadius: 3,
              filter: (item) => item.datasetIndex === 1,
              callbacks: {
                label: (ctx) => {
                  const idx = ctx.dataIndex
                  return `${steps[idx].label}: ${(steps[idx].value * 100).toFixed(1)}%`
                },
              },
            },
          },
        }}
        height={200}
      />
    </div>
  )
}
