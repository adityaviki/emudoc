// The page an invitation's link opens (backend/app/invitations.py): who invited the address to
// which organisation and as what, and the way to accept: joining, signed in with the address;
// signing in first, when the address has an account; or making one here or through a provider,
// which joins that organisation and starts none of its own. The token is kept in the tab while
// the user signs in, so that the page finds it again when they come back.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useContext } from 'react'
import type { ReactNode } from 'react'
import { Link, useNavigate } from 'react-router'
import { ApiError, ROLE_LABELS, auth } from '../api.ts'
import type { InvitationLink, Me } from '../api.ts'
import { MeContext, forgetUser, loginPath, setSignedIn, signOut, startAgainInOrganization } from '../auth.ts'
import { Button, Notice } from '../components/ui.tsx'
import { formatDate } from '../format.ts'
import { browserTimeZone } from '../timeZones.ts'
import { forgetLinkToken, useLinkToken } from './linkToken.ts'
import { NewAccountForm, OpeningLink } from './SignupPage.tsx'
import type { NewAccount } from './SignupPage.tsx'
import { AuthCard, ProviderButtons } from './shared.tsx'

const PATH = '/invitation'
// Where the token waits in the tab's session storage while its user signs in.
const KEPT = 'invitation-token'

const TO_PROJECTS = (
  <Link to="/" className="font-medium text-accent hover:underline">
    Continue to your projects
  </Link>
)
const TO_SIGN_IN = (
  <Link to="/login" className="font-medium text-accent hover:underline">
    Sign in
  </Link>
)

export function InvitationPage() {
  const me = useContext(MeContext)
  const token = useLinkToken(KEPT)
  const link = useQuery({
    queryKey: ['invitation', token],
    queryFn: () => auth.invitation(token),
    enabled: token !== '',
    retry: false,
    staleTime: Infinity,
  })

  if (!token) {
    return (
      <AuthCard title="This link is incomplete" footer={me ? TO_PROJECTS : TO_SIGN_IN}>
        <p className="text-[13px] leading-relaxed text-muted">
          The address is missing its token, which can happen when a link is copied by hand. Open the link from the email
          again.
        </p>
      </AuthCard>
    )
  }
  if (link.isPending) return <OpeningLink />
  if (link.isError) {
    return (
      <AuthCard title="This invitation no longer works" footer={me ? TO_PROJECTS : TO_SIGN_IN}>
        <Notice tone="bad">{link.error.message}</Notice>
      </AuthCard>
    )
  }

  const invitation = link.data
  if (invitation.expired) {
    return (
      <AuthCard title="This invitation has expired" intro={<Invited invitation={invitation} />} footer={me ? TO_PROJECTS : TO_SIGN_IN}>
        <p className="text-[13px] leading-relaxed text-muted">
          It ran out on {formatDate(invitation.expires_at)}. {invitation.invited_by || 'Whoever invited you'} can send it
          again from the organisation's page: ask them to.
        </p>
      </AuthCard>
    )
  }
  if (me) {
    return me.email === invitation.email ? (
      <Accept token={token} invitation={invitation} />
    ) : (
      <SignedInElsewhere me={me} invitation={invitation} />
    )
  }
  return invitation.has_account ? <SignInFirst invitation={invitation} /> : <SignUp token={token} invitation={invitation} />
}

/** Who invited the address to which organisation, and as what. */
function Invited({ invitation, after }: { invitation: InvitationLink; after?: ReactNode }) {
  const role = ROLE_LABELS[invitation.role].toLowerCase()
  return (
    <>
      {invitation.invited_by || 'A member'} invited <span className="font-medium text-ink">{invitation.email}</span> to
      join <span className="font-medium text-ink">{invitation.organization}</span> as {/^[aeiou]/.test(role) ? 'an' : 'a'}{' '}
      {role}.{after && <> {after}</>}
    </>
  )
}

function Accept({ token, invitation }: { token: string; invitation: InvitationLink }) {
  const accept = useMutation({
    mutationFn: () => auth.acceptInvitation(token),
    // The session works in the organisation from now on: the app starts again there.
    onSuccess: () => {
      forgetLinkToken(KEPT)
      startAgainInOrganization()
    },
  })
  return (
    <AuthCard
      title={`Join ${invitation.organization}`}
      intro={<Invited invitation={invitation} after="You work in it from then on, beside your other organisations." />}
      footer={TO_PROJECTS}
    >
      <div className="space-y-3">
        {accept.isError && <Notice tone="bad">{accept.error.message}</Notice>}
        <Button variant="primary" className="w-full" onClick={() => accept.mutate()} disabled={accept.isPending}>
          Join {invitation.organization}
        </Button>
      </div>
    </AuthCard>
  )
}

function SignedInElsewhere({ me, invitation }: { me: Me; invitation: InvitationLink }) {
  const queryClient = useQueryClient()
  // Signing out here leaves the page as it is, for the right account to sign in or be made.
  const leave = useMutation({ mutationFn: () => signOut(queryClient) })
  return (
    <AuthCard title={`Join ${invitation.organization}`} intro={<Invited invitation={invitation} />} footer={TO_PROJECTS}>
      <div className="space-y-3">
        <Notice tone="warn">
          This browser is signed in as {me.email}. Only an account with {invitation.email} can accept the invitation: sign
          out to sign in with it, or to create it.
        </Notice>
        <Button className="w-full" onClick={() => leave.mutate()} disabled={leave.isPending}>
          Sign out
        </Button>
      </div>
    </AuthCard>
  )
}

function SignInFirst({ invitation }: { invitation: InvitationLink }) {
  const navigate = useNavigate()
  return (
    <AuthCard title={`Join ${invitation.organization}`} intro={<Invited invitation={invitation} />}>
      <div className="space-y-3">
        <p className="text-[13px] leading-relaxed text-muted">
          {invitation.email} has an Emudoc account. Sign in with it to accept; you come back here.
        </p>
        <Button variant="primary" className="w-full" onClick={() => navigate(loginPath(PATH))}>
          Sign in to accept
        </Button>
      </div>
    </AuthCard>
  )
}

function SignUp({ token, invitation }: { token: string; invitation: InvitationLink }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const signUp = useMutation({
    mutationFn: (fields: NewAccount) => auth.signUpInvited({ token, ...fields, time_zone: browserTimeZone() }),
    onSuccess: (account) => {
      forgetLinkToken(KEPT)
      forgetUser(queryClient)
      setSignedIn(queryClient, account)
      navigate('/', { replace: true })
    },
    // The terms may have changed while the page was open; fetching them again unticks the box.
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) void queryClient.invalidateQueries({ queryKey: ['legal'] })
    },
  })
  return (
    <AuthCard
      title={`Join ${invitation.organization}`}
      intro={<Invited invitation={invitation} after="Create your account to accept." />}
      footer="Have an account with another address? Ask for an invitation to that one."
    >
      <ProviderButtons next={PATH} verb="Sign up" invitation={token} />
      <NewAccountForm
        email={invitation.email}
        pending={signUp.isPending}
        onSubmit={(fields) => signUp.mutate(fields)}
        error={signUp.isError && <Notice tone="bad">{signUp.error.message}</Notice>}
      />
    </AuthCard>
  )
}
