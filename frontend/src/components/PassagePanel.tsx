import { useQuery } from '@tanstack/react-query'
import { ExternalLink, X } from 'lucide-react'
import { Link } from 'react-router'
import { api, pageLabel, sectionPath } from '../api.ts'
import { SourceText } from './Markdown.tsx'
import { IconButton, Loading, Notice } from './ui.tsx'

interface PassagePanelProps {
  projectId: number
  passageId: number
  onClose: () => void
}

/** Shows the exact source text behind a citation, with a way into the document. */
export function PassagePanel({ projectId, passageId, onClose }: PassagePanelProps) {
  const passage = useQuery({ queryKey: ['passage', passageId], queryFn: () => api.passage(passageId) })

  return (
    <aside className="flex h-full flex-col rounded-lg border border-line bg-surface">
      <div className="flex items-start justify-between gap-2 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <p className="text-xs font-medium tracking-wide text-muted uppercase">Source</p>
          {passage.data && (
            <>
              <p className="truncate font-medium">{passage.data.document_title}</p>
              <p className="text-xs text-muted">
                {[
                  sectionPath(passage.data.context, passage.data.document_title),
                  pageLabel(passage.data.page_start, passage.data.page_end),
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
            </>
          )}
        </div>
        <IconButton label="Close" onClick={onClose}>
          <X className="size-4" />
        </IconButton>
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-3 text-sm">
        {passage.isPending && <Loading />}
        {passage.isError && <Notice tone="bad">This passage is no longer available.</Notice>}
        {passage.data && <SourceText text={passage.data.text} />}
      </div>
      {passage.data && (
        <div className="border-t border-line px-4 py-2.5">
          <Link
            to={`/projects/${projectId}/documents/${passage.data.document_id}?passage=${passage.data.id}`}
            className="inline-flex items-center gap-1.5 text-sm font-medium text-accent hover:underline"
          >
            <ExternalLink className="size-4" />
            Open in the document
          </Link>
        </div>
      )}
    </aside>
  )
}
