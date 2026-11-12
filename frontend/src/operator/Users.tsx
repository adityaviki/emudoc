// The users, found by a part of their address or name: their organisations and roles, and how
// they sign in, for the operator answering one of them.

import { useQuery } from '@tanstack/react-query'
import type { FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router'
import { Button, Chip, FIELD, Loading, Notice } from '../components/ui.tsx'
import { formatDate, formatDateTime } from '../format.ts'
import { operatorApi } from './api.ts'
import { Cell, Heading, Table } from './Table.tsx'

export function UsersPage() {
  const [search, setSearch] = useSearchParams()
  const q = search.get('q') ?? ''
  const users = useQuery({ queryKey: ['operator', 'users', q], queryFn: () => operatorApi.users(q) })
  const find = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const given = String(new FormData(event.currentTarget).get('q') ?? '').trim()
    setSearch(given ? { q: given } : {})
  }
  return (
    <>
      <Heading title="Users">
        {q ? 'The users whose address or name holds what you looked for, the newest first.' : 'The newest users first.'}
      </Heading>
      <form onSubmit={find} className="mb-4 flex max-w-md gap-2" role="search">
        <input key={q} name="q" defaultValue={q} aria-label="Find users" placeholder="A part of an address or a name" className={FIELD} />
        <Button type="submit">Find</Button>
      </form>
      {users.isPending ? (
        <Loading />
      ) : users.isError ? (
        <Notice tone="bad">{users.error.message}</Notice>
      ) : users.data.users.length === 0 ? (
        <Notice tone="info">No user matches “{q}”.</Notice>
      ) : (
        <>
          <Table
            columns={[{ label: 'User' }, { label: 'Organisations' }, { label: 'Two-factor' }, { label: 'Last seen' }, { label: 'Joined' }]}
          >
            {users.data.users.map((user) => (
              <tr key={user.id}>
                <Cell>
                  <div className="font-medium">{user.name || '—'}</div>
                  <div className="text-muted">
                    {user.email} · {user.id}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {user.operator && <Chip tone="accent">operator</Chip>}
                    {!user.email_verified && <Chip>address unconfirmed</Chip>}
                    {user.deletion_due_at && <Chip>deleted {formatDate(user.deletion_due_at)}</Chip>}
                  </div>
                </Cell>
                <Cell>
                  {user.memberships.length === 0
                    ? '—'
                    : user.memberships.map((membership) => (
                        <div key={membership.org_id}>
                          <Link to={`/operator/organizations/${membership.org_id}`} className="text-accent hover:underline">
                            {membership.slug}
                          </Link>{' '}
                          <span className="text-muted">{membership.role}</span>
                        </div>
                      ))}
                </Cell>
                <Cell>{user.mfa_enabled ? 'on' : 'off'}</Cell>
                <Cell className="whitespace-nowrap text-muted">{user.last_seen_at ? formatDateTime(user.last_seen_at) : 'never'}</Cell>
                <Cell className="whitespace-nowrap text-muted">{formatDate(user.created_at)}</Cell>
              </tr>
            ))}
          </Table>
          {users.data.more && <p className="mt-3 text-xs text-muted">More users match: look for more of the address.</p>}
        </>
      )}
    </>
  )
}
