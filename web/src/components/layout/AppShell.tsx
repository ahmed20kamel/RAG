import { Suspense, useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { ConversationSidebar } from '@/components/chat/ConversationSidebar'
import { Icon, type IconName } from '@/components/ui/Icon'
import { Button, IconButton, Kbd, Skeleton } from '@/components/ui/primitives'
import { cx } from '@/utils/cx'
import { Modal } from '@/components/ui/Modal'
import { AccountMenu } from '@/components/layout/AccountMenu'
import { useTranslation, type TranslationKey } from '@/hooks/useTranslation'
import { useHotkeys, modifierLabel } from '@/hooks/useHotkeys'
import { useIsMobile } from '@/hooks/useMediaQuery'
import { can, useCurrentUser } from '@/hooks/useAuth'
import { usePreferences } from '@/state/preferences'
import { useHealth } from '@/hooks/useSystem'
import type { Permission } from '@/types/auth'
import './shell.css'

interface NavItem {
  to: string
  icon: IconName
  label: TranslationKey
  /** Hidden unless the signed-in user holds it — the route would only 403. */
  permission?: Permission
}

const NAV: NavItem[] = [
  { to: '/chat', icon: 'chat', label: 'nav.chat' },
  { to: '/library', icon: 'library', label: 'nav.library' },
  { to: '/knowledge', icon: 'layers', label: 'knowledge.title', permission: 'knowledge.read' },
  { to: '/knowledge?mine=1', icon: 'sparkles', label: 'nav.learned', permission: 'knowledge.propose' },
  { to: '/candidates', icon: 'inbox', label: 'candidates.title', permission: 'knowledge.propose' },
  { to: '/upload', icon: 'upload', label: 'nav.upload' },
  { to: '/settings', icon: 'settings', label: 'nav.settings' },
  { to: '/admin/users', icon: 'shield', label: 'admin.users', permission: 'user.manage' },
  { to: '/admin/quality', icon: 'chart', label: 'quality.title', permission: 'system.monitor' },
  { to: '/admin/monitoring', icon: 'monitor', label: 'monitor.title', permission: 'system.monitor' },
]

function ThemeToggle() {
  const { t } = useTranslation()
  const theme = usePreferences((state) => state.theme)
  const cycleTheme = usePreferences((state) => state.cycleTheme)
  const icon: IconName = theme === 'light' ? 'sun' : theme === 'dark' ? 'moon' : 'monitor'
  return <IconButton icon={icon} label={t('theme.toggle')} onClick={cycleTheme} />
}

function LanguageToggle() {
  const { t, language } = useTranslation()
  const setLanguage = usePreferences((state) => state.setLanguage)
  return (
    <IconButton
      icon="globe"
      label={t('language.label')}
      onClick={() => setLanguage(language === 'ar' ? 'en' : 'ar')}
    />
  )
}

/** A dot in the top bar: green when every dependency answered, amber when one did not. */
function HealthDot() {
  const { data, isLoading, isError } = useHealth()
  const { t } = useTranslation()

  if (isLoading) return <Skeleton width="4.5rem" height="1.25rem" radius="999px" />

  const healthy = !isError && data?.status === 'ok'
  return (
    <NavLink to="/settings" className={cx('health-dot', healthy ? 'health-dot--ok' : 'health-dot--warn')}>
      <span className="health-dot__mark" />
      {healthy ? t('library.indexed') : t('library.degraded')}
    </NavLink>
  )
}

function ShortcutsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  const mod = modifierLabel()
  const rows: Array<[string, string[]]> = [
    [t('shortcuts.newChat'), [mod, 'K']],
    [t('shortcuts.focusComposer'), ['/']],
    [t('shortcuts.toggleTheme'), [mod, 'J']],
    [t('shortcuts.showHelp'), ['?']],
    [t('shortcuts.send'), ['Enter']],
    [t('shortcuts.newline'), ['Shift', 'Enter']],
    [t('shortcuts.close'), ['Esc']],
  ]
  return (
    <Modal open={open} title={t('shortcuts.title')} onClose={onClose}>
      <dl className="shortcut-list">
        {rows.map(([label, keys]) => (
          <div key={label} className="shortcut-list__row">
            <dt>{label}</dt>
            <dd>
              {keys.map((key) => (
                <Kbd key={key}>{key}</Kbd>
              ))}
            </dd>
          </div>
        ))}
      </dl>
    </Modal>
  )
}

export function AppShell() {
  const { t } = useTranslation()
  const location = useLocation()
  const isMobile = useIsMobile()
  const { data: user } = useCurrentUser()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [shortcutsOpen, setShortcutsOpen] = useState(false)
  const cycleTheme = usePreferences((state) => state.cycleTheme)

  // A route change closes the drawer, otherwise it would cover the page just opened.
  useEffect(() => setDrawerOpen(false), [location.pathname])

  useHotkeys([
    { key: 'j', ctrlOrMeta: true, whileTyping: true, handler: cycleTheme },
    { key: '?', shift: true, handler: () => setShortcutsOpen(true) },
  ])

  const navigate = useNavigate()
  // One sidebar, as in the chat assistants people already know: new chat and the
  // conversation history first, the system's other pages below them.
  const conversations = (
    <ConversationSidebar variant="rail" onSelect={() => { setDrawerOpen(false); navigate('/chat') }} />
  )
  const nav = (
    <nav className="rail__nav" aria-label={t('chat.tools')}>
      <p className="rail__section">{t('chat.tools')}</p>
      {NAV.filter((item) => item.to !== '/chat' && (!item.permission || can(user, item.permission))).map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          className={({ isActive }) => cx('rail__link', isActive && 'rail__link--active')}
        >
          <Icon name={item.icon} size={18} />
          <span>{t(item.label)}</span>
        </NavLink>
      ))}
    </nav>
  )

  return (
    <div className="shell">
      {!isMobile && (
        <aside className="rail">
          <NavLink to="/chat" className="rail__brand">
            <span className="rail__logo">
              <Icon name="sparkles" size={17} />
            </span>
            <span className="rail__brand-text">
              <strong>{t('app.name')}</strong>
              <small>{t('app.tagline')}</small>
            </span>
          </NavLink>
          {conversations}
          {nav}
          <div className="rail__footer">
            <Button variant="ghost" size="sm" icon="keyboard" block onClick={() => setShortcutsOpen(true)}>
              {t('shortcuts.title')}
            </Button>
          </div>
        </aside>
      )}

      {isMobile && drawerOpen && (
        <div className="drawer-backdrop" onClick={() => setDrawerOpen(false)}>
          <aside className="rail rail--drawer" onClick={(event) => event.stopPropagation()}>
            <div className="rail__brand">
              <span className="rail__logo">
                <Icon name="sparkles" size={17} />
              </span>
              <span className="rail__brand-text">
                <strong>{t('app.name')}</strong>
              </span>
            </div>
            {conversations}
            {nav}
          </aside>
        </div>
      )}

      <div className="shell__main">
        <header className="topbar">
          {isMobile && (
            <IconButton icon="menu" label={t('nav.openMenu')} onClick={() => setDrawerOpen(true)} />
          )}
          <div className="topbar__spacer" />
          {/* The system's own health is the administrator's concern; to anyone else a
              "degraded" badge only says something is wrong that they cannot fix. */}
          {can(user, 'system.monitor') && <HealthDot />}
          <LanguageToggle />
          <ThemeToggle />
          <AccountMenu />
        </header>

        <main className="shell__content">
          <Suspense fallback={<RouteFallback />}>
            <Outlet />
          </Suspense>
        </main>
      </div>

      <ShortcutsDialog open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />
    </div>
  )
}

function RouteFallback() {
  return (
    <div className="route-fallback">
      <Skeleton width="14rem" height="1.75rem" />
      <Skeleton width="100%" height="9rem" radius="14px" />
      <Skeleton width="100%" height="9rem" radius="14px" />
    </div>
  )
}
