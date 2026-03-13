import { useState, useEffect, useCallback } from 'react'
import { colors } from '../theme'
import { api, Market } from '../api'

function parseOutcomePrices(raw: string | unknown): [number | null, number | null] {
  try {
    const arr = typeof raw === 'string' ? JSON.parse(raw) : raw
    if (Array.isArray(arr) && arr.length >= 2) {
      return [parseFloat(arr[0]), parseFloat(arr[1])]
    }
  } catch {}
  return [null, null]
}

function formatDate(iso: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

export default function Markets() {
  const [markets, setMarkets] = useState<Market[]>([])
  const [sort, setSort] = useState('volume24hr')
  const [limit, setLimit] = useState(20)
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<Market | null>(null)

  const refresh = useCallback(() => {
    setLoading(true)
    api.fetchMarkets(sort, limit).then(setMarkets).catch(() => {}).finally(() => setLoading(false))
  }, [sort, limit])

  useEffect(() => { refresh() }, [refresh])

  return (
    <div>
      {/* Controls */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 12, alignItems: 'center' }}>
        <label style={{ color: colors.textMuted, fontSize: 13 }}>
          Sort:
          <select
            value={sort}
            onChange={e => setSort(e.target.value)}
            style={{
              marginLeft: 6,
              background: colors.bgCard,
              color: colors.textPrimary,
              border: `1px solid ${colors.border}`,
              borderRadius: 4,
              padding: '4px 8px',
              fontSize: 13,
            }}
          >
            <option value="volume24hr">Volume</option>
            <option value="liquidityNum">Liquidity</option>
            <option value="startDate">Newest</option>
          </select>
        </label>
        <label style={{ color: colors.textMuted, fontSize: 13 }}>
          Limit:
          <select
            value={limit}
            onChange={e => setLimit(Number(e.target.value))}
            style={{
              marginLeft: 6,
              background: colors.bgCard,
              color: colors.textPrimary,
              border: `1px solid ${colors.border}`,
              borderRadius: 4,
              padding: '4px 8px',
              fontSize: 13,
            }}
          >
            <option value={20}>20</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </label>
        <button
          onClick={refresh}
          style={{
            padding: '4px 14px',
            borderRadius: 4,
            border: `1px solid ${colors.border}`,
            background: colors.bgCard,
            color: colors.textPrimary,
            cursor: 'pointer',
            fontSize: 13,
          }}
        >
          Refresh
        </button>
        {loading && <span style={{ color: colors.textDim, fontSize: 13 }}>Loading...</span>}
      </div>

      {/* Table */}
      <div style={{
        background: colors.bgCard,
        border: `1px solid ${colors.border}`,
        borderRadius: 8,
        overflow: 'auto',
      }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ color: colors.textMuted, textAlign: 'left' }}>
              <th style={{ padding: '8px', width: 30 }}>#</th>
              <th style={{ padding: '8px', textAlign: 'right' }}>YES</th>
              <th style={{ padding: '8px', textAlign: 'right' }}>NO</th>
              <th style={{ padding: '8px', textAlign: 'right' }}>Liquidity</th>
              <th style={{ padding: '8px', textAlign: 'right' }}>Vol 24H</th>
              <th style={{ padding: '8px', textAlign: 'right' }}>Spread</th>
              <th style={{ padding: '8px' }}>Expires</th>
              <th style={{ padding: '8px' }}>Question</th>
            </tr>
          </thead>
          <tbody>
            {markets.map((m, i) => {
              const [yes, no] = parseOutcomePrices(m.outcomePrices)
              const isSelected = selected?.conditionId === m.conditionId
              return (
                <tr
                  key={m.conditionId || i}
                  onClick={() => setSelected(isSelected ? null : m)}
                  style={{
                    borderTop: `1px solid ${colors.border}`,
                    background: isSelected ? colors.bgSecondary : 'transparent',
                    cursor: 'pointer',
                  }}
                >
                  <td style={{ padding: '6px 8px', color: colors.textDim }}>{i + 1}</td>
                  <td style={{ padding: '6px 8px', textAlign: 'right', color: colors.success }}>
                    {yes !== null ? (yes * 100).toFixed(1) + '%' : '—'}
                  </td>
                  <td style={{ padding: '6px 8px', textAlign: 'right', color: colors.danger }}>
                    {no !== null ? (no * 100).toFixed(1) + '%' : '—'}
                  </td>
                  <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                    ${(m.liquidityNum || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  </td>
                  <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                    ${(m.volume24hr || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  </td>
                  <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                    {m.spread != null ? (m.spread * 100).toFixed(1) + '%' : '—'}
                  </td>
                  <td style={{ padding: '6px 8px', color: colors.textDim, whiteSpace: 'nowrap' }}>
                    {formatDate(m.endDate)}
                  </td>
                  <td style={{ padding: '6px 8px', maxWidth: 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {m.question}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Detail panel */}
      {selected && (
        <div style={{
          marginTop: 12,
          background: colors.bgCard,
          border: `1px solid ${colors.border}`,
          borderRadius: 8,
          padding: 16,
        }}>
          <h3 style={{ marginBottom: 8, fontSize: 15 }}>{selected.question}</h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 12, fontSize: 13 }}>
            <div>
              <span style={{ color: colors.textMuted }}>Condition ID: </span>
              <span style={{ fontFamily: 'monospace', fontSize: 11 }}>{selected.conditionId}</span>
            </div>
            {(() => {
              const [yes, no] = parseOutcomePrices(selected.outcomePrices)
              return (
                <>
                  <div><span style={{ color: colors.textMuted }}>YES: </span><span style={{ color: colors.success }}>{yes !== null ? (yes * 100).toFixed(1) + '%' : '—'}</span></div>
                  <div><span style={{ color: colors.textMuted }}>NO: </span><span style={{ color: colors.danger }}>{no !== null ? (no * 100).toFixed(1) + '%' : '—'}</span></div>
                </>
              )
            })()}
            <div><span style={{ color: colors.textMuted }}>Liquidity: </span>${(selected.liquidityNum || 0).toLocaleString()}</div>
            <div><span style={{ color: colors.textMuted }}>Volume 24H: </span>${(selected.volume24hr || 0).toLocaleString()}</div>
            <div><span style={{ color: colors.textMuted }}>Spread: </span>{selected.spread != null ? (selected.spread * 100).toFixed(1) + '%' : '—'}</div>
            <div><span style={{ color: colors.textMuted }}>Best Bid: </span>{selected.bestBid ?? '—'}</div>
            <div><span style={{ color: colors.textMuted }}>Best Ask: </span>{selected.bestAsk ?? '—'}</div>
            <div><span style={{ color: colors.textMuted }}>Expires: </span>{formatDate(selected.endDate)}</div>
          </div>
        </div>
      )}
    </div>
  )
}
