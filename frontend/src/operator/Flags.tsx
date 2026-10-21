// The feature flags across the deployment, as the `flags` command prints them: how far each is
// rolled out, how many organisations it is on for, and those an operator turned it on or off for.

import { useQuery } from '@tanstack/react-query'
import { Fragment } from 'react'
import { Link } from 'react-router'
import { Loading, Notice } from '../components/ui.tsx'
import { operatorApi } from './api.ts'
import type { FlagRollout } from './api.ts'
import { Cell, Heading, Table } from './Table.tsx'
import { rolledOut } from './words.ts'

export function FlagsPage() {
  const flags = useQuery({ queryKey: ['operator', 'flags'], queryFn: operatorApi.flags })
  return (
    <>
      <Heading title="Feature flags" command="flags">
        The features being rolled out (FLAGS in backend/app/flags.py): each is on for the share of organisations its rollout takes, which a release
        moves, and for those it was turned on for on their page here.
      </Heading>
      {flags.isPending ? (
        <Loading />
      ) : flags.isError ? (
        <Notice tone="bad">{flags.error.message}</Notice>
      ) : flags.data.length === 0 ? (
        <Notice tone="info">No feature is behind a flag: FLAGS in backend/app/flags.py names none.</Notice>
      ) : (
        <Table columns={[{ label: 'Flag' }, { label: 'Rolled out to' }, { label: 'On for', figure: true }, { label: 'Turned on for' }, { label: 'Turned off for' }]}>
          {flags.data.map((flag) => (
            <tr key={flag.name}>
              <Cell>
                <span className="font-mono text-xs">{flag.name}</span>
                <span className="block text-muted">{flag.title}</span>
              </Cell>
              <Cell className="text-muted">{rolledOut(flag.share)}</Cell>
              <Cell figure>
                {flag.on.toLocaleString('en')} of {flag.organizations.toLocaleString('en')}
              </Cell>
              <Cell>
                <Chosen flag={flag} on />
              </Cell>
              <Cell>
                <Chosen flag={flag} on={false} />
              </Cell>
            </tr>
          ))}
        </Table>
      )}
    </>
  )
}

/** The organisations a flag was turned on, or off, for, each a link to its page, where the choice is changed. */
function Chosen({ flag, on }: { flag: FlagRollout; on: boolean }) {
  const chosen = flag.chosen.filter((organization) => organization.on === on)
  if (chosen.length === 0) return <span className="text-muted">—</span>
  return chosen.map((organization, index) => (
    <Fragment key={organization.org_id}>
      {index > 0 && ', '}
      <Link to={`/operator/organizations/${organization.org_id}`} className="text-accent hover:underline" title={organization.slug}>
        {organization.name}
      </Link>
    </Fragment>
  ))
}
