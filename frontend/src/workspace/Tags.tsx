import { ArrowLeft, Pencil, Plus, Tag as TagIcon, Trash2, X } from 'lucide-react'
import { useRef, useState } from 'react'
import { mayChange } from '../api.ts'
import type { Tag, TagColor } from '../api.ts'
import { useMe } from '../auth.ts'
import { Confirm } from '../components/Confirm.tsx'
import { Button, EmptyState, IconButton, InputField, Loading, Notice } from '../components/ui.tsx'
import { ColorField, TagDot } from './Places.tsx'
import { useOrganizing, useTags } from './organizing.ts'
import { useWorkspace } from './state.ts'

/**
 * The project's tags, in the library's place (backend/app/tags.py): each with how many documents carry it, the way to
 * list them, and for whoever may change the project, renamed, recoloured and deleted, and a new one made. A document is
 * tagged from its menu in the library.
 */
export function TagsView() {
  const workspace = useWorkspace()
  const { project } = workspace
  const changes = mayChange(useMe().role)
  const tags = useTags(project.id)
  const organizing = useOrganizing(project.id)
  const [name, setName] = useState('')
  const [color, setColor] = useState<TagColor>('grey')
  const [editing, setEditing] = useState<number | null>(null)
  const [asking, setAsking] = useState<Tag | null>(null)
  const [refused, setRefused] = useState<string | null>(null)
  // A tag deleted takes its row's buttons with the focus: the keyboard goes on from the heading.
  const heading = useRef<HTMLHeadingElement>(null)
  const listed = tags.data?.tags ?? []
  const room = listed.length < (tags.data?.max_tags ?? 0)

  const make = () => {
    const trimmed = name.trim()
    if (!trimmed) return
    setRefused(null)
    organizing.createTag.mutate({ name: trimmed, color }, { onSuccess: () => setName(''), onError: (error) => setRefused(error.message) })
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <div className="flex h-11 shrink-0 items-center gap-1.5 border-b border-line bg-surface px-2">
        <IconButton label="Back to the library" onClick={() => workspace.goToFolder(null)}>
          <ArrowLeft className="size-4" />
        </IconButton>
        <h2 ref={heading} tabIndex={-1} className="flex-1 text-[13px] font-semibold">
          Tags
        </h2>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        <p className="text-xs text-muted">
          A tag marks documents wherever they are in the project. Give one to a document from its menu in the library, and list a tag’s documents from
          the menu above the files.
        </p>
        {refused && (
          <Notice tone="bad">
            <div className="flex items-start justify-between gap-3">
              <span>{refused}</span>
              <IconButton label="Dismiss" onClick={() => setRefused(null)} className="p-0.5!">
                <X className="size-3.5" />
              </IconButton>
            </div>
          </Notice>
        )}
        {tags.isPending && <Loading />}
        {tags.isError && <Notice tone="bad">Could not load the project’s tags: {tags.error.message}</Notice>}
        {tags.data && listed.length === 0 && (
          <EmptyState icon={<TagIcon />} title="No tags yet">
            {changes ? 'Make the project’s first tag below.' : 'A member who may change the project makes its tags.'}
          </EmptyState>
        )}
        {listed.length > 0 && (
          <ul aria-label="The project’s tags" className="divide-y divide-line rounded-lg border border-line bg-surface">
            {listed.map((tag) =>
              editing === tag.id ? (
                <TagEditor
                  key={tag.id}
                  tag={tag}
                  onSave={(change) =>
                    organizing.updateTag.mutate(
                      { id: tag.id, ...change },
                      { onSuccess: () => setEditing(null), onError: (error) => setRefused(error.message) },
                    )
                  }
                  onCancel={() => setEditing(null)}
                />
              ) : (
                <li key={tag.id} className="flex items-center gap-2 px-3 py-2 text-[13px]">
                  <TagDot color={tag.color} className="size-2.5" />
                  <button type="button" className="min-w-0 truncate text-left font-medium hover:underline" onClick={() => workspace.showPlace({ tag: tag.id })}>
                    {tag.name}
                  </button>
                  <span className="flex-1 text-xs text-muted">
                    {tag.document_count} document{tag.document_count === 1 ? '' : 's'}
                  </span>
                  {changes && (
                    <>
                      <IconButton label={`Change “${tag.name}”`} onClick={() => setEditing(tag.id)}>
                        <Pencil className="size-3.5" />
                      </IconButton>
                      <IconButton label={`Delete “${tag.name}”`} danger onClick={() => setAsking(tag)}>
                        <Trash2 className="size-3.5" />
                      </IconButton>
                    </>
                  )}
                </li>
              ),
            )}
          </ul>
        )}
        {changes && tags.data && (
          <div className="flex items-end gap-2 rounded-lg border border-line bg-surface p-3">
            <InputField
              label="New tag"
              className="min-w-0 flex-1"
              value={name}
              maxLength={50}
              disabled={!room}
              onChange={(event) => setName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') make()
              }}
            />
            <ColorField value={color} onChange={setColor} />
            <Button
              icon={<Plus className="size-4" />}
              onClick={make}
              disabled={!name.trim() || !room || organizing.createTag.isPending}
              title={room ? undefined : `A project has at most ${tags.data.max_tags} tags.`}
            >
              Add
            </Button>
          </div>
        )}
      </div>
      {asking && (
        <Confirm
          title={`Delete the tag “${asking.name}”?`}
          body={`It is taken off the ${asking.document_count} document${asking.document_count === 1 ? '' : 's'} that carry it. The documents stay where they are.`}
          action="Delete"
          onConfirm={() => {
            heading.current?.focus()
            organizing.deleteTag.mutate(asking.id, { onError: (error) => setRefused(error.message) })
          }}
          onClose={() => setAsking(null)}
        />
      )}
    </div>
  )
}

function TagEditor({ tag, onSave, onCancel }: { tag: Tag; onSave: (change: { name: string; color: TagColor }) => void; onCancel: () => void }) {
  const [name, setName] = useState(tag.name)
  const [color, setColor] = useState<TagColor>(tag.color)
  return (
    <li className="flex flex-wrap items-end gap-2 px-3 py-2">
      <InputField
        label="Name"
        autoFocus
        className="min-w-0 flex-1"
        value={name}
        maxLength={50}
        onChange={(event) => setName(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && name.trim()) onSave({ name: name.trim(), color })
          if (event.key === 'Escape') onCancel()
        }}
      />
      <ColorField value={color} onChange={setColor} />
      <Button variant="primary" disabled={!name.trim()} onClick={() => onSave({ name: name.trim(), color })}>
        Save
      </Button>
      <Button onClick={onCancel}>Cancel</Button>
    </li>
  )
}
