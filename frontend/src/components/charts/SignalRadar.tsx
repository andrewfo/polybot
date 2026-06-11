import { Radar } from 'react-chartjs-2'
import {
  Chart as ChartJS,
  RadialLinearScale,
  PointElement,
  LineElement,
  Filler,
  Tooltip,
  Legend,
} from 'chart.js'
import { colors, fonts } from '../../theme'

ChartJS.register(RadialLinearScale, PointElement, LineElement, Filler, Tooltip, Legend)

interface SignalData {
  source: string
  probability: number | null
  confidence: number
  data_points: number
  effective_weight: number
}

const palette = ['#3fb970', '#ffffff', '#ffaa00', '#b8b8b8', '#06b6d4']

export default function SignalRadar({ signals, marketPrice }: { signals: SignalData[]; marketPrice: number }) {
  const usable = signals.filter(s => s.probability != null && s.confidence > 0)
  if (usable.length === 0) return null

  // Normalize data_points to 0-1 scale (max across signals = 1.0)
  const maxDp = Math.max(...usable.map(s => s.data_points), 1)
  const maxWeight = Math.max(...usable.map(s => s.effective_weight), 0.1)

  const dimensions = ['Probability', 'Confidence', 'Data Points', 'Weight', 'Edge vs Market']

  const datasets = usable.map((s, i) => {
    const edge = Math.abs((s.probability ?? 0.5) - marketPrice)
    return {
      label: s.source.replace(/_/g, ' '),
      data: [
        (s.probability ?? 0.5) * 100,
        s.confidence * 100,
        (s.data_points / maxDp) * 100,
        (s.effective_weight / maxWeight) * 100,
        Math.min(edge * 200, 100), // scale edge: 50% edge = 100
      ],
      borderColor: palette[i % palette.length],
      backgroundColor: palette[i % palette.length] + '25',
      borderWidth: 2,
      pointRadius: 3,
      pointBackgroundColor: palette[i % palette.length],
    }
  })

  return (
    <div style={{ maxHeight: 280 }}>
      <Radar
        data={{ labels: dimensions, datasets }}
        options={{
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            r: {
              min: 0,
              max: 100,
              ticks: {
                display: false,
                stepSize: 25,
              },
              grid: { color: 'rgba(233, 230, 223, 0.05)' },
              angleLines: { color: 'rgba(233, 230, 223, 0.05)' },
              pointLabels: {
                color: colors.textMuted,
                font: { size: 10 },
              },
            },
          },
          plugins: {
            legend: {
              position: 'bottom' as const,
              labels: {
                color: colors.textMuted,
                font: { size: 10 },
                boxWidth: 12,
                padding: 8,
              },
            },
            tooltip: {
              backgroundColor: 'rgba(21, 22, 26, 0.97)',
              borderColor: colors.border,
              borderWidth: 1,
              cornerRadius: 3,
              callbacks: {
                label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.r.toFixed(1)}`,
              },
            },
          },
        }}
        height={260}
      />
    </div>
  )
}
