import { keepPreviousData, useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { ArrowDown, ArrowLeft, ArrowUp, ListFilter, Search, X } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import type { FocusEvent, KeyboardEvent } from 'react'
import { api } from '../api.ts'
import type { ExtractionTemplate, Fact, FactRow, TemplateField } from '../api.ts'
import { Floating } from '../components/Menu.tsx'
import { Button, EmptyState, IconButton, InputField, Loading, Notice, SelectField, Spinner } from '../components/ui.tsx'
import {
  BY_NAME,
  ariaSort,
  cellAfter,
  cellDescription,
  cellWords,
  conditionProblem,
  conditionWords,
  factTableKey,
  fieldWords,
  nextSort,
  operatorsFor,
  quoteWords,
  rowsWords,
  sourceOf,
  tableQuery,
} from '../factTable.ts'
import type { Operator, TableAsk, TableCondition, TableSort } from '../factTable.ts'
import { templatesKey } from '../templates.ts'
import { useWorkspace } from './state.ts'

/**
 * The facts table of the project's documents of one kind, in the library's place: a row for each
 * document read for the kind's template, a column for each of its fields, sorted by a column's
 * header and kept by the words and conditions above it. A cell shows its field's first fact,
 * its quote on hover or focus, and opens it where it was found in the document.
 */
export function FactsView({ kind }: { kind: string }) {
  const workspace = useWorkspace()
  const { project } = workspace
  const heading = useRef<HTMLHeadingElement>(null)
  const id = useId()
  // Where the keyboard goes back to once a condition is added, given up or removed.
  const toAdder = () => window.document.getElementById(`${id}-condition`)?.focus()
  const [sort, setSort] = useState<TableSort>(BY_NAME)
  const [conditions, setConditions] = useState<TableCondition[]>([])
  const [typed, setTyped] = useState('')
  const [words, setWords] = useState('')
  const [adding, setAdding] = useState(false)
  // Another kind chosen starts its table afresh, the choice keeping the focus where it is.
  const [shownKind, setShownKind] = useState(kind)
  if (kind !== shownKind) {
    setShownKind(kind)
    setSort(BY_NAME)
    setConditions([])
    setTyped('')
    setWords('')
    setAdding(false)
  }
  useEffect(() => {
    const timer = window.setTimeout(() => setWords(typed.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [typed])
  // Opened from a button that went with the view before it: the keyboard goes on from here.
  useEffect(() => {
    if (document.activeElement === document.body) heading.current?.focus()
  }, [])

  const templates = useQuery({ queryKey: templatesKey(project.id), queryFn: () => api.templates(project.id) })
  const ask: TableAsk = { kind, sort, conditions, words }
  const table = useInfiniteQuery({
    queryKey: factTableKey(project.id, ask),
    queryFn: ({ pageParam }) => api.factTable(project.id, tableQuery(ask, pageParam)),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_after,
    // A new sort or condition keeps the rows before it in view until its own come.
    placeholderData: keepPreviousData,
  })
  const first = table.data?.pages[0]
  const template = first?.template
  const rows = table.data?.pages.flatMap((page) => page.rows) ?? []
  const filtered = conditions.length > 0 || words !== ''
  // The kinds to choose from: those of the templates with documents read for them, and this one.
  const kinds = (templates.data?.templates ?? []).filter((other) => other.documents > 0 || other.doc_type === kind)

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <div className="flex h-11 shrink-0 items-center gap-1.5 border-b border-line bg-surface px-2">
        <IconButton label="Back to the templates" onClick={workspace.showTemplates}>
          <ArrowLeft className="size-4" />
        </IconButton>
        <h2 ref={heading} tabIndex={-1} className="flex-1 truncate text-[13px] font-semibold">
          {template ? `${template.title} facts` : 'Facts'}
        </h2>
      </div>
      <div className="@container shrink-0 space-y-2 border-b border-line bg-surface px-3 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <select
            aria-label="Kind of document"
            value={kind}
            onChange={(event) => workspace.showFacts(event.target.value)}
            className="field h-8 w-auto! max-w-48 px-2 py-0 text-[13px]"
          >
            {kinds.length === 0 && <option value={kind}>{kind}</option>}
            {kinds.map((other) => (
              <option key={other.doc_type} value={other.doc_type}>
                {other.title} ({other.documents})
              </option>
            ))}
          </select>
          <div className="relative min-w-40 flex-1">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted" />
            <input
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Escape') setTyped('')
              }}
              placeholder="Find in the rows"
              aria-label="Find in the rows"
              className="field h-8 pr-8 pl-8 text-[13px]"
            />
            {typed && (
              <button
                type="button"
                aria-label="Clear"
                onClick={() => setTyped('')}
                className="absolute top-1/2 right-1.5 -translate-y-1/2 rounded p-0.5 text-muted transition hover:bg-sunken hover:text-ink"
              >
                <X className="size-3.5" />
              </button>
            )}
          </div>
          <Button
            id={`${id}-condition`}
            icon={<ListFilter className="size-4" />}
            aria-expanded={adding}
            disabled={!template}
            onClick={() => setAdding(!adding)}
          >
            Condition
          </Button>
        </div>
        {adding && template && (
          <ConditionForm
            fields={template.fields}
            onAdd={(condition) => {
              setConditions([...conditions, condition])
              setAdding(false)
              toAdder()
            }}
            onCancel={() => {
              setAdding(false)
              toAdder()
            }}
          />
        )}
        {conditions.length > 0 && (
          <ul aria-label="Conditions" className="flex flex-wrap gap-1.5">
            {conditions.map((condition, index) => {
              const said = conditionWords(condition, template?.fields.find((field) => field.name === condition.field))
              return (
                <li key={index} className="inline-flex items-center gap-0.5 rounded-md bg-accent-soft py-0.5 pr-0.5 pl-2 text-xs text-accent">
                  {said}
                  <IconButton
                    label={`Remove the condition ${said}`}
                    className="size-5! text-accent!"
                    onClick={() => {
                      setConditions(conditions.filter((_, other) => other !== index))
                      toAdder()
                    }}
                  >
                    <X className="size-3.5" />
                  </IconButton>
                </li>
              )
            })}
          </ul>
        )}
        {first && (
          <p role="status" className="flex items-center gap-1.5 text-xs text-muted">
            {rowsWords(first.total, first.template.documents, filtered)}
            {table.isPlaceholderData && <Spinner className="size-3" />}
          </p>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {table.isPending && <Loading />}
        {table.isError && (
          <div className="p-3">
            <Notice tone="bad">Could not load the table: {table.error.message}</Notice>
          </div>
        )}
        {template && first && first.total === 0 && (
          <EmptyState title={filtered ? 'No document meets these conditions' : `No document has been read for the ${template.title} template yet`}>
            {filtered
              ? 'Remove a condition, or find other words.'
              : `Documents of the kind uploaded from now on are read for it, and those here already once they are read again from the templates.`}
          </EmptyState>
        )}
        {template && rows.length > 0 && (
          <>
            <Grid template={template} rows={rows} total={first?.total ?? rows.length} sort={sort} onSort={(field) => setSort(nextSort(sort, field))} />
            {table.hasNextPage && (
              <div className="p-3">
                <Button disabled={table.isFetchingNextPage} onClick={() => void table.fetchNextPage()}>
                  {table.isFetchingNextPage ? 'Loading…' : 'Show more'}
                </Button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

/** Where the keyboard is in the grid: the row (0 the header's) and the column (0 the documents'). */
type At = [number, number]

/**
 * The table as a grid: one stop of Tab, the arrows, Home, End and the page keys moving between
 * its cells, each cell's button or the cell itself taking the focus; the header's sort, the
 * name's column opens the document, and a cell its fact.
 */
function Grid({
  template,
  rows,
  total,
  sort,
  onSort,
}: {
  template: ExtractionTemplate
  rows: FactRow[]
  total: number
  sort: TableSort
  onSort: (field: string | null) => void
}) {
  const workspace = useWorkspace()
  const grid = useRef<HTMLTableElement>(null)
  const [at, setAt] = useState<At>([0, 0])
  const size: At = [rows.length + 1, template.fields.length + 1]
  // The rows may have gone since the keyboard was on one: the nearest that stays.
  const active: At = [Math.min(at[0], size[0] - 1), Math.min(at[1], size[1] - 1)]
  const stop = (row: number, column: number) => (row === active[0] && column === active[1] ? 0 : -1)

  function onKeyDown(event: KeyboardEvent<HTMLTableElement>) {
    if (!(event.target instanceof HTMLElement) || !event.target.dataset.cell) return
    const next = cellAfter(active, event.key, size[0], size[1], event.ctrlKey || event.metaKey)
    if (!next) return
    event.preventDefault()
    setAt(next)
    grid.current?.querySelector<HTMLElement>(`[data-cell="${next[0]}:${next[1]}"]`)?.focus()
  }
  function onFocus(event: FocusEvent<HTMLTableElement>) {
    const cell = event.target.dataset.cell
    if (cell) setAt(cell.split(':').map(Number) as At)
  }

  const header = (field: TemplateField | null, column: number) => {
    const sorted = ariaSort(sort, field?.name ?? null)
    const Arrow = sorted === 'descending' ? ArrowDown : ArrowUp
    return (
      <th
        key={field?.name ?? ''}
        scope="col"
        aria-sort={sorted}
        className={`sticky top-0 border-b border-line bg-sunken px-1 py-1 text-left text-xs font-medium text-muted ${column === 0 ? 'left-0 z-30' : 'z-20'}`}
      >
        <button
          type="button"
          data-cell={`0:${column}`}
          tabIndex={stop(0, column)}
          title={field?.description || undefined}
          onClick={() => onSort(field?.name ?? null)}
          className="flex w-full items-center gap-1 rounded px-1 py-0.5 whitespace-nowrap transition hover:bg-surface hover:text-ink focus-visible:-outline-offset-2!"
        >
          {field ? fieldWords(field.name) : 'Document'}
          <Arrow aria-hidden="true" className={`size-3 shrink-0 ${sorted === 'none' ? 'opacity-0' : ''}`} />
        </button>
      </th>
    )
  }

  return (
    <table
      ref={grid}
      role="grid"
      aria-label={`Facts of ${template.doc_type} documents`}
      aria-rowcount={total + 1}
      aria-colcount={size[1]}
      onKeyDown={onKeyDown}
      onFocus={onFocus}
      className="border-separate border-spacing-0 text-[13px]"
    >
      <thead>
        <tr aria-rowindex={1}>
          {header(null, 0)}
          {template.fields.map((field, index) => header(field, index + 1))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => {
          const line = index + 1
          return (
            <tr key={row.document_id} aria-rowindex={line + 1}>
              <th scope="row" className="sticky left-0 z-10 border-r border-b border-line bg-surface px-1 py-1 text-left font-normal">
                <button
                  type="button"
                  data-cell={`${line}:0`}
                  tabIndex={stop(line, 0)}
                  title={row.filename}
                  onClick={() => workspace.openDocument(row.document_id)}
                  className="block max-w-56 truncate rounded px-1 py-0.5 text-left font-medium transition hover:bg-sunken focus-visible:-outline-offset-2!"
                >
                  {row.title}
                </button>
              </th>
              {template.fields.map((field, column) => {
                const facts = row.cells[field.name] ?? []
                const cell = `${line}:${column + 1}`
                return facts.length ? (
                  <FactCell key={field.name} cell={cell} tabIndex={stop(line, column + 1)} field={field} facts={facts} documentId={row.document_id} />
                ) : (
                  <td
                    key={field.name}
                    data-cell={cell}
                    tabIndex={stop(line, column + 1)}
                    className="border-b border-line px-2 py-1 text-muted focus-visible:-outline-offset-2!"
                  >
                    <span aria-hidden="true">—</span>
                    <span className="sr-only">Not stated</span>
                  </td>
                )
              })}
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

/**
 * A cell of facts: its first value, and how many more. Hovering or focusing it shows each fact's
 * quote and page; a cell of one fact opens it in the document, a cell of several lists them to
 * open one.
 */
function FactCell({
  cell,
  tabIndex,
  field,
  facts,
  documentId,
}: {
  cell: string
  tabIndex: number
  field: TemplateField
  facts: Fact[]
  documentId: number
}) {
  const workspace = useWorkspace()
  const described = useId()
  const button = useRef<HTMLButtonElement>(null)
  const [preview, setPreview] = useState<DOMRect | null>(null)
  const [listing, setListing] = useState<DOMRect | null>(null)
  const timer = useRef<number | undefined>(undefined)
  const show = () => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setPreview(button.current?.getBoundingClientRect() ?? null), 250)
  }
  const hide = () => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setPreview(null), 150)
  }
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const { value, more } = cellWords(facts)
  const open = (fact: Fact) => workspace.openDocument(documentId, sourceOf(fact))

  return (
    <td className="border-b border-line px-1 py-1">
      <button
        ref={button}
        type="button"
        data-cell={cell}
        tabIndex={tabIndex}
        aria-describedby={described}
        aria-haspopup={more > 0 ? 'dialog' : undefined}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        onClick={() => {
          window.clearTimeout(timer.current)
          setPreview(null)
          if (more > 0) setListing(button.current?.getBoundingClientRect() ?? null)
          else open(facts[0])
        }}
        className="flex max-w-60 items-center gap-1 rounded px-1 py-0.5 text-left whitespace-nowrap transition hover:bg-sunken focus-visible:-outline-offset-2!"
      >
        <span className="truncate">{value}</span>
        {more > 0 && <span className="shrink-0 rounded bg-sunken px-1 text-[11px] font-medium text-muted">+{more}</span>}
      </button>
      <span id={described} className="sr-only">
        {cellDescription(facts)}
      </span>
      {preview && !listing && (
        <Floating anchor={preview} onClose={() => setPreview(null)} ignore={button} preview className="w-80 max-w-[90vw]">
          <div onMouseEnter={show} onMouseLeave={hide} className="text-[13px]">
            <p className="border-b border-line bg-sunken/50 px-3 py-1.5 text-xs font-medium text-muted">{fieldWords(field.name)}</p>
            <ul className="max-h-64 divide-y divide-line overflow-y-auto">
              {facts.map((fact) => (
                <li key={fact.id} className="px-3 py-1.5">
                  <p className="font-medium break-words">{fact.value}</p>
                  <p className="text-xs text-muted">{quoteWords(fact)}</p>
                </li>
              ))}
            </ul>
          </div>
        </Floating>
      )}
      {listing && (
        <Floating anchor={listing} onClose={() => setListing(null)} ignore={button} className="w-80 max-w-[90vw]">
          <div role="dialog" aria-label={`Each ${fieldWords(field.name)}`} className="text-[13px]">
            <p className="border-b border-line bg-sunken/50 px-3 py-1.5 text-xs font-medium text-muted">Open one in the document</p>
            <ul className="max-h-72 overflow-y-auto p-1">
              {facts.map((fact) => (
                <li key={fact.id}>
                  <button
                    type="button"
                    onClick={() => {
                      setListing(null)
                      open(fact)
                    }}
                    className="block w-full rounded px-2 py-1.5 text-left transition hover:bg-sunken"
                  >
                    <span className="block font-medium break-words">{fact.value}</span>
                    <span className="block text-xs text-muted">{quoteWords(fact)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </Floating>
      )}
    </td>
  )
}

/** A condition chosen in words: a field, what it asks of it, and the value it is held to. */
function ConditionForm({
  fields,
  onAdd,
  onCancel,
}: {
  fields: TemplateField[]
  onAdd: (condition: TableCondition) => void
  onCancel: () => void
}) {
  const id = useId()
  const [name, setName] = useState(fields[0]?.name ?? '')
  const [op, setOp] = useState<Operator>('has')
  const [value, setValue] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const field = fields.find((other) => other.name === name)
  const offered = operatorsFor(field?.type ?? 'text')
  // A field of another type may not take the condition chosen for the one before.
  const chosen = offered.some((other) => other.op === op) ? op : 'has'
  const valued = chosen !== 'has' && chosen !== 'lacks'
  const compared = chosen === 'min' || chosen === 'max'

  return (
    <form
      aria-labelledby={`${id}-heading`}
      className="space-y-2 rounded-md border border-line bg-page p-2.5"
      onSubmit={(event) => {
        event.preventDefault()
        const condition = { field: name, op: chosen, value: valued ? value : '' }
        const found = conditionProblem(condition, field)
        if (found) setProblem(found)
        else onAdd(condition)
      }}
      onKeyDown={(event) => {
        if (event.key === 'Escape') onCancel()
      }}
    >
      <h3 id={`${id}-heading`} className="text-xs font-medium text-muted">
        Keep the rows whose…
      </h3>
      <div className="grid gap-2 @md:grid-cols-3">
        <SelectField label="Field" value={name} autoFocus onChange={(event) => setName(event.target.value)}>
          {fields.map((other) => (
            <option key={other.name} value={other.name}>
              {fieldWords(other.name)}
            </option>
          ))}
        </SelectField>
        <SelectField label="Condition" value={chosen} onChange={(event) => setOp(event.target.value as Operator)}>
          {offered.map((other) => (
            <option key={other.op} value={other.op}>
              {other.label}
            </option>
          ))}
        </SelectField>
        {valued && (
          <InputField
            label={compared && field?.type === 'date' ? 'Date' : compared ? 'Number' : 'Words'}
            type={compared ? (field?.type === 'date' ? 'date' : 'number') : 'text'}
            step={compared && field?.type !== 'date' ? 'any' : undefined}
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
        )}
      </div>
      {problem && (
        <p role="alert" className="text-xs text-bad">
          {problem}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button onClick={onCancel}>Cancel</Button>
        <Button type="submit" variant="primary">
          Add
        </Button>
      </div>
    </form>
  )
}
