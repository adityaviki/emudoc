import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Pause, Pencil, Play, Plus, Trash2, X } from 'lucide-react'
import { useId, useRef, useState } from 'react'
import { api, mayChange } from '../api.ts'
import type { Automation, CrossCheck, ExtractionTemplate } from '../api.ts'
import { useMe } from '../auth.ts'
import {
  AUDIENCES,
  REVIEWS,
  automationDeletionQuestion,
  automationSentence,
  automationStatus,
  automationsKey,
  draftOf,
  draftProblem,
  folderChoices,
  inputOf,
  wordedDraft,
} from '../automations.ts'
import type { AutomationDraft } from '../automations.ts'
import { Confirm } from '../components/Confirm.tsx'
import { Button, Checkbox, IconButton, InputField, Loading, Notice, Radio, SelectField } from '../components/ui.tsx'
import { useFolders } from '../hooks.ts'
import { reviewersKey } from '../reviews.ts'
import { useWorkspace } from './state.ts'

/**
 * The project's automations, under its cross-checks in their view: each runs them on its own when
 * documents of a kind arrive in a folder, or anywhere, and tells whom it names what they found,
 * the runs it makes listed with the others. Whoever may change the project makes, changes, turns
 * off and deletes them; anyone who sees it reads them.
 */
export function AutomationsSection({ checks, templates }: { checks: CrossCheck[]; templates: ExtractionTemplate[] }) {
  const { project } = useWorkspace()
  const me = useMe()
  const changes = mayChange(me.role)
  const queryClient = useQueryClient()
  const automations = useQuery({ queryKey: automationsKey(project.id), queryFn: () => api.automations(project.id) })
  const [editing, setEditing] = useState<{ automation: Automation | null } | null>(null)
  const [asking, setAsking] = useState<Automation | null>(null)
  const [said, setSaid] = useState<string | null>(null)
  const [refused, setRefused] = useState<string | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const invalidate = () => void queryClient.invalidateQueries({ queryKey: automationsKey(project.id) })

  const remove = useMutation({
    mutationFn: (automation: Automation) => api.deleteAutomation(automation.id),
    onMutate: () => {
      setRefused(null)
      // The row goes with its buttons: the keyboard goes on from the heading.
      heading.current?.focus()
    },
    onSuccess: (_, automation) => setSaid(`Deleted “${automation.title}”.`),
    onError: (error) => setRefused(error.message),
    onSettled: invalidate,
  })
  const turn = useMutation({
    mutationFn: (automation: Automation) => api.updateAutomation(automation.id, { ...inputOf(draftOf(automation, [])), enabled: !automation.enabled }),
    onMutate: () => setRefused(null),
    onSuccess: (turned) => setSaid(`Turned “${turned.title}” ${turned.enabled ? 'on' : 'off'}.`),
    onError: (error) => setRefused(error.message),
    onSettled: invalidate,
  })

  const listed = automations.data?.automations ?? []
  const room = listed.length < (automations.data?.max_automations ?? 0)
  const makerOf = (automation: Automation) => (automation.created_by === me.id ? 'you' : (automation.made_by ?? 'whoever made it'))
  const close = (saved: string | null) => {
    setEditing(null)
    if (saved) setSaid(saved)
    requestAnimationFrame(() => heading.current?.focus())
  }

  return (
    <section aria-labelledby="automations" className="space-y-2 pt-2">
      <div className="flex items-center justify-between gap-2">
        <h3 id="automations" ref={heading} tabIndex={-1} className="text-xs font-semibold text-muted">
          Automations
        </h3>
        {changes && !editing && (
          <Button
            icon={<Plus className="size-4" />}
            disabled={!automations.data || !room}
            title={room ? undefined : `A project has at most ${automations.data?.max_automations} automations.`}
            onClick={() => {
              setSaid(null)
              setEditing({ automation: null })
            }}
          >
            New automation
          </Button>
        )}
      </div>
      <p className="text-xs text-muted">
        An automation runs the cross-checks on its own once documents arrive, and tells whoever should know what they found about them.
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
      {said && !editing && (
        <Notice tone="info">
          <div className="flex items-start justify-between gap-3">
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
      {automations.isPending && <Loading />}
      {automations.isError && <Notice tone="bad">Could not load the automations: {automations.error.message}</Notice>}
      {editing ? (
        <AutomationForm automation={editing.automation} checks={checks} templates={templates} onClose={close} />
      ) : (
        <>
          {automations.data && listed.length === 0 && <p className="text-xs text-muted">No automations yet.</p>}
          {listed.length > 0 && (
            <ul aria-label="Automations" className="space-y-2">
              {listed.map((automation) => (
                <li key={automation.id} className="rounded-md border border-line bg-surface px-3 py-2">
                  <div className="flex items-start gap-2">
                    <div className="min-w-0 flex-1">
                      <p className="text-[13px] font-medium break-words">{automation.title}</p>
                      <p className="text-xs text-muted">{automationSentence(automation, makerOf(automation))}</p>
                      <p className="text-xs text-ink">{automationStatus(automation)}</p>
                      {automation.problem && <p className="mt-1 text-xs text-warn">{automation.problem}</p>}
                    </div>
                    {changes && (
                      <span className="flex shrink-0">
                        <IconButton
                          label={`${automation.enabled ? 'Turn off' : 'Turn on'} ${automation.title}`}
                          // One whose chosen cross-checks all went is changed in the form, which asks for others.
                          disabled={turn.isPending || automation.check_ids?.length === 0}
                          onClick={() => turn.mutate(automation)}
                        >
                          {automation.enabled ? <Pause className="size-4" /> : <Play className="size-4" />}
                        </IconButton>
                        <IconButton
                          label={`Change ${automation.title}`}
                          onClick={() => {
                            setSaid(null)
                            setEditing({ automation })
                          }}
                        >
                          <Pencil className="size-4" />
                        </IconButton>
                        <IconButton label={`Delete ${automation.title}`} danger disabled={remove.isPending} onClick={() => setAsking(automation)}>
                          <Trash2 className="size-4" />
                        </IconButton>
                      </span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {asking && <Confirm {...automationDeletionQuestion(asking)} onConfirm={() => remove.mutate(asking)} onClose={() => setAsking(null)} />}
    </section>
  )
}

/** An automation written whole: its name, what it watches arrive where, what it runs, with a report or not, what it puts in review for whom, whom it tells, its sentence said as it is chosen. */
function AutomationForm({
  automation,
  checks,
  templates,
  onClose,
}: {
  automation: Automation | null
  checks: CrossCheck[]
  templates: ExtractionTemplate[]
  onClose: (saved: string | null) => void
}) {
  const { project } = useWorkspace()
  const me = useMe()
  const queryClient = useQueryClient()
  const id = useId()
  const folders = useFolders(project.id)
  const reviewers = useQuery({ queryKey: reviewersKey(project.id), queryFn: () => api.reviewers(project.id), staleTime: 60_000 })
  const [draft, setDraft] = useState<AutomationDraft>(() => draftOf(automation, templates.map((template) => template.doc_type)))
  const [problem, setProblem] = useState<string | null>(null)
  const change = (patch: Partial<AutomationDraft>) => setDraft((current) => ({ ...current, ...patch }))

  const save = useMutation({
    mutationFn: () => (automation ? api.updateAutomation(automation.id, inputOf(draft)) : api.createAutomation(project.id, inputOf(draft))),
    onMutate: () => setProblem(null),
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: automationsKey(project.id) })
      onClose(`Saved “${saved.title}”.`)
    },
    onError: (error) => setProblem(error.message),
  })

  if (!folders.data) return <Loading />
  const choices = folderChoices(folders.data)
  const names = new Map(checks.map((check) => [check.id, check.title]))
  // Whom it may assign, and one it assigns who may no longer, while it does.
  const members = new Map((reviewers.data ?? []).map((reviewer) => [reviewer.id, reviewer.name]))
  if (automation?.assignee_id != null && !members.has(automation.assignee_id)) {
    members.set(automation.assignee_id, automation.assignee ?? 'A former member')
  }
  const maker = automation && automation.created_by !== me.id ? (automation.made_by ?? 'whoever made it') : 'you'

  return (
    <form
      aria-labelledby={`${id}-heading`}
      className="@container space-y-3 rounded-md border border-line bg-surface p-2.5"
      onSubmit={(event) => {
        event.preventDefault()
        const found = draftProblem(draft)
        if (found) setProblem(found)
        else save.mutate()
      }}
    >
      <h4 id={`${id}-heading`} className="text-[13px] font-semibold">
        {automation ? `Change “${automation.title}”` : 'New automation'}
      </h4>
      <InputField label="Name" value={draft.title} maxLength={200} autoFocus onChange={(event) => change({ title: event.target.value })} />
      <div className="grid gap-2 @sm:grid-cols-2">
        <SelectField label="When this arrives" value={draft.kind} onChange={(event) => change({ kind: event.target.value })}>
          <option value="">Any document</option>
          {templates.map((template) => (
            <option key={template.doc_type} value={template.doc_type}>
              {template.title}
            </option>
          ))}
        </SelectField>
        <SelectField
          label="In"
          value={draft.folder_id ?? ''}
          hint="A folder’s subfolders with it."
          onChange={(event) => change({ folder_id: event.target.value ? Number(event.target.value) : null })}
        >
          <option value="">Anywhere in the project</option>
          {choices.map((folder) => (
            <option key={folder.id} value={folder.id}>
              {folder.label}
            </option>
          ))}
        </SelectField>
      </div>
      <fieldset className="space-y-2">
        <legend className="text-xs font-medium text-muted">Run</legend>
        <Radio name={`${id}-checks`} label="Every cross-check of the project" checked={draft.every} onChange={() => change({ every: true })} />
        <Radio name={`${id}-checks`} label="These cross-checks" checked={!draft.every} onChange={() => change({ every: false })} />
        {!draft.every && (
          <div className="space-y-1.5 pl-6.5">
            {checks.map((check) => (
              <Checkbox
                key={check.id}
                label={check.title}
                checked={draft.check_ids.includes(check.id)}
                onChange={(event) =>
                  change({ check_ids: event.target.checked ? [...draft.check_ids, check.id] : draft.check_ids.filter((chosen) => chosen !== check.id) })
                }
              />
            ))}
          </div>
        )}
      </fieldset>
      <Checkbox
        label="With a discrepancy report by the model, which counts as a question asked"
        checked={draft.report}
        onChange={(event) => change({ report: event.target.checked })}
      />
      <Checkbox
        label="Run them again when a document they compare it with arrives anywhere in the project, such as its delivery note"
        checked={draft.recheck}
        onChange={(event) => change({ recheck: event.target.checked })}
      />
      <div className="grid gap-2 @sm:grid-cols-2">
        <SelectField
          label="Put in review"
          value={draft.review}
          onChange={(event) => change({ review: event.target.value as AutomationDraft['review'] })}
        >
          {REVIEWS.map((review) => (
            <option key={review.value} value={review.value}>
              {review.label}
            </option>
          ))}
        </SelectField>
        {draft.review && (
          <SelectField
            label="Assigned to"
            value={draft.assignee_id ?? ''}
            hint="Never a document a member put in review already."
            onChange={(event) => change({ assignee_id: event.target.value ? Number(event.target.value) : null })}
          >
            <option value="">Nobody</option>
            {[...members].map(([memberId, name]) => (
              <option key={memberId} value={memberId}>
                {memberId === me.id ? `${name} (you)` : name}
              </option>
            ))}
          </SelectField>
        )}
      </div>
      <SelectField
        label="Tell what they found"
        value={draft.audience}
        onChange={(event) => change({ audience: event.target.value as AutomationDraft['audience'] })}
      >
        {AUDIENCES.map((audience) => (
          <option key={audience.value} value={audience.value}>
            {audience.label}
          </option>
        ))}
      </SelectField>
      <p className="rounded-md bg-sunken px-3 py-2 text-xs text-ink">{automationSentence(wordedDraft(draft, choices, names, members), maker)}</p>
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
