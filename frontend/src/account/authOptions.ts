import { useQuery } from '@tanstack/react-query'
import { auth } from '../api.ts'

export const AUTH_OPTIONS_KEY = ['auth-options']

/**
 * What the sign-in pages can offer, asked once a page load: the identity providers set up,
 * whether anyone may sign up, with the way to ask for an account while nobody may, and whether
 * the install has no account yet, which sends every page to the setup (`GET /api/auth/options`,
 * backend/app/signups.py). The setup marks it set up once done.
 */
export function useAuthOptions() {
  return useQuery({ queryKey: AUTH_OPTIONS_KEY, queryFn: auth.options, staleTime: Infinity })
}
