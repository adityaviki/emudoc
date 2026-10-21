// A project's exports, offered to whoever may change the project: the member asks for one, the worker makes it
// (backend/app/export.py) and tells them when it is ready, and this lists their own until each expires, with its file.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { api, exportFileUrl } from '../api.ts'
import type { Project } from '../api.ts'
import { Button, Loading, Notice } from '../components/ui.tsx'
import { EXPORT_DAYS, describeExport, making } from '../exports.ts'
import { formatDate, formatDateTime } from '../format.ts'

/** How often the list is asked again while an export is being made, in milliseconds. */
const WHILE_MAKING = 2000

export function ProjectExports({ project, onClose }: { project: Project; onClose: () => void }) {
  const queryClient = useQueryClient()
  const key = ['project', project.id, 'exports']
  const exports = useQuery({
    queryKey: key,
    queryFn: () => api.projectExports(project.id),
    refetchInterval: (query) => (query.state.data && making(query.state.data) ? WHILE_MAKING : false),
  })
  const make = useMutation({
    mutationFn: () => api.exportProject(project.id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: key }),
  })
  const box = useRef<HTMLElement>(null)

  // Opened above the projects, which may be far below where the card was, as the access form is.
  useEffect(() => {
    void box.current?.scrollIntoView({ block: 'nearest' })
  }, [])

  return (
    <section ref={box} aria-label={`Export ${project.name}`} className="mb-6 rounded-xl border border-line bg-surface p-5 shadow-xs">
      <h2 className="font-semibold">Export {project.name}</h2>
      <p className="mt-0.5 mb-4 text-[13px] text-muted">
        One ZIP file: each document as it was uploaded, in its folders, and what was read from it (its outline, passages
        and facts), with the chats shared with the project and your own. It is made in the background, you are told when
        it is ready, and it is kept {EXPORT_DAYS} days, for you alone.
      </p>
      {exports.isPending && <Loading />}
      {exports.isError && <Notice tone="bad">{exports.error.message}</Notice>}
      {exports.data && exports.data.length > 0 && (
        <ul className="mb-4 divide-y divide-line border-y border-line">
          {exports.data.map((made) => (
            <li key={made.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2 text-[13px]">
              <span className="w-36 shrink-0 text-muted tabular-nums">{formatDateTime(made.created_at)}</span>
              <span className={`min-w-0 flex-1 ${made.status === 'failed' ? 'text-bad' : ''}`}>{describeExport(made, formatDate)}</span>
              {made.status === 'ready' && (
                <a href={exportFileUrl(project.id, made.id)} download className="inline-flex items-center gap-1 font-medium text-accent hover:underline">
                  <Download className="size-3.5" aria-hidden />
                  Download
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
      {make.isError && (
        <div className="mb-3">
          <Notice tone="bad">{make.error.message}</Notice>
        </div>
      )}
      <div className="flex gap-2">
        <Button variant="primary" onClick={() => make.mutate()} disabled={make.isPending || (!!exports.data && making(exports.data))}>
          Make an export
        </Button>
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      </div>
    </section>
  )
}
