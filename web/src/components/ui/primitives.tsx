import {
  forwardRef,
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react'
import { Icon, type IconName } from './Icon'
import { cx } from '@/utils/cx'
import './ui.css'

/* Button ------------------------------------------------------------------ */

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
export type ButtonSize = 'sm' | 'md' | 'lg'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  icon?: IconName
  iconEnd?: IconName
  block?: boolean
  loading?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', icon, iconEnd, block, loading, children, className, disabled, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      className={cx('btn', `btn--${variant}`, `btn--${size}`, block && 'btn--block', className)}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <Spinner size={14} /> : icon ? <Icon name={icon} size={size === 'lg' ? 18 : 16} /> : null}
      {children}
      {iconEnd && !loading ? <Icon name={iconEnd} size={size === 'lg' ? 18 : 16} /> : null}
    </button>
  )
})

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon: IconName
  label: string
  size?: 'sm' | 'md'
  active?: boolean
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { icon, label, size = 'md', active, className, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      className={cx('icon-btn', `icon-btn--${size}`, active && 'icon-btn--active', className)}
      aria-label={label}
      title={label}
      {...rest}
    >
      <Icon name={icon} size={size === 'sm' ? 15 : 17} />
    </button>
  )
})

/* Card -------------------------------------------------------------------- */

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  padded?: boolean
  raised?: boolean
}

export function Card({ padded, raised, className, children, ...rest }: CardProps) {
  return (
    <div className={cx('card', padded && 'card--padded', raised && 'card--raised', className)} {...rest}>
      {children}
    </div>
  )
}

export function CardHeader({ title, action }: { title: ReactNode; action?: ReactNode }) {
  return (
    <div className="card__header">
      <div className="card__title">{title}</div>
      {action}
    </div>
  )
}

/* Badge ------------------------------------------------------------------- */

export type BadgeTone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger' | 'info'

export function Badge({
  tone = 'neutral',
  dot,
  children,
  className,
  ...rest
}: HTMLAttributes<HTMLSpanElement> & { tone?: BadgeTone; dot?: boolean }) {
  return (
    <span className={cx('badge', `badge--${tone}`, className)} {...rest}>
      {dot && <span className="badge__dot" />}
      {children}
    </span>
  )
}

/* Fields ------------------------------------------------------------------ */

export function Field({
  label,
  hint,
  htmlFor,
  children,
}: {
  label?: string
  hint?: string
  htmlFor?: string
  children: ReactNode
}) {
  return (
    <div className="field">
      {label && (
        <label className="field__label" htmlFor={htmlFor}>
          {label}
        </label>
      )}
      {children}
      {hint && <span className="field__hint">{hint}</span>}
    </div>
  )
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...rest }, ref) {
    return <input ref={ref} className={cx('input', className)} {...rest} />
  },
)

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...rest }, ref) {
    return <textarea ref={ref} className={cx('textarea', className)} {...rest} />
  },
)

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  function Select({ className, children, ...rest }, ref) {
    return (
      <select ref={ref} className={cx('select', className)} {...rest}>
        {children}
      </select>
    )
  },
)

export const SearchField = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function SearchField({ className, ...rest }, ref) {
    return (
      <div className={cx('search-field', className)}>
        <span className="search-field__icon">
          <Icon name="search" size={15} />
        </span>
        <Input ref={ref} type="search" {...rest} />
      </div>
    )
  },
)

/* Feedback ---------------------------------------------------------------- */

export function Spinner({ size = 16 }: { size?: number }) {
  return <span className="spinner" style={{ inlineSize: size, blockSize: size }} role="presentation" />
}

export function Skeleton({
  width,
  height = '1rem',
  radius,
  className,
}: {
  width?: string | number
  height?: string | number
  radius?: string
  className?: string
}) {
  return (
    <span
      className={cx('skeleton', className)}
      style={{ display: 'block', inlineSize: width, blockSize: height, borderRadius: radius }}
      aria-hidden="true"
    />
  )
}

export function EmptyState({
  icon = 'inbox',
  title,
  body,
  action,
}: {
  icon?: IconName
  title: string
  body?: string
  action?: ReactNode
}) {
  return (
    <div className="state">
      <span className="state__icon">
        <Icon name={icon} size={22} />
      </span>
      <span className="state__title">{title}</span>
      {body && <p className="state__body">{body}</p>}
      {action}
    </div>
  )
}

export function ErrorState({ title, body, action }: { title: string; body?: string; action?: ReactNode }) {
  return (
    <div className="state" role="alert">
      <span className="state__icon state__icon--danger">
        <Icon name="alert" size={22} />
      </span>
      <span className="state__title">{title}</span>
      {body && <p className="state__body">{body}</p>}
      {action}
    </div>
  )
}

export function Progress({ value, label }: { value?: number; label?: string }) {
  const indeterminate = value === undefined
  return (
    <div
      className={cx('progress', indeterminate && 'progress--indeterminate')}
      role="progressbar"
      aria-label={label}
      aria-valuenow={indeterminate ? undefined : Math.round(value * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div className="progress__bar" style={indeterminate ? undefined : { inlineSize: `${value * 100}%` }} />
    </div>
  )
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd">{children}</kbd>
}

export function Tooltip({ label, children }: { label: string; children: ReactNode }) {
  return (
    <span className="tooltip-host" data-tooltip={label}>
      {children}
    </span>
  )
}

/* Tabs -------------------------------------------------------------------- */

export interface TabItem<T extends string> {
  id: T
  label: string
  count?: number
}

export function Tabs<T extends string>({
  items,
  active,
  onChange,
}: {
  items: TabItem<T>[]
  active: T
  onChange: (id: T) => void
}) {
  return (
    <div className="tabs" role="tablist">
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          role="tab"
          className="tab"
          aria-selected={item.id === active}
          onClick={() => onChange(item.id)}
        >
          {item.label}
          {item.count !== undefined && <span className="tab__count">{item.count}</span>}
        </button>
      ))}
    </div>
  )
}

/* Segmented control ------------------------------------------------------- */

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: Array<{ value: T; label: string; icon?: IconName }>
  value: T
  onChange: (value: T) => void
  label: string
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className="segmented__option"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.icon && <Icon name={option.icon} size={14} />}
          {option.label}
        </button>
      ))}
    </div>
  )
}
