import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowLeft, ArrowUp, Check, Download, FileSearch, FileSpreadsheet, FileText, History, ListChecks, ListFilter, PenLine, Search, TriangleAlert, Undo2, X } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import type { FocusEvent, KeyboardEvent } from 'react'
import { api } from '../api.ts'
import type { ExtractionTemplate, Fact, FactCheck, FactCheckInput, FactRow, TemplateField } from '../api.ts'
import { Floating, Menu } from '../components/Menu.tsx'
import type { MenuItem } from '../components/Menu.tsx'
import { Button, EmptyState, IconButton, InputField, Loading, Notice, SelectField, Spinner } from '../components/ui.tsx'
import {
  BY_NAME,
  ariaSort,
  cellAfter,
  cellDescription,
  cellMark,
  cellWords,
  checkWords,
  conditionProblem,
  conditionWords,
  correctionOf,
  correctionProblem,
  draftOf,
  earlierWords,
  exportUrl,
  factTableKey,
  factTablesKey,
  fieldWords,
  nextSort,
  operatorsFor,
  quoteWords,
  uncheckedWords,
  rowsWords,
  sourceOf,
  tableQuery,
  valueless,
} from '../factTable.ts'
import type { ExportFormat, Operator, TableAsk, TableCondition, TableSort } from '../factTable.ts'
import { templatesKey } from '../templates.ts'
import { ReviewMark } from './Reviews.tsx'
import { useWorkspace } from './state.ts'

/**
 * The facts table of the project's documents of one kind, in the library's place: a row for each
 * document read for the kind's template, a column for each of its fields, sorted by a column's
 * header and kept by the words and conditions above it, or to the rows with a fact nobody checked.
 * A cell shows its field's first fact, its quote on hover or focus, and opens its facts to check,
 * correct or open where they were found in the document.
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
  const [unchecked, setUnchecked] = useState(false)
  const grid = useRef<HTMLDivElement>(null)
  // Another kind chosen starts its table afresh, the choice keeping the focus where it is.
  const [shownKind, setShownKind] = useState(kind)
  if (kind !== shownKind) {
    setShownKind(kind)
    setSort(BY_NAME)
    setConditions([])
    setTyped('')
    setWords('')
    setAdding(false)
    setUnchecked(false)
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
  const ask: TableAsk = { kind, sort, conditions, words, unchecked }
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
  const filtered = conditions.length > 0 || words !== '' || unchecked
  // A change that took the row holding the focus out of a table of the unchecked rows: the
  // keyboard goes on from the grid's cell where it was, or the heading once no row is left.
  const restore = () =>
    window.requestAnimationFrame(() => {
      if (document.activeElement && document.activeElement !== document.body) return
      ;(grid.current?.querySelector<HTMLElement>('[data-cell][tabindex="0"]') ?? heading.current)?.focus()
    })
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
          <Button
            icon={<ListChecks className="size-4" />}
            aria-pressed={unchecked}
            disabled={!template}
            title="Only the documents with a fact nobody has checked"
            onClick={() => setUnchecked(!unchecked)}
          >
            Unchecked
          </Button>
          <DownloadMenu projectId={project.id} ask={ask} disabled={!first || first.total === 0} />
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
            {rowsWords(first.total, first.template.documents, filtered)}, {uncheckedWords(first.unchecked)}
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
          <EmptyState
            title={
              unchecked && first.template.documents > 0 && conditions.length === 0 && words === ''
                ? 'Every fact of these documents is checked'
                : filtered
                  ? 'No document meets these conditions'
                  : `No document has been read for the ${template.title} template yet`
            }
          >
            {unchecked && conditions.length === 0 && words === ''
              ? 'Each fact a member checked or corrected is marked in its cell. Turn off “Unchecked” to see every document.'
              : filtered
              ? 'Remove a condition, or find other words.'
              : `Documents of the kind uploaded from now on are read for it, and those here already once they are read again from the templates.`}
          </EmptyState>
        )}
        {template && rows.length > 0 && (
          <>
            <div ref={grid}>
              <Grid
                template={template}
                rows={rows}
                total={first?.total ?? rows.length}
                sort={sort}
                onSort={(field) => setSort(nextSort(sort, field))}
                onRemoved={restore}
              />
            </div>
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

/** The rows the table keeps as a file to save, every page of them: the table as it shows, or a workbook with each fact's source and check. */
function DownloadMenu({ projectId, ask, disabled }: { projectId: number; ask: TableAsk; disabled: boolean }) {
  const button = useRef<HTMLButtonElement>(null)
  const [anchor, setAnchor] = useState<DOMRect | null>(null)
  const close = () => setAnchor(null)
  // Followed as a click on a link would be, so the download leaves the page where it is.
  const save = (format: ExportFormat) => () => {
    const link = window.document.createElement('a')
    Object.assign(link, { href: exportUrl(projectId, ask, format), download: '' })
    link.click()
  }
  const items: MenuItem[] = [
    { label: 'CSV file, as the table shows', icon: FileText, onSelect: save('csv') },
    { label: "Excel workbook, with each fact's source", icon: FileSpreadsheet, onSelect: save('xlsx') },
  ]
  return (
    <>
      <Button
        ref={button}
        icon={<Download className="size-4" />}
        aria-haspopup="menu"
        aria-expanded={anchor !== null}
        disabled={disabled}
        onClick={(event) => setAnchor(anchor ? null : event.currentTarget.getBoundingClientRect())}
      >
        Download
      </Button>
      {anchor && (
        <Floating anchor={anchor} alignEnd onClose={close} ignore={button}>
          <Menu items={items} onClose={close} />
        </Floating>
      )}
    </>
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
  onRemoved,
}: {
  template: ExtractionTemplate
  rows: FactRow[]
  total: number
  sort: TableSort
  onSort: (field: string | null) => void
  onRemoved: () => void
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
                  className="flex max-w-56 items-center gap-1.5 rounded px-1 py-0.5 text-left font-medium transition hover:bg-sunken focus-visible:-outline-offset-2!"
                >
                  <span className="truncate">{row.title}</span>
                  <ReviewMark document={row} />
                </button>
              </th>
              {template.fields.map((field, column) => {
                const facts = row.cells[field.name] ?? []
                const earlier = row.earlier[field.name] ?? []
                const cell = `${line}:${column + 1}`
                return facts.length || earlier.length ? (
                  <FactCell
                    key={field.name}
                    cell={cell}
                    tabIndex={stop(line, column + 1)}
                    field={field}
                    facts={facts}
                    earlier={earlier}
                    documentId={row.document_id}
                    title={row.title}
                    onRemoved={onRemoved}
                  />
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
 * A cell of facts: its first value, how many more, and a mark for what a member should know of
 * them. Hovering or focusing it shows each fact's quote and check; pressing it opens its facts,
 * each to check, correct or open in the document, and a check made before the document was read
 * again that no fact holds, to forget.
 */
function FactCell({
  cell,
  tabIndex,
  field,
  facts,
  earlier,
  documentId,
  title,
  onRemoved,
}: {
  cell: string
  tabIndex: number
  field: TemplateField
  facts: Fact[]
  earlier: FactCheck[]
  documentId: number
  title: string
  onRemoved: () => void
}) {
  const described = useId()
  const button = useRef<HTMLButtonElement>(null)
  const [preview, setPreview] = useState<DOMRect | null>(null)
  const [panel, setPanel] = useState<DOMRect | null>(null)
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
  const mark = cellMark(facts, earlier)
  const Mark = mark === 'checked' ? Check : mark === 'flagged' ? TriangleAlert : mark === 'changed' ? History : null

  return (
    <td className="border-b border-line px-1 py-1">
      <button
        ref={button}
        type="button"
        data-cell={cell}
        tabIndex={tabIndex}
        aria-describedby={described}
        aria-haspopup="dialog"
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        onClick={() => {
          window.clearTimeout(timer.current)
          setPreview(null)
          // Opened, and measured, once the table has scrolled to the cell the click focused,
          // whose scroll would otherwise close the panel as it opens.
          window.requestAnimationFrame(() => setPanel(button.current?.getBoundingClientRect() ?? null))
        }}
        className="flex max-w-60 items-center gap-1 rounded px-1 py-0.5 text-left whitespace-nowrap transition hover:bg-sunken focus-visible:-outline-offset-2!"
      >
        <span className={`truncate ${facts.length ? '' : 'text-muted'}`}>{facts.length ? value : '—'}</span>
        {more > 0 && <span className="shrink-0 rounded bg-sunken px-1 text-[11px] font-medium text-muted">+{more}</span>}
        {Mark && <Mark aria-hidden="true" className={`size-3.5 shrink-0 ${mark === 'checked' ? 'text-good' : 'text-warn'}`} />}
      </button>
      <span id={described} className="sr-only">
        {cellDescription(facts, earlier)}
      </span>
      {preview && !panel && (
        <Floating anchor={preview} onClose={() => setPreview(null)} ignore={button} preview className="w-80 max-w-[90vw]">
          <div onMouseEnter={show} onMouseLeave={hide} className="text-[13px]">
            <p className="border-b border-line bg-sunken/50 px-3 py-1.5 text-xs font-medium text-muted">{fieldWords(field.name)}</p>
            <ul className="max-h-64 divide-y divide-line overflow-y-auto">
              {facts.map((fact) => (
                <li key={fact.id} className="px-3 py-1.5">
                  <p className="font-medium break-words">{fact.value}</p>
                  <p className="text-xs text-muted">{quoteWords(fact)}</p>
                  {fact.check && <p className="text-xs text-good">{checkWords(fact.check)}</p>}
                </li>
              ))}
              {earlier.map((check) => (
                <li key={`earlier-${check.id}`} className="px-3 py-1.5 text-xs text-warn">
                  {earlierWords(check)}
                </li>
              ))}
            </ul>
          </div>
        </Floating>
      )}
      {panel && (
        <Floating anchor={panel} onClose={() => setPanel(null)} ignore={button} className="w-96 max-w-[90vw]">
          <div role="dialog" aria-label={`${fieldWords(field.name)} of ${title}`} className="text-[13px]">
            <p className="border-b border-line bg-sunken/50 px-3 py-1.5 text-xs font-medium text-muted">
              {fieldWords(field.name)} · {title}
            </p>
            <ul className="max-h-96 divide-y divide-line overflow-y-auto">
              {facts.map((fact) => (
                <FactItem
                  key={fact.id}
                  fact={fact}
                  onOpen={() => setPanel(null)}
                  documentId={documentId}
                  onRemoved={onRemoved}
                />
              ))}
              {earlier.map((check) => (
                <EarlierItem key={`earlier-${check.id}`} check={check} onRemoved={onRemoved} />
              ))}
            </ul>
          </div>
        </Floating>
      )}
    </td>
  )
}

/** A fact in its cell's panel: its value, where it was read and who checked it, and the buttons that check, correct or open it. */
function FactItem({ fact, documentId, onOpen, onRemoved }: { fact: Fact; documentId: number; onOpen: () => void; onRemoved: () => void }) {
  const workspace = useWorkspace()
  const [correcting, setCorrecting] = useState(false)
  const change = useFactChange(onRemoved)
  const check = fact.check
  // The form gives the keyboard back to the button that opened it, once that is on the page again.
  const returning = useRef(false)
  const correct = (button: HTMLButtonElement | null) => {
    if (!button || !returning.current) return
    returning.current = false
    button.focus()
  }
  const back = () => {
    returning.current = true
    setCorrecting(false)
  }
  // A button pressed waits for its answer without being disabled, which would drop the focus it holds.
  const busy = change.isPending

  return (
    <li className="space-y-1 px-3 py-2">
      <p className="font-medium break-words">{fact.value}</p>
      <p className="text-xs text-muted">{quoteWords(fact)}</p>
      <p className={`flex items-center gap-1 text-xs ${check ? 'text-good' : fact.verified ? 'text-muted' : 'text-warn'}`}>
        {check ? <Check aria-hidden="true" className="size-3.5 shrink-0" /> : !fact.verified && <TriangleAlert aria-hidden="true" className="size-3.5 shrink-0" />}
        {check ? checkWords(check) : 'Not checked.'}
      </p>
      {correcting ? (
        <CorrectionForm
          fact={fact}
          pending={busy}
          onCancel={back}
          onSave={(body) => {
            if (!busy) change.mutate({ fact, body }, { onSuccess: back })
          }}
        />
      ) : (
        <div className="flex flex-wrap gap-1.5 pt-1">
          <Button
            icon={check ? <Undo2 className="size-4" /> : <Check className="size-4" />}
            aria-disabled={busy}
            onClick={() => {
              if (!busy) change.mutate(check ? { check } : { fact, body: {} })
            }}
          >
            {check ? 'Take back the check' : 'Check'}
          </Button>
          <Button ref={correct} icon={<PenLine className="size-4" />} aria-disabled={busy} onClick={() => !busy && setCorrecting(true)}>
            Correct…
          </Button>
          <Button
            icon={<FileSearch className="size-4" />}
            onClick={() => {
              onOpen()
              workspace.openDocument(documentId, sourceOf(fact))
            }}
          >
            Open in the document
          </Button>
        </div>
      )}
      {change.isError && (
        <p role="alert" className="text-xs text-bad">
          {change.error.message}
        </p>
      )}
    </li>
  )
}

/** A check no fact holds since the document was read again: what the member said, and the button that forgets it. */
function EarlierItem({ check, onRemoved }: { check: FactCheck; onRemoved: () => void }) {
  const change = useFactChange(onRemoved)
  return (
    <li className="space-y-1 px-3 py-2">
      <p className="flex items-start gap-1 text-xs text-warn">
        <History aria-hidden="true" className="mt-px size-3.5 shrink-0" />
        {earlierWords(check)}
      </p>
      <Button icon={<X className="size-4" />} aria-disabled={change.isPending} onClick={() => !change.isPending && change.mutate({ check })}>
        Forget it
      </Button>
      {change.isError && (
        <p role="alert" className="text-xs text-bad">
          {change.error.message}
        </p>
      )}
    </li>
  )
}

/** What a member does to a fact from its panel: check or correct it (`body`), or take a check back. */
type FactChange = { fact: Fact; body: FactCheckInput; check?: undefined } | { check: FactCheck; fact?: undefined; body?: undefined }

/** Check, correct or take back, then load the project's tables again; `onRemoved` once they are, for a row the change took out of a table kept to the rows to review. */
function useFactChange(onRemoved: () => void) {
  const { project } = useWorkspace()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (change: FactChange) => {
      if (change.check) await api.removeCheck(change.check.id)
      else await api.checkFact(change.fact.id, change.body)
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: factTablesKey(project.id) })
      onRemoved()
    },
  })
}

/** The value the document states, as the fact's type takes it: words, a number, an amount and its currency, or a date. */
function CorrectionForm({
  fact,
  pending,
  onSave,
  onCancel,
}: {
  fact: Fact
  pending: boolean
  onSave: (body: FactCheckInput) => void
  onCancel: () => void
}) {
  const [draft, setDraft] = useState(() => draftOf(fact))
  const [problem, setProblem] = useState<string | null>(null)
  const type = fact.value_type
  const numeric = type === 'number' || type === 'money'

  return (
    <form
      aria-label={`Correct ${fact.value}`}
      className="space-y-2 rounded-md border border-line bg-page p-2"
      onSubmit={(event) => {
        event.preventDefault()
        const found = correctionProblem(type, draft.value)
        if (found) setProblem(found)
        else onSave(correctionOf(type, draft.value, draft.currency))
      }}
      onKeyDown={(event) => {
        // Escape gives up the correction, and leaves the panel open.
        if (event.key !== 'Escape') return
        event.stopPropagation()
        onCancel()
      }}
    >
      <div className="flex gap-2">
        <InputField
          className="flex-1"
          label={type === 'date' ? 'Date' : type === 'money' ? 'Amount' : numeric ? 'Number' : 'Value'}
          type={type === 'date' ? 'date' : numeric ? 'number' : 'text'}
          step={numeric ? 'any' : undefined}
          value={draft.value}
          autoFocus
          onChange={(event) => setDraft({ ...draft, value: event.target.value })}
        />
        {type === 'money' && (
          <InputField
            className="w-20"
            label="Currency"
            maxLength={10}
            value={draft.currency}
            onChange={(event) => setDraft({ ...draft, currency: event.target.value })}
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
        <Button type="submit" variant="primary" aria-disabled={pending}>
          {pending ? 'Saving…' : 'Save'}
        </Button>
      </div>
    </form>
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
  const valued = !valueless(chosen)
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
