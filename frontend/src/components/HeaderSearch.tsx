// The header's search box, on every page of the shell. In a project it searches the project as the words rest, the
// results in a tab beside its documents (workspace/SearchTab.tsx); on /search it searches every project the same way;
// anywhere else Enter opens that search of every project with the words.

import { useQuery } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router'
import { api } from '../api.ts'
import { narrows } from '../library.ts'
import { EMPTY_SEARCH_ALL, HEADER_SEARCH, SEARCH_PATH, headerSearchWords, parseSearchAll, searchAllUrl } from '../search.ts'
import { parseWorkspaceUrl, searchedUrl, searching } from '../workspace/url.ts'
import { SearchBox } from './SearchBox.tsx'

export function HeaderSearch() {
  const location = useLocation()
  const navigate = useNavigate()
  const workspace = parseWorkspaceUrl(location.pathname, location.search)
  const projectId = workspace?.projectId
  // The workspace's own query of the project, whose name the box says it searches.
  const project = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => api.project(projectId!),
    enabled: projectId != null,
  })
  const everywhere = location.pathname === SEARCH_PATH

  let query = ''
  let live = true
  let onSearch: (q: string) => void
  if (workspace) {
    query = workspace.search?.q ?? ''
    // Read from the address as it is now, not as it was drawn: the words can rest twice before a render.
    onSearch = (q) => {
      const now = parseWorkspaceUrl(window.location.pathname, window.location.search)
      if (now) navigate(searchedUrl(now, q), { replace: searching(now) })
    }
  } else if (everywhere) {
    query = parseSearchAll(new URLSearchParams(location.search)).q
    onSearch = (q) => {
      const now = parseSearchAll(new URLSearchParams(window.location.search))
      navigate(searchAllUrl({ q, filter: now.filter }), { replace: true })
    }
  } else {
    live = false
    onSearch = (q) => navigate(searchAllUrl({ q, filter: EMPTY_SEARCH_ALL }))
  }
  const words = headerSearchWords(
    workspace ? { name: project.data?.name ?? 'the project', narrowed: workspace.search != null && narrows(workspace.search.filter) } : null,
  )

  return (
    <div role="search" className="w-full max-w-md">
      <SearchBox
        id={HEADER_SEARCH}
        label={words.label}
        placeholder={words.placeholder}
        query={query}
        live={live}
        onSearch={onSearch}
        tour="search"
        className="w-full"
      />
    </div>
  )
}
