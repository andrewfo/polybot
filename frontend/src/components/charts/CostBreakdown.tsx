import { Doughnut } from 'react-chartjs-2'
import { Chart as ChartJS, ArcElement, Tooltip, Legend } from 'chart.js'
import { colors, fonts } from '../../theme'
import { ModelBreakdown } from '../../api'

ChartJS.register(ArcElement, Tooltip, Legend)

const palette = ['#00e5ff', '#8b5cf6', '#00ff88', '#ffaa00', '#ff3366', '#06b6d4']

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
              font: { size: 10, family: fonts.body },
              boxWidth: 10,
              boxHeight: 10,
              borderRadius: 2,
              padding: 8,
            },
          },
          tooltip: {
            backgroundColor: 'rgba(8, 13, 26, 0.95)',
            borderColor: colors.border,
            borderWidth: 1,
            cornerRadius: 8,
            titleFont: { size: 11, family: fonts.body },
            bodyFont: { size: 12, family: fonts.mono },
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
