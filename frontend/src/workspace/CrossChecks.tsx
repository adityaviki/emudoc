import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ListChecks, Pencil, Plus, Trash2, X } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { api, mayChange } from '../api.ts'
import type { CrossCheck, CrossCheckSuggestion, ExtractionTemplate, FindingOutcome } from '../api.ts'
import { useMe } from '../auth.ts'
import { Confirm } from '../components/Confirm.tsx'
import { Button, Checkbox, EmptyState, IconButton, InputField, Loading, Notice, Radio, Segmented, SelectField } from '../components/ui.tsx'
import {
  OUTCOMES,
  OUTCOME_CHOICES,
  checkSentence,
  comparisonsFor,
  countsWords,
  crossChecksKey,
  deletionQuestion,
  draftOf,
  draftProblem,
  fieldType,
  findingsKey,
  fitted,
  inputOf,
  marginUnit,
  suggestionInput,
  wordedDraft,
} from '../crossChecks.ts'
import type { CheckDraft } from '../crossChecks.ts'
import { fieldWords, sourceOf } from '../factTable.ts'
import { templatesKey } from '../templates.ts'
import { RunsSection } from './CheckRuns.tsx'
import { FindingItem } from './Findings.tsx'
import { useWorkspace } from './state.ts'

/** What the form is open on: a new rule, or one of those listed. */
type Editing = { check: CrossCheck | null }

/**
 * The project's cross-checks, in the library's place: rules that compare a field of one kind of
 * document with a field of the same document, of the documents of another kind that state the
 * same key, or of every document of another kind, each with what it finds now, and the rules
 * suggested for the project's templates. Whoever may change the project makes, changes and
 * deletes them; anyone who sees it reads what each finds.
 */
export function CrossChecksView() {
  const workspace = useWorkspace()
  const { project } = workspace
  const changes = mayChange(useMe().role)
  const queryClient = useQueryClient()
  const checks = useQuery({ queryKey: crossChecksKey(project.id), queryFn: () => api.crossChecks(project.id) })
  const templates = useQuery({ queryKey: templatesKey(project.id), queryFn: () => api.templates(project.id) })
  const [editing, setEditing] = useState<Editing | null>(null)
  const [asking, setAsking] = useState<CrossCheck | null>(null)
  const [said, setSaid] = useState<string | null>(null)
  const [refused, setRefused] = useState<string | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  // Opened from a button that went with the view before it: the keyboard goes on from here.
  useEffect(() => {
    if (window.document.activeElement === window.document.body) heading.current?.focus()
  }, [])
  const invalidate = () => void queryClient.invalidateQueries({ queryKey: crossChecksKey(project.id) })

  const remove = useMutation({
    mutationFn: (check: CrossCheck) => api.deleteCrossCheck(check.id),
    onMutate: () => {
      setRefused(null)
      // The row goes with its buttons: the keyboard goes on from the heading.
      heading.current?.focus()
    },
    onSuccess: (_, check) => setSaid(`Deleted “${check.title}”.`),
    onError: (error) => setRefused(error.message),
    onSettled: invalidate,
  })
  const add = useMutation({
    mutationFn: (suggestion: CrossCheckSuggestion) => api.createCrossCheck(project.id, suggestionInput(suggestion)),
    onMutate: () => setRefused(null),
    onSuccess: (check) => {
      setSaid(`Added “${check.title}”.`)
      // The suggestion goes with its button: the keyboard goes on from the heading.
      heading.current?.focus()
    },
    onError: (error) => setRefused(error.message),
    onSettled: invalidate,
  })

  const listed = checks.data?.checks ?? []
  const suggested = checks.data?.suggestions ?? []
  const room = listed.length < (checks.data?.max_checks ?? 0)
  const kinds = templates.data?.templates ?? []
  const close = (saved: string | null) => {
    setEditing(null)
    if (saved) setSaid(saved)
    requestAnimationFrame(() => heading.current?.focus())
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <div className="flex h-11 shrink-0 items-center gap-1.5 border-b border-line bg-surface px-2">
        <IconButton label="Back to the library" onClick={() => workspace.goToFolder(null)}>
          <ArrowLeft className="size-4" />
        </IconButton>
        <h2 ref={heading} tabIndex={-1} className="flex-1 truncate text-[13px] font-semibold">
          Cross-checks
        </h2>
        {changes && !editing && (
          <Button
            icon={<Plus className="size-4" />}
            disabled={!checks.data || !templates.data || !room}
            title={room ? undefined : `A project has at most ${checks.data?.max_checks} cross-checks.`}
            onClick={() => {
              setSaid(null)
              setEditing({ check: null })
            }}
          >
            New cross-check
          </Button>
        )}
      </div>
      <div className="@container min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
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
        {editing && !templates.data ? (
          <Loading />
        ) : editing ? (
          <CheckForm check={editing.check} templates={kinds} onClose={close} />
        ) : (
          <>
            <p className="text-xs text-muted">
              Rules that compare the facts of this project's documents with one another: an invoice's prices with the price list, its
              quantities with what was delivered. What each finds is worked out from the facts as they read now, a member's corrections
              included.
            </p>
            {said && (
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
            {checks.isPending && <Loading />}
            {checks.isError && <Notice tone="bad">Could not load the cross-checks: {checks.error.message}</Notice>}
            {/* With suggestions below, a line says it; without, the empty page does. */}
            {checks.data && listed.length === 0 && suggested.length > 0 && (
              <p className="text-[13px] font-medium">No cross-checks yet{changes ? ': add a suggested one, or make your own.' : '.'}</p>
            )}
            {checks.data && listed.length === 0 && suggested.length === 0 && (
              <EmptyState icon={<ListChecks />} title="No cross-checks yet">
                {changes ? 'Make one to compare the facts of one kind of document with another’s.' : 'Whoever may change this project makes them.'}
              </EmptyState>
            )}
            {listed.length > 0 && (
              <ul aria-label="Cross-checks" className="space-y-2">
                {listed.map((check) => (
                  <CheckRow
                    key={check.id}
                    check={check}
                    changes={changes}
                    busy={remove.isPending}
                    onOpen={() => workspace.showFindings(check.id)}
                    onEdit={() => {
                      setSaid(null)
                      setEditing({ check })
                    }}
                    onDelete={() => setAsking(check)}
                  />
                ))}
              </ul>
            )}
            {listed.length > 0 && <RunsSection runnable={listed.some((check) => !check.problem)} changes={changes} />}
            {suggested.length > 0 && (
              <section aria-labelledby="suggested-checks" className="space-y-2 pt-2">
                <h3 id="suggested-checks" className="text-xs font-semibold text-muted">
                  Suggested for this project's templates
                </h3>
                <ul aria-label="Suggested cross-checks" className="space-y-2">
                  {suggested.map((suggestion) => (
                    <li key={suggestion.title} className="flex items-start gap-2 rounded-md border border-dashed border-line px-3 py-2">
                      <div className="min-w-0 flex-1">
                        <p className="text-[13px] font-medium break-words">{suggestion.title}</p>
                        <p className="text-xs text-muted">{checkSentence(suggestion)}</p>
                      </div>
                      {changes && (
                        <Button
                          icon={<Plus className="size-4" />}
                          disabled={add.isPending || !room}
                          onClick={() => add.mutate(suggestion)}
                          aria-label={`Add ${suggestion.title}`}
                          className="shrink-0"
                        >
                          Add
                        </Button>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </>
        )}
      </div>
      {asking && <Confirm {...deletionQuestion(asking)} onConfirm={() => remove.mutate(asking)} onClose={() => setAsking(null)} />}
    </div>
  )
}

function CheckRow({
  check,
  changes,
  busy,
  onOpen,
  onEdit,
  onDelete,
}: {
  check: CrossCheck
  changes: boolean
  busy: boolean
  onOpen: () => void
  onEdit: () => void
  onDelete: () => void
}) {
  return (
    <li className="rounded-md border border-line bg-surface px-3 py-2">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-medium break-words">{check.title}</p>
          <p className="text-xs text-muted">{checkSentence(check)}</p>
        </div>
        {changes && (
          <span className="flex shrink-0">
            <IconButton label={`Change ${check.title}`} disabled={busy} onClick={onEdit}>
              <Pencil className="size-4" />
            </IconButton>
            <IconButton label={`Delete ${check.title}`} danger disabled={busy} onClick={onDelete}>
              <Trash2 className="size-4" />
            </IconButton>
          </span>
        )}
      </div>
      {check.problem ? (
        <p className="mt-1 text-xs text-warn">{check.problem}</p>
      ) : (
        check.counts && (
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <Button icon={<ListChecks className="size-4" />} onClick={onOpen} aria-label={`What ${check.title} finds`}>
              What it finds
            </Button>
            <span className="text-xs text-muted">{countsWords(check.counts)}</span>
          </div>
        )
      )}
    </li>
  )
}

/**
 * A rule written whole: its name, what it checks, what with, which documents it pairs, and how
 * the two compare, its sentence said as it is chosen.
 */
function CheckForm({
  check,
  templates,
  onClose,
}: {
  check: CrossCheck | null
  templates: ExtractionTemplate[]
  onClose: (saved: string | null) => void
}) {
  const { project } = useWorkspace()
  const queryClient = useQueryClient()
  const id = useId()
  const [draft, setDraft] = useState<CheckDraft>(() => draftOf(check, templates))
  const [problem, setProblem] = useState<string | null>(null)
  const change = (patch: Partial<CheckDraft>) => setDraft((current) => fitted({ ...current, ...patch }, templates))

  const save = useMutation({
    mutationFn: () => (check ? api.updateCrossCheck(check.id, inputOf(draft)) : api.createCrossCheck(project.id, inputOf(draft))),
    onMutate: () => setProblem(null),
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: crossChecksKey(project.id) })
      void queryClient.invalidateQueries({ queryKey: ['findings', saved.id] })
      onClose(`Saved “${saved.title}”.`)
    },
    onError: (error) => setProblem(error.message),
  })

  const fields = (kind: string) => templates.find((template) => template.doc_type === kind)?.fields ?? []
  const leftType = fieldType(templates, draft.left_kind, draft.left_field)
  const rightType = fieldType(templates, draft.right_kind, draft.right_field)
  const comparisons = comparisonsFor(leftType, rightType)
  const ordered = draft.comparison === 'equal' || draft.comparison === 'at_most' || draft.comparison === 'at_least'
  const numbers = (leftType === 'number' || leftType === 'money') && (rightType === 'number' || rightType === 'money')

  const kindSelect = (label: string, value: string, onChange: (kind: string) => void) => (
    <SelectField label={label} value={value} onChange={(event) => onChange(event.target.value)}>
      {templates.map((template) => (
        <option key={template.doc_type} value={template.doc_type}>
          {template.title}
        </option>
      ))}
    </SelectField>
  )
  const fieldSelect = (label: string, kind: string, value: string, onChange: (name: string) => void) => (
    <SelectField label={label} value={value} onChange={(event) => onChange(event.target.value)}>
      {fields(kind).map((field) => (
        <option key={field.name} value={field.name}>
          {fieldWords(field.name)}
        </option>
      ))}
    </SelectField>
  )

  return (
    <form
      aria-labelledby={`${id}-heading`}
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault()
        const found = draftProblem(draft, templates)
        if (found) setProblem(found)
        else save.mutate()
      }}
    >
      <h3 id={`${id}-heading`} className="text-[13px] font-semibold">
        {check ? `Change “${check.title}”` : 'New cross-check'}
      </h3>
      <InputField label="Name" value={draft.title} maxLength={200} autoFocus onChange={(event) => change({ title: event.target.value })} />
      <fieldset className="space-y-2 rounded-md border border-line bg-surface p-2.5">
        <legend className="px-1 text-xs font-medium text-muted">What it checks</legend>
        <div className="grid gap-2 @sm:grid-cols-2">
          {kindSelect('Kind of document', draft.left_kind, (kind) => change({ left_kind: kind }))}
          {fieldSelect('Field', draft.left_kind, draft.left_field, (name) => change({ left_field: name }))}
        </div>
      </fieldset>
      <div className="grid gap-2 @sm:grid-cols-2">
        <SelectField
          label="Compared how"
          value={draft.comparison}
          disabled={comparisons.length === 0}
          hint={comparisons.length === 0 ? 'These two fields cannot be compared: choose two numbers or amounts, two dates or two texts.' : undefined}
          onChange={(event) => change({ comparison: event.target.value as CheckDraft['comparison'] })}
        >
          {comparisons.map((option) => (
            <option key={option.comparison} value={option.comparison}>
              {option.label}
            </option>
          ))}
        </SelectField>
        {ordered && (
          <InputField
            label="Margin (optional)"
            hint={`How far apart they may be and still agree, in ${marginUnit(leftType)}.`}
            type="number"
            min={0}
            step={leftType === 'date' ? 1 : 'any'}
            value={draft.margin}
            onChange={(event) => change({ margin: event.target.value })}
          />
        )}
      </div>
      <fieldset className="space-y-2 rounded-md border border-line bg-surface p-2.5">
        <legend className="px-1 text-xs font-medium text-muted">Compared with</legend>
        <div className="grid gap-2 @sm:grid-cols-2">
          {kindSelect('Kind of document', draft.right_kind, (kind) => change({ right_kind: kind }))}
          {fieldSelect('Field', draft.right_kind, draft.right_field, (name) => change({ right_field: name }))}
        </div>
      </fieldset>
      <fieldset className="space-y-2">
        <legend className="text-xs font-medium text-muted">Of which documents</legend>
        <Radio
          name={`${id}-pairing`}
          label="The same document"
          hint={draft.right_kind === draft.left_kind ? undefined : 'Only for two fields of one kind of document.'}
          checked={draft.pairing === 'same'}
          disabled={draft.right_kind !== draft.left_kind}
          onChange={() => change({ pairing: 'same' })}
        />
        <Radio
          name={`${id}-pairing`}
          label="Those that state the same key"
          hint="Such as an invoice and the delivery notes of its purchase order."
          checked={draft.pairing === 'key'}
          onChange={() => change({ pairing: 'key' })}
        />
        {draft.pairing === 'key' && (
          <div className="grid gap-2 pl-6.5 @sm:grid-cols-2">
            {fieldSelect(`Key of the ${draft.left_kind}`, draft.left_kind, draft.left_key, (name) => change({ left_key: name }))}
            {fieldSelect(`Key of the ${draft.right_kind}`, draft.right_kind, draft.right_key, (name) => change({ right_key: name }))}
          </div>
        )}
        <Radio
          name={`${id}-pairing`}
          label={`Every ${draft.right_kind} in the project`}
          hint="Such as the price list or the contract."
          checked={draft.pairing === 'every'}
          onChange={() => change({ pairing: 'every' })}
        />
      </fieldset>
      <Checkbox
        label="Line by line: each value with the one of the other document that names the same item"
        checked={draft.by_item}
        onChange={(event) => change({ by_item: event.target.checked })}
      />
      {numbers && draft.pairing !== 'same' && (
        <Checkbox
          label="Add up the values of every document paired, such as the deliveries of one order"
          checked={draft.summed}
          onChange={(event) => change({ summed: event.target.checked })}
        />
      )}
      <p className="rounded-md bg-sunken px-3 py-2 text-xs text-ink">{checkSentence(wordedDraft(draft, templates))}</p>
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

/**
 * What a cross-check finds now, in the library's place: a value of each document it checks
 * with the values it was compared with, each opening its document where it is written, those
 * that differ first.
 */
export function FindingsView({ checkId }: { checkId: number }) {
  const workspace = useWorkspace()
  const heading = useRef<HTMLHeadingElement>(null)
  const [shown, setShown] = useState<FindingOutcome | 'all'>('all')
  const outcome = shown === 'all' ? null : shown
  useEffect(() => {
    if (window.document.activeElement === window.document.body) heading.current?.focus()
  }, [])
  const findings = useInfiniteQuery({
    queryKey: findingsKey(checkId, outcome),
    queryFn: ({ pageParam }) => api.findings(checkId, outcome, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_after,
  })
  const first = findings.data?.pages[0]
  const check = first?.check
  const listed = findings.data?.pages.flatMap((page) => page.findings) ?? []
  const counts = check?.counts

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <div className="flex h-11 shrink-0 items-center gap-1.5 border-b border-line bg-surface px-2">
        <IconButton label="Back to the cross-checks" onClick={workspace.showChecks}>
          <ArrowLeft className="size-4" />
        </IconButton>
        <h2 ref={heading} tabIndex={-1} className="flex-1 truncate text-[13px] font-semibold">
          {check ? `What “${check.title}” finds` : 'What the cross-check finds'}
        </h2>
      </div>
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
        {findings.isPending && <Loading />}
        {findings.isError && <Notice tone="bad">Could not load what it finds: {findings.error.message}</Notice>}
        {check && <p className="text-xs text-muted">{checkSentence(check)}</p>}
        {check?.problem && <Notice tone="warn">{check.problem} Change the cross-check or the template to check the documents again.</Notice>}
        {counts && (
          <div className="flex flex-wrap items-center gap-2">
            <Segmented
              value={shown}
              onChange={setShown}
              options={OUTCOME_CHOICES.map((option) => ({
                ...option,
                label: `${option.label} ${option.value === 'all' ? counts.differs + counts.missing + counts.holds : counts[option.value]}`,
              }))}
            />
          </div>
        )}
        {first && !check?.problem && listed.length === 0 && (
          <EmptyState icon={<ListChecks />} title={outcome ? `None ${OUTCOMES[outcome].toLowerCase()}` : 'Nothing to check yet'}>
            {outcome ? 'Show all to see the others.' : 'No document of its kind has been read for its template.'}
          </EmptyState>
        )}
        {listed.length > 0 && check && (
          <ul aria-label="Findings" className="space-y-2">
            {listed.map((finding) => (
              <FindingItem
                key={`${finding.document_id}:${finding.left?.id ?? 'none'}:${finding.reason}`}
                finding={finding}
                check={check}
                onOpen={(documentId, fact) => workspace.openDocument(documentId, fact ? sourceOf(fact) : null)}
              />
            ))}
          </ul>
        )}
        {findings.hasNextPage && (
          <Button disabled={findings.isFetchingNextPage} onClick={() => void findings.fetchNextPage()}>
            {findings.isFetchingNextPage ? 'Loading…' : 'Show more'}
          </Button>
        )}
      </div>
    </div>
  )
}
