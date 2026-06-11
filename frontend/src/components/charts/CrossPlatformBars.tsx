import { Bar } from 'react-chartjs-2'
import { Chart as ChartJS, CategoryScale, LinearScale, BarElement, Tooltip } from 'chart.js'
import { colors, fonts } from '../../theme'

ChartJS.register(CategoryScale, LinearScale, BarElement, Tooltip)

interface MatchedMarket {
  platform: string
  title: string
  probability: number
  similarity?: number
  forecasters?: number
  volume?: number
}

const platformColors: Record<string, string> = {
  manifold: '#b8b8b8',
  kalshi: '#ffaa00',
  polymarket: '#ffffff',
}

export default function CrossPlatformBars({
  markets, consensusProb,
}: {
  markets: MatchedMarket[]
  consensusProb?: number
}) {
  if (markets.length === 0) return null

  const labels = markets.map(m => {
    const sim = m.similarity != null ? ` (${(m.similarity * 100).toFixed(0)}% match)` : ''
    return `${m.platform}${sim}`
  })
  const probabilities = markets.map(m => m.probability * 100)
  const barColors = markets.map(m => (platformColors[m.platform] || colors.accent) + '80')
  const borders = markets.map(m => platformColors[m.platform] || colors.accent)

  return (
    <div>
      <div style={{ maxHeight: 200 }}>
        <Bar
          data={{
            labels,
            datasets: [
              {
                label: 'Probability',
                data: probabilities,
                backgroundColor: barColors,
                borderColor: borders,
                borderWidth: 1,
                borderRadius: 4,
                barThickness: 22,
              },
            ],
          }}
          options={{
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            scales: {
              x: {
                min: 0, max: 100,
                grid: { color: 'rgba(233, 230, 223, 0.05)' },
                border: { display: false },
                ticks: { color: colors.textDim, callback: v => v + '%', font: { size: 10 } },
              },
              y: {
                grid: { display: false },
                border: { display: false },
                ticks: { color: colors.textMuted, font: { size: 10, family: fonts.body } },
              },
            },
            plugins: {
              tooltip: {
                backgroundColor: 'rgba(21, 22, 26, 0.97)',
                borderColor: colors.border,
                borderWidth: 1,
                cornerRadius: 3,
                callbacks: {
                  label: (ctx) => {
                    const m = markets[ctx.dataIndex]
                    const extra = m.forecasters ? ` (${m.forecasters} forecasters)` : m.volume ? ` ($${m.volume} vol)` : ''
                    return `${(ctx.parsed.x ?? 0).toFixed(1)}%${extra}`
                  },
                },
              },
            },
          }}
          height={Math.max(markets.length * 36 + 30, 100)}
        />
      </div>
      {consensusProb != null && (
        <div style={{
          marginTop: 6, fontSize: 10, color: colors.textDim,
          display: 'flex', alignItems: 'center', gap: 6,
        }}>
          <span style={{ width: 12, height: 2, background: colors.danger, display: 'inline-block' }} />
          Weighted consensus: <span style={{
            fontFamily: fonts.mono, color: colors.danger, fontWeight: 600,
          }}>{(consensusProb * 100).toFixed(1)}%</span>
        </div>
      )}
    </div>
  )
}
