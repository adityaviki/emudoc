import { ArrowLeft, Search } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router'
import type { HelpArticle } from '../api.ts'
import { Markdown } from '../components/Markdown.tsx'
import { FIELD, Loading, Notice } from '../components/ui.tsx'
import { useHelpArticles, usePageTitle } from '../hooks.ts'
import { matching, nothingFound } from './articles.ts'

/**
 * The help pages, a member's: their contents at /help, with a search over them, and an article at
 * /help/<slug>, both in the shell, so that the help menu leads to them from anywhere. The server
 * fills them in from the deployment's own figures and services (backend/app/help), so they say
 * what this Emudoc does; the menu itself says whom to write to.
 */
export function HelpPage() {
  const { slug } = useParams()
  const help = useHelpArticles()
  const scroller = useRef<HTMLDivElement>(null)
  // An article opened from the foot of another starts at its top.
  useEffect(() => {
    scroller.current?.scrollTo(0, 0)
  }, [slug])
  const article = slug ? help.data?.find((found) => found.slug === slug) : undefined
  usePageTitle(slug ? article && `${article.title} · Emudoc help` : 'Help · Emudoc')

  return (
    <div ref={scroller} className="h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl px-6 py-10">
        {help.isPending ? (
          <Loading />
        ) : help.isError ? (
          <Notice tone="bad">{help.error.message}</Notice>
        ) : slug ? (
          <>
            <Link to="/help" className="mb-3 inline-flex items-center gap-1 text-[13px] text-muted hover:text-ink">
              <ArrowLeft className="size-3.5" />
              All help
            </Link>
            {article ? (
              <article className="rounded-xl border border-line bg-surface px-6 py-5 text-sm shadow-xs">
                <Markdown text={article.text} followLinks />
              </article>
            ) : (
              <Notice tone="info">There is no help page at this address.</Notice>
            )}
          </>
        ) : (
          <Contents articles={help.data} />
        )}
        <p className="mt-4 text-center text-[13px] leading-relaxed text-muted">
          Still stuck? The question mark in the header says whom to write to.
        </p>
      </div>
    </div>
  )
}

function Contents({ articles }: { articles: HelpArticle[] }) {
  const [query, setQuery] = useState('')
  const found = matching(articles, query)
  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">Help</h1>
      <div className="relative mt-4">
        <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted" />
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search the help"
          aria-label="Search the help"
          className={`${FIELD} pl-8`}
        />
      </div>
      {found.length ? (
        <ul className="mt-4 divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface shadow-xs">
          {found.map((article) => (
            <li key={article.slug}>
              <Link to={`/help/${article.slug}`} className="block px-5 py-3 transition hover:bg-sunken">
                <span className="block text-sm font-medium">{article.title}</span>
                <span className="mt-0.5 block text-[13px] text-muted">{article.summary}</span>
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-6 text-center text-[13px] text-muted">{nothingFound(query)}</p>
      )}
    </>
  )
}
