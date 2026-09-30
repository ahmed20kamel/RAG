import { api } from './client'
import type { CurrentUser, LoginRequest, TeamSummary, UserListResponse, UserRecord } from '@/types/auth'

export const authApi = {
  login: (body: LoginRequest) => api.post<CurrentUser>('/api/auth/login', body),
  logout: () => api.post<void>('/api/auth/logout'),
  me: () => api.get<CurrentUser>('/api/auth/me'),
  changePassword: (current_password: string, new_password: string) =>
    api.post<void>('/api/auth/password', { current_password, new_password }),
}

export const usersApi = {
  list: (query: { search?: string; role?: string; limit?: number; offset?: number } = {}) =>
    api.get<UserListResponse>('/api/users', {
      query: { ...query, limit: query.limit ?? 50, offset: query.offset ?? 0 },
    }),
  create: (body: {
    email: string
    password: string
    display_name?: string
    role?: string
    team_id?: string | null
  }) => api.post<UserRecord>('/api/users', body),
  update: (
    id: string,
    body: Partial<{
      display_name: string
      role: string
      team_id: string | null
      is_active: boolean
      new_password: string
    }>,
  ) => api.patch<UserRecord>(`/api/users/${id}`, body),
  deactivate: (id: string) => api.delete<void>(`/api/users/${id}`),
  teams: () => api.get<TeamSummary[]>('/api/teams'),
  createTeam: (name: string, department = '') =>
    api.post<TeamSummary>('/api/teams', { name, department }),
}
