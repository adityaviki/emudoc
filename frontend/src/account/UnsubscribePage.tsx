import { useMutation } from '@tanstack/react-query'
import { useContext } from 'react'
import { Link } from 'react-router'
import { auth } from '../api.ts'
import { MeContext } from '../auth.ts'
import { Button, Notice } from '../components/ui.tsx'
import { useLinkToken } from './linkToken.ts'
import { AuthCard } from './shared.tsx'

/**
 * The page the link at the foot of a notice opens, signed in or not: the token says whose
 * notices stop. They stop only when the button is pressed, since a mail filter that opens every
 * link would otherwise stop them for its reader; a mail program's own "unsubscribe" posts to the
 * same route by the notice's headers, and needs no page.
 */
export function UnsubscribePage() {
  const me = useContext(MeContext)
  const token = useLinkToken()
  const stop = useMutation({ mutationFn: () => auth.unsubscribe(token) })
  const account = me ? (
    <Link to="/account" className="font-medium text-accent hover:underline">
      your account page
    </Link>
  ) : (
    <>
      your account page once you{' '}
      <Link to="/login" className="font-medium text-accent hover:underline">
        sign in
      </Link>
    </>
  )

  if (!token) {
    return (
      <AuthCard title="This link is incomplete">
        <p className="text-[13px] leading-relaxed text-muted">
          The address is missing its token, which can happen when a link is copied by hand. Open the link from the
          email again, or turn notices off on {account}.
        </p>
      </AuthCard>
    )
  }

  if (stop.isSuccess) {
    return (
      <AuthCard title="Notices are off">
        <p className="text-[13px] leading-relaxed text-muted">
          Emudoc sends you no more notices. Email about your account, invitations and billing still comes. To have
          notices again, turn them on on {account}.
        </p>
      </AuthCard>
    )
  }

  return (
    <AuthCard
      title="Stop the notices"
      intro="Emudoc will send you no more notices about your work. Email about your account, invitations and billing still comes."
    >
      {stop.isError && (
        <div className="mb-3">
          <Notice tone="bad">{stop.error.message}</Notice>
        </div>
      )}
      <Button variant="primary" className="w-full" onClick={() => stop.mutate()} disabled={stop.isPending}>
        Stop the notices
      </Button>
    </AuthCard>
  )
}
