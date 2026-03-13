import { useState, useEffect, useCallback } from 'react'
import { colors, cardStyle } from '../theme'
import { api, Market } from '../api'

function parseOutcomePrices(raw: string | unknown): [number | null, number | null] {
  try {
    const arr = typeof raw === 'string' ? JSON.parse(raw) : raw
    if (Array.isArray(arr) && arr.length >= 2) return [parseFloat(arr[0]), parseFloat(arr[1])]
  } catch {}
  return [null, null]
}

function formatDate(iso: string): string {
  if (!iso) return '\u2014'
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

  const selectStyle: React.CSSProperties = {
    background: colors.bgCard,
    color: colors.textPrimary,
    border: `1px solid ${colors.border}`,
    borderRadius: 8,
    padding: '6px 10px',
    fontSize: 13,
    fontFamily: 'inherit',
    cursor: 'pointer',
    outline: 'none',
  }

  return (
    <div>
      {/* Controls */}
      <div style={{
        display: 'flex', gap: 10, marginBottom: 14, alignItems: 'center',
        ...cardStyle, padding: '12px 16px', flexWrap: 'wrap',
      }}>
        <label style={{ color: colors.textMuted, fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
          Sort by
          <select value={sort} onChange={e => setSort(e.target.value)} style={selectStyle}>
            <option value="volume24hr">Volume</option>
            <option value="liquidityNum">Liquidity</option>
            <option value="startDate">Newest</option>
          </select>
        </label>
        <label style={{ color: colors.textMuted, fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
          Show
          <select value={limit} onChange={e => setLimit(Number(e.target.value))} style={selectStyle}>
            <option value={20}>20</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </label>
        <button
          onClick={refresh}
          style={{
            ...selectStyle, cursor: 'pointer', fontWeight: 500,
            transition: 'all 0.15s',
          }}
          onMouseEnter={e => { e.currentTarget.style.borderColor = colors.accent }}
          onMouseLeave={e => { e.currentTarget.style.borderColor = colors.border }}
        >
          Refresh
        </button>
        {loading && (
          <span style={{ color: colors.accent, fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ animation: 'spin 1s linear infinite', display: 'inline-block' }}>&#x21BB;</span>
            Loading...
            <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
          </span>
        )}
        <span style={{ marginLeft: 'auto', color: colors.textDim, fontSize: 12 }}>
          {markets.length} markets
        </span>
      </div>

      {/* Table */}
      <div style={{ ...cardStyle, padding: 0, overflow: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr>
              {['#', 'YES', 'NO', 'Liquidity', 'Vol 24H', 'Spread', 'Expires', 'Question'].map((h, i) => (
                <th key={h} style={{
                  padding: '12px', textAlign: i <= 0 || i >= 6 ? 'left' : 'right',
                  color: colors.textDim, fontWeight: 500, fontSize: 11,
                  textTransform: 'uppercase', letterSpacing: '0.06em',
                  borderBottom: `1px solid ${colors.border}`,
                  position: 'sticky', top: 0, background: colors.bgCard,
                }}>
                  {h}
                </th>
              ))}
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
                    borderBottom: `1px solid ${colors.border}`,
                    background: isSelected ? colors.accentDim : 'transparent',
                    cursor: 'pointer',
                    transition: 'background 0.15s',
                  }}
                  onMouseEnter={e => { if (!isSelected) e.currentTarget.style.background = 'rgba(59,130,246,0.05)' }}
                  onMouseLeave={e => { if (!isSelected) e.currentTarget.style.background = 'transparent' }}
                >
                  <td style={{ padding: '10px 12px', color: colors.textDim, fontSize: 12 }}>{i + 1}</td>
                  <td style={{ padding: '10px 12px', textAlign: 'right', color: colors.success, fontFamily: "'JetBrains Mono', monospace", fontSize: 12 }}>
                    {yes !== null ? (yes * 100).toFixed(1) + '%' : '\u2014'}
                  </td>
                  <td style={{ padding: '10px 12px', textAlign: 'right', color: colors.danger, fontFamily: "'JetBrains Mono', monospace", fontSize: 12 }}>
                    {no !== null ? (no * 100).toFixed(1) + '%' : '\u2014'}
                  </td>
                  <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: "'JetBrains Mono', monospace", fontSize: 12 }}>
                    ${(m.liquidityNum || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  </td>
                  <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: "'JetBrains Mono', monospace", fontSize: 12 }}>
                    ${(m.volume24hr || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  </td>
                  <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: "'JetBrains Mono', monospace", fontSize: 12 }}>
                    {m.spread != null ? (m.spread * 100).toFixed(1) + '%' : '\u2014'}
                  </td>
                  <td style={{ padding: '10px 12px', color: colors.textDim, whiteSpace: 'nowrap', fontSize: 12 }}>
                    {formatDate(m.endDate)}
                  </td>
                  <td style={{
                    padding: '10px 12px', maxWidth: 400, overflow: 'hidden',
                    textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
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
          ...cardStyle, marginTop: 14,
          borderLeft: `3px solid ${colors.accent}`,
        }}>
          <h3 style={{ marginBottom: 10, fontSize: 15, fontWeight: 600 }}>{selected.question}</h3>
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
            gap: 12, fontSize: 13,
          }}>
            <DetailField label="Condition ID" value={selected.conditionId} mono />
            {(() => {
              const [yes, no] = parseOutcomePrices(selected.outcomePrices)
              return <>
                <DetailField label="YES" value={yes !== null ? (yes * 100).toFixed(1) + '%' : '\u2014'} color={colors.success} />
                <DetailField label="NO" value={no !== null ? (no * 100).toFixed(1) + '%' : '\u2014'} color={colors.danger} />
              </>
            })()}
            <DetailField label="Liquidity" value={`$${(selected.liquidityNum || 0).toLocaleString()}`} />
            <DetailField label="Volume 24H" value={`$${(selected.volume24hr || 0).toLocaleString()}`} />
            <DetailField label="Spread" value={selected.spread != null ? (selected.spread * 100).toFixed(1) + '%' : '\u2014'} />
            <DetailField label="Best Bid" value={String(selected.bestBid ?? '\u2014')} />
            <DetailField label="Best Ask" value={String(selected.bestAsk ?? '\u2014')} />
            <DetailField label="Expires" value={formatDate(selected.endDate)} />
          </div>
        </div>
      )}
    </div>
  )
}

function DetailField({ label, value, mono, color }: { label: string; value: string; mono?: boolean; color?: string }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: colors.textDim, marginBottom: 2 }}>{label}</div>
      <div style={{
        fontFamily: mono ? "'JetBrains Mono', monospace" : 'inherit',
        fontSize: mono ? 11 : 13,
        color: color || colors.textPrimary,
        fontWeight: 500,
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>
        {value}
      </div>
    </div>
  )
}
