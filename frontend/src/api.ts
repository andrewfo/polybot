// API fetch wrappers for all /api/* endpoints

export interface ServiceHealth {
  name: string
  healthy: boolean
  latency_ms: number | null
  error: string | null
}

export interface HealthCheck {
  check_name: string
  status: string
  message: string
}

export interface HealthResponse {
  services: ServiceHealth[]
  health_checks: HealthCheck[]
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
  paused: boolean
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
  placed_at?: string
  market_question?: string
  fill_price?: number | null
  pnl?: number | null
  resolution_status?: string
}

export interface FrontierDecision {
  id: number
  market_id: string
  estimated_prob: number
  effective_prob: number
  market_price: number
  edge: number
  kelly_fraction: number
  bet_size_usd: number
  confidence: number
  should_trade: number
  skip_reason: string
  timestamp: string
}

export interface Signal {
  id: number
  market_id: string
  signal_source: string
  probability: number
  confidence: number
  reasoning: string
  model_used: string
  timestamp: string
  raw_data?: string
}

export interface TradeDetail {
  trade: Trade
  frontier_decision: FrontierDecision | null
  signals: Signal[]
}

export interface LogEntry {
  timestamp: string
  level: string
  name: string
  message: string
}

export interface PnlSnapshot {
  timestamp: string
  total_value: number
  available_cash: number
  unrealized_pnl: number
  realized_pnl_today: number
  realized_pnl_total: number
}

export interface PnlResponse {
  snapshots: PnlSnapshot[]
  daily_pnl: number
  total_pnl: number
  trade_count: number
  win_rate: number
}

export interface PaperBalance {
  starting_balance: number
  realized_pnl: number
  deployed_capital: number
  unrealized_pnl: number
  available_cash: number
  total_value: number
  open_positions: number
}

export interface CycleInfo {
  last_run: string | null
  next_run: string | null
  seconds_remaining: number | null
  interval_minutes: number
}

export interface DiscoveryCycle extends CycleInfo {
  markets_found: number
  markets_ranked: number
}

export interface AggregationCycle extends CycleInfo {
  batch_size: number
}

export interface ActivityEvent {
  type: string
  message: string
  detail: string
  timestamp: string
}

export interface SessionStats {
  markets_discovered: number
  markets_analyzed: number
  trades_executed: number
  markets_skipped: number
}

export interface CyclesResponse {
  discovery: DiscoveryCycle
  aggregation: AggregationCycle
  position_monitor: CycleInfo
  uptime_seconds: number | null
  session_stats: SessionStats
  activity_feed: ActivityEvent[]
}

// Learning engine types

export interface LearningRecommendation {
  parameter: string
  current_value: number
  recommended_value: number
  confidence: number
  sample_count: number
  reason: string
  auto_applied: boolean
}

export interface LearningReport {
  status?: string
  message?: string
  timestamp?: string
  data_sufficiency?: string | Record<string, boolean>
  total_decisions?: number
  resolved_decisions?: number
  rec_count?: number
  recommendations?: LearningRecommendation[]
  bias?: {
    mean_bias: number
    abs_mean_error: number
    sample_count: number
  }
  frontier_bias?: {
    mean_bias: number
    abs_mean_error: number
    sample_count: number
  }
  skip_retro?: {
    total_skipped: number
    resolved_skipped: number
    would_have_profited: number
  }
  skip_summary?: {
    total_skipped: number
    resolved_count: number
    missed_opportunities: number
  }
}

export interface CalibrationBucket {
  bucket: string
  count: number
  avg_estimated: number
  avg_actual: number
  bias: number
}

export interface CalibrationResponse {
  mean_bias: number
  abs_mean_error: number
  sample_count: number
  calibration_curve: CalibrationBucket[]
  bias_by_confidence: Record<string, { count: number; mean_bias: number }>
  bias_by_price: Record<string, { count: number; mean_bias: number }>
}

export interface SkipAnalysis {
  total_skipped: number
  resolved_count: number
  missed_opportunities: number
  avg_missed_edge: number
  top_missed_reasons: Record<string, number>
  recommendation: string
}

export interface ParameterOverride {
  parameter: string
  original_value: number
  current_value: number
  applied_at: string
  source_report_ts: string
  confidence: number
  sample_count: number
  reason: string
  active: number
}

// Database explorer types

export interface DbColumn {
  name: string
  type: string
}

export interface DbTableInfo {
  name: string
  row_count: number
  columns: DbColumn[]
}

export interface DbTableRows {
  table: string
  total: number
  offset: number
  limit: number
  rows: Record<string, unknown>[]
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
  fetchPnl: () => fetchJSON<PnlResponse>('/api/pnl'),
  fetchPaperBalance: () => fetchJSON<PaperBalance>('/api/paper-balance'),
  fetchMarkets: (sort = 'volume24hr', limit = 20) =>
    fetchJSON<Market[]>(`/api/markets?sort=${sort}&limit=${limit}`),
  fetchAnalysisList: () => fetchJSON<AnalysisSummary[]>('/api/analysis'),
  fetchAnalysisDetail: (conditionId: string) =>
    fetchJSON<AnalysisDetail>(`/api/analysis/${conditionId}`),
  fetchTrades: () => fetchJSON<Trade[]>('/api/trades'),
  fetchTradeDetail: (tradeId: string) => fetchJSON<TradeDetail>(`/api/trades/${tradeId}`),
  fetchLogs: (level = 'ALL', limit = 100) =>
    fetchJSON<LogEntry[]>(`/api/logs?level=${level}&limit=${limit}`),
  startBot: () => postJSON<{ status: string }>('/api/bot/start'),
  stopBot: () => postJSON<{ status: string }>('/api/bot/stop'),
  pauseBot: () => postJSON<{ status: string }>('/api/bot/pause'),
  resumeBot: () => postJSON<{ status: string }>('/api/bot/resume'),
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
  fetchCycles: () => fetchJSON<CyclesResponse>('/api/bot/cycles'),

  // Learning engine
  fetchLearningReport: () => fetchJSON<LearningReport>('/api/learning/report'),
  fetchLearningHistory: (limit = 20) => fetchJSON<LearningReport[]>(`/api/learning/history?limit=${limit}`),
  runLearningCycle: () => postJSON<{ status: string; recommendations: number; report: LearningReport }>('/api/learning/run'),
  fetchLearningRecommendations: () => fetchJSON<LearningRecommendation[]>('/api/learning/recommendations'),
  fetchLearningCalibration: () => fetchJSON<CalibrationResponse>('/api/learning/calibration'),
  fetchSkipAnalysis: () => fetchJSON<SkipAnalysis>('/api/learning/skip-analysis'),
  fetchOverrides: () => fetchJSON<ParameterOverride[]>('/api/learning/overrides'),
  revertOverride: (parameter: string) => postJSON<{ status: string }>(`/api/learning/overrides/${parameter}/revert`),
  setOverride: (parameter: string, value: number, reason: string) =>
    postJSON<{ status: string; value: number }>(`/api/learning/overrides/${parameter}/set`, { value, reason }),

  // Database explorer
  fetchDbTables: () => fetchJSON<DbTableInfo[]>('/api/db/tables'),
  fetchDbTableRows: (table: string, limit = 50, offset = 0, orderBy = 'rowid', desc = true) =>
    fetchJSON<DbTableRows>(`/api/db/tables/${table}?limit=${limit}&offset=${offset}&order_by=${orderBy}&desc=${desc}`),
}
