/** Mirrors `app/schemas/auth.py` and `app/core/permissions.py`. */

export type Role = 'viewer' | 'contributor' | 'knowledge_manager' | 'admin' | 'service'

/** Capability strings the backend checks. The interface hides what the user lacks. */
export type Permission =
  | 'chat.ask'
  | 'document.read'
  | 'document.upload'
  | 'document.delete'
  | 'knowledge.read'
  | 'knowledge.propose'
  | 'knowledge.approve'
  | 'knowledge.edit'
  | 'knowledge.archive'
  | 'user.manage'
  | 'system.read'
  | 'system.monitor'

export interface UserRecord {
  id: string
  email: string
  display_name: string
  role: Role
  team_id: string | null
  is_active: boolean
  created_at: string
  last_login_at: string | null
}

export interface CurrentUser extends UserRecord {
  permissions: Permission[]
  team_name: string
}

export interface LoginRequest {
  email: string
  password: string
}

export interface UserListResponse {
  total: number
  items: UserRecord[]
}

export interface TeamSummary {
  id: string
  name: string
  department: string
  created_at: string
}
