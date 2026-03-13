import { Doughnut } from 'react-chartjs-2'
import { Chart as ChartJS, ArcElement, Tooltip } from 'chart.js'
import { colors, fonts } from '../../theme'

ChartJS.register(ArcElement, Tooltip)

interface Props {
  value: number       // 0-1
  label?: string
  thresholds?: { low: number; medium: number }  // e.g. { low: 0.25, medium: 0.5 }
}

export default function ConfidenceGauge({ value, label = 'Confidence', thresholds }: Props) {
  const pct = Math.max(0, Math.min(1, value))
  const low = thresholds?.low ?? 0.25
  const med = thresholds?.medium ?? 0.5

  const gaugeColor = pct < low ? colors.danger : pct < med ? colors.warning : colors.success
  const remaining = 1 - pct

  return (
    <div style={{ position: 'relative', width: 120, height: 80 }}>
      <Doughnut
        data={{
          datasets: [{
            data: [pct * 100, remaining * 100],
            backgroundColor: [gaugeColor + 'cc', colors.border],
            borderWidth: 0,
            circumference: 180,
            rotation: 270,
          }],
        }}
        options={{
          responsive: true,
          maintainAspectRatio: false,
          cutout: '75%',
          plugins: {
            tooltip: { enabled: false },
          },
        }}
        width={120}
        height={80}
      />
      <div style={{
        position: 'absolute', bottom: 0, left: 0, right: 0,
        textAlign: 'center',
      }}>
        <div style={{
          fontSize: 18, fontWeight: 700, color: gaugeColor,
          fontFamily: fonts.mono,
          lineHeight: 1,
        }}>
          {(pct * 100).toFixed(0)}%
        </div>
        <div style={{ fontSize: 9, color: colors.textDim, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
          {label}
        </div>
      </div>
    </div>
  )
}
