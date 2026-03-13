// API fetch wrappers for all /api/* endpoints

export interface ServiceHealth {
  name: string
  healthy: boolean
  latency_ms: number | null
  error: string | null
}

export interface HealthResponse {
  services: ServiceHealth[]
}

export interface WalletResponse {
  address: string
  usdc: number
  matic: number
  has_gas: boolean
  positions_count: number
}

export interface Position {
  token_id: string
  market_id: string
  market_question: string
  side: string
  avg_entry: number
  size: number
  current_price: number
  unrealized_pnl: number
  opened_at: string
  last_updated: string
  paper: number
}

export interface ModelBreakdown {
  model: string
  calls: number
  input_tokens: number
  output_tokens: number
  cost: number
}

export interface TaskBreakdown {
  task_type: string
  calls: number
  cost: number
}

export interface CostResponse {
  daily: number
  monthly: number
  total_calls: number
  model_breakdown: ModelBreakdown[]
  task_breakdown: TaskBreakdown[]
}

export interface BotStatus {
  running: boolean
  phase: string
  cycle_count: number
  paper_trading: boolean
}

export interface Market {
  conditionId: string
  question: string
  outcomePrices: string
  liquidityNum: number
  volume24hr: number
  spread: number | null
  bestBid: number | null
  bestAsk: number | null
  endDate: string
  clobTokenIds: string
  outcomes: string
  [key: string]: unknown
}

export interface AnalysisSummary {
  condition_id: string
  question: string
  status: string
  decision: string | null
  edge: number | null
}

export interface AnalysisDetail {
  [key: string]: unknown
}

export interface Trade {
  id: string
  market_id: string
  token_id: string
  side: string
  price: number
  size: number
  status: string
  paper: number
  timestamp: string
  order_id?: string
  market_question?: string
}

export interface LogEntry {
  timestamp: string
  level: string
  name: string
  message: string
}

const BASE = ''

async function fetchJSON<T>(url: string): Promise<T> {
  const resp = await fetch(BASE + url)
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json()
}

async function postJSON<T>(url: string, body?: unknown): Promise<T> {
  const resp = await fetch(BASE + url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json()
}

export const api = {
  fetchHealth: () => fetchJSON<HealthResponse>('/api/health'),
  fetchWallet: () => fetchJSON<WalletResponse>('/api/wallet'),
  fetchPositions: () => fetchJSON<Position[]>('/api/positions'),
  fetchCosts: () => fetchJSON<CostResponse>('/api/costs'),
  fetchBotStatus: () => fetchJSON<BotStatus>('/api/bot/status'),
  fetchMarkets: (sort = 'volume24hr', limit = 20) =>
    fetchJSON<Market[]>(`/api/markets?sort=${sort}&limit=${limit}`),
  fetchAnalysisList: () => fetchJSON<AnalysisSummary[]>('/api/analysis'),
  fetchAnalysisDetail: (conditionId: string) =>
    fetchJSON<AnalysisDetail>(`/api/analysis/${conditionId}`),
  fetchTrades: () => fetchJSON<Trade[]>('/api/trades'),
  fetchLogs: (level = 'ALL', limit = 100) =>
    fetchJSON<LogEntry[]>(`/api/logs?level=${level}&limit=${limit}`),
  startBot: () => postJSON<{ status: string }>('/api/bot/start'),
  stopBot: () => postJSON<{ status: string }>('/api/bot/stop'),
  runAggregate: (question: string, marketPrice: number) =>
    postJSON<{ status: string; condition_id?: string; probability?: number }>(
      '/api/commands/aggregate',
      { question, market_price: marketPrice },
    ),
  runSignalTest: (question: string) =>
    postJSON<{ question: string; signals: Array<{ source: string; probability: number | null; confidence: number; reasoning: string }> }>(
      '/api/commands/signal-test',
      { question },
    ),
}
