import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, FileDiff, Pencil, Plus, RotateCcw, Table2, Trash2, X } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { api, isPlanLimit, mayChange } from '../api.ts'
import type { ExtractionTemplate, FactType, PlanRefusal } from '../api.ts'
import { ME_KEY, useMe } from '../auth.ts'
import { Confirm } from '../components/Confirm.tsx'
import { PlanLimitNotice } from '../components/PlanLimit.tsx'
import { Button, Checkbox, Chip, IconButton, InputField, Loading, Notice, SelectField } from '../components/ui.tsx'
import { deletedReadAgain, factChangesKey, queuedWords, readAgainQuestion, savedReadAgain, savedWords } from '../factChanges.ts'
import type { ReadAgain } from '../factChanges.ts'
import { factTablesKey } from '../factTable.ts'
import { useStatus } from '../hooks.ts'
import {
  FACT_TYPES,
  MODEL_AWAY,
  NEW_FIELD,
  deletionQuestion,
  draftOf,
  draftProblem,
  factName,
  inputOf,
  originOf,
  templateWords,
  templatesKey,
} from '../templates.ts'
import type { FieldDraft } from '../templates.ts'
import { listingsKey } from './listing.ts'
import { useWorkspace } from './state.ts'

/** What the form is open on: a new template, or one of those listed, a built-in one to become the project's own. */
type Editing = { template: ExtractionTemplate | null }

/** What the last change did, and the documents it offers to read again for it. */
type Done = { said: string; read: ReadAgain | null }

/**
 * The project's extraction templates, in the library's place: the fields its documents of each
 * kind are read for, the built-in ones among them, each of which a project's own of its kind
 * replaces. Whoever may change the project makes, changes and deletes them, and has a changed
 * template's documents read again for it, then reads what each reading changed; a viewer reads
 * them.
 */
export function TemplatesView() {
  const workspace = useWorkspace()
  const { project } = workspace
  const changes = mayChange(useMe().role)
  const queryClient = useQueryClient()
  const health = useStatus()
  const templates = useQuery({ queryKey: templatesKey(project.id), queryFn: () => api.templates(project.id) })
  const [editing, setEditing] = useState<Editing | null>(null)
  const [asking, setAsking] = useState<ExtractionTemplate | 'read' | null>(null)
  // What the last change did, which offers to read its documents again.
  const [done, setDone] = useState<Done | null>(null)
  const [refused, setRefused] = useState<PlanRefusal | string | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const id = useId()
  // Opened from a button that went with the view before it: the keyboard goes on from here.
  useEffect(() => {
    if (window.document.activeElement === window.document.body) heading.current?.focus()
  }, [])
  const fail = (error: Error) =>
    setRefused(isPlanLimit(error) ? { message: error.message, limit: error.limit } : error.message)

  const remove = useMutation({
    mutationFn: (template: ExtractionTemplate) => api.deleteTemplate(template.id ?? 0),
    onMutate: () => {
      setRefused(null)
      // The row goes with its buttons: the keyboard goes on from the heading.
      heading.current?.focus()
    },
    onSuccess: (_, template) => setDone({ said: `Deleted “${template.title}”.`, read: deletedReadAgain(template) }),
    onError: fail,
    onSettled: () => {
      for (const queryKey of [templatesKey(project.id), factTablesKey(project.id)]) void queryClient.invalidateQueries({ queryKey })
    },
  })
  const readAgain = useMutation({
    mutationFn: (read: ReadAgain) => api.readAgain(project.id, read.ask),
    onMutate: () => setRefused(null),
    // The button that asked goes: the keyboard goes on from the one that shows what changed.
    onSuccess: () =>
      requestAnimationFrame(() => (window.document.getElementById(`${id}-changes`) ?? heading.current)?.focus()),
    onError: fail,
    onSettled: () => {
      for (const queryKey of [
        listingsKey(project.id),
        templatesKey(project.id),
        factTablesKey(project.id),
        factChangesKey(project.id),
        ['project', project.id],
        ME_KEY,
      ]) {
        void queryClient.invalidateQueries({ queryKey: [...queryKey] })
      }
    },
  })

  const listed = templates.data?.templates ?? []
  const own = listed.filter((template) => !template.built_in).length
  const room = own < (templates.data?.max_templates ?? 0)
  const modelAway = health.data?.llm.available === false
  const close = (saved: Done | null) => {
    setEditing(null)
    if (saved) {
      readAgain.reset()
      setDone(saved)
    }
    requestAnimationFrame(() => heading.current?.focus())
  }
  // The documents the last change offers to read again, until they are.
  const offer = changes && done?.read && done.read.documents > 0 && !readAgain.isSuccess ? done.read : null
  const queued = readAgain.isSuccess ? readAgain.data.queued : null

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <div className="flex h-11 shrink-0 items-center gap-1.5 border-b border-line bg-surface px-2">
        <IconButton label="Back to the library" onClick={() => workspace.goToFolder(null)}>
          <ArrowLeft className="size-4" />
        </IconButton>
        <h2 ref={heading} tabIndex={-1} className="flex-1 truncate text-[13px] font-semibold">
          Extraction templates
        </h2>
        {changes && !editing && (
          <Button
            icon={<Plus className="size-4" />}
            disabled={!templates.data || !room}
            title={room ? undefined : `A project has at most ${templates.data?.max_templates} templates of its own.`}
            onClick={() => {
              setDone(null)
              readAgain.reset()
              setEditing({ template: null })
            }}
          >
            New template
          </Button>
        )}
      </div>
      <div className="@container min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
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
        {editing ? (
          <TemplateForm template={editing.template} onClose={close} />
        ) : (
          <>
            <p className="text-xs text-muted">
              What is read from each kind of document in this project, as its facts. The AI model finds the kind a document is, then reads
              its template's fields. A built-in template applies until the project has its own for its kind.
            </p>
            {done && (
              <Notice tone="info">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <p>{queued != null ? queuedWords(queued) : `${done.said} ${savedWords(done.read)}`}</p>
                    {offer && modelAway && <p className="mt-0.5 text-xs text-muted">{MODEL_AWAY}</p>}
                  </div>
                  <span className="flex shrink-0 items-center gap-1">
                    {offer && (
                      <Button
                        icon={<RotateCcw className="size-4" />}
                        disabled={readAgain.isPending || modelAway}
                        onClick={() => setAsking('read')}
                      >
                        {readAgainQuestion(offer).action}
                      </Button>
                    )}
                    {queued != null && queued > 0 && done.read && (
                      <Button
                        id={`${id}-changes`}
                        icon={<FileDiff className="size-4" />}
                        onClick={() => workspace.showChanges(done.read?.kind ?? '')}
                      >
                        What changed
                      </Button>
                    )}
                    <IconButton
                      label="Dismiss"
                      onClick={() => {
                        setDone(null)
                        readAgain.reset()
                        heading.current?.focus()
                      }}
                      className="p-0.5!"
                    >
                      <X className="size-3.5" />
                    </IconButton>
                  </span>
                </div>
              </Notice>
            )}
            {templates.isPending && <Loading />}
            {templates.isError && <Notice tone="bad">Could not load the templates: {templates.error.message}</Notice>}
            <ul aria-label="Templates" className="space-y-2">
              {listed.map((template) => (
                <TemplateRow
                  key={template.id ?? template.doc_type}
                  template={template}
                  changes={changes}
                  busy={remove.isPending}
                  onEdit={() => {
                    setDone(null)
                    readAgain.reset()
                    setEditing({ template })
                  }}
                  onDelete={() => setAsking(template)}
                  onTable={() => workspace.showFacts(template.doc_type)}
                  onChanges={() => workspace.showChanges(template.doc_type)}
                />
              ))}
            </ul>
          </>
        )}
      </div>
      {asking === 'read' && offer && (
        <Confirm {...readAgainQuestion(offer)} danger={false} onConfirm={() => readAgain.mutate(offer)} onClose={() => setAsking(null)} />
      )}
      {asking && asking !== 'read' && (
        <Confirm {...deletionQuestion(asking)} onConfirm={() => remove.mutate(asking)} onClose={() => setAsking(null)} />
      )}
    </div>
  )
}

function TemplateRow({
  template,
  changes,
  busy,
  onEdit,
  onDelete,
  onTable,
  onChanges,
}: {
  template: ExtractionTemplate
  changes: boolean
  busy: boolean
  onEdit: () => void
  onDelete: () => void
  onTable: () => void
  onChanges: () => void
}) {
  const origin = originOf(template)
  return (
    <li className="rounded-md border border-line bg-surface px-3 py-2">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-1.5 text-[13px] font-medium">
            <span className="min-w-0 break-words">{template.title}</span>
            {origin && <Chip tone={template.built_in ? 'neutral' : 'accent'}>{origin}</Chip>}
          </p>
          <p className="text-xs text-muted">{templateWords(template)}</p>
        </div>
        {changes &&
          (template.built_in ? (
            <Button onClick={onEdit} aria-label={`Customise ${template.title}`} className="shrink-0">
              Customise
            </Button>
          ) : (
            <span className="flex shrink-0">
              <IconButton label={`Change ${template.title}`} disabled={busy} onClick={onEdit}>
                <Pencil className="size-4" />
              </IconButton>
              <IconButton label={`Delete ${template.title}`} danger disabled={busy} onClick={onDelete}>
                <Trash2 className="size-4" />
              </IconButton>
            </span>
          ))}
      </div>
      {(template.documents > 0 || template.changed > 0 || template.reading > 0) && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {template.documents > 0 && (
            <Button icon={<Table2 className="size-4" />} onClick={onTable} aria-label={`Facts table of ${template.title}`}>
              Facts table
            </Button>
          )}
          {(template.changed > 0 || template.reading > 0) && (
            <Button icon={<FileDiff className="size-4" />} onClick={onChanges} aria-label={`What changed in the ${template.title} facts`}>
              What changed
            </Button>
          )}
        </div>
      )}
      <details className="mt-1.5">
        <summary className="cursor-pointer text-xs text-muted hover:text-ink">Its fields</summary>
        <ul className="mt-1.5 space-y-1">
          {template.fields.map((field) => (
            <li key={field.name} className="text-xs leading-snug">
              <span className="font-mono text-ink">{field.name}</span>
              <span className="text-muted">
                {' '}
                · {FACT_TYPES[field.type]}
                {field.required ? ', required' : ''}
                {field.description ? ` · ${field.description}` : ''}
                {field.examples.length ? ` · for example ${field.examples.join(', ')}` : ''}
              </span>
            </li>
          ))}
        </ul>
      </details>
    </li>
  )
}

type Row = FieldDraft & { key: number }

/**
 * A template written whole: its name, the kind of document it is for and its fields. A built-in
 * one opened here is saved as the project's own, in the built-in one's place.
 */
function TemplateForm({ template, onClose }: { template: ExtractionTemplate | null; onClose: (saved: Done | null) => void }) {
  const { project } = useWorkspace()
  const queryClient = useQueryClient()
  const id = useId()
  const [draft, setDraft] = useState(() => draftOf(template))
  const [rows, setRows] = useState<Row[]>(() => draft.fields.map((field, key) => ({ ...field, key })))
  const nextKey = useRef(rows.length)
  // The row the member just added, whose name takes the focus as it appears.
  const [added, setAdded] = useState<number | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [refused, setRefused] = useState<PlanRefusal | null>(null)
  const own = template && !template.built_in ? template : null

  const save = useMutation({
    mutationFn: () => {
      const input = inputOf({ ...draft, fields: rows })
      return own?.id != null ? api.updateTemplate(own.id, input) : api.createTemplate(project.id, input)
    },
    onMutate: () => {
      setProblem(null)
      setRefused(null)
    },
    onSuccess: (saved) => {
      for (const queryKey of [templatesKey(project.id), factTablesKey(project.id)]) void queryClient.invalidateQueries({ queryKey })
      onClose({ said: `Saved “${saved.title}”.`, read: savedReadAgain(saved, template) })
    },
    onError: (error) => (isPlanLimit(error) ? setRefused({ message: error.message, limit: error.limit }) : setProblem(error.message)),
  })

  const change = (key: number, patch: Partial<FieldDraft>) =>
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...patch } : row)))
  const heading = own ? `Change “${own.title}”` : template ? `Your own ${template.title} template` : 'New template'

  return (
    <form
      aria-labelledby={`${id}-heading`}
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault()
        const found = draftProblem({ ...draft, fields: rows })
        if (found) setProblem(found)
        else save.mutate()
      }}
    >
      <div>
        <h3 id={`${id}-heading`} className="text-[13px] font-semibold">
          {heading}
        </h3>
        {template?.built_in && (
          <p className="mt-0.5 text-xs text-muted">
            Saved, it is this project's own template for {template.doc_type} documents, in the built-in one's place. Deleting it brings the
            built-in one back.
          </p>
        )}
      </div>
      <InputField
        label="Name"
        value={draft.title}
        maxLength={200}
        autoFocus
        onChange={(event) => setDraft({ ...draft, title: event.target.value })}
      />
      <InputField
        label="Kind of document"
        hint="A few words, such as invoice or delivery note: the AI model finds which documents are of this kind by them."
        value={draft.doc_type}
        maxLength={100}
        onChange={(event) => setDraft({ ...draft, doc_type: event.target.value })}
      />
      <InputField
        label="What the kind is (optional)"
        value={draft.description}
        maxLength={300}
        onChange={(event) => setDraft({ ...draft, description: event.target.value })}
      />
      {rows.map((row, index) => (
        <FieldEditor
          key={row.key}
          row={row}
          number={index + 1}
          focused={row.key === added}
          removable={rows.length > 1}
          onChange={(patch) => change(row.key, patch)}
          onRemove={() => {
            setRows((current) => current.filter((other) => other.key !== row.key))
            // The row goes with the focus: the keyboard goes on from the button that adds one.
            window.document.getElementById(`${id}-add`)?.focus()
          }}
        />
      ))}
      <Button
        id={`${id}-add`}
        icon={<Plus className="size-4" />}
        disabled={rows.length >= 30}
        onClick={() => {
          const key = nextKey.current++
          setRows((current) => [...current, { ...NEW_FIELD, key }])
          setAdded(key)
        }}
      >
        Add a field
      </Button>
      {problem && (
        <p role="alert" className="text-xs text-bad">
          {problem}
        </p>
      )}
      {refused && <PlanLimitNotice refusal={refused} onDismiss={() => setRefused(null)} />}
      <div className="flex justify-end gap-2">
        <Button onClick={() => onClose(null)}>Cancel</Button>
        <Button type="submit" variant="primary" disabled={save.isPending}>
          {save.isPending ? 'Saving…' : 'Save'}
        </Button>
      </div>
    </form>
  )
}

function FieldEditor({
  row,
  number,
  focused,
  removable,
  onChange,
  onRemove,
}: {
  row: Row
  number: number
  focused: boolean
  removable: boolean
  onChange: (patch: Partial<FieldDraft>) => void
  onRemove: () => void
}) {
  const named = factName(row.name)
  return (
    <fieldset className="space-y-2 rounded-md border border-line bg-surface p-2.5">
      <legend className="px-1 text-xs font-medium text-muted">Field {number}</legend>
      <div className="grid gap-2 @sm:grid-cols-[1fr_10rem]">
        <InputField
          label="Name"
          value={row.name}
          maxLength={100}
          autoFocus={focused}
          hint={named && named !== row.name.trim() ? `Its facts are named ${named}.` : undefined}
          onChange={(event) => onChange({ name: event.target.value })}
        />
        <SelectField label="Type" value={row.type} onChange={(event) => onChange({ type: event.target.value as FactType })}>
          {(Object.keys(FACT_TYPES) as FactType[]).map((type) => (
            <option key={type} value={type}>
              {FACT_TYPES[type]}
            </option>
          ))}
        </SelectField>
      </div>
      <InputField label="What it is" value={row.description} maxLength={300} onChange={(event) => onChange({ description: event.target.value })} />
      <InputField
        label="Examples"
        hint="As documents write them; separate several with a semicolon."
        value={row.examples}
        onChange={(event) => onChange({ examples: event.target.value })}
      />
      <div className="flex items-center justify-between gap-2">
        <Checkbox label="Every document of the kind states it" checked={row.required} onChange={(event) => onChange({ required: event.target.checked })} />
        {removable && (
          <IconButton label={`Remove field ${number}`} danger onClick={onRemove}>
            <X className="size-4" />
          </IconButton>
        )}
      </div>
    </fieldset>
  )
}
