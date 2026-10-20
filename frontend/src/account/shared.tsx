// What the sign-in pages share: the page frame, the card the forms sit in, a password input
// that can show what was typed, and the buttons for signing in through a provider.

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import type { ReactNode } from 'react'
import { Link, Outlet } from 'react-router'
import { auth, oauthStartUrl } from '../api.ts'
import { ThemeToggle } from '../components/ThemeToggle.tsx'
import { InputField } from '../components/ui.tsx'
import type { InputFieldProps } from '../components/ui.tsx'
import { browserTimeZone } from '../timeZones.ts'

/**
 * The frame of every page outside the signed-in shell: the logo above a centred card, and below
 * it the pages anyone may read before signing up.
 */
export function AuthLayout() {
  return (
    <div className="flex min-h-dvh flex-col">
      <div className="flex justify-end p-3">
        <ThemeToggle />
      </div>
      <main className="flex flex-1 flex-col items-center px-4 pt-6 pb-10 sm:pt-12">
        <Link to="/" className="mb-6 flex items-center gap-2 rounded-md text-lg font-semibold tracking-tight">
          <img src="/favicon.svg" alt="" className="size-7" />
          Emudoc
        </Link>
        <Outlet />
      </main>
      <LegalLinks />
    </div>
  )
}

/** The terms, the privacy policy and the subprocessor list, at the foot of a page. */
export function LegalLinks() {
  const links = [
    { to: '/terms', label: 'Terms of service' },
    { to: '/privacy', label: 'Privacy policy' },
    { to: '/subprocessors', label: 'Subprocessors' },
  ]
  return (
    <nav className="flex flex-wrap justify-center gap-x-4 gap-y-1 px-4 pb-6 text-xs text-muted">
      {links.map((link) => (
        <Link key={link.to} to={link.to} className="hover:text-ink hover:underline">
          {link.label}
        </Link>
      ))}
    </nav>
  )
}

interface AuthCardProps {
  title: string
  intro?: ReactNode
  children: ReactNode
  /** A line under the card, usually the way to the other page: sign in or sign up. */
  footer?: ReactNode
}

export function AuthCard({ title, intro, children, footer }: AuthCardProps) {
  return (
    <div className="w-full max-w-sm">
      <div className="rounded-xl border border-line bg-surface p-6 shadow-xs">
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        {intro && <p className="mt-1 text-[13px] leading-relaxed text-muted">{intro}</p>}
        <div className="mt-5">{children}</div>
      </div>
      {footer && <p className="mt-4 text-center text-[13px] text-muted">{footer}</p>}
    </div>
  )
}

/** A password input with a switch to show what was typed, instead of asking for it twice. */
export function PasswordField({ corner, ...props }: Omit<InputFieldProps, 'type'>) {
  const [shown, setShown] = useState(false)
  return (
    <InputField
      type={shown ? 'text' : 'password'}
      corner={
        <span className="flex gap-3">
          {corner}
          <button type="button" onClick={() => setShown(!shown)} className="text-muted hover:text-ink">
            {shown ? 'Hide' : 'Show'}
          </button>
        </span>
      }
      {...props}
    />
  )
}

// The marks of the providers the server can offer. Brand marks keep their own colours.
const PROVIDER_ICONS: Record<string, ReactNode> = {
  google: (
    <svg viewBox="0 0 24 24" className="size-4" aria-hidden>
      <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" />
      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
      <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18A11 11 0 0 0 1 12c0 1.78.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
      <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
    </svg>
  ),
  microsoft: (
    <svg viewBox="0 0 24 24" className="size-4" aria-hidden>
      <path fill="#F25022" d="M2 2h9.5v9.5H2z" />
      <path fill="#7FBA00" d="M12.5 2H22v9.5h-9.5z" />
      <path fill="#00A4EF" d="M2 12.5h9.5V22H2z" />
      <path fill="#FFB900" d="M12.5 12.5H22V22h-9.5z" />
    </svg>
  ),
}

/** One button per identity provider the server is set up with; nothing when there is none. */
export function ProviderButtons({ next, verb }: { next: string; verb: string }) {
  const options = useQuery({ queryKey: ['auth-options'], queryFn: auth.options, staleTime: Infinity })
  const providers = options.data?.providers ?? []
  if (!providers.length) return null
  return (
    <>
      <div className="space-y-2">
        {providers.map((provider) => (
          <a
            key={provider.name}
            href={oauthStartUrl(provider.name, next, browserTimeZone())}
            className="flex h-9 w-full items-center justify-center gap-2.5 rounded-md border border-line bg-surface text-[13px] font-medium shadow-xs transition hover:bg-sunken"
          >
            {PROVIDER_ICONS[provider.name]}
            {verb} with {provider.label}
          </a>
        ))}
      </div>
      <div className="my-5 flex items-center gap-3 text-xs text-muted">
        <span className="h-px flex-1 bg-line" />
        or
        <span className="h-px flex-1 bg-line" />
      </div>
    </>
  )
}
