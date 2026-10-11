// Who is signed in. The app asks the server once at start and keeps the answer; the signed-in
// shell hands it to its pages through a context, and sign-in, sign-up and sign-out change it.

import { useQuery } from '@tanstack/react-query'
import { createContext, useCallback, useContext } from 'react'
import type { QueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { auth } from './api.ts'
import type { Me } from './api.ts'

export const ME_KEY = ['me'] as const

/** The signed-in user, null when there is none, undefined until the server has answered. */
export function useMeQuery() {
  return useQuery({ queryKey: ME_KEY, queryFn: auth.me, staleTime: Infinity, retry: false })
}

export const MeContext = createContext<Me | null>(null)

/** The signed-in user. Only pages inside the signed-in shell may ask, so there always is one. */
export function useMe(): Me {
  const me = useContext(MeContext)
  if (!me) throw new Error('useMe() was called outside the signed-in shell.')
  return me
}

/** Remember who just signed in, so the shell renders without another round trip. */
export function setSignedIn(queryClient: QueryClient, me: Me | null) {
  queryClient.setQueryData(ME_KEY, me)
}

/**
 * Sign out here. The session ends on the sign-in page, not before leaving: the shell answers a
 * vanished user with a redirect that remembers the page, and the router applies the move to
 * `/login` later than that, so a sign-out that forgot the user first would keep a `next`.
 */
export function useSignOut() {
  const navigate = useNavigate()
  return useCallback(() => navigate('/login', { replace: true, state: { signOut: true } }), [navigate])
}

/** End the session and drop everything cached for the user; the sign-in page runs this. */
export async function signOut(queryClient: QueryClient) {
  queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== ME_KEY[0] })
  setSignedIn(queryClient, null)
  // Should this fail, the cookie is already useless to the app and the session expires by itself.
  await auth.logout()
}

/** The sign-in page, returning to `next` afterwards; the front page needs no parameter. */
export function loginPath(next: string): string {
  return next === '/' ? '/login' : `/login?${new URLSearchParams({ next })}`
}

/** The path to go to after signing in: the one asked for, if it is on this site, else home. */
export function safeNext(next: string | null): string {
  return next && next.startsWith('/') && !next.startsWith('//') ? next : '/'
}

/** Where the browser is now, as a `next` parameter. */
export function currentPath(): string {
  return window.location.pathname + window.location.search
}
