// Who is signed in. The app asks the server once at start and keeps the answer; the signed-in
// shell hands it to its pages through a context, and sign-in, sign-up and sign-out change it.

import { useQuery } from '@tanstack/react-query'
import { createContext, useCallback, useContext } from 'react'
import type { QueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { auth, showOrganization } from './api.ts'
import type { Me } from './api.ts'

export const ME_KEY = ['me'] as const

/** The signed-in user, null when there is none, undefined until the server has answered. */
export function useMeQuery() {
  return useQuery({ queryKey: ME_KEY, queryFn: whoIsSignedIn, staleTime: Infinity, retry: false })
}

/** Ask the server who is signed in, and name their organisation in every request from now on. */
async function whoIsSignedIn(): Promise<Me | null> {
  const me = await auth.me()
  showOrganization(me?.organization.id ?? null)
  return me
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
  showOrganization(me?.organization.id ?? null)
  queryClient.setQueryData(ME_KEY, me)
}

/**
 * Start the app again in the organisation the session works in now, after a switch here or in
 * another tab: a page load, so that nothing fetched in the one before stays in the cache, in a
 * component's state or in an answer being followed, and the page starts from the front.
 */
export function startAgainInOrganization() {
  window.location.assign('/')
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
  forgetUser(queryClient)
  // Should this fail, the cookie is already useless to the app and the session expires by itself.
  await auth.logout()
}

/**
 * Drop the user and everything fetched for them, as when the server stopped accepting the
 * session: whoever signs in next in this tab, a colleague included, starts from nothing rather
 * than from the last user's chats and documents.
 */
export function forgetUser(queryClient: QueryClient) {
  queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== ME_KEY[0] })
  setSignedIn(queryClient, null)
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
