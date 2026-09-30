import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  IconButton,
  Input,
  SearchField,
  Select,
  Skeleton,
  type BadgeTone,
} from '@/components/ui/primitives'
import { ConfirmDialog, Modal } from '@/components/ui/Modal'
import { can, useCurrentUser } from '@/hooks/useAuth'
import { useDebounced } from '@/hooks/useDebounced'
import { useIsMobile } from '@/hooks/useMediaQuery'
import { useTranslation } from '@/hooks/useTranslation'
import { usersApi } from '@/services/auth'
import { toast } from '@/state/toasts'
import { formatDate, formatDateTime } from '@/utils/format'
import type { Role, TeamSummary, UserRecord } from '@/types/auth'
import './users.css'

const PAGE_SIZE = 50
const MIN_PASSWORD = 10
const ROLES: Role[] = ['viewer', 'contributor', 'knowledge_manager', 'admin', 'service']

/** Privilege should be readable at a glance, not inferred from the wording of the role. */
const ROLE_TONE: Record<Role, BadgeTone> = {
  viewer: 'neutral',
  contributor: 'neutral',
  knowledge_manager: 'info',
  admin: 'accent',
  service: 'warning',
}

interface UsersQuery {
  search?: string
  role?: string
  limit: number
  offset: number
}

/** What a PATCH may carry — mirrors `usersApi.update`. */
type UserPatch = Partial<{
  display_name: string
  role: string
  team_id: string | null
  is_active: boolean
  new_password: string
}>

const usersKeys = {
  all: ['users'] as const,
  list: (query: UsersQuery) => ['users', 'list', query] as const,
  teams: ['users', 'teams'] as const,
}

/* Dialogs ----------------------------------------------------------------- */

function RoleOptions() {
  const { t } = useTranslation()
  return (
    <>
      {ROLES.map((role) => (
        <option key={role} value={role}>
          {t(`roles.${role}`)}
        </option>
      ))}
    </>
  )
}

function TeamOptions({ teams }: { teams: TeamSummary[] }) {
  return (
    <>
      {teams.map((team) => (
        <option key={team.id} value={team.id}>
          {team.name}
        </option>
      ))}
    </>
  )
}

function CreateUserDialog({
  teams,
  pending,
  onClose,
  onSubmit,
}: {
  teams: TeamSummary[]
  pending: boolean
  onClose: () => void
  onSubmit: (body: {
    email: string
    password: string
    display_name?: string
    role?: string
    team_id?: string | null
  }) => void
}) {
  const { t } = useTranslation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [role, setRole] = useState<Role>('viewer')
  const [teamId, setTeamId] = useState('')

  const valid = email.trim().length > 0 && password.length >= MIN_PASSWORD

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!valid) return
    onSubmit({
      email: email.trim(),
      password,
      display_name: displayName.trim() || undefined,
      role,
      team_id: teamId || null,
    })
  }

  return (
    <Modal
      open
      title={t('admin.addUser')}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button type="submit" form="user-create" variant="primary" loading={pending} disabled={!valid}>
            {t('admin.addUser')}
          </Button>
        </>
      }
    >
      <form id="user-create" className="user-form" onSubmit={submit}>
        <Field label={t('auth.email')} htmlFor="user-create-email">
          <Input
            id="user-create-email"
            type="email"
            required
            autoFocus
            autoComplete="off"
            dir="ltr"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </Field>

        <Field label={t('auth.password')} hint={t('auth.minPassword')} htmlFor="user-create-password">
          <Input
            id="user-create-password"
            type="password"
            required
            minLength={MIN_PASSWORD}
            autoComplete="new-password"
            dir="ltr"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>

        <Field label={t('admin.displayName')} htmlFor="user-create-name">
          <Input
            id="user-create-name"
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
          />
        </Field>

        <div className="user-form__row">
          <Field label={t('auth.role')} htmlFor="user-create-role">
            <Select
              id="user-create-role"
              value={role}
              onChange={(event) => setRole(event.target.value as Role)}
            >
              <RoleOptions />
            </Select>
          </Field>

          <Field label={t('auth.team')} htmlFor="user-create-team">
            <Select
              id="user-create-team"
              value={teamId}
              onChange={(event) => setTeamId(event.target.value)}
            >
              <option value="">{t('common.none')}</option>
              <TeamOptions teams={teams} />
            </Select>
          </Field>
        </div>
      </form>
    </Modal>
  )
}

function EditUserDialog({
  user,
  teams,
  pending,
  onClose,
  onSubmit,
}: {
  user: UserRecord
  teams: TeamSummary[]
  pending: boolean
  onClose: () => void
  onSubmit: (patch: UserPatch) => void
}) {
  const { t } = useTranslation()
  const [displayName, setDisplayName] = useState(user.display_name)
  const [role, setRole] = useState<Role>(user.role)
  const [teamId, setTeamId] = useState(user.team_id ?? '')
  const [active, setActive] = useState(user.is_active)
  const [password, setPassword] = useState('')

  const passwordValid = password.length === 0 || password.length >= MIN_PASSWORD

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!passwordValid) return
    // Only what changed is sent: an untouched field must not overwrite a value someone
    // else edited between this list loading and this save.
    const patch: UserPatch = {}
    const name = displayName.trim()
    if (name && name !== user.display_name) patch.display_name = name
    if (role !== user.role) patch.role = role
    if ((teamId || null) !== user.team_id) patch.team_id = teamId || null
    if (active !== user.is_active) patch.is_active = active
    if (password) patch.new_password = password
    onSubmit(patch)
  }

  return (
    <Modal
      open
      title={t('common.edit')}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            type="submit"
            form="user-edit"
            variant="primary"
            loading={pending}
            disabled={!passwordValid}
          >
            {t('common.confirm')}
          </Button>
        </>
      }
    >
      <form id="user-edit" className="user-form" onSubmit={submit}>
        <p className="user-form__subject" dir="ltr">
          {user.email}
        </p>

        <Field label={t('admin.displayName')} htmlFor="user-edit-name">
          <Input
            id="user-edit-name"
            autoFocus
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
          />
        </Field>

        <div className="user-form__row">
          <Field label={t('auth.role')} htmlFor="user-edit-role">
            <Select
              id="user-edit-role"
              value={role}
              onChange={(event) => setRole(event.target.value as Role)}
            >
              <RoleOptions />
            </Select>
          </Field>

          <Field label={t('auth.team')} htmlFor="user-edit-team">
            <Select
              id="user-edit-team"
              value={teamId}
              onChange={(event) => setTeamId(event.target.value)}
            >
              <option value="">{t('common.none')}</option>
              <TeamOptions teams={teams} />
            </Select>
          </Field>
        </div>

        <label className="user-form__check" htmlFor="user-edit-active">
          <input
            id="user-edit-active"
            type="checkbox"
            checked={active}
            onChange={(event) => setActive(event.target.checked)}
          />
          <span>{t('admin.active')}</span>
        </label>

        <Field label={t('auth.newPassword')} hint={t('auth.minPassword')} htmlFor="user-edit-password">
          <Input
            id="user-edit-password"
            type="password"
            autoComplete="new-password"
            dir="ltr"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>
      </form>
    </Modal>
  )
}

/* Page -------------------------------------------------------------------- */

export function UsersPage() {
  const { t, language } = useTranslation()
  const { data: currentUser } = useCurrentUser()
  const isMobile = useIsMobile()
  const client = useQueryClient()

  const [search, setSearch] = useState('')
  const [role, setRole] = useState<Role | ''>('')
  const [offset, setOffset] = useState(0)
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<UserRecord | null>(null)
  const [pendingDeactivate, setPendingDeactivate] = useState<UserRecord | null>(null)

  const debouncedSearch = useDebounced(search, 300)
  const allowed = can(currentUser, 'user.manage')

  // A narrowed filter can leave the current page past the end of the new result set.
  useEffect(() => setOffset(0), [debouncedSearch, role])

  const query = useMemo<UsersQuery>(
    () => ({
      search: debouncedSearch.trim() || undefined,
      role: role || undefined,
      limit: PAGE_SIZE,
      offset,
    }),
    [debouncedSearch, role, offset],
  )

  const list = useQuery({
    queryKey: usersKeys.list(query),
    queryFn: () => usersApi.list(query),
    enabled: allowed,
    placeholderData: (previous) => previous,
  })

  const teams = useQuery({
    queryKey: usersKeys.teams,
    queryFn: usersApi.teams,
    enabled: allowed,
    staleTime: 60_000,
  })

  const invalidate = () => void client.invalidateQueries({ queryKey: usersKeys.all })
  const reportError = (error: Error) => toast.error(t('errors.title'), error.message)

  const create = useMutation({
    mutationFn: usersApi.create,
    onSuccess: (user) => {
      invalidate()
      setCreating(false)
      toast.success(t('admin.created'), user.email)
    },
    onError: reportError,
  })

  const update = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: UserPatch }) => usersApi.update(id, patch),
    onSuccess: (user) => {
      invalidate()
      setEditing(null)
      toast.success(t('admin.updated'), user.display_name || user.email)
    },
    onError: reportError,
  })

  const deactivate = useMutation({
    mutationFn: (id: string) => usersApi.deactivate(id),
    onSuccess: () => {
      invalidate()
      toast.success(t('admin.updated'))
    },
    onError: reportError,
  })

  // The server enforces this as well; refusing here only avoids a page that would 403.
  if (!allowed) {
    return (
      <div className="page">
        <ErrorState title={t('auth.forbidden')} />
      </div>
    )
  }

  const users = list.data?.items ?? []
  const total = list.data?.total ?? 0
  const teamList = teams.data ?? []
  const teamName = (id: string | null) => teamList.find((team) => team.id === id)?.name ?? '—'

  const confirmDeactivate = () => {
    const target = pendingDeactivate
    setPendingDeactivate(null)
    if (target) deactivate.mutate(target.id)
  }

  const status = (user: UserRecord) => (
    <Badge tone={user.is_active ? 'success' : 'neutral'} dot>
      {user.is_active ? t('admin.active') : t('admin.disabled')}
    </Badge>
  )

  const actions = (user: UserRecord) => (
    <>
      <IconButton icon="settings" label={t('common.edit')} size="sm" onClick={() => setEditing(user)} />
      <IconButton
        icon="archive"
        label={t('admin.deactivate')}
        size="sm"
        disabled={!user.is_active || (deactivate.isPending && deactivate.variables === user.id)}
        onClick={() => setPendingDeactivate(user)}
      />
    </>
  )

  let body
  if (list.isError) {
    body = (
      <ErrorState
        title={t('errors.title')}
        body={list.error instanceof Error ? list.error.message : t('errors.generic')}
        action={
          <Button variant="secondary" icon="refresh" onClick={() => void list.refetch()}>
            {t('common.retry')}
          </Button>
        }
      />
    )
  } else if (list.isLoading) {
    body = (
      <div className="users-table__skeletons">
        {Array.from({ length: 6 }, (_, index) => (
          <Skeleton key={index} height="2.25rem" />
        ))}
      </div>
    )
  } else if (users.length === 0) {
    body = <EmptyState icon="inbox" title={t('admin.noUsers')} />
  } else if (isMobile) {
    body = (
      <div className="users-cards">
        {users.map((user) => (
          <Card key={user.id} className="users-card">
            <div className="users-card__head">
              <span className="users-card__title">
                {user.display_name || user.email}
                <span className="users-card__email" dir="ltr">
                  {user.email}
                </span>
              </span>
              {status(user)}
            </div>

            <div className="users-card__grid">
              <div>
                <span className="users-card__label">{t('auth.role')}</span>
                <Badge tone={ROLE_TONE[user.role]}>{t(`roles.${user.role}`)}</Badge>
              </div>
              <div>
                <span className="users-card__label">{t('auth.team')}</span>
                <span className="users-card__value">{teamName(user.team_id)}</span>
              </div>
              <div>
                <span className="users-card__label">{t('admin.lastLogin')}</span>
                <span className="users-card__value" title={formatDateTime(user.last_login_at, language)}>
                  {formatDate(user.last_login_at, language)}
                </span>
              </div>
            </div>

            <div className="users-card__actions">{actions(user)}</div>
          </Card>
        ))}
      </div>
    )
  } else {
    body = (
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>{t('admin.displayName')}</th>
              <th>{t('auth.email')}</th>
              <th>{t('auth.role')}</th>
              <th>{t('auth.team')}</th>
              <th>{t('admin.status')}</th>
              <th>{t('admin.lastLogin')}</th>
              <th>{t('common.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {users.map((user) => (
              <tr key={user.id}>
                <td>
                  <strong className="users-table__name">{user.display_name || '—'}</strong>
                </td>
                <td className="users-table__email" dir="ltr">
                  {user.email}
                </td>
                <td>
                  <Badge tone={ROLE_TONE[user.role]}>{t(`roles.${user.role}`)}</Badge>
                </td>
                <td className="users-table__muted">{teamName(user.team_id)}</td>
                <td>{status(user)}</td>
                <td className="table__numeric" title={formatDateTime(user.last_login_at, language)}>
                  {formatDate(user.last_login_at, language)}
                </td>
                <td>
                  <div className="users-table__actions">{actions(user)}</div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  const showCount = !list.isError && users.length > 0
  const showPager = total > PAGE_SIZE

  return (
    <div className="page">
      <header className="page__head">
        <h1 className="page__title">{t('admin.users')}</h1>
        <Button variant="primary" icon="plus" onClick={() => setCreating(true)}>
          {t('admin.addUser')}
        </Button>
      </header>

      <div className="users-filters">
        <div className="users-filters__search">
          <SearchField
            value={search}
            placeholder={t('admin.searchUsers')}
            aria-label={t('common.search')}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <div className="users-filters__select">
          <Field label={t('auth.role')} htmlFor="users-role">
            <Select
              id="users-role"
              value={role}
              onChange={(event) => setRole(event.target.value as Role | '')}
            >
              <option value="">{t('admin.allRoles')}</option>
              <RoleOptions />
            </Select>
          </Field>
        </div>
      </div>

      {isMobile && !list.isError && users.length > 0 ? body : <Card className="users-table">{body}</Card>}

      {showCount && (
        <div className="users-pager">
          <span className="users-pager__count">
            {t('common.rowsShown', { shown: offset + users.length, total })}
          </span>
          {showPager && (
            <div className="users-pager__buttons">
              <Button
                variant="secondary"
                size="sm"
                icon="chevronStart"
                disabled={offset === 0}
                onClick={() => setOffset((current) => Math.max(0, current - PAGE_SIZE))}
              >
                {t('common.previous')}
              </Button>
              <Button
                variant="secondary"
                size="sm"
                iconEnd="chevronEnd"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => setOffset((current) => current + PAGE_SIZE)}
              >
                {t('common.next')}
              </Button>
            </div>
          )}
        </div>
      )}

      {creating && (
        <CreateUserDialog
          teams={teamList}
          pending={create.isPending}
          onClose={() => setCreating(false)}
          onSubmit={(body) => create.mutate(body)}
        />
      )}

      {editing && (
        <EditUserDialog
          key={editing.id}
          user={editing}
          teams={teamList}
          pending={update.isPending}
          onClose={() => setEditing(null)}
          onSubmit={(patch) => update.mutate({ id: editing.id, patch })}
        />
      )}

      <ConfirmDialog
        open={pendingDeactivate !== null}
        title={t('admin.deactivate')}
        message={t('admin.deactivateConfirm')}
        confirmLabel={t('admin.deactivate')}
        destructive
        onConfirm={confirmDeactivate}
        onCancel={() => setPendingDeactivate(null)}
      />
    </div>
  )
}
