import { LoaderCircle } from 'lucide-react'
import type { ButtonHTMLAttributes, ReactNode } from 'react'

const BUTTON_VARIANTS = {
  primary: 'bg-accent text-white hover:opacity-90 dark:text-page',
  secondary: 'border border-line bg-surface text-ink hover:bg-sunken',
  ghost: 'text-muted hover:bg-sunken hover:text-ink',
  danger: 'text-bad hover:bg-sunken',
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: keyof typeof BUTTON_VARIANTS
  icon?: ReactNode
}

export function Button({ variant = 'secondary', icon, children, className = '', ...props }: ButtonProps) {
  return (
    <button
      type="button"
      className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${BUTTON_VARIANTS[variant]} ${className}`}
      {...props}
    >
      {icon}
      {children}
    </button>
  )
}

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string
  danger?: boolean
}

export function IconButton({ label, danger, children, className = '', ...props }: IconButtonProps) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      className={`rounded-md p-1.5 text-muted transition hover:bg-sunken disabled:opacity-40 ${danger ? 'hover:text-bad' : 'hover:text-ink'} ${className}`}
      {...props}
    >
      {children}
    </button>
  )
}

export function Chip({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'accent' }) {
  const tones = {
    neutral: 'bg-sunken text-muted',
    accent: 'bg-accent-soft text-accent',
  }
  return (
    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium ${tones[tone]}`}>
      {children}
    </span>
  )
}

export function Spinner({ className = 'size-4' }: { className?: string }) {
  return <LoaderCircle className={`animate-spin text-muted ${className}`} aria-label="Loading" />
}

export function Loading() {
  return (
    <div className="flex justify-center py-16">
      <Spinner className="size-5" />
    </div>
  )
}

export function EmptyState({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-16 text-center">
      {icon && <div className="text-muted">{icon}</div>}
      <p className="font-medium">{title}</p>
      {children && <div className="max-w-md text-sm text-muted">{children}</div>}
    </div>
  )
}

export function Notice({ tone = 'warn', children }: { tone?: 'warn' | 'bad' | 'info'; children: ReactNode }) {
  const tones = {
    warn: 'border-warn/30 text-warn',
    bad: 'border-bad/30 text-bad',
    info: 'border-line text-muted',
  }
  return <div className={`rounded-md border bg-surface px-3 py-2 text-sm ${tones[tone]}`}>{children}</div>
}
