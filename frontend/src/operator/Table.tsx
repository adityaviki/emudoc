import type { ReactNode } from 'react'

/**
 * A column of a table: its heading, whether it holds figures, which line up on the right, and
 * whether its heading is for screen readers alone, over buttons whose names say what they do.
 */
export interface Column {
  label: string
  figure?: boolean
  unseen?: boolean
}

/** A table of the area's rows, which scrolls sideways on a narrow screen rather than squeeze its columns. */
export function Table({ columns, children }: { columns: Column[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-surface">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-b border-line text-left text-xs text-muted">
            {columns.map((column) => (
              <th key={column.label} className={`px-3 py-2 font-medium whitespace-nowrap ${column.figure ? 'text-right' : ''}`}>
                {column.unseen ? <span className="sr-only">{column.label}</span> : column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">{children}</tbody>
      </table>
    </div>
  )
}

export function Cell({ figure, children, className = '' }: { figure?: boolean; children?: ReactNode; className?: string }) {
  return <td className={`px-3 py-2 align-top ${figure ? 'text-right whitespace-nowrap tabular-nums' : ''} ${className}`}>{children}</td>
}

/** The page's title, with what it shows and the command that prints the same. */
export function Heading({ title, command, children }: { title: string; command?: string; children?: ReactNode }) {
  return (
    <div className="mb-5">
      <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
      {children && <p className="mt-1 text-[13px] leading-relaxed text-muted">{children}</p>}
      {command && (
        <p className="mt-1 text-xs text-muted">
          In a shell: <code className="rounded bg-sunken px-1 py-px">python -m app.maintenance {command}</code>
        </p>
      )}
    </div>
  )
}
