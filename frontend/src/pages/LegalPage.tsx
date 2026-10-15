import { Markdown } from '../components/Markdown.tsx'
import { Loading, Notice } from '../components/ui.tsx'
import { useLegal } from '../hooks.ts'

/**
 * The terms of service or the privacy policy, public so that they can be read before signing
 * up. The server fills them in from the deployment's own settings (backend/app/legal/): who runs
 * it, where the documents are kept, how long each thing is kept, so the page says what the app
 * does.
 */
export function LegalPage({ document }: { document: 'terms' | 'privacy' }) {
  const legal = useLegal()
  return (
    <div className="w-full max-w-2xl">
      {legal.isPending ? (
        <Loading />
      ) : legal.isError ? (
        <Notice tone="bad">{legal.error.message}</Notice>
      ) : (
        <article className="rounded-xl border border-line bg-surface px-6 py-5 text-sm shadow-xs">
          <Markdown text={legal.data[document]} followLinks />
        </article>
      )}
    </div>
  )
}
