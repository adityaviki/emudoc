import { ArrowDownUp, ChevronDown, Clock, FolderCog, Folder as FolderIcon, LayoutGrid, List, Star, Tag as TagIcon, Tags, X } from 'lucide-react'
import { useId, useRef, useState } from 'react'
import { REVIEW_LABELS, REVIEW_STATES } from '../reviews.ts'
import { TAG_COLORS } from '../api.ts'
import type { Document, DocumentFilter, DocumentType, Folder, ListingPlace, Tag, TagColor } from '../api.ts'
import { folderChoices } from '../automations.ts'
import { Confirm } from '../components/Confirm.tsx'
import type { MenuItem } from '../components/Menu.tsx'
import { Checkbox, FIELD, IconButton, InputField, SelectField } from '../components/ui.tsx'
import { useContextMenu } from '../components/useContextMenu.tsx'
import { FIRST_ORDER, MAX_WITHIN, SORTS, SORT_NAMES, TAG_COLOR_LABELS, datesProblem, filterWords, placeName, sortWords, tagsWords } from '../library.ts'
import type { LibraryLayout } from '../library.ts'
import { nextState, overfull, overfullWords, tagStates, tagsChange } from '../selection.ts'
import type { TagState, TagsChange } from '../selection.ts'
import type { Arrangement } from './listing.ts'

/** Each colour's dot, as Tailwind finds the classes written out whole. */
const TAG_DOT: Record<TagColor, string> = {
  grey: 'bg-tag-grey',
  red: 'bg-tag-red',
  orange: 'bg-tag-orange',
  yellow: 'bg-tag-yellow',
  green: 'bg-tag-green',
  teal: 'bg-tag-teal',
  blue: 'bg-tag-blue',
  purple: 'bg-tag-purple',
  pink: 'bg-tag-pink',
}

/**
 * Words for the eye alone beside a row's or a tile's name, which its description reads out apart: drawn from an attribute
 * by CSS, they are no text of the row's own, whose name is the file's (axe's label-content-name-mismatch holds every
 * text drawn inside it to its name).
 */
export function Seen({ text, className = '' }: { text: string; className?: string }) {
  return <span aria-hidden data-text={text} className={`before:content-[attr(data-text)] ${className}`} />
}

export function TagDot({ color, className = 'size-2' }: { color: TagColor; className?: string }) {
  return <span aria-hidden className={`inline-block shrink-0 rounded-full ${TAG_DOT[color]} ${className}`} />
}

/**
 * A document's tags beside its name: the first few as chips, and how many more; read out by `id`, a sentence of them all,
 * which the row names in its description. The sentence is `hidden`, which a description still reads, so that it is no
 * text of the row's own, whose name is the document's alone; the chips are for the eye.
 */
export function TagChips({ tags, id, shown = 2 }: { tags: Tag[]; id: string; shown?: number }) {
  if (!tags.length) return null
  const rest = tags.length - shown
  return (
    <span className="ml-2 flex min-w-0 shrink items-center gap-1">
      <span id={id} hidden>
        {tagsWords(tags)}
      </span>
      {tags.slice(0, shown).map((tag) => (
        <span key={tag.id} aria-hidden className="flex min-w-0 items-center gap-1 rounded bg-sunken px-1.5 py-px text-[11px] text-ink">
          <TagDot color={tag.color} />
          <Seen text={tag.name} className="max-w-20 truncate" />
        </span>
      ))}
      {rest > 0 && <Seen text={`+${rest}`} className="text-[11px] text-muted" />}
    </span>
  )
}

interface PlaceBarProps {
  place: ListingPlace
  arrangement: Arrangement
  onArrange: (arrangement: Arrangement) => void
  layout: LibraryLayout
  onLayout: (layout: LibraryLayout) => void
  tags: Tag[]
  onPlace: (place: ListingPlace) => void
  onTags: () => void
  onFiling: () => void
}

/**
 * Under the library's search: what it shows (all its files, the member's favourites or recent ones, or a tag's documents)
 * and the way to the project's tags and filing rules, the order it lists them in, and as a list or a grid.
 */
export function PlaceBar(props: PlaceBarProps) {
  const { place, arrangement, layout, tags } = props
  const menu = useContextMenu()
  const folder = 'folder' in place
  const recent = 'recent' in place
  const current = folder ? 'All files' : placeName(place, tags)
  const isPlace = (other: ListingPlace) => JSON.stringify(other) === JSON.stringify(place)
  const choose = (other: ListingPlace): MenuItem => ({
    label: 'folder' in other ? 'All files' : placeName(other, tags),
    icon: 'tag' in other ? TagIcon : 'favorites' in other ? Star : 'recent' in other ? Clock : FolderIcon,
    checked: 'folder' in other ? folder : isPlace(other),
    onSelect: () => props.onPlace(other),
  })
  const places: MenuItem[] = [
    choose({ folder: null }),
    choose({ favorites: true }),
    choose({ recent: true }),
    ...(tags.length ? (['separator', ...tags.map((tag) => choose({ tag: tag.id }))] as MenuItem[]) : []),
    'separator',
    { label: 'Tags…', icon: Tags, onSelect: props.onTags },
    { label: 'Filing rules…', icon: FolderCog, onSelect: props.onFiling },
  ]
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
    <div className="flex items-center gap-1 px-2 pb-2">
      <button
        type="button"
        aria-haspopup="menu"
        aria-label={`Show: ${current}`}
        onClick={(event) => menu.openAt(event.currentTarget, places)}
        className={`${button} font-medium`}
      >
        <span className="truncate">{current}</span>
        <ChevronDown className="size-3.5 shrink-0 text-muted" />
      </button>
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
      <div role="group" aria-label="Show as" className="flex shrink-0 rounded-md bg-sunken p-0.5">
        {(
          [
            ['list', 'List', List],
            ['grid', 'Grid', LayoutGrid],
          ] as const
        ).map(([value, label, Icon]) => (
          <button
            key={value}
            type="button"
            aria-pressed={layout === value}
            aria-label={label}
            title={label}
            onClick={() => props.onLayout(value)}
            className={`flex size-6.5 items-center justify-center rounded-[5px] transition ${
              layout === value ? 'bg-surface text-ink shadow-xs' : 'text-muted hover:text-ink'
            }`}
          >
            <Icon className="size-3.5" />
          </button>
        ))}
      </div>
      {menu.element}
    </div>
  )
}

interface DocumentTagsDialogProps {
  /** The document, or the several chosen together in the library. */
  documents: Document[]
  tags: Tag[]
  /** How many tags the project may have, past which none is made here. */
  room: boolean
  onCreate: (name: string, color: TagColor) => Promise<Tag>
  onSave: (change: TagsChange) => void
  onClose: () => void
}

/**
 * Asks which of the project's tags a document carries, or which go on and off every one of several, and makes one more
 * there and then. A tag only some of them carry is shown mixed, and left so unless it is pressed.
 */
export function DocumentTagsDialog({ documents, tags, room, onCreate, onSave, onClose }: DocumentTagsDialogProps) {
  const [was] = useState(() => tagStates(documents, tags))
  const [states, setStates] = useState(was)
  const [name, setName] = useState('')
  const [color, setColor] = useState<TagColor>('grey')
  const [refused, setRefused] = useState<string | null>(null)
  const [making, setMaking] = useState(false)
  const set = (id: number, state: TagState) => setStates((held) => new Map(held).set(id, state))
  const change = tagsChange(was, states)
  const full = overfull(documents, change)
  const make = () => {
    const trimmed = name.trim()
    if (!trimmed) return
    setMaking(true)
    setRefused(null)
    onCreate(trimmed, color).then(
      (made) => {
        set(made.id, 'all')
        setName('')
      },
      (error: Error) => setRefused(error.message),
    ).finally(() => setMaking(false))
  }
  const [one] = documents
  const several = documents.length > 1
  return (
    <Confirm
      title={several ? `Tags of ${documents.length} documents` : `Tags of “${one.filename}”`}
      body={
        several
          ? 'Tick a tag to put it on every one of them, or clear it to take it off every one. A tag only some of them carry is shown half ticked, and stays as it is unless you press it.'
          : 'Choose the tags it carries. The library shows them on it, and lists each tag’s documents wherever they are.'
      }
      action="Save"
      danger={false}
      disabled={full.length > 0}
      onConfirm={() => onSave(change)}
      onClose={onClose}
    >
      <div className="space-y-3">
        {tags.length > 0 ? (
          <fieldset className="max-h-56 space-y-1.5 overflow-y-auto">
            <legend className="sr-only">The project’s tags</legend>
            {tags.map((tag, index) => {
              const state = states.get(tag.id) ?? 'none'
              return (
                <Checkbox
                  key={tag.id}
                  data-autofocus={index === 0 ? true : undefined}
                  checked={state === 'all'}
                  mixed={state === 'some'}
                  onChange={() => set(tag.id, nextState(state, was.get(tag.id) ?? 'none'))}
                  label={
                    <span className="flex items-center gap-1.5">
                      <TagDot color={tag.color} />
                      {tag.name}
                    </span>
                  }
                />
              )
            })}
          </fieldset>
        ) : (
          <p className="text-[13px] text-muted">The project has no tags yet. Make the first one here.</p>
        )}
        {room && (
          <div className="flex items-end gap-2">
            <InputField
              label="New tag"
              className="min-w-0 flex-1"
              data-autofocus={tags.length === 0 ? true : undefined}
              value={name}
              maxLength={50}
              onChange={(event) => setName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  make()
                }
              }}
            />
            <ColorField value={color} onChange={setColor} />
            <button type="button" onClick={make} disabled={!name.trim() || making} className="h-8 rounded-md border border-line px-2.5 text-[13px] transition hover:bg-sunken disabled:text-muted">
              Add
            </button>
          </div>
        )}
        {(refused || full.length > 0) && (
          <p role="alert" className="text-xs text-bad">
            {refused ?? overfullWords(full)}
          </p>
        )}
      </div>
    </Confirm>
  )
}

/** A tag's colour, chosen from the page's own. */
export function ColorField({ value, onChange, label = 'Colour' }: { value: TagColor; onChange: (color: TagColor) => void; label?: string }) {
  return (
    <SelectField label={label} value={value} onChange={(event) => onChange(event.target.value as TagColor)}>
      {TAG_COLORS.map((color) => (
        <option key={color} value={color}>
          {TAG_COLOR_LABELS[color]}
        </option>
      ))}
    </SelectField>
  )
}

interface FilterFieldsProps {
  filter: DocumentFilter
  onChange: (filter: DocumentFilter) => void
  tags: Tag[]
  /** The types the model gave the project's documents, to choose from. */
  types: DocumentType[]
  folders: Folder[]
  /** Whether documents' reviews are rolled out to the organisation, which a filter may then narrow by. */
  reviews: boolean
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

/** The parts of a filter, each its own fields: tags, types, the documents' dates, their reviews, a folder and the words they mention. */
function FilterFields({ filter, onChange, tags, types, folders, reviews }: FilterFieldsProps) {
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
      {tags.length > 0 && (
        <fieldset className="space-y-1.5">
          <legend className="mb-1 text-xs font-medium text-muted">Tagged</legend>
          {tags.map((tag) => (
            <Checkbox
              key={tag.id}
              checked={filter.tags.includes(tag.id)}
              onChange={(event) => set({ tags: toggled(filter.tags, tag.id, event.target.checked) })}
              label={
                <span className="flex items-center gap-1.5">
                  <TagDot color={tag.color} />
                  {tag.name}
                </span>
              }
            />
          ))}
          {filter.tags.length > 1 && (
            <Checkbox className="pt-1" checked={filter.every_tag} onChange={(event) => set({ every_tag: event.target.checked })} label="Every one of these tags, not any of them" />
          )}
        </fieldset>
      )}
      <TypesField types={types} chosen={filter.types} onChange={(chosen) => set({ types: chosen })} />
      <DatesField after={filter.after} before={filter.before} onChange={set} />
      {reviews && (
        <fieldset className="space-y-1.5">
          <legend className="mb-1 text-xs font-medium text-muted">In review</legend>
          {REVIEW_STATES.map((state) => (
            <Checkbox
              key={state}
              checked={filter.reviews.includes(state)}
              onChange={(event) => set({ reviews: toggled(filter.reviews, state, event.target.checked) })}
              label={REVIEW_LABELS[state]}
            />
          ))}
        </fieldset>
      )}
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

/** Asks what a search searches within: by tags, types, the documents' dates, their reviews, a folder and the words they mention. */
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
          {problem ?? `${filterWords(filter, fields.tags, folderPath)}.`}
        </p>
      </div>
    </Confirm>
  )
}
