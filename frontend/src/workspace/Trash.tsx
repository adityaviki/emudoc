import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, FileText, Folder as FolderIcon, MessagesSquare, RotateCcw, Trash2, X } from 'lucide-react'
import { useState } from 'react'
import { api, isPlanLimit, mayChange } from '../api.ts'
import type { PlanRefusal, Trashed } from '../api.ts'
import { ME_KEY, useMe } from '../auth.ts'
import { PlanLimitNotice } from '../components/PlanLimit.tsx'
import { Button, EmptyState, IconButton, Loading, Notice } from '../components/ui.tsx'
import { formatDate } from '../format.ts'
import { describeTrashed, trashedName, trashKey } from '../trash.ts'
import { listingsKey } from './listing.ts'
import { useWorkspace } from './state.ts'

const ICONS = { folder: FolderIcon, document: FileText, chat: MessagesSquare }

/**
 * The project's trash, in the library's place: the folders and files deleted from it and the
 * member's own chats, each kept the days the server says, then deleted for good. A viewer reads
 * what was deleted from the library and restores only their chats, as the server lets them.
 */
export function TrashView() {
  const workspace = useWorkspace()
  const { project } = workspace
  const changes = mayChange(useMe().role)
  const queryClient = useQueryClient()
  const trash = useQuery({ queryKey: trashKey(project.id), queryFn: () => api.projectTrash(project.id) })
  // What the server refused: the plan's limit as the way past it, anything else as it said.
  const [refused, setRefused] = useState<PlanRefusal | string | null>(null)
  const fail = (error: Error) =>
    setRefused(isPlanLimit(error) ? { message: error.message, limit: error.limit } : error.message)
  // A restore puts things back into the library, the counts, the chats and the plan's figures.
  const settle = () => {
    for (const queryKey of [trashKey(project.id), listingsKey(project.id), ['folders', project.id], ['project', project.id], ['chats', project.id], ME_KEY]) {
      void queryClient.invalidateQueries({ queryKey: [...queryKey] })
    }
  }
  const options = { onMutate: () => setRefused(null), onError: fail, onSettled: settle }
  const restore = useMutation({ mutationFn: (item: Trashed) => api.restore(item.kind, item.id), ...options })
  const purge = useMutation({ mutationFn: (item: Trashed) => api.purge(item.kind, item.id), ...options })
  const empty = useMutation({ mutationFn: () => api.emptyTrash(project.id), ...options })
  const busy = restore.isPending || purge.isPending || empty.isPending
  const items = trash.data?.items ?? []
  const more = (trash.data?.total ?? 0) - items.length

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <div className="flex h-11 shrink-0 items-center gap-1.5 border-b border-line bg-surface px-2">
        <IconButton label="Back to the library" onClick={() => workspace.goToFolder(null)}>
          <ArrowLeft className="size-4" />
        </IconButton>
        <h2 className="flex-1 text-[13px] font-semibold">Trash</h2>
        {changes && items.length > 0 && (
          <Button
            variant="danger"
            disabled={busy}
            onClick={() => {
              if (window.confirm('Delete everything in the trash for good? This cannot be undone.')) empty.mutate()
            }}
          >
            Empty trash
          </Button>
        )}
      </div>
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
        {trash.data && items.length > 0 && (
          <p className="text-xs text-muted">What is deleted waits here {trash.data.days} days, then is deleted for good.</p>
        )}
        {typeof refused === 'string' && (
          <Notice tone="bad">
            <div className="flex items-start justify-between gap-3">
              <span>{refused}</span>
              <IconButton label="Dismiss" onClick={() => setRefused(null)} className="p-0.5!">
                <X className="size-3.5" />
              </IconButton>
            </div>
          </Notice>
        )}
        {refused && typeof refused !== 'string' && <PlanLimitNotice refusal={refused} onDismiss={() => setRefused(null)} />}
        {trash.isPending && <Loading />}
        {trash.isError && <Notice tone="bad">Could not load the trash: {trash.error.message}</Notice>}
        {trash.data && items.length === 0 && (
          <EmptyState icon={<Trash2 />} title="The trash is empty">
            Deleted folders, files and chats wait here {trash.data.days} days before they are deleted for good.
          </EmptyState>
        )}
        <ul aria-label="In the trash" className="space-y-0.5">
          {items.map((item) => {
            const Icon = ICONS[item.kind]
            const name = trashedName(item)
            // Folders and files are the library's, which a viewer only reads; a chat is the member's own.
            const offered = changes || item.kind === 'chat'
            return (
              <li key={`${item.kind}:${item.id}`} className="flex items-center gap-2.5 rounded-md px-2 py-1.5 hover:bg-sunken">
                <Icon className="size-4 shrink-0 text-muted" aria-hidden />
                <div className="min-w-0 flex-1" title={`Deleted for good on ${formatDate(item.purged_at)}`}>
                  <p className="truncate text-[13px]">{name}</p>
                  <p className="truncate text-xs text-muted">{describeTrashed(item, formatDate(item.deleted_at))}</p>
                </div>
                {offered && (
                  <span className="flex shrink-0">
                    <IconButton label={`Restore ${name}`} disabled={busy} onClick={() => restore.mutate(item)}>
                      <RotateCcw className="size-4" />
                    </IconButton>
                    <IconButton
                      label={`Delete ${name} for good`}
                      danger
                      disabled={busy}
                      onClick={() => {
                        if (window.confirm(`Delete “${name}” for good? This cannot be undone.`)) purge.mutate(item)
                      }}
                    >
                      <Trash2 className="size-4" />
                    </IconButton>
                  </span>
                )}
              </li>
            )
          })}
        </ul>
        {more > 0 && <p className="text-xs text-muted">And {more} more deleted before these.</p>}
      </div>
    </div>
  )
}
