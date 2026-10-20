import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useContext, useEffect } from 'react'
import { Link, useNavigate } from 'react-router'
import { auth } from '../api.ts'
import { ME_KEY, MeContext } from '../auth.ts'
import { Button, Notice, Spinner } from '../components/ui.tsx'
import { useLinkToken } from './linkToken.ts'
import { AuthCard } from './shared.tsx'

/**
 * The page a change-of-address link opens, signed in or not: opening it makes the address it
 * was sent to the account's. Redeemed as the page loads, through a query resolving to true,
 * for the reasons VerifyPage gives.
 */
export function ConfirmEmailPage() {
  const me = useContext(MeContext)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const token = useLinkToken()
  const confirm = useQuery({
    queryKey: ['confirm-email', token],
    queryFn: () => auth.confirmEmailChange(token).then(() => true),
    enabled: token !== '',
    retry: false,
    staleTime: Infinity,
  })

  // The signed-in user's record holds the old address until it is read again.
  useEffect(() => {
    if (confirm.isSuccess && me) queryClient.invalidateQueries({ queryKey: ME_KEY })
  }, [confirm.isSuccess, me, queryClient])

  if (!token) {
    return (
      <AuthCard title="This link is incomplete">
        <p className="text-[13px] leading-relaxed text-muted">
          The address is missing its token, which can happen when a link is copied by hand. Open the link from the
          email again.
        </p>
      </AuthCard>
    )
  }

  if (confirm.isPending) {
    return (
      <AuthCard title="Changing your email address">
        <div className="flex justify-center py-4">
          <Spinner className="size-5" />
        </div>
      </AuthCard>
    )
  }

  if (confirm.isError) {
    return (
      <AuthCard title="This link no longer works">
        <Notice tone="bad">{confirm.error.message}</Notice>
        <p className="mt-3 text-[13px] leading-relaxed text-muted">
          Your account keeps the address it had.{' '}
          <Link to={me ? '/account' : '/login'} className="font-medium text-accent hover:underline">
            {me ? 'Ask for the change again' : 'Sign in'}
          </Link>
          {me ? ' from your account page.' : ' and ask for the change again from your account page.'}
        </p>
      </AuthCard>
    )
  }

  return (
    <AuthCard
      title="Email address changed"
      intro="Your account's address is now the one this link was sent to. Sign in with it from now on."
    >
      <Button variant="primary" className="w-full" onClick={() => navigate(me ? '/account' : '/login')}>
        {me ? 'Back to your account' : 'Sign in'}
      </Button>
    </AuthCard>
  )
}
