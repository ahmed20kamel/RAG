import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { Icon } from '@/components/ui/Icon'
import { Badge, Button, Field, Input } from '@/components/ui/primitives'
import { Modal } from '@/components/ui/Modal'
import { can, useChangePassword, useCurrentUser, useLogout } from '@/hooks/useAuth'
import { useTranslation } from '@/hooks/useTranslation'
import { toast } from '@/state/toasts'
import './account-menu.css'

const MIN_PASSWORD = 10

/** Two letters at most: enough to tell colleagues apart without a stored avatar. */
function initials(source: string): string {
  const letters = source
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => [...part][0] ?? '')
    .join('')
  return letters.toUpperCase() || '?'
}

function PasswordDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const change = useChangePassword()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')

  const valid = current.length > 0 && next.length >= MIN_PASSWORD

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!valid) return
    change.mutate(
      { current, next },
      {
        onSuccess: () => {
          toast.success(t('auth.passwordChanged'))
          onClose()
        },
        onError: (error) => toast.error(t('errors.title'), error.message),
      },
    )
  }

  return (
    <Modal
      open
      title={t('auth.changePassword')}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            type="submit"
            form="account-password"
            variant="primary"
            loading={change.isPending}
            disabled={!valid}
          >
            {t('auth.changePassword')}
          </Button>
        </>
      }
    >
      <form id="account-password" className="account-password" onSubmit={submit}>
        <Field label={t('auth.currentPassword')} htmlFor="account-current-password">
          <Input
            id="account-current-password"
            type="password"
            required
            autoFocus
            autoComplete="current-password"
            dir="ltr"
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
          />
        </Field>

        <Field label={t('auth.newPassword')} hint={t('auth.minPassword')} htmlFor="account-new-password">
          <Input
            id="account-new-password"
            type="password"
            required
            minLength={MIN_PASSWORD}
            autoComplete="new-password"
            dir="ltr"
            value={next}
            onChange={(event) => setNext(event.target.value)}
          />
        </Field>
      </form>
    </Modal>
  )
}

export function AccountMenu() {
  const { t } = useTranslation()
  const { data: user } = useCurrentUser()
  const location = useLocation()
  const navigate = useNavigate()
  const logout = useLogout()

  const [open, setOpen] = useState(false)
  const [passwordOpen, setPasswordOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)

  // The panel is anchored to the bar, not the page: a route change would leave it
  // hanging over something it no longer belongs to.
  useEffect(() => setOpen(false), [location.pathname])

  useEffect(() => {
    if (!open) return

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setOpen(false)
      trigger.current?.focus()
    }
    const onPointerDown = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false)
    }

    document.addEventListener('keydown', onKeyDown)
    document.addEventListener('pointerdown', onPointerDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.removeEventListener('pointerdown', onPointerDown)
    }
  }, [open])

  if (!user) return null

  const name = user.display_name || user.email

  const onSignOut = () => {
    setOpen(false)
    logout.mutate(undefined, { onSettled: () => navigate('/login', { replace: true }) })
  }

  return (
    <div className="account-menu" ref={root}>
      <button
        ref={trigger}
        type="button"
        className="account-menu__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t('auth.account')}
        onClick={() => setOpen((current) => !current)}
      >
        <span className="account-menu__avatar" aria-hidden="true">
          {initials(name)}
        </span>
        <span className="account-menu__label">{name}</span>
        <Icon name="chevronDown" size={14} className="account-menu__caret" />
      </button>

      {open && (
        <div className="account-menu__panel" role="menu" aria-label={t('auth.account')}>
          <div className="account-menu__identity">
            <span className="account-menu__name">{name}</span>
            <span className="account-menu__email" dir="ltr">
              {user.email}
            </span>
            <span className="account-menu__meta">
              <Badge tone="accent">{t(`roles.${user.role}`)}</Badge>
              {user.team_name && <span className="account-menu__team">{user.team_name}</span>}
            </span>
          </div>

          <div className="account-menu__items">
            <button
              type="button"
              role="menuitem"
              className="account-menu__item"
              onClick={() => {
                setOpen(false)
                setPasswordOpen(true)
              }}
            >
              {t('auth.changePassword')}
            </button>

            {can(user, 'user.manage') && (
              <Link to="/admin/users" role="menuitem" className="account-menu__item">
                {t('admin.users')}
              </Link>
            )}

            <button
              type="button"
              role="menuitem"
              className="account-menu__item account-menu__item--danger"
              disabled={logout.isPending}
              onClick={onSignOut}
            >
              {t('auth.signOut')}
            </button>
          </div>
        </div>
      )}

      {passwordOpen && <PasswordDialog onClose={() => setPasswordOpen(false)} />}
    </div>
  )
}
