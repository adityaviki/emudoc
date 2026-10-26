// The files held in more than one of the projects the member sees (backend/app/duplicates.py), each with where every
// copy of it is, a page at a time, the one whose latest copy came last first.

import { useInfiniteQuery } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { Link } from 'react-router'
import { api } from '../api.ts'
import { Button, Loading, Notice } from '../components/ui.tsx'
import { DUPLICATES_KEY, copyPlace, copyUrl, duplicatesWords } from '../details.ts'
import { formatSize } from '../format.ts'

export function DuplicatesPage() {
  const pages = useInfiniteQuery({
    queryKey: DUPLICATES_KEY,
    queryFn: ({ pageParam }) => api.duplicates(pageParam),
    initialPageParam: null as number | null,
    getNextPageParam: (page) => page.next,
  })
  const first = pages.data?.pages[0]
  const files = pages.data?.pages.flatMap((page) => page.duplicates) ?? []

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl px-6 py-10">
        <Link to="/" className="inline-flex items-center gap-1 text-[13px] text-muted hover:text-ink">
          <ArrowLeft className="size-3.5" />
          Projects
        </Link>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">Duplicates</h1>
        <p className="mt-1 mb-6 text-[13px] text-muted">
          {first ? `${duplicatesWords(first)} ` : ''}Only copies of the same bytes count: a file saved again, or a scan of a printed one,
          is not.
        </p>
        {pages.isPending && <Loading />}
        {pages.isError && <Notice tone="bad">Could not load the duplicates: {pages.error.message}</Notice>}
        {files.length > 0 && (
          <ul className="divide-y divide-line border-y border-line">
            {files.map((file) => (
              <li key={file.copies[0].id} className="py-3 text-[13px]">
                <p className="font-medium break-words">
                  {file.copies[0].filename}{' '}
                  <span className="font-normal text-muted">
                    · {formatSize(file.size_bytes)} · {file.copies.length} copies
                  </span>
                </p>
                <ul className="mt-1">
                  {file.copies.map((copy) => (
                    <li key={copy.id} className="text-xs text-muted">
                      {/* A link a line of its own is a target of 24 pixels at least (WCAG 2.5.8). */}
                      <Link to={copyUrl(copy)} className="inline-flex min-h-6 items-center font-medium text-accent hover:underline">
                        {copyPlace(copy)}
                      </Link>
                      {copy.filename !== file.copies[0].filename && ` as “${copy.filename}”`}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        )}
        {pages.hasNextPage && (
          <div className="mt-4 text-center">
            <Button onClick={() => pages.fetchNextPage()} disabled={pages.isFetchingNextPage}>
              Show more
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}
