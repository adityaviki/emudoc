import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, FolderCog, Pause, Pencil, Play, Plus, Trash2, X } from 'lucide-react'
import { useId, useRef, useState } from 'react'
import { api, mayChange } from '../api.ts'
import type { FilingAction, FilingCondition, FilingRule, FilingRuleInput, Folder, Tag } from '../api.ts'
import { useMe } from '../auth.ts'
import { folderChoices } from '../automations.ts'
import { Confirm } from '../components/Confirm.tsx'
import { Button, Checkbox, EmptyState, IconButton, InputField, Loading, Notice, SelectField } from '../components/ui.tsx'
import { fieldWords } from '../factTable.ts'
import { useFolders } from '../hooks.ts'
import { NEW_RULE, ruleInput, ruleProblem, ruleSentence } from '../library.ts'
import { useDocumentTypes, useFilingRules, useOrganizing, useTags } from './organizing.ts'
import { useWorkspace } from './state.ts'

const CONDITIONS: { value: FilingCondition; label: string }[] = [
  { value: 'type', label: 'Its type is' },
  { value: 'fact', label: 'A fact read from it holds' },
  { value: 'any', label: 'Every document' },
]
const ACTIONS: { value: FilingAction; label: string }[] = [
  { value: 'folder', label: 'Move it into a folder' },
  { value: 'by_year', label: 'File it under a folder by its year' },
  { value: 'by_month', label: 'File it under a folder by its year and month' },
  { value: 'tag', label: 'Give it a tag' },
]

/**
 * The project's filing rules, in the library's place (backend/app/filing.py): once a new document has been read for its
 * type and facts, the first rule that moves it files it, if it arrived at the top of the library, and every rule that tags
 * it tags it, wherever it arrived. Whoever may change the project makes, changes, turns off and deletes them; anyone who
 * sees it reads them.
 */
export function FilingView() {
  const workspace = useWorkspace()
  const { project } = workspace
  const changes = mayChange(useMe().role) && !project.sample
  const rules = useFilingRules(project.id)
  const tags = useTags(project.id)
  const folders = useFolders(project.id)
  const organizing = useOrganizing(project.id)
  const [editing, setEditing] = useState<{ rule: FilingRule | null } | null>(null)
  const [asking, setAsking] = useState<FilingRule | null>(null)
  const [said, setSaid] = useState<string | null>(null)
  const [refused, setRefused] = useState<string | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const listed = rules.data?.rules ?? []
  const room = listed.length < (rules.data?.max_filing_rules ?? 0)
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
        <h2 ref={heading} tabIndex={-1} className="flex-1 text-[13px] font-semibold">
          Filing rules
        </h2>
        {changes && !editing && (
          <Button
            icon={<Plus className="size-4" />}
            disabled={!rules.data || !room}
            title={room ? undefined : `A project has at most ${rules.data?.max_filing_rules} filing rules.`}
            onClick={() => {
              setSaid(null)
              setEditing({ rule: null })
            }}
          >
            New rule
          </Button>
        )}
      </div>
      <div className="@container min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        <p className="text-xs text-muted">
          Once a new document has been read for its type and facts, the first rule here that moves it files it, if it arrived at the top of the library;
          a document uploaded into a folder, or from a connected folder, stays where it went. Every rule that tags it tags it, wherever it arrived. A rule
          files what arrives after it is made, once.
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
        {said && !editing && <Notice tone="info">{said}</Notice>}
        {editing && (
          <RuleForm
            rule={editing.rule}
            tags={tags.data?.tags ?? []}
            folders={folders.data ?? []}
            busy={organizing.saveRule.isPending}
            onSave={(rule) =>
              organizing.saveRule.mutate(
                { id: editing.rule?.id ?? null, rule },
                { onSuccess: () => close(editing.rule ? 'Saved the rule.' : 'Made the rule.'), onError: (error) => setRefused(error.message) },
              )
            }
            onCancel={() => close(null)}
          />
        )}
        {(rules.isPending || tags.isPending) && <Loading />}
        {rules.isError && <Notice tone="bad">Could not load the filing rules: {rules.error.message}</Notice>}
        {rules.data && listed.length === 0 && !editing && (
          <EmptyState icon={<FolderCog />} title="No filing rules yet">
            {changes
              ? 'Make one to have new invoices go into their folder, contracts filed by year, or a supplier’s documents tagged, as they arrive.'
              : 'A member who may change the project makes its filing rules.'}
          </EmptyState>
        )}
        {listed.length > 0 && (
          <ol aria-label="The project’s filing rules, in the order they apply" className="divide-y divide-line rounded-lg border border-line bg-surface">
            {listed.map((rule) => (
              <li key={rule.id} className="flex items-start gap-2 px-3 py-2 text-[13px]">
                <div className="min-w-0 flex-1">
                  <p className={rule.enabled ? '' : 'text-muted'}>
                    {ruleSentence(rule, rule.folder, rule.tag)}.{!rule.enabled && ' Off.'}
                  </p>
                  {rule.problem && <p className="mt-0.5 text-xs text-warn">{rule.problem}</p>}
                </div>
                {changes && (
                  <span className="flex shrink-0 gap-0.5">
                    <IconButton
                      label={rule.enabled ? 'Turn the rule off' : 'Turn the rule on'}
                      onClick={() =>
                        organizing.saveRule.mutate(
                          { id: rule.id, rule: { ...ruleInput(rule), enabled: !rule.enabled } },
                          { onError: (error) => setRefused(error.message) },
                        )
                      }
                    >
                      {rule.enabled ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
                    </IconButton>
                    <IconButton label="Change the rule" onClick={() => setEditing({ rule })}>
                      <Pencil className="size-3.5" />
                    </IconButton>
                    <IconButton label="Delete the rule" danger onClick={() => setAsking(rule)}>
                      <Trash2 className="size-3.5" />
                    </IconButton>
                  </span>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>
      {asking && (
        <Confirm
          title="Delete the filing rule?"
          body={`${ruleSentence(asking, asking.folder, asking.tag)}: from now on, nothing is filed by it. What it filed stays where it is.`}
          action="Delete"
          onConfirm={() => {
            heading.current?.focus()
            organizing.deleteRule.mutate(asking.id, { onSuccess: () => setSaid('Deleted the rule.'), onError: (error) => setRefused(error.message) })
          }}
          onClose={() => setAsking(null)}
        />
      )}
    </div>
  )
}

interface RuleFormProps {
  rule: FilingRule | null
  tags: Tag[]
  folders: Folder[]
  busy: boolean
  onSave: (rule: FilingRuleInput) => void
  onCancel: () => void
}

/** A filing rule written whole: what it looks at in a new document, and what it does to one that matches. */
function RuleForm({ rule, tags, folders, busy, onSave, onCancel }: RuleFormProps) {
  const { project } = useWorkspace()
  const [draft, setDraft] = useState<FilingRuleInput>(rule ? ruleInput(rule) : NEW_RULE)
  const types = useDocumentTypes(project.id)
  const factNames = useQuery({ queryKey: ['fact-names', project.id], queryFn: () => api.factNames(project.id) })
  const id = useId()
  const set = (change: Partial<FilingRuleInput>) => setDraft((held) => ({ ...held, ...change }))
  const problem = ruleProblem(draft)
  const choices = folderChoices(folders)
  const folder = choices.find((choice) => choice.id === draft.folder_id)?.label ?? null
  const tag = tags.find((one) => one.id === draft.tag_id)?.name ?? null
  return (
    <section aria-label={rule ? 'Change the rule' : 'New rule'} className="space-y-3 rounded-lg border border-line bg-surface p-3">
      <div className="grid gap-3 @sm:grid-cols-2">
        <SelectField label="When" value={draft.condition} onChange={(event) => set({ condition: event.target.value as FilingCondition })}>
          {CONDITIONS.map((choice) => (
            <option key={choice.value} value={choice.value}>
              {choice.label}
            </option>
          ))}
        </SelectField>
        {draft.condition === 'type' && (
          <>
            <InputField label="Type" list={`${id}-types`} placeholder="invoice" value={draft.value ?? ''} onChange={(event) => set({ value: event.target.value })} />
            <datalist id={`${id}-types`}>
              {types.data?.map((one) => <option key={one.type} value={one.type} />)}
            </datalist>
          </>
        )}
        {draft.condition === 'fact' && (
          <>
            <InputField label="Fact" list={`${id}-facts`} placeholder="supplier" value={draft.field ?? ''} onChange={(event) => set({ field: event.target.value })} />
            <datalist id={`${id}-facts`}>
              {factNames.data?.map((one) => (
                <option key={one.name} value={one.name}>
                  {fieldWords(one.name)}
                </option>
              ))}
            </datalist>
            <InputField label="Holds the words" placeholder="Acme" value={draft.value ?? ''} onChange={(event) => set({ value: event.target.value })} />
          </>
        )}
      </div>
      <div className="grid gap-3 @sm:grid-cols-2">
        <SelectField label="Then" value={draft.action} onChange={(event) => set({ action: event.target.value as FilingAction })}>
          {ACTIONS.map((choice) => (
            <option key={choice.value} value={choice.value}>
              {choice.label}
            </option>
          ))}
        </SelectField>
        {draft.action === 'tag' ? (
          <SelectField label="Tag" value={draft.tag_id ?? ''} onChange={(event) => set({ tag_id: event.target.value ? Number(event.target.value) : null })}>
            <option value="">Choose a tag</option>
            {tags.map((one) => (
              <option key={one.id} value={one.id}>
                {one.name}
              </option>
            ))}
          </SelectField>
        ) : (
          <SelectField label="Folder" value={draft.folder_id ?? ''} onChange={(event) => set({ folder_id: event.target.value ? Number(event.target.value) : null })}>
            <option value="">{draft.action === 'folder' ? 'Choose a folder' : 'The top of the library'}</option>
            {choices.map((choice) => (
              <option key={choice.id} value={choice.id}>
                {choice.label}
              </option>
            ))}
          </SelectField>
        )}
      </div>
      <Checkbox checked={draft.enabled} onChange={(event) => set({ enabled: event.target.checked })} label="On: it files what arrives from now on" />
      <p className="text-xs text-muted" aria-live="polite">
        {problem ?? `${ruleSentence(draft, folder, tag)}.`}
      </p>
      <div className="flex justify-end gap-2">
        <Button onClick={onCancel}>Cancel</Button>
        <Button variant="primary" disabled={problem !== null || busy} onClick={() => onSave(ruleInput(draft))}>
          {rule ? 'Save' : 'Make the rule'}
        </Button>
      </div>
    </section>
  )
}
