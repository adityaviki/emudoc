import type { ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import { Link } from 'react-router'
import remarkGfm from 'remark-gfm'
import { parsePipeTable } from '../format.ts'

const CITATION = /\[P(\d+)\]/g

interface MarkdownProps {
  text: string
  /** Renders a [P<id>] reference found in the text. */
  citation?: (passageId: number) => ReactNode
  /**
   * Whether the text's own links lead anywhere. Off for model output, which a document can
   * steer: a link there could carry what the model read to another site in its address, one
   * click away. It then shows its text and address, to copy, without leading anywhere. The
   * app's own pages turn it on, and a path on this site opens in the app.
   */
  followLinks?: boolean
}

export function Markdown({ text, citation, followLinks = false }: MarkdownProps) {
  // Citations become links so they survive Markdown parsing, then render as chips below.
  const source = citation ? text.replace(CITATION, '[P$1](#p$1)') : text
  return (
    <div className="prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a({ href = '', children }) {
            const match = /^#p(\d+)$/.exec(href)
            if (match && citation) return citation(Number(match[1]))
            if (!followLinks) {
              const shown = typeof children === 'string' && children === href
              return (
                <span>
                  {children}
                  {!shown && href && <span className="break-all text-muted"> ({href})</span>}
                </span>
              )
            }
            if (href.startsWith('/') && !href.startsWith('//')) return <Link to={href}>{children}</Link>
            return (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            )
          },
          table({ node: _node, ...props }) {
            return (
              <div className="overflow-x-auto">
                <table className="data-table" {...props} />
              </div>
            )
          },
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  )
}

/** Source text as stored: plain paragraphs, with pipe tables drawn as tables. */
export function SourceText({ text }: { text: string }) {
  return (
    <div className="space-y-2">
      {text.split('\n\n').map((part, index) => {
        const table = parsePipeTable(part)
        if (table) return <PipeTable key={index} rows={table} />
        return (
          <p key={index} className="whitespace-pre-wrap">
            {part}
          </p>
        )
      })}
    </div>
  )
}

export function PipeTable({ rows }: { rows: string[][] }) {
  const [header, ...body] = rows
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr>
            {header.map((cell, index) => (
              <th key={index}>{cell}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, index) => (
                <td key={index}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
