import { api } from './client'
import type { EffectiveConfig, HealthResponse } from '@/types/api'

export const systemApi = {
  health: () => api.get<HealthResponse>('/api/health'),
  config: () => api.get<EffectiveConfig>('/api/config'),
  stages: () => api.get<{ stages: string[] }>('/api/chat/stages'),
}
