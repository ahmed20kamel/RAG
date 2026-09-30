import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Field, Input } from '@/components/ui/primitives'
import { Icon } from '@/components/ui/Icon'
import { useCurrentUser, useLogin } from '@/hooks/useAuth'
import { useTranslation } from '@/hooks/useTranslation'
import { usePreferences } from '@/state/preferences'
import { ApiError } from '@/services/client'
import './login.css'

export function LoginPage() {
  const { t, language } = useTranslation()
  const setLanguage = usePreferences((state) => state.setLanguage)
  const navigate = useNavigate()
  const { data: user } = useCurrentUser()
  const login = useLogin()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  // Someone who is already signed in has no business on this page — arriving here by
  // a stale bookmark should land them in the app, not at a second login.
  useEffect(() => {
    if (user) navigate('/chat', { replace: true })
  }, [user, navigate])

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!email.trim() || !password) return
    login.mutate(
      { email: email.trim(), password },
      { onSuccess: () => navigate('/chat', { replace: true }) },
    )
  }

  const message =
    login.error instanceof ApiError
      ? login.error.status === 401
        ? t('auth.invalid')
        : login.error.message
      : login.error
        ? t('errors.generic')
        : null

  return (
    <div className="login">
      <button
        type="button"
        className="login__lang"
        onClick={() => setLanguage(language === 'ar' ? 'en' : 'ar')}
      >
        <Icon name="globe" size={15} />
        {language === 'ar' ? 'English' : 'العربية'}
      </button>

      <form className="login__card" onSubmit={onSubmit}>
        <span className="login__logo">
          <Icon name="sparkles" size={22} />
        </span>
        <h1 className="login__title">{t('auth.welcome')}</h1>
        <p className="login__body">{t('auth.welcomeBody')}</p>

        <Field label={t('auth.email')} htmlFor="email">
          <Input
            id="email"
            type="email"
            autoComplete="username"
            autoFocus
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            dir="ltr"
          />
        </Field>

        <Field label={t('auth.password')} htmlFor="password">
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            dir="ltr"
          />
        </Field>

        {message && (
          <p className="login__error" role="alert">
            <Icon name="alert" size={15} />
            {message}
          </p>
        )}

        <Button
          type="submit"
          variant="primary"
          size="lg"
          block
          loading={login.isPending}
          disabled={!email.trim() || !password}
        >
          {login.isPending ? t('auth.signingIn') : t('auth.signIn')}
        </Button>
      </form>
    </div>
  )
}
