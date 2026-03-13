import { Doughnut } from 'react-chartjs-2'
import { Chart as ChartJS, ArcElement, Tooltip, Legend } from 'chart.js'
import { colors } from '../../theme'
import { ModelBreakdown } from '../../api'

ChartJS.register(ArcElement, Tooltip, Legend)

const palette = [colors.accent, colors.success, colors.warning, colors.danger, colors.textMuted]

export default function CostBreakdown({ data }: { data: ModelBreakdown[] }) {
  if (data.length === 0) return null

  return (
    <Doughnut
      data={{
        labels: data.map(d => d.model.split('/').pop() || d.model),
        datasets: [{
          data: data.map(d => d.cost),
          backgroundColor: data.map((_, i) => palette[i % palette.length]),
          borderWidth: 0,
        }],
      }}
      options={{
        responsive: true,
        plugins: {
          legend: {
            position: 'bottom',
            labels: { color: colors.textMuted, font: { size: 10 }, boxWidth: 12 },
          },
          tooltip: {
            callbacks: {
              label: ctx => {
                const val = ctx.parsed
                return `$${val.toFixed(4)} (${data[ctx.dataIndex].calls} calls)`
              },
            },
          },
        },
      }}
    />
  )
}
