import { useState, useEffect, useCallback } from 'react'
import { colors, cardStyle, fonts, animDelay, glowShadow } from '../theme'
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
    borderRadius: 4,
    padding: '6px 10px',
    fontSize: 11,
    fontFamily: fonts.mono,
    cursor: 'pointer',
    outline: 'none',
    transition: 'border-color 0.2s, box-shadow 0.2s',
    letterSpacing: '0.02em',
  }

  return (
    <div>
      {/* Controls */}
      <div style={{
        ...cardStyle, padding: '10px 16px', marginBottom: 14,
        display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap',
      }}>
        <label style={{ color: colors.textMuted, fontSize: 10, display: 'flex', alignItems: 'center', gap: 6, fontFamily: fonts.mono, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
          Sort
          <select value={sort} onChange={e => setSort(e.target.value)} style={selectStyle}>
            <option value="volume24hr">Volume</option>
            <option value="liquidityNum">Liquidity</option>
            <option value="startDate">Newest</option>
          </select>
        </label>
        <label style={{ color: colors.textMuted, fontSize: 10, display: 'flex', alignItems: 'center', gap: 6, fontFamily: fonts.mono, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
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
            transition: 'all 0.2s',
          }}
          onMouseEnter={e => {
            e.currentTarget.style.borderColor = colors.accent
            e.currentTarget.style.boxShadow = `0 0 8px rgba(0,229,255,0.15)`
          }}
          onMouseLeave={e => {
            e.currentTarget.style.borderColor = colors.border
            e.currentTarget.style.boxShadow = 'none'
          }}
        >
          Refresh
        </button>
        {loading && (
          <span style={{ color: colors.accent, fontSize: 11, display: 'flex', alignItems: 'center', gap: 6, fontFamily: fonts.mono }}>
            <span style={{ animation: 'spin 1s linear infinite', display: 'inline-block' }}>&#x21BB;</span>
            Loading...
          </span>
        )}
        <span style={{ marginLeft: 'auto', color: colors.textDim, fontSize: 10, fontFamily: fonts.mono, letterSpacing: '0.04em' }}>
          {markets.length} MARKETS
        </span>
      </div>

      {/* Table */}
      <div style={{ ...cardStyle, padding: 0, overflow: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr>
              {['#', 'YES', 'NO', 'Liquidity', 'Vol 24H', 'Spread', 'Expires', 'Question'].map((h, i) => (
                <th key={h} style={{
                  padding: '10px 12px', textAlign: i <= 0 || i >= 6 ? 'left' : 'right',
                  color: colors.textDim, fontWeight: 500, fontSize: 9,
                  textTransform: 'uppercase', letterSpacing: '0.08em',
                  borderBottom: `1px solid ${colors.border}`,
                  position: 'sticky', top: 0, background: colors.bgCard,
                  fontFamily: fonts.mono, zIndex: 2,
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
                    background: isSelected ? 'rgba(0, 229, 255, 0.04)' : 'transparent',
                    cursor: 'pointer',
                    transition: 'all 0.2s',
                  }}
                  onMouseEnter={e => {
                    if (!isSelected) {
                      e.currentTarget.style.background = 'rgba(0, 229, 255, 0.02)'
                      e.currentTarget.style.borderColor = colors.borderLight
                    }
                  }}
                  onMouseLeave={e => {
                    if (!isSelected) {
                      e.currentTarget.style.background = 'transparent'
                      e.currentTarget.style.borderColor = colors.border
                    }
                  }}
                >
                  <td style={{ padding: '10px 12px', color: colors.textDim, fontSize: 10, fontFamily: fonts.mono }}>{i + 1}</td>
                  <td style={{
                    padding: '10px 12px', textAlign: 'right',
                    fontFamily: fonts.mono, fontSize: 11,
                    color: colors.success,
                    textShadow: `0 0 12px ${colors.success}20`,
                  }}>
                    {yes !== null ? (yes * 100).toFixed(1) + '%' : '\u2014'}
                  </td>
                  <td style={{
                    padding: '10px 12px', textAlign: 'right',
                    fontFamily: fonts.mono, fontSize: 11,
                    color: colors.danger,
                    textShadow: `0 0 12px ${colors.danger}20`,
                  }}>
                    {no !== null ? (no * 100).toFixed(1) + '%' : '\u2014'}
                  </td>
                  <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: fonts.mono, fontSize: 11, color: colors.textSecondary }}>
                    ${(m.liquidityNum || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  </td>
                  <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: fonts.mono, fontSize: 11, color: colors.textSecondary }}>
                    ${(m.volume24hr || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  </td>
                  <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: fonts.mono, fontSize: 11, color: colors.textSecondary }}>
                    {m.spread != null ? (m.spread * 100).toFixed(1) + '%' : '\u2014'}
                  </td>
                  <td style={{ padding: '10px 12px', color: colors.textDim, whiteSpace: 'nowrap', fontSize: 11, fontFamily: fonts.mono }}>
                    {formatDate(m.endDate)}
                  </td>
                  <td style={{
                    padding: '10px 12px', maxWidth: 400, overflow: 'hidden',
                    textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 12,
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
          borderLeft: `2px solid ${colors.accent}`,
          animation: 'fadeInUp 0.25s ease forwards',
          boxShadow: glowShadow(colors.accent, 0.05),
        }}>
          <h3 style={{
            marginBottom: 12, fontSize: 14, fontWeight: 600,
            fontFamily: fonts.display,
          }}>
            {selected.question}
          </h3>
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
            gap: 12, fontSize: 12,
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
      <div style={{
        fontSize: 9, color: colors.textDim, marginBottom: 3,
        fontFamily: fonts.mono, textTransform: 'uppercase', letterSpacing: '0.08em',
      }}>
        {label}
      </div>
      <div style={{
        fontFamily: mono ? fonts.mono : fonts.body,
        fontSize: mono ? 10 : 12,
        color: color || colors.textPrimary,
        fontWeight: 500,
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        textShadow: color ? `0 0 12px ${color}25` : 'none',
      }}>
        {value}
      </div>
    </div>
  )
}
