// The search of every project the member sees (backend/app/api/search.py): one ranking of their passages, each hit
// naming its project and opening its document at the passage, narrowed to some of the projects, the documents' types
// and a span of their dates. The address holds the search (src/search.ts), so that it can be gone back to and shared;
// the header's box (components/HeaderSearch.tsx) gives its words.

import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, Search, SlidersHorizontal, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { api } from '../api.ts'
import type { Project, SearchAllFilter } from '../api.ts'
import { Confirm } from '../components/Confirm.tsx'
import { Button, Checkbox, EmptyState, IconButton, Loading, Notice } from '../components/ui.tsx'
import {
  ALL_TYPES_KEY,
  EMPTY_SEARCH_ALL,
  HEADER_SEARCH,
  hitUrl,
  narrowsAll,
  parseSearchAll,
  searchAllParts,
  searchAllProblem,
  searchAllUrl,
  searchAllWords,
  withoutSearchAllPart,
} from '../search.ts'
import type { SearchAll } from '../search.ts'
import { Hit } from '../workspace/SearchTab.tsx'
import { DatesField, TypesField } from '../workspace/Places.tsx'

export function SearchPage() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const { q: query, filter } = parseSearchAll(params)
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.projects })
  const results = useQuery({
    queryKey: ['search-all', query, filter],
    queryFn: () => api.searchAll(query, filter),
    enabled: query !== '',
  })
  const [filtering, setFiltering] = useState(false)
  const filterButton = useRef<HTMLButtonElement>(null)
  // A filter changed is a step of its own in the history; the header's box replaces the words as they rest.
  const show = (search: SearchAll) => navigate(searchAllUrl(search))
  const named = projects.data ?? []
  const parts = searchAllParts(filter, named)
  const hits = results.data?.hits ?? []

  // Opened from a link, the page is ready for words; from the header's box, the box has the focus already.
  useEffect(() => {
    window.document.getElementById(HEADER_SEARCH)?.focus()
  }, [])

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl px-6 py-10">
        <Link to="/" className="inline-flex items-center gap-1 text-[13px] text-muted hover:text-ink">
          <ArrowLeft className="size-3.5" />
          Projects
        </Link>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">Search</h1>
        <p className="mt-1 mb-6 text-[13px] text-muted">Every project you see, by the words of its documents and by their meaning together.</p>
        <div className="flex items-center gap-2">
          <Button ref={filterButton} icon={<SlidersHorizontal className="size-3.5" />} onClick={() => setFiltering(true)} className="h-8 px-2.5 text-[13px]">
            Filter
          </Button>
        </div>
        {parts.length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {parts.map(({ part, words }) => (
              <span key={part} className="inline-flex max-w-full min-w-0 items-center gap-0.5 rounded-md bg-accent-soft py-px pr-0.5 pl-2 text-xs text-accent">
                <span className="truncate">{words}</span>
                <IconButton
                  label={`Take off: ${words}`}
                  className="size-5! text-accent hover:text-ink"
                  onClick={() => {
                    show({ q: query, filter: withoutSearchAllPart(filter, part) })
                    filterButton.current?.focus()
                  }}
                >
                  <X className="size-3" />
                </IconButton>
              </span>
            ))}
          </div>
        )}
        <div className="mt-4 space-y-2">
          {!query && (
            <EmptyState icon={<Search />} title="What should be searched for?">
              {searchAllWords(filter, named)}. Type words in the search box at the top to search them.
            </EmptyState>
          )}
          {results.isPending && query !== '' && <Loading />}
          {results.isError && <Notice tone="bad">Search failed: {results.error.message}</Notice>}
          {results.data && !results.data.semantic && <Notice>The embedding service is offline, so these are keyword matches only.</Notice>}
          {results.data && hits.length === 0 && (
            <EmptyState title={`Nothing found for "${query}"`}>
              Results combine exact word matches with matches on meaning.
              {/* A narrowed search may miss what the other projects hold. */}
              {narrowsAll(filter) && (
                <div className="mt-4">
                  <Button icon={<Search className="size-4" />} onClick={() => show({ q: query, filter: EMPTY_SEARCH_ALL })}>
                    Search every project
                  </Button>
                </div>
              )}
            </EmptyState>
          )}
          {hits.map((hit) => (
            <Hit key={hit.passage_id} hit={hit} project={hit.project_name} onOpen={() => navigate(hitUrl(hit))} />
          ))}
        </div>
        {filtering && (
          <SearchAllFilterDialog filter={filter} projects={named} onApply={(chosen) => show({ q: query, filter: chosen })} onClose={() => setFiltering(false)} />
        )}
      </div>
    </div>
  )
}

interface SearchAllFilterDialogProps {
  filter: SearchAllFilter
  projects: Project[]
  onApply: (filter: SearchAllFilter) => void
  onClose: () => void
}

/** Asks what the search searches within: some of the projects, with the types of their documents asked for as it opens, and dates. */
function SearchAllFilterDialog({ filter: held, projects, onApply, onClose }: SearchAllFilterDialogProps) {
  const [filter, setFilter] = useState(held)
  const types = useQuery({ queryKey: ALL_TYPES_KEY, queryFn: api.allDocumentTypes })
  const problem = searchAllProblem(filter)
  const set = (change: Partial<SearchAllFilter>) => setFilter({ ...filter, ...change })
  return (
    <Confirm
      title="Filter the search"
      body="Search only what matches what you choose. A part left empty takes every project or document."
      action="Apply"
      danger={false}
      disabled={problem !== null}
      onConfirm={() => onApply(filter)}
      onClose={onClose}
    >
      <div className="max-h-[55vh] space-y-4 overflow-y-auto pr-1">
        {(projects.length > 1 || filter.projects.length > 0) && (
          <fieldset className="space-y-1.5">
            <legend className="mb-1 text-xs font-medium text-muted">In the projects</legend>
            {projects.map((project) => (
              <Checkbox
                key={project.id}
                checked={filter.projects.includes(project.id)}
                onChange={(event) =>
                  set({ projects: event.target.checked ? [...filter.projects, project.id] : filter.projects.filter((id) => id !== project.id) })
                }
                label={project.name}
              />
            ))}
          </fieldset>
        )}
        <TypesField types={types.data ?? []} chosen={filter.types} onChange={(chosen) => set({ types: chosen })} />
        <DatesField after={filter.after} before={filter.before} onChange={set} />
        <p className="text-xs text-muted" aria-live="polite">
          {problem ?? `${searchAllWords(filter, projects)}.`}
        </p>
      </div>
    </Confirm>
  )
}
