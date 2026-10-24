import { sourceLabel } from '../api.ts'
import type { Source } from '../api.ts'
import { Markdown } from '../components/Markdown.tsx'
import { CitationChip } from './ChatPane.tsx'

/**
 * A text the model wrote whole, a discrepancy report or a draft, its citations numbered in the order it first makes
 * them and opening the passages they cite, which it lists below it. A reference to a passage it was never shown stays
 * plain text. With `lines`, a line break is kept where it is written, as a letter's address needs and as the draft's
 * files keep it (backend/app/draft_export.py).
 */
export function CitedText({ text, sources, onOpen, lines = false }: { text: string; sources: Source[]; onOpen: (source: Source) => void; lines?: boolean }) {
  const known = new Map(sources.map((source) => [source.id, source]))
  const cited = [...new Set([...text.matchAll(/\[P(\d+)\]/g)].map((match) => Number(match[1])))].filter((id) => known.has(id))
  const numberOf = (id: number) => cited.indexOf(id) + 1
  return (
    <>
      <div className={lines ? 'text-[13px] [&_p]:whitespace-pre-line' : 'text-[13px]'}>
        <Markdown
          text={text}
          citation={(id) => {
            const source = known.get(id)
            return source ? <CitationChip number={numberOf(id)} source={source} onOpen={() => onOpen(source)} /> : <span className="text-muted">[P{id}]</span>
          }}
        />
      </div>
      {cited.length > 0 && (
        <div className="border-t border-line pt-2.5">
          <p className="mb-1 text-[11px] font-medium tracking-wide text-muted uppercase">Sources</p>
          <ol className="space-y-0.5 text-xs text-muted">
            {cited.map((id) => (
              <li key={id}>
                <button
                  type="button"
                  onClick={() => onOpen(known.get(id)!)}
                  className="-mx-1 flex gap-2 rounded px-1 py-0.5 text-left transition hover:bg-sunken hover:text-ink"
                >
                  <span className="w-3 shrink-0 font-semibold text-accent tabular-nums">{numberOf(id)}</span>
                  {sourceLabel(known.get(id)!)}
                </button>
              </li>
            ))}
          </ol>
        </div>
      )}
    </>
  )
}
