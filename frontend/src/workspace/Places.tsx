import { ArrowDownUp, Clock, Folder as FolderIcon, Star, Trash2, X } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useId, useRef, useState } from 'react'
import type { DocumentFilter, DocumentType, Folder, ListingPlace } from '../api.ts'
import { Confirm } from '../components/Confirm.tsx'
import type { MenuItem } from '../components/Menu.tsx'
import { Checkbox, FIELD, IconButton, SelectField } from '../components/ui.tsx'
import { useContextMenu } from '../components/useContextMenu.tsx'
import { FIRST_ORDER, MAX_WITHIN, SORTS, SORT_NAMES, allNamed, datesProblem, filterWords, folderChoices, sortWords } from '../library.ts'
import type { Arrangement } from './listing.ts'

/**
 * Words for the eye alone beside a row's name, which its description reads out apart: drawn from an attribute
 * by CSS, they are no text of the row's own, whose name is the file's (axe's label-content-name-mismatch holds every
 * text drawn inside it to its name).
 */
export function Seen({ text, className = '' }: { text: string; className?: string }) {
  return <span aria-hidden data-text={text} className={`before:content-[attr(data-text)] ${className}`} />
}

interface PlaceBarProps {
  place: ListingPlace
  arrangement: Arrangement
  onArrange: (arrangement: Arrangement) => void
  onPlace: (place: ListingPlace) => void
  onTrash: () => void
}

/** The places the bar offers, each an icon: all the files in their folders, the member's favourites, the documents they opened lately. */
const PLACES: { label: string; icon: LucideIcon; place: ListingPlace }[] = [
  { label: 'All files', icon: FolderIcon, place: { folder: null } },
  { label: 'Favourites', icon: Star, place: { favorites: true } },
  { label: 'Recent', icon: Clock, place: { recent: true } },
]

/** Each of the group's buttons, pressed or not, as the bar draws it inside its sunken group. */
const segment = (pressed: boolean) =>
  `flex size-6.5 items-center justify-center rounded-[5px] transition ${pressed ? 'bg-surface text-ink shadow-xs' : 'text-muted hover:text-ink'}`

/**
 * Above the library's search box: what it shows, each place an icon pressed while it is shown (all its files, the
 * member's favourites or recent ones), and in the same group the way to the project's trash, a page of its own that is
 * never pressed here; then the order it lists them in. A search matches the names of the place shown, all the files of
 * the project from a folder.
 */
export function PlaceBar(props: PlaceBarProps) {
  const { place, arrangement } = props
  const menu = useContextMenu()
  const recent = 'recent' in place
  const shown = (one: ListingPlace) =>
    'folder' in one ? 'folder' in place || allNamed(place) : 'favorites' in one ? 'favorites' in place : 'recent' in place
  const orders: MenuItem[] = [
    ...SORT_NAMES.map((sort) => ({
      label: SORTS[sort].label,
      checked: arrangement.sort === sort,
      onSelect: () => props.onArrange({ sort, order: sort === arrangement.sort ? arrangement.order : FIRST_ORDER[sort] }),
    })),
    'separator',
    ...(['asc', 'desc'] as const).map((order) => ({
      label: SORTS[arrangement.sort][order],
      checked: arrangement.order === order,
      onSelect: () => props.onArrange({ ...arrangement, order }),
    })),
  ]
  const button = 'flex h-7 min-w-0 items-center gap-1.5 rounded-md px-2 text-[13px] text-ink transition hover:bg-sunken disabled:text-muted disabled:hover:bg-transparent'
  return (
    <div className="flex items-center gap-1">
      <div role="group" aria-label="Show" className="flex shrink-0 rounded-md bg-sunken p-0.5">
        {PLACES.map(({ label, icon: Icon, place: one }) => (
          <button
            key={label}
            type="button"
            aria-pressed={shown(one)}
            aria-label={label}
            title={label}
            onClick={() => props.onPlace(one)}
            className={segment(shown(one))}
          >
            <Icon className="size-3.5" />
          </button>
        ))}
        <button type="button" aria-label="Trash" title="Trash" onClick={props.onTrash} className={segment(false)}>
          <Trash2 className="size-3.5" />
        </button>
      </div>
      <div className="flex-1" />
      <button
        type="button"
        aria-haspopup="menu"
        aria-label={recent ? 'Order: the latest opened first' : `Order: ${sortWords(arrangement.sort, arrangement.order)}`}
        title={recent ? 'The documents you opened lately are listed the latest first.' : undefined}
        disabled={recent}
        onClick={(event) => menu.openAt(event.currentTarget, orders)}
        className={button}
      >
        <ArrowDownUp className="size-3.5 shrink-0 text-muted" />
        <span className="hidden truncate @xs:inline">{recent ? 'Latest opened' : SORTS[arrangement.sort].label}</span>
      </button>
      {menu.element}
    </div>
  )
}

interface FilterFieldsProps {
  filter: DocumentFilter
  onChange: (filter: DocumentFilter) => void
  /** The types the model gave the project's documents, to choose from. */
  types: DocumentType[]
  folders: Folder[]
}

const toggled = <T,>(list: T[], value: T, on: boolean) => (on ? [...list, value] : list.filter((other) => other !== value))

/** The types a filter may narrow by, ticked where it does: the documents' own, and one chosen that no document has now, so that it can be taken off. */
export function TypesField({ types, chosen, onChange }: { types: DocumentType[]; chosen: string[]; onChange: (types: string[]) => void }) {
  const choices = [...new Set([...types.map((one) => one.type), ...chosen])]
  if (!choices.length) return null
  return (
    <fieldset className="space-y-1.5">
      <legend className="mb-1 text-xs font-medium text-muted">Of the type</legend>
      {choices.map((type) => (
        <Checkbox key={type} checked={chosen.includes(type)} onChange={(event) => onChange(toggled(chosen, type, event.target.checked))} label={type} />
      ))}
    </fieldset>
  )
}

/** The span of the documents' own dates a filter narrows to, either end open. */
export function DatesField({ after, before, onChange }: { after: string | null; before: string | null; onChange: (dates: { after: string | null; before: string | null }) => void }) {
  const id = useId()
  return (
    <fieldset>
      <legend className="mb-1 text-xs font-medium text-muted">Dated</legend>
      <div className="flex flex-wrap gap-2">
        <label htmlFor={`${id}-after`} className="text-[13px]">
          From
          <input id={`${id}-after`} type="date" className={`${FIELD} mt-1`} value={after ?? ''} onChange={(event) => onChange({ after: event.target.value || null, before })} />
        </label>
        <label htmlFor={`${id}-before`} className="text-[13px]">
          To
          <input id={`${id}-before`} type="date" className={`${FIELD} mt-1`} value={before ?? ''} onChange={(event) => onChange({ after, before: event.target.value || null })} />
        </label>
      </div>
    </fieldset>
  )
}

/** The parts of a filter, each its own fields: types, the documents' dates, a folder and the words they mention. */
function FilterFields({ filter, onChange, types, folders }: FilterFieldsProps) {
  const [words, setWords] = useState('')
  const wordsInput = useRef<HTMLInputElement>(null)
  const set = (change: Partial<DocumentFilter>) => onChange({ ...filter, ...change })
  const room = filter.mentions.length < MAX_WITHIN
  const mention = () => {
    const trimmed = words.trim()
    if (!trimmed || !room) return
    if (!filter.mentions.includes(trimmed)) set({ mentions: [...filter.mentions, trimmed] })
    setWords('')
  }
  return (
    <>
      <TypesField types={types} chosen={filter.types} onChange={(chosen) => set({ types: chosen })} />
      <DatesField after={filter.after} before={filter.before} onChange={set} />
      <SelectField label="In" value={filter.folder_id ?? ''} onChange={(event) => set({ folder_id: event.target.value ? Number(event.target.value) : null })}>
        <option value="">Anywhere in the project</option>
        {folderChoices(folders).map((choice) => (
          <option key={choice.id} value={choice.id}>
            {choice.label} and the folders in it
          </option>
        ))}
      </SelectField>
      <fieldset className="space-y-1.5">
        <legend className="mb-1 text-xs font-medium text-muted">Mentioning</legend>
        {filter.mentions.map((one, index) => (
          <div key={one} className="flex items-center justify-between gap-2 rounded-md bg-sunken py-0.5 pr-0.5 pl-2 text-[13px]">
            <span className="min-w-0 truncate">“{one}”</span>
            <IconButton
              label={`Take off “${one}”`}
              onClick={() => {
                set({ mentions: filter.mentions.filter((_, other) => other !== index) })
                wordsInput.current?.focus()
              }}
            >
              <X className="size-3.5" />
            </IconButton>
          </div>
        ))}
        {room && (
          <div className="flex items-center gap-2">
            <input
              ref={wordsInput}
              aria-label="Words a document mentions"
              placeholder="Words a passage holds, every one"
              className={`${FIELD} min-w-0 flex-1`}
              value={words}
              maxLength={200}
              onChange={(event) => setWords(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  mention()
                }
              }}
            />
            <button type="button" onClick={mention} disabled={!words.trim()} className="h-8 rounded-md border border-line px-2.5 text-[13px] transition hover:bg-sunken disabled:text-muted">
              Add
            </button>
          </div>
        )}
      </fieldset>
    </>
  )
}

interface SearchFilterDialogProps extends Omit<FilterFieldsProps, 'onChange'> {
  onApply: (filter: DocumentFilter) => void
  onClose: () => void
}

/** Asks what a search searches within: by types, the documents' dates, a folder and the words they mention. */
export function SearchFilterDialog({ filter: held, onApply, onClose, ...fields }: SearchFilterDialogProps) {
  const [filter, setFilter] = useState(held)
  const problem = datesProblem(filter)
  const folderPath = folderChoices(fields.folders).find((choice) => choice.id === filter.folder_id)?.label ?? null
  return (
    <Confirm
      title="Filter the search"
      body="Search only the documents that match what you choose. A part left empty takes every document."
      action="Apply"
      danger={false}
      disabled={problem !== null}
      onConfirm={() => onApply(filter)}
      onClose={onClose}
    >
      <div className="max-h-[55vh] space-y-4 overflow-y-auto pr-1">
        <FilterFields {...fields} filter={filter} onChange={setFilter} />
        <p className="text-xs text-muted" aria-live="polite">
          {problem ?? `${filterWords(filter, folderPath)}.`}
        </p>
      </div>
    </Confirm>
  )
}
