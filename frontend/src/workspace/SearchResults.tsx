// The project's search's passages, in the library below the files whose names hold its words (workspace/Library.tsx):
// the library's box gives its words, the address holds them with what narrows the passages (workspace/url.ts), and a
// hit opens its document in the middle pane at the passage, the search staying in the library beside it.

import { useQuery } from '@tanstack/react-query'
import { Globe, Search, SlidersHorizontal, X } from 'lucide-react'
import { useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { api, pageLabel, sectionPath } from '../api.ts'
import type { SearchHit } from '../api.ts'
import { Button, Chip, EmptyState, IconButton, Loading, Notice } from '../components/ui.tsx'
import { EMPTY_FILTER, MAX_WITHIN, filterParts, filterWords, folderChoices, narrows, withoutPart } from '../library.ts'
import { EMPTY_SEARCH_ALL, LIBRARY_SEARCH, searchAllUrl } from '../search.ts'
import { useDocumentTypes } from './organizing.ts'
import { SearchFilterDialog } from './Places.tsx'
import { useWorkspace } from './state.ts'
import type { WorkspaceSearch } from './url.ts'

/** The search's passages, under a heading with its filter, the parts that narrow it, and the way to search within what it found. */
export function SearchResults({ search }: { search: WorkspaceSearch }) {
  const workspace = useWorkspace()
  const navigate = useNavigate()
  const { project, folders } = workspace
  const { q: query, filter } = search
  const [filtering, setFiltering] = useState(false)
  const filterButton = useRef<HTMLButtonElement>(null)
  const results = useQuery({
    queryKey: ['search', project.id, query, filter],
    queryFn: () => api.search(project.id, query, filter),
    enabled: query !== '',
  })
  const folder = folderChoices(folders).find((choice) => choice.id === filter.folder_id)?.label ?? null
  const parts = filterParts(filter, folder)
  const hits = results.data?.hits ?? []
  const within = query !== '' && hits.length > 0 && filter.mentions.length < MAX_WITHIN

  return (
    <section aria-labelledby={PASSAGES_HEADING} className="space-y-2 px-1 pt-3 pb-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <h2 id={PASSAGES_HEADING} className="mr-1 px-1 text-xs font-semibold text-muted">
          Passages
        </h2>
        <Button ref={filterButton} icon={<SlidersHorizontal className="size-3.5" />} onClick={() => setFiltering(true)} className="h-7 px-2 text-xs">
          Filter
        </Button>
        {parts.map(({ part, words }) => (
          <span key={String(part)} className="inline-flex max-w-full min-w-0 items-center gap-0.5 rounded-md bg-accent-soft py-px pr-0.5 pl-2 text-xs text-accent">
            <span className="truncate">{words}</span>
            <IconButton
              label={`Take off: ${words}`}
              className="size-5! text-accent hover:text-ink"
              onClick={() => {
                const rest = withoutPart(filter, part)
                workspace.showSearch({ q: query, filter: rest })
                // The last part taken off a search without words ends it, and the box stays to search again.
                if (query || narrows(rest)) filterButton.current?.focus()
                else window.document.getElementById(LIBRARY_SEARCH)?.focus()
              }}
            >
              <X className="size-3" />
            </IconButton>
          </span>
        ))}
        <span className="ml-auto flex gap-1.5">
          {within && (
            <Button
              icon={<Search className="size-3.5" />}
              title="Search again only in the documents with a passage that holds every one of these words"
              className="h-7 px-2 text-xs"
              onClick={() => {
                workspace.showSearch({ q: '', filter: { ...filter, mentions: [...filter.mentions, query] } })
                window.document.getElementById(LIBRARY_SEARCH)?.focus()
              }}
            >
              Search within these
            </Button>
          )}
        </span>
      </div>
      {!query && (
        <EmptyState icon={<Search />} title="What should be searched for?">
          {filterWords(filter, folder)}. Type words in the search box above to search them.
        </EmptyState>
      )}
      {results.isPending && query !== '' && <Loading />}
      {results.isError && <Notice tone="bad">Search failed: {results.error.message}</Notice>}
      {results.data && !results.data.semantic && <Notice>The embedding service is offline, so these are keyword matches only.</Notice>}
      {results.data && hits.length === 0 && (
        <EmptyState title={`No passage found for "${query}"${folder ? ` in ${folder}` : ''}`}>
          Passages are found by their exact words and by their meaning.
          <div className="mt-4 flex flex-wrap justify-center gap-2">
            {/* A narrowed search may miss what the rest of the project holds, and the project what the others do. */}
            {narrows(filter) && (
              <Button icon={<Search className="size-4" />} onClick={() => workspace.showSearch({ q: query, filter: EMPTY_FILTER })}>
                Search all of {project.name}
              </Button>
            )}
            <Button icon={<Globe className="size-4" />} onClick={() => navigate(searchAllUrl({ q: query, filter: EMPTY_SEARCH_ALL }))}>
              Search every project
            </Button>
          </div>
        </EmptyState>
      )}
      {hits.map((hit) => (
        <Hit key={hit.passage_id} hit={hit} onOpen={() => workspace.openDocument(hit.document_id, { passage: hit.passage_id })} />
      ))}
      {filtering && (
        <SearchFilterEditor
          filter={filter}
          folders={folders}
          onApply={(chosen) => workspace.showSearch({ q: query, filter: chosen })}
          onClose={() => setFiltering(false)}
        />
      )}
    </section>
  )
}

/** The passages' heading, which names their section. */
const PASSAGES_HEADING = 'search-passages'

/** The search's filter form, with the types the model gave the project's documents to choose from, asked for as it opens. */
function SearchFilterEditor(props: Omit<Parameters<typeof SearchFilterDialog>[0], 'types'>) {
  const { project } = useWorkspace()
  const types = useDocumentTypes(project.id)
  return <SearchFilterDialog {...props} types={types.data ?? []} />
}

/** A passage a search found, which opens the document at it; `project` names where it is, for a search of several projects. */
export function Hit({ hit, project, onOpen }: { hit: SearchHit; project?: string; onOpen: () => void }) {
  const location = [project, sectionPath(hit.context, hit.document_title), pageLabel(hit.page_start, hit.page_end)]
    .filter(Boolean)
    .join(' · ')
  // Which ranking found the passage: exact words, meaning, or both.
  const found = [hit.keyword_rank && 'words', hit.vector_rank && 'meaning'].filter(Boolean).join(' + ')

  return (
    <button
      type="button"
      onClick={onOpen}
      className="block w-full rounded-lg border border-line bg-surface px-4 py-3 text-left shadow-xs transition hover:border-ink/15 hover:shadow-sm"
    >
      <div className="flex items-baseline justify-between gap-3">
        <p className="min-w-0 truncate text-[13px] font-semibold">
          {hit.document_title}
          {hit.doc_type && (
            <span className="ml-2 align-middle">
              <Chip>{hit.doc_type}</Chip>
            </span>
          )}
        </p>
        <span className="shrink-0 text-[11px] text-muted" title="How this passage was found">
          {found}
        </span>
      </div>
      {location && <p className="mt-0.5 text-xs text-muted">{location}</p>}
      <p className="mt-2 text-[13px] leading-relaxed text-ink/90">
        {hit.snippet.map((segment, index) =>
          segment.match ? <mark key={index}>{segment.text}</mark> : <span key={index}>{segment.text}</span>,
        )}
      </p>
    </button>
  )
}
