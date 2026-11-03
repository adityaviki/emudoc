// What the document tab says of a document beyond its file: its details as a member set them (backend/app/details.py),
// in a dialog of their own; its note, which the project's members share (backend/app/notes.py); and its copies in the
// other projects the member sees (backend/app/duplicates.py).

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router'
import { api, organizations } from '../api.ts'
import type { Document, DocumentDetail, DocumentNote } from '../api.ts'
import { useMe } from '../auth.ts'
import { Button, IconButton, InputField, Notice, SelectField } from '../components/ui.tsx'
import {
  DETAILS,
  DETAIL_LABELS,
  copiesKey,
  copyPlace,
  copyUrl,
  detailsChange,
  detailsForm,
  documentLanguageChoices,
  noteKey,
  noteWords,
  readValue,
  readWords,
} from '../details.ts'
import { LANGUAGES_KEY } from '../organizationSettings.ts'
import { useDetailsChange } from './mutations.ts'
import { useDocumentTypes } from './organizing.ts'

// The longest note the server keeps (DocumentNoteIn in backend/app/schemas.py).
const NOTE_CHARS = 10_000
// A note longer than this shows its first lines until the reader asks for all of it.
const SHORT_NOTE = 280

/**
 * The document's title, type, date and language to set, in a modal over the page for as long as it is mounted, as
 * `Confirm` is: the browser's own dialog, Escape closing it and the focus going back to the button that opened it. Under
 * a detail a member set, what the reading found, which "Use the reading" takes again.
 */
export function DetailsDialog({ document, onClose }: { document: Document; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const id = useId()
  const [form, setForm] = useState(() => detailsForm(document))
  const [asRead, setAsRead] = useState<ReadonlySet<DocumentDetail>>(new Set())
  const types = useDocumentTypes(document.project_id)
  const languages = useQuery({ queryKey: LANGUAGES_KEY, queryFn: organizations.languages, staleTime: Infinity })
  const { organization } = useMe()
  const change = useDetailsChange(document.project_id)

  useEffect(() => {
    const shown = dialog.current
    // Strict mode runs an effect twice, and a dialog already open stays as it is.
    if (!shown || shown.open) return
    shown.showModal()
    shown.querySelector<HTMLElement>('[data-autofocus]')?.focus()
  }, [])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const body = detailsChange(document, form, asRead)
    if (Object.keys(body).length === 0) dialog.current?.close()
    else change.mutate({ id: document.id, change: body }, { onSuccess: () => dialog.current?.close() })
  }
  const edit = (detail: DocumentDetail, value: string) => {
    setForm((held) => ({ ...held, [detail]: value }))
    setAsRead((held) => new Set([...held].filter((taken) => taken !== detail)))
  }
  const take = (detail: DocumentDetail) => {
    setForm((held) => ({ ...held, [detail]: readValue(document, detail) }))
    setAsRead((held) => new Set([...held, detail]))
  }

  return (
    <dialog
      ref={dialog}
      aria-labelledby={`${id}-title`}
      aria-describedby={`${id}-body`}
      onClose={onClose}
      className="m-auto w-lg max-w-[calc(100vw-32px)] rounded-xl border border-line bg-surface p-5 text-ink shadow-xl shadow-ink/8 backdrop:bg-scrim"
    >
      <form onSubmit={submit}>
        <div className="flex items-start justify-between gap-3">
          <h2 id={`${id}-title`} className="font-semibold break-words">
            Details of “{document.filename}”
          </h2>
          <IconButton label="Close" onClick={() => dialog.current?.close()}>
            <X className="size-4" />
          </IconButton>
        </div>
        <p id={`${id}-body`} className="mt-1 text-sm text-muted">
          The library, its search, filing rules and the agent read these, and the search matches the document’s words in its
          language. What you set is kept when the document is read again or given a new version.
        </p>
        <div className="mt-4 space-y-3">
          {DETAILS.map((detail) => {
            const set = document.edited_details.includes(detail) && !asRead.has(detail)
            return (
              <div key={detail}>
                {detail === 'language' ? (
                  <SelectField
                    label={DETAIL_LABELS[detail]}
                    value={form[detail]}
                    onChange={(event) => edit(detail, event.target.value)}
                    aria-describedby={set ? `${id}-${detail}` : undefined}
                  >
                    {documentLanguageChoices(languages.data, organization, form[detail]).map((choice) => (
                      <option key={choice.value} value={choice.value}>
                        {choice.label}
                      </option>
                    ))}
                  </SelectField>
                ) : (
                  <InputField
                    label={DETAIL_LABELS[detail]}
                    type={detail === 'doc_date' ? 'date' : 'text'}
                    value={form[detail]}
                    onChange={(event) => edit(detail, event.target.value)}
                    maxLength={detail === 'title' ? 500 : 100}
                    placeholder={detail === 'title' ? document.filename : undefined}
                    list={detail === 'doc_type' ? `${id}-types` : undefined}
                    aria-describedby={set ? `${id}-${detail}` : undefined}
                    data-autofocus={detail === 'title' || undefined}
                  />
                )}
                {set && (
                  <p id={`${id}-${detail}`} className="mt-1 text-xs text-muted">
                    {readWords(document, detail)}{' '}
                    <button type="button" className="font-medium text-accent hover:underline" onClick={() => take(detail)}>
                      Use the reading
                    </button>
                  </p>
                )}
                {asRead.has(detail) && <p className="mt-1 text-xs text-muted">It follows the reading again once saved.</p>}
              </div>
            )
          })}
          <datalist id={`${id}-types`}>
            {types.data?.map((type) => (
              <option key={type.type} value={type.type} />
            ))}
          </datalist>
        </div>
        {change.isError && (
          <div className="mt-3">
            <Notice tone="bad">{change.error.message}</Notice>
          </div>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <Button onClick={() => dialog.current?.close()}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={change.isPending}>
            Save
          </Button>
        </div>
      </form>
    </dialog>
  )
}

interface NotePanelProps {
  document: Document
  note: DocumentNote | undefined
  /** Whether the member may change the project, and so write the note. */
  changes: boolean
  writing: boolean
  onWrite: () => void
  /** Called once the editor closes, saved or not. */
  onDone: () => void
}

/** The note under the document's header: read, its first lines while it is long, or written in its place. */
export function NotePanel({ document, note, changes, writing, onWrite, onDone }: NotePanelProps) {
  const [whole, setWhole] = useState(false)
  if (writing) return <NoteEditor document={document} note={note} onDone={onDone} />
  if (!note?.text) return null
  const long = note.text.length > SHORT_NOTE || note.text.split('\n').length > 3
  return (
    <section aria-label="Note" className="mt-2 rounded-md border border-line bg-sunken px-3 py-2 print:hidden">
      <p className={`text-[13px] break-words whitespace-pre-wrap ${long && !whole ? 'line-clamp-3' : ''}`}>{note.text}</p>
      <p className="mt-1 flex flex-wrap items-baseline gap-x-3 text-xs text-muted">
        <span>{noteWords(note)}</span>
        {long && (
          <button type="button" className="font-medium text-accent hover:underline" aria-expanded={whole} onClick={() => setWhole((shown) => !shown)}>
            {whole ? 'Show less' : 'Show all'}
          </button>
        )}
        {changes && (
          <button type="button" className="font-medium text-accent hover:underline" onClick={onWrite}>
            Edit the note
          </button>
        )}
      </p>
    </section>
  )
}

/** The note as the member writes it, from what it held as the editor opened; Escape or Cancel leaves it as it was. */
function NoteEditor({ document, note, onDone }: { document: Document; note: DocumentNote | undefined; onDone: () => void }) {
  const queryClient = useQueryClient()
  const id = useId()
  const box = useRef<HTMLTextAreaElement>(null)
  const [text, setText] = useState(note?.text ?? '')
  const save = useMutation({
    mutationFn: () => api.saveNote(document.id, text),
    onSuccess: (saved) => {
      queryClient.setQueryData(noteKey(document.id), saved)
      onDone()
    },
  })
  useEffect(() => {
    box.current?.focus()
  }, [])
  return (
    <form
      className="mt-2 print:hidden"
      onSubmit={(event) => {
        event.preventDefault()
        save.mutate()
      }}
    >
      <label htmlFor={`${id}-text`} className="mb-1 block text-xs font-medium text-muted">
        Note
      </label>
      <textarea
        ref={box}
        id={`${id}-text`}
        aria-describedby={`${id}-hint`}
        className="field block min-h-20 w-full px-3 py-1.5 text-sm"
        rows={3}
        maxLength={NOTE_CHARS}
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          // Escape leaves the note as it was, as Cancel does.
          if (event.key === 'Escape') {
            event.stopPropagation()
            onDone()
          }
        }}
      />
      <p id={`${id}-hint`} className="mt-1 text-xs text-muted">
        Everyone who reads the project reads it, with your name. Leave it empty to delete it.
      </p>
      {save.isError && (
        <p role="alert" className="mt-1 text-xs text-bad">
          {save.error.message}
        </p>
      )}
      <div className="mt-2 flex gap-2">
        <Button type="submit" variant="primary" disabled={save.isPending}>
          Save the note
        </Button>
        <Button onClick={onDone}>Cancel</Button>
      </div>
    </form>
  )
}

/** The other projects the member sees that hold the document's file too, each copy a link to it there. */
export function Copies({ document }: { document: Document }) {
  const copies = useQuery({ queryKey: copiesKey(document.id), queryFn: () => api.copies(document.id) })
  if (!copies.data?.copies.length) return null
  return (
    <p className="mt-1 text-xs text-muted print:hidden">
      Also in{' '}
      {copies.data.copies.map((copy, index) => (
        <span key={copy.id}>
          {index > 0 && ', '}
          <Link to={copyUrl(copy)} title={copy.filename} className="font-medium text-accent hover:underline">
            {copyPlace(copy)}
          </Link>
        </span>
      ))}
    </p>
  )
}
