import { useQuery } from '@tanstack/react-query'
import { auth } from '../api.ts'

/**
 * What the sign-in pages can offer, asked once a page load: the identity providers set up, and
 * whether anyone may sign up, with the way to ask for an account while nobody may
 * (`GET /api/auth/options`, backend/app/signups.py).
 */
export function useAuthOptions() {
  return useQuery({ queryKey: ['auth-options'], queryFn: auth.options, staleTime: Infinity })
}
