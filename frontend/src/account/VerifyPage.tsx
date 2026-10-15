import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { UseMutationResult } from '@tanstack/react-query'
import { useContext, useEffect } from 'react'
import { Link, useNavigate } from 'react-router'
import { auth } from '../api.ts'
import { ME_KEY, MeContext } from '../auth.ts'
import { Button, Notice, Spinner } from '../components/ui.tsx'
import { useLinkToken } from './linkToken.ts'
import { AuthCard } from './shared.tsx'

/**
 * The page a verification link opens, signed in or not. The token is redeemed as soon as the
 * page loads; a query rather than a mutation, so a second mount in development does not spend
 * it twice.
 */
export function VerifyPage() {
  const me = useContext(MeContext)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const token = useLinkToken()
  const verify = useQuery({
    queryKey: ['verify', token],
    queryFn: () => auth.verifyEmail(token),
    enabled: token !== '',
    retry: false,
    staleTime: Infinity,
  })
  const resend = useMutation({ mutationFn: auth.resendVerification })

  // The signed-in user's record says unverified until it is read again.
  useEffect(() => {
    if (verify.isSuccess && me) queryClient.invalidateQueries({ queryKey: ME_KEY })
  }, [verify.isSuccess, me, queryClient])

  if (!token) {
    return (
      <AuthCard title="This link is incomplete">
        <p className="text-[13px] leading-relaxed text-muted">
          The address is missing its token, which can happen when a link is copied by hand. Open the link from the
          email again{me ? ', or request a new one below' : ''}.
        </p>
        {me && <ResendButton resend={resend} />}
      </AuthCard>
    )
  }

  if (verify.isPending) {
    return (
      <AuthCard title="Confirming your email address">
        <div className="flex justify-center py-4">
          <Spinner className="size-5" />
        </div>
      </AuthCard>
    )
  }

  if (verify.isError) {
    return (
      <AuthCard title="This link no longer works">
        <Notice tone="bad">{verify.error.message}</Notice>
        {me ? (
          <ResendButton resend={resend} />
        ) : (
          <p className="mt-3 text-[13px] leading-relaxed text-muted">
            <Link to="/login" className="font-medium text-accent hover:underline">
              Sign in
            </Link>{' '}
            and request a new link from your account page.
          </p>
        )}
      </AuthCard>
    )
  }

  return (
    <AuthCard title="Email address confirmed" intro="Thanks. Your account is ready to use.">
      <Button variant="primary" className="w-full" onClick={() => navigate(me ? '/' : '/login')}>
        {me ? 'Continue to your projects' : 'Sign in'}
      </Button>
    </AuthCard>
  )
}

function ResendButton({ resend }: { resend: UseMutationResult<void, Error, void> }) {
  if (resend.isSuccess) {
    return <p className="mt-3 text-[13px] text-muted">A new link is on its way. It works for 24 hours.</p>
  }
  return (
    <div className="mt-3 space-y-2">
      <Button onClick={() => resend.mutate()} disabled={resend.isPending}>
        Send a new link
      </Button>
      {resend.isError && <Notice tone="bad">{resend.error.message}</Notice>}
    </div>
  )
}
