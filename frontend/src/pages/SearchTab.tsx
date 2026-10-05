import { useQuery } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router'
import { api, pageLabel, sectionPath } from '../api.ts'
import type { SearchHit } from '../api.ts'
import { Chip, EmptyState, Loading, Notice } from '../components/ui.tsx'
import { useProject } from '../hooks.ts'

export function SearchTab() {
  const project = useProject()
  const [params, setParams] = useSearchParams()
  const query = params.get('q') ?? ''
  const [draft, setDraft] = useState(query)
  const results = useQuery({
    queryKey: ['search', project.id, query],
    queryFn: () => api.search(project.id, query),
    enabled: query.trim().length > 0,
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    setParams(draft.trim() ? { q: draft.trim() } : {})
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <form onSubmit={submit} className="relative">
        <Search className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
        <input
          autoFocus
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Search by words or by meaning"
          className="w-full rounded-lg border border-line bg-surface py-2.5 pr-3 pl-9 outline-none focus:border-accent"
        />
      </form>

      {!query && (
        <EmptyState title="Search every passage in this project">
          Results combine exact word matches with matches on meaning, so a question in your own
          words works too.
        </EmptyState>
      )}
      {query && results.isPending && <Loading />}
      {results.isError && <Notice tone="bad">Search failed: {results.error.message}</Notice>}
      {results.data && !results.data.semantic && (
        <Notice>The embedding service is offline, so these are keyword matches only.</Notice>
      )}
      {results.data?.hits.length === 0 && <EmptyState title={`Nothing found for "${query}"`} />}

      <ol className="space-y-2">
        {results.data?.hits.map((hit) => (
          <li key={hit.passage_id}>
            <Hit hit={hit} projectId={project.id} />
          </li>
        ))}
      </ol>
    </div>
  )
}

function Hit({ hit, projectId }: { hit: SearchHit; projectId: number }) {
  const location = [sectionPath(hit.context, hit.document_title), pageLabel(hit.page_start, hit.page_end)]
    .filter(Boolean)
    .join(' · ')
  // Which ranking found the passage: exact words, meaning, or both.
  const found = [hit.keyword_rank && 'words', hit.vector_rank && 'meaning'].filter(Boolean).join(' + ')

  return (
    <Link
      to={`/projects/${projectId}/documents/${hit.document_id}?passage=${hit.passage_id}`}
      className="block rounded-lg border border-line bg-surface px-4 py-3 transition hover:border-accent"
    >
      <div className="flex items-baseline justify-between gap-3">
        <p className="min-w-0 truncate font-medium">
          {hit.document_title}
          {hit.doc_type && (
            <span className="ml-2 align-middle">
              <Chip>{hit.doc_type}</Chip>
            </span>
          )}
        </p>
        <span className="shrink-0 text-xs text-muted" title="How this passage was found">
          {found}
        </span>
      </div>
      {location && <p className="text-xs text-muted">{location}</p>}
      <p className="mt-1.5 text-sm">
        {hit.snippet.map((segment, index) =>
          segment.match ? <mark key={index}>{segment.text}</mark> : <span key={index}>{segment.text}</span>,
        )}
      </p>
    </Link>
  )
}
