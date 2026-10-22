import { ArrowLeft, Search, Sparkles } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router'
import type { HelpArticle } from '../api.ts'
import { Markdown } from '../components/Markdown.tsx'
import { FIELD, Loading, Notice } from '../components/ui.tsx'
import { useHelp } from '../hooks.ts'
import { matching } from './articles.ts'

/**
 * The help pages: their contents at /help, with a search over them, and an article at
 * /help/<slug>. Public, like the terms, so that the sign-in pages lead to them too. The server
 * fills them in from the deployment's own figures (backend/app/help), so they say what the app
 * does.
 */
export function HelpPage() {
  const { slug } = useParams()
  const help = useHelp()
  // An article opened from the foot of another starts at its top.
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [slug])

  if (help.isPending) return <Loading />
  if (help.isError) return <Notice tone="bad">{help.error.message}</Notice>
  const { articles, support_email: address } = help.data
  if (!slug) return <Contents articles={articles} address={address} />
  const article = articles.find((found) => found.slug === slug)
  return (
    <div className="w-full max-w-2xl">
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
      <StillStuck address={address} />
    </div>
  )
}

function Contents({ articles, address }: { articles: HelpArticle[]; address: string | null }) {
  const [query, setQuery] = useState('')
  const found = matching(articles, query)
  return (
    <div className="w-full max-w-2xl">
      <div className="flex items-baseline justify-between gap-4">
        <h1 className="text-lg font-semibold tracking-tight">Help</h1>
        <Link to="/whats-new" className="inline-flex items-center gap-1 text-[13px] text-muted hover:text-ink">
          <Sparkles className="size-3.5" />
          What's new
        </Link>
      </div>
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
        <p className="mt-6 text-center text-[13px] text-muted">The help says nothing of “{query.trim()}”.</p>
      )}
      <StillStuck address={address} />
    </div>
  )
}

/** The way to support, under every help page. */
function StillStuck({ address }: { address: string | null }) {
  return (
    <p className="mt-4 text-center text-[13px] leading-relaxed text-muted">
      Still stuck? Write to support from the question mark in the header once you are signed in
      {address && (
        <>
          , or to{' '}
          <a href={`mailto:${address}`} className="text-ink underline underline-offset-2">
            {address}
          </a>
        </>
      )}
      .
    </p>
  )
}
