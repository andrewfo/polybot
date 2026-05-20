import { useState, useEffect, useCallback } from 'react'
import { colors, cardStyle, glowShadow, fonts, animDelay } from '../theme'
import { api, DbTableInfo, DbTableRows } from '../api'

const TABLE_CATEGORIES: Record<string, { label: string; tables: string[] }> = {
  trading: {
    label: 'Core Trading',
    tables: ['trades', 'positions', 'bankroll', 'frontier_decisions'],
  },
  tuning: {
    label: 'Tuning & Learning',
    tables: ['signals', 'signal_calibration', 'parameter_overrides', 'parameter_change_snapshots', 'skipped_markets'],
  },
  operational: {
    label: 'Operational',
    tables: ['llm_costs', 'market_cache', 'market_regimes'],
  },
}

function TableCard({ table, isActive, onClick, index }: {
  table: DbTableInfo; isActive: boolean; onClick: () => void; index: number
}) {
  return (
    <button
      onClick={onClick}
      style={{
        ...animDelay(index),
        background: isActive ? 'rgba(255, 255, 255, 0.08)' : colors.gradientCard,
        border: `1px solid ${isActive ? colors.borderHover : colors.border}`,
        borderRadius: 8,
        padding: '10px 14px',
        cursor: 'pointer',
        textAlign: 'left',
        transition: 'all 0.2s ease',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        boxShadow: isActive ? glowShadow(colors.accent, 0.06) : 'none',
      }}
    >
      <span style={{
        fontFamily: fonts.mono,
        fontSize: 12,
        color: isActive ? colors.accent : colors.textSecondary,
        fontWeight: isActive ? 600 : 400,
      }}>
        {table.name}
      </span>
      <span style={{
        fontFamily: fonts.mono,
        fontSize: 10,
        color: colors.textMuted,
        background: 'rgba(255, 255, 255, 0.05)',
        padding: '2px 8px',
        borderRadius: 10,
      }}>
        {table.row_count.toLocaleString()}
      </span>
    </button>
  )
}

function DataTable({ data, columns }: { data: Record<string, unknown>[]; columns: string[] }) {
  if (data.length === 0) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: colors.textMuted, fontSize: 13 }}>
        No rows in this table
      </div>
    )
  }

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{
        width: '100%',
        borderCollapse: 'collapse',
        fontFamily: fonts.mono,
        fontSize: 11,
      }}>
        <thead>
          <tr>
            {columns.map(col => (
              <th key={col} style={{
                padding: '8px 12px',
                textAlign: 'left',
                color: colors.accent,
                fontWeight: 600,
                fontSize: 10,
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
                borderBottom: `1px solid ${colors.border}`,
                whiteSpace: 'nowrap',
                position: 'sticky',
                top: 0,
                background: colors.bgCard,
              }}>
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((row, i) => (
            <tr key={i} style={{
              borderBottom: `1px solid ${colors.border}`,
              background: i % 2 === 0 ? 'transparent' : 'rgba(255, 255, 255, 0.015)',
            }}>
              {columns.map(col => (
                <td key={col} style={{
                  padding: '7px 12px',
                  color: colors.textSecondary,
                  maxWidth: 300,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}>
                  {formatCell(row[col])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return '\u2014'
  if (typeof value === 'number') {
    if (Number.isInteger(value)) return value.toLocaleString()
    return value.toFixed(4)
  }
  if (typeof value === 'string' && value.length > 80) return value.slice(0, 80) + '\u2026'
  return String(value)
}

export default function Database() {
  const [tables, setTables] = useState<DbTableInfo[]>([])
  const [activeTable, setActiveTable] = useState<string | null>(null)
  const [tableData, setTableData] = useState<DbTableRows | null>(null)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(0)
  const PAGE_SIZE = 50

  useEffect(() => {
    api.fetchDbTables().then(setTables).catch(() => {})
  }, [])

  const loadTable = useCallback((name: string, offset = 0) => {
    setActiveTable(name)
    setLoading(true)
    setPage(offset / PAGE_SIZE)
    api.fetchDbTableRows(name, PAGE_SIZE, offset)
      .then(setTableData)
      .catch(() => setTableData(null))
      .finally(() => setLoading(false))
  }, [])

  const activeTableInfo = tables.find(t => t.name === activeTable)
  const totalPages = tableData ? Math.ceil(tableData.total / PAGE_SIZE) : 0

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '260px 1fr', gap: 20, minHeight: 500 }}>
      {/* Sidebar — table list */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {Object.entries(TABLE_CATEGORIES).map(([key, cat]) => {
          const catTables = tables.filter(t => cat.tables.includes(t.name))
          if (catTables.length === 0) return null
          return (
            <div key={key}>
              <div style={{
                fontSize: 10,
                fontWeight: 700,
                color: colors.textMuted,
                textTransform: 'uppercase',
                letterSpacing: '0.08em',
                marginBottom: 6,
                fontFamily: fonts.mono,
              }}>
                {cat.label}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {catTables.map((t, i) => (
                  <TableCard
                    key={t.name}
                    table={t}
                    isActive={activeTable === t.name}
                    onClick={() => loadTable(t.name)}
                    index={i}
                  />
                ))}
              </div>
            </div>
          )
        })}
        {/* Uncategorized tables */}
        {(() => {
          const allCategorized = Object.values(TABLE_CATEGORIES).flatMap(c => c.tables)
          const uncategorized = tables.filter(t => !allCategorized.includes(t.name))
          if (uncategorized.length === 0) return null
          return (
            <div>
              <div style={{
                fontSize: 10, fontWeight: 700, color: colors.textMuted,
                textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6,
                fontFamily: fonts.mono,
              }}>
                Other
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {uncategorized.map((t, i) => (
                  <TableCard
                    key={t.name}
                    table={t}
                    isActive={activeTable === t.name}
                    onClick={() => loadTable(t.name)}
                    index={i}
                  />
                ))}
              </div>
            </div>
          )
        })()}
      </div>

      {/* Main content — table data */}
      <div style={{
        ...cardStyle,
        padding: 0,
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
      }}>
        {!activeTable ? (
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            height: '100%', color: colors.textMuted, fontSize: 13,
            flexDirection: 'column', gap: 8, padding: 40,
          }}>
            <span style={{ fontSize: 28, opacity: 0.4 }}>{'\u2756'}</span>
            <span>Select a table to view its data</span>
          </div>
        ) : (
          <>
            {/* Table header */}
            <div style={{
              padding: '14px 18px',
              borderBottom: `1px solid ${colors.border}`,
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              background: 'rgba(255, 255, 255, 0.02)',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <span style={{
                  fontFamily: fonts.mono, fontSize: 14, fontWeight: 700, color: colors.accent,
                }}>
                  {activeTable}
                </span>
                {activeTableInfo && (
                  <span style={{
                    fontFamily: fonts.mono, fontSize: 11, color: colors.textMuted,
                  }}>
                    {activeTableInfo.row_count.toLocaleString()} rows
                    {' \u00B7 '}
                    {activeTableInfo.columns.length} columns
                  </span>
                )}
              </div>
              {/* Pagination */}
              {totalPages > 1 && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <PagButton
                    label="\u2190"
                    disabled={page === 0}
                    onClick={() => loadTable(activeTable, (page - 1) * PAGE_SIZE)}
                  />
                  <span style={{ fontFamily: fonts.mono, fontSize: 11, color: colors.textMuted }}>
                    {page + 1} / {totalPages}
                  </span>
                  <PagButton
                    label="\u2192"
                    disabled={page >= totalPages - 1}
                    onClick={() => loadTable(activeTable, (page + 1) * PAGE_SIZE)}
                  />
                </div>
              )}
            </div>
            {/* Table content */}
            <div style={{ flex: 1, overflow: 'auto', maxHeight: 600 }}>
              {loading ? (
                <div style={{ padding: 40, textAlign: 'center', color: colors.textMuted }}>
                  Loading...
                </div>
              ) : tableData ? (
                <DataTable
                  data={tableData.rows}
                  columns={activeTableInfo?.columns.map(c => c.name) || []}
                />
              ) : (
                <div style={{ padding: 40, textAlign: 'center', color: colors.danger }}>
                  Failed to load table data
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function PagButton({ label, disabled, onClick }: { label: string; disabled: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        background: disabled ? 'transparent' : 'rgba(255, 255, 255, 0.06)',
        border: `1px solid ${disabled ? colors.border : colors.borderLight}`,
        borderRadius: 4,
        padding: '4px 10px',
        color: disabled ? colors.textDim : colors.accent,
        cursor: disabled ? 'default' : 'pointer',
        fontFamily: fonts.mono,
        fontSize: 12,
        transition: 'all 0.2s ease',
      }}
    >
      {label}
    </button>
  )
}
