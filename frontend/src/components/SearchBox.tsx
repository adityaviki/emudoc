import { Search, X } from 'lucide-react'
import { useEffect, useEffectEvent, useRef, useState } from 'react'
import type { RefObject } from 'react'

interface SearchBoxProps {
  query: string
  placeholder: string
  onSearch: (q: string) => void
  /** Its name, which says what it searches: the project, or every project. */
  label?: string
  inputRef?: RefObject<HTMLInputElement | null>
  id?: string
  /** Whether it searches as the words are typed, once they rest; false for Enter alone, where a search leaves the page. */
  live?: boolean
  /** The tour's step that points at it (src/tour.ts). */
  tour?: string
  /** Its width where it stands. */
  className?: string
}

/** A search box that searches as the words are typed, once they rest, and at once on Enter. */
export function SearchBox({
  query,
  placeholder,
  onSearch,
  label = 'Search',
  inputRef,
  id,
  live = true,
  tour,
  className = 'min-w-40 flex-1',
}: SearchBoxProps) {
  const [draft, setDraft] = useState(query)
  const own = useRef<HTMLInputElement | null>(null)
  // The URL can change the query from outside (opening a tab, going back).
  const [lastQuery, setLastQuery] = useState(query)
  if (query !== lastQuery) {
    setLastQuery(query)
    setDraft(query)
  }

  // The page's latest way to search, which a render makes anew, without starting the wait again.
  const search = useEffectEvent(onSearch)
  useEffect(() => {
    const trimmed = draft.trim()
    if (!live || trimmed === query) return
    const timer = window.setTimeout(() => search(trimmed), 300)
    return () => window.clearTimeout(timer)
  }, [draft, query, live])

  return (
    <div data-tour={tour} className={`relative ${className}`}>
      <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted" />
      <input
        ref={(input) => {
          own.current = input
          if (inputRef) inputRef.current = input
        }}
        id={id}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          const trimmed = draft.trim()
          if (event.key === 'Escape') setDraft('')
          else if (event.key === 'Enter' && (live ? trimmed !== query : trimmed !== '')) onSearch(trimmed)
        }}
        placeholder={placeholder}
        aria-label={label}
        className="field h-8 pr-8 pl-8 text-[13px]"
      />
      {draft && (
        <button
          type="button"
          aria-label="Clear search"
          onClick={() => {
            setDraft('')
            // The button goes with the words: the keyboard stays in the box.
            own.current?.focus()
          }}
          className="absolute top-1/2 right-1 flex size-6 -translate-y-1/2 items-center justify-center rounded text-muted transition hover:bg-sunken hover:text-ink"
        >
          <X className="size-3.5" />
        </button>
      )}
    </div>
  )
}
