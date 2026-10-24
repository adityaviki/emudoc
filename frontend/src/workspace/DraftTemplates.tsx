import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Pencil, Plus, RotateCcw, X } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { api, mayChange } from '../api.ts'
import type { DraftKind, DraftSection, DraftTemplate } from '../api.ts'
import { useMe } from '../auth.ts'
import { Confirm } from '../components/Confirm.tsx'
import { Button, Checkbox, Chip, FIELD, IconButton, InputField, Loading, Notice } from '../components/ui.tsx'
import {
  GUIDANCE_CHARS,
  HEADING_CHARS,
  MAX_SECTIONS,
  NEW_SECTION,
  SECTION_CHARS,
  TONE_CHARS,
  builtInAgainQuestion,
  draftTemplatesKey,
  originWords,
  sectionsWords,
  templateFormOf,
  templateInputOf,
  templateProblem,
} from '../draftTemplates.ts'
import { KIND_LABELS } from '../drafts.ts'
import { useWorkspace } from './state.ts'

/**
 * How the project's drafts of each kind read, in the library's place: the template in force for each of the five kinds,
 * the built-in one or the project's own in its place, its tone, its parts and what else it asks. Whoever may change
 * the project customises a built-in one, changes its own and gives a kind its built-in one back; a viewer reads them.
 */
export function DraftTemplatesView() {
  const workspace = useWorkspace()
  const { project } = workspace
  const changes = mayChange(useMe().role)
  const queryClient = useQueryClient()
  const templates = useQuery({ queryKey: draftTemplatesKey(project.id), queryFn: () => api.draftTemplates(project.id) })
  const [editing, setEditing] = useState<DraftTemplate | null>(null)
  const [asking, setAsking] = useState<DraftKind | null>(null)
  const [said, setSaid] = useState<string | null>(null)
  const [refused, setRefused] = useState<string | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  // Opened from a button that went with the view before it: the keyboard goes on from here.
  useEffect(() => {
    if (window.document.activeElement === window.document.body) heading.current?.focus()
  }, [])

  const reset = useMutation({
    mutationFn: (kind: DraftKind) => api.deleteDraftTemplate(project.id, kind),
    onMutate: () => {
      setRefused(null)
      // The row's buttons change under the focus: the keyboard goes on from the heading.
      heading.current?.focus()
    },
    onSuccess: (_, kind) => setSaid(`The ${KIND_LABELS[kind].toLowerCase()} template is the built-in one again.`),
    onError: (error) => setRefused(error.message),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: draftTemplatesKey(project.id) }),
  })
  const close = (saved: string | null) => {
    setEditing(null)
    if (saved) setSaid(saved)
    requestAnimationFrame(() => heading.current?.focus())
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <div className="flex h-11 shrink-0 items-center gap-1.5 border-b border-line bg-surface px-2">
        <IconButton label="Back to the drafts" onClick={workspace.showDrafts}>
          <ArrowLeft className="size-4" />
        </IconButton>
        <h2 ref={heading} tabIndex={-1} className="flex-1 truncate text-[13px] font-semibold">
          Draft templates
        </h2>
      </div>
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
        {refused && <Notice tone="bad">{refused}</Notice>}
        {editing ? (
          <TemplateForm template={editing} onClose={close} />
        ) : (
          <>
            <p className="text-xs text-muted">
              How the project’s drafts of each kind read: their tone, their parts in order and what else every draft of the kind
              should do. A built-in template applies until the project has its own for the kind. Whatever a template says, the
              model cites each figure where it is written and leaves what no document states for you to fill in.
            </p>
            {said && (
              <Notice tone="info">
                <div className="flex items-start justify-between gap-2">
                  <span>{said}</span>
                  <IconButton
                    label="Dismiss"
                    onClick={() => {
                      setSaid(null)
                      heading.current?.focus()
                    }}
                    className="p-0.5!"
                  >
                    <X className="size-3.5" />
                  </IconButton>
                </div>
              </Notice>
            )}
            {templates.isPending && <Loading />}
            {templates.isError && <Notice tone="bad">Could not load the templates: {templates.error.message}</Notice>}
            <ul aria-label="Draft templates" className="space-y-2">
              {templates.data?.templates.map((template) => (
                <TemplateRow
                  key={template.kind}
                  template={template}
                  changes={changes}
                  busy={reset.isPending}
                  onEdit={() => {
                    setSaid(null)
                    setEditing(template)
                  }}
                  onReset={() => setAsking(template.kind)}
                />
              ))}
            </ul>
          </>
        )}
      </div>
      {asking && <Confirm {...builtInAgainQuestion(asking)} onConfirm={() => reset.mutate(asking)} onClose={() => setAsking(null)} />}
    </div>
  )
}

function TemplateRow({
  template,
  changes,
  busy,
  onEdit,
  onReset,
}: {
  template: DraftTemplate
  changes: boolean
  busy: boolean
  onEdit: () => void
  onReset: () => void
}) {
  const name = KIND_LABELS[template.kind]
  return (
    <li className="rounded-md border border-line bg-surface px-3 py-2">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-1.5 text-[13px] font-medium">
            <span>{name}</span>
            <Chip tone={template.built_in ? 'neutral' : 'accent'}>{originWords(template)}</Chip>
          </p>
          {template.tone && <p className="text-xs break-words text-ink">{template.tone}</p>}
          <p className="text-xs break-words text-muted">{sectionsWords(template)}</p>
        </div>
        {changes &&
          (template.built_in ? (
            <Button onClick={onEdit} aria-label={`Customise ${name}`} className="shrink-0">
              Customise
            </Button>
          ) : (
            <span className="flex shrink-0">
              <IconButton label={`Change ${name}`} disabled={busy} onClick={onEdit}>
                <Pencil className="size-4" />
              </IconButton>
              <IconButton label={`Use the built-in ${name.toLowerCase()} template`} disabled={busy} onClick={onReset}>
                <RotateCcw className="size-4" />
              </IconButton>
            </span>
          ))}
      </div>
      <details className="mt-1.5">
        <summary className="cursor-pointer text-xs text-muted hover:text-ink">Its parts</summary>
        <ol className="mt-1.5 list-decimal space-y-1 pl-5">
          {template.sections.map((section) => (
            <li key={section.heading} className="text-xs leading-snug">
              <span className="text-ink">{section.heading}</span>
              <span className="text-muted">
                {section.required ? '' : ', where it applies'}
                {section.description ? ` · ${section.description}` : ''}
              </span>
            </li>
          ))}
        </ol>
        {template.guidance && <p className="mt-1.5 text-xs break-words text-muted">Also: {template.guidance}</p>}
      </details>
    </li>
  )
}

type Row = DraftSection & { key: number }

/** A kind's template written whole: its tone, its parts and what else it asks. A built-in one saved here becomes the project's own. */
function TemplateForm({ template, onClose }: { template: DraftTemplate; onClose: (said: string | null) => void }) {
  const { project } = useWorkspace()
  const queryClient = useQueryClient()
  const id = useId()
  const [form, setForm] = useState(() => templateFormOf(template))
  const [rows, setRows] = useState<Row[]>(() => form.sections.map((section, key) => ({ ...section, key })))
  const nextKey = useRef(rows.length)
  // The part the member just added, whose name takes the focus as it appears.
  const [added, setAdded] = useState<number | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const name = KIND_LABELS[template.kind].toLowerCase()
  const written = { ...form, sections: rows }

  const save = useMutation({
    mutationFn: () => api.saveDraftTemplate(project.id, template.kind, templateInputOf(written)),
    onMutate: () => setProblem(null),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: draftTemplatesKey(project.id) })
      onClose(`Saved the project’s own ${name} template.`)
    },
    onError: (error) => setProblem(error.message),
  })
  const change = (key: number, patch: Partial<DraftSection>) => setRows((current) => current.map((row) => (row.key === key ? { ...row, ...patch } : row)))

  return (
    <form
      aria-labelledby={`${id}-heading`}
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault()
        const found = templateProblem(written)
        if (found) setProblem(found)
        else save.mutate()
      }}
    >
      <div>
        <h3 id={`${id}-heading`} className="text-[13px] font-semibold">
          {template.built_in ? `Your own ${name} template` : `Change the ${name} template`}
        </h3>
        {template.built_in && (
          <p className="mt-0.5 text-xs text-muted">
            Saved, it is how this project’s {name}s are written, in the built-in template’s place. Using the built-in one again brings
            it back.
          </p>
        )}
      </div>
      <div>
        <label htmlFor={`${id}-tone`} className="mb-1 block text-xs font-medium text-muted">
          Tone
        </label>
        <textarea
          id={`${id}-tone`}
          className={`${FIELD} min-h-14 resize-y`}
          rows={2}
          maxLength={TONE_CHARS}
          autoFocus
          aria-describedby={`${id}-tone-hint`}
          value={form.tone}
          onChange={(event) => setForm({ ...form, tone: event.target.value })}
        />
        <p id={`${id}-tone-hint`} className="mt-1 text-xs text-muted">
          How it reads: formal or plain, in which language, how the reader is addressed.
        </p>
      </div>
      {rows.map((row, index) => (
        <SectionEditor
          key={row.key}
          row={row}
          number={index + 1}
          focused={row.key === added}
          onChange={(patch) => change(row.key, patch)}
          onRemove={() => {
            setRows((current) => current.filter((other) => other.key !== row.key))
            // The part goes with the focus: the keyboard goes on from the button that adds one.
            window.document.getElementById(`${id}-add`)?.focus()
          }}
        />
      ))}
      <Button
        id={`${id}-add`}
        icon={<Plus className="size-4" />}
        disabled={rows.length >= MAX_SECTIONS}
        onClick={() => {
          const key = nextKey.current++
          setRows((current) => [...current, { ...NEW_SECTION, key }])
          setAdded(key)
        }}
      >
        Add a part
      </Button>
      <div>
        <label htmlFor={`${id}-guidance`} className="mb-1 block text-xs font-medium text-muted">
          Also (optional)
        </label>
        <textarea
          id={`${id}-guidance`}
          className={`${FIELD} min-h-14 resize-y`}
          rows={2}
          maxLength={GUIDANCE_CHARS}
          aria-describedby={`${id}-guidance-hint`}
          value={form.guidance}
          onChange={(event) => setForm({ ...form, guidance: event.target.value })}
        />
        <p id={`${id}-guidance-hint`} className="mt-1 text-xs text-muted">
          Anything else every {name} should do, such as a reference to quote or how to sign.
        </p>
      </div>
      {problem && (
        <p role="alert" className="text-xs text-bad">
          {problem}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button onClick={() => onClose(null)}>Cancel</Button>
        <Button type="submit" variant="primary" disabled={save.isPending}>
          {save.isPending ? 'Saving…' : 'Save'}
        </Button>
      </div>
    </form>
  )
}

function SectionEditor({
  row,
  number,
  focused,
  onChange,
  onRemove,
}: {
  row: Row
  number: number
  focused: boolean
  onChange: (patch: Partial<DraftSection>) => void
  onRemove: () => void
}) {
  return (
    <fieldset className="space-y-2 rounded-md border border-line bg-surface p-2.5">
      <legend className="px-1 text-xs font-medium text-muted">Part {number}</legend>
      <InputField label="Name" value={row.heading} maxLength={HEADING_CHARS} autoFocus={focused} onChange={(event) => onChange({ heading: event.target.value })} />
      <InputField label="What goes in it" value={row.description} maxLength={SECTION_CHARS} onChange={(event) => onChange({ description: event.target.value })} />
      <div className="flex items-center justify-between gap-2">
        <Checkbox label="Every draft has it" checked={row.required} onChange={(event) => onChange({ required: event.target.checked })} />
        <IconButton label={`Remove part ${number}`} danger onClick={onRemove}>
          <X className="size-4" />
        </IconButton>
      </div>
    </fieldset>
  )
}
