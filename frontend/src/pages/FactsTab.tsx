import { useQuery } from '@tanstack/react-query'
import { Table2, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { api, factLink, pageLabel } from '../api.ts'
import { EmptyState, Loading, Notice } from '../components/ui.tsx'
import { useProject } from '../hooks.ts'

export function FactsTab() {
  const project = useProject()
  const [name, setName] = useState('')
  const [text, setText] = useState('')
  const names = useQuery({ queryKey: ['fact-names', project.id], queryFn: () => api.factNames(project.id) })
  const facts = useQuery({
    queryKey: ['facts', project.id, name, text],
    queryFn: () => api.facts(project.id, { name, q: text }),
    placeholderData: (previous) => previous,
  })

  if (names.isPending) return <Loading />
  if (names.data?.length === 0) {
    return (
      <EmptyState icon={<Table2 className="size-8" />} title="No facts yet">
        Facts are the dates, parties, amounts and identifiers the AI model extracts from each
        document. They appear here once documents have been indexed with a model configured.
      </EmptyState>
    )
  }

  const field = 'rounded-md border border-line bg-surface px-3 py-1.5 text-sm outline-none focus:border-accent'
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <select className={field} value={name} onChange={(event) => setName(event.target.value)}>
          <option value="">All facts</option>
          {names.data?.map((item) => (
            <option key={item.name} value={item.name}>
              {item.name} ({item.count})
            </option>
          ))}
        </select>
        <input
          className={`${field} w-64`}
          placeholder="Filter by value"
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
        {facts.data && (
          <span className="text-sm text-muted">
            {facts.data.total} fact{facts.data.total === 1 ? '' : 's'}
          </span>
        )}
      </div>

      {facts.isError && <Notice tone="bad">Could not load facts: {facts.error.message}</Notice>}
      {facts.data && facts.data.facts.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-line bg-surface">
          <table className="w-full text-sm">
            <thead className="border-b border-line text-left text-xs text-muted">
              <tr>
                <th className="px-4 py-2 font-medium">Fact</th>
                <th className="px-3 py-2 font-medium">Value</th>
                <th className="px-3 py-2 font-medium">Document</th>
                <th className="px-3 py-2 font-medium">Page</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {facts.data.facts.map((fact) => (
                <tr key={fact.id} className="align-top">
                  <td className="px-4 py-2 font-mono text-xs whitespace-nowrap text-muted">{fact.name}</td>
                  <td className="px-3 py-2">
                    <Link to={factLink(project.id, fact)} className="hover:text-accent" title={fact.quote ?? undefined}>
                      {fact.value}
                      {fact.currency && <span className="text-muted"> {fact.currency}</span>}
                    </Link>
                    {!fact.verified && <Unverified />}
                  </td>
                  <td className="max-w-56 truncate px-3 py-2 text-muted">{fact.document_title}</td>
                  <td className="px-3 py-2 whitespace-nowrap text-muted">{pageLabel(fact.page)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {facts.data?.facts.length === 0 && <EmptyState title="No facts match these filters" />}
    </div>
  )
}

export function Unverified() {
  return (
    <span
      className="ml-1.5 inline-flex items-center gap-0.5 align-middle text-xs text-warn"
      title="The quote given for this fact was not found in the document, so check it against the source."
    >
      <TriangleAlert className="size-3.5" />
      unverified
    </span>
  )
}
