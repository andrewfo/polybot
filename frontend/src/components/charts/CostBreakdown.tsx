import { Doughnut } from 'react-chartjs-2'
import { Chart as ChartJS, ArcElement, Tooltip, Legend } from 'chart.js'
import { colors } from '../../theme'
import { ModelBreakdown } from '../../api'

ChartJS.register(ArcElement, Tooltip, Legend)

const palette = ['#3b82f6', '#8b5cf6', '#22c55e', '#f59e0b', '#ef4444', '#06b6d4']

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
          hoverBorderWidth: 2,
          hoverBorderColor: '#fff',
        }],
      }}
      options={{
        responsive: true,
        cutout: '65%',
        plugins: {
          legend: {
            position: 'bottom',
            labels: {
              color: colors.textMuted,
              font: { size: 10, family: 'Inter' },
              boxWidth: 10,
              boxHeight: 10,
              borderRadius: 2,
              padding: 8,
            },
          },
          tooltip: {
            backgroundColor: 'rgba(11, 21, 41, 0.95)',
            borderColor: colors.border,
            borderWidth: 1,
            cornerRadius: 8,
            titleFont: { size: 11, family: 'Inter' },
            bodyFont: { size: 12, family: "'JetBrains Mono', monospace" },
            callbacks: {
              label: ctx => {
                const val = ctx.parsed
                return ` $${val.toFixed(4)} (${data[ctx.dataIndex].calls} calls)`
              },
            },
          },
        },
      }}
    />
  )
}
