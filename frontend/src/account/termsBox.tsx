import { useState } from 'react'
import { Checkbox, Notice } from '../components/ui.tsx'
import { useLegal } from '../hooks.ts'

/**
 * The box that accepts the terms of service and the privacy policy, as the page shows them, for a new account: the
 * sign-up link's page, an invitation's and the setup's. It accepts the edition on show, which the server records; a
 * newer one, fetched after a 409, unticks it. `version` is the edition ticked, while it is the one on show.
 */
export function useTermsBox() {
  const legal = useLegal()
  // The edition that was on show when the box was ticked: a newer one unticks it.
  const [acceptedVersion, setAcceptedVersion] = useState<string | null>(null)
  const version = acceptedVersion !== null && acceptedVersion === legal.data?.version ? acceptedVersion : null
  const box = (
    <>
      <Checkbox
        required
        disabled={!legal.data}
        checked={version !== null}
        onChange={(event) => setAcceptedVersion(event.target.checked ? (legal.data?.version ?? null) : null)}
        label={
          <>
            I accept the{' '}
            <a href="/terms" target="_blank" rel="noreferrer" className="font-medium text-accent hover:underline">
              terms of service
            </a>{' '}
            and have read the{' '}
            <a href="/privacy" target="_blank" rel="noreferrer" className="font-medium text-accent hover:underline">
              privacy policy
            </a>
            .
          </>
        }
      />
      {legal.isError && <Notice tone="bad">{legal.error.message}</Notice>}
    </>
  )
  return { ready: Boolean(legal.data), version, box }
}
