import { api } from './client'

/** Mirrors `app/api/routes/monitoring.py` and `app/services/metrics.py`. */
export interface Spread {
  p50: number
  p95: number
  max: number
  mean: number
}

export interface MonitorAlert {
  level: 'critical' | 'warning'
  key: string
  message: string
}

export interface DailyTraffic {
  date: string
  total: number
  answered: number
  refused: number
  clarified: number
  error: number
  web: number
  p50_ms: number
  p95_ms: number
}

export interface TrafficSummary {
  window_days: number
  total: number
  by_outcome: Record<string, number>
  by_channel: Record<string, number>
  rates: Record<string, number>
  latency: { total: Spread; retrieval: Spread; generation: Spread }
  stages: Record<string, Spread>
  quality: {
    answered: number
    complete_rate: number
    completion_pass_rate: number
    with_unsupported_values: number
    with_conflicts: number
    mean_retrieved: number
  }
  daily: DailyTraffic[]
  refusal_reasons: { reason: string; label: string; count: number }[]
  slowest: { answer_id: string; total_ms: number; outcome: string; created_at: string; model: string }[]
  recent_errors: { created_at: string; error_type: string; channel: string }[]
  alerts: MonitorAlert[]
}

export interface ComponentHealth {
  reachable?: boolean
  [key: string]: unknown
}

export interface MonitoringResponse {
  model: string
  components: {
    generation: ComponentHealth
    embeddings: ComponentHealth
    vectors: ComponentHealth
    reranker: { name: string; loaded?: boolean; error?: string; mean_ms?: number; fallbacks?: number }
    backup: {
      last_success: string | null
      age_hours: number | null
      stale: boolean
      last_error: string
      last_size: number | null
      mirrored: boolean
      mirror_configured: boolean
    }
    disk: { free_gb: number; total_gb: number; low: boolean }
  }
  traffic: TrafficSummary
}

export interface NightlyReport {
  written_at: string
  markdown: string
}

/** Mirrors `app/services/quality_report.py`. */
export interface QualityWindow {
  date: string
  total: number
  answered: number
  refused: number
  clarified: number
  errors: number
  verified: number
  up: number
  down: number
  learned: number
  answer_rate: number | null
  verified_rate: number | null
}

export interface QualityPeriod {
  questions: number
  answer_rate: number | null
  verified_rate: number | null
  satisfaction: number | null
  up: number
  down: number
  remembered: number
  median_seconds: number | null
  users: number
}

export interface QualityReport {
  days: number
  series: QualityWindow[]
  totals: QualityPeriod
  this_week: QualityPeriod
  last_week: QualityPeriod
  learned: {
    corrections: number
    terms: number
    from_rephrasing: number
    other: number
    wordings: number
    follow_ups: number
    shared_pending: number
    active_personal: number
  }
  unanswered: Array<{ question: string; count: number }>
  disliked: Array<{ question: string; answer: string; date: string }>
}

export const monitoringApi = {
  quality: (days: number) => api.get<QualityReport>(`/api/admin/quality?days=${days}`),
  get: (days: number) => api.get<MonitoringResponse>(`/api/admin/monitoring?days=${days}`),
  latestReport: () => api.get<NightlyReport>('/api/admin/reports/latest'),
  indexHealth: () => api.get<{ markdown: string }>('/api/admin/index-health'),
}
