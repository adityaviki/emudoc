import { useQuery } from '@tanstack/react-query'
import { api } from '../api.ts'
import { Loading, Notice } from '../components/ui.tsx'

/**
 * The companies this deployment hands its users' data to, public so that whoever decides
 * whether to upload can read it first. The server reads the list from its own settings
 * (backend/app/subprocessors.py), so the page changes when a provider does.
 */
export function SubprocessorsPage() {
  const list = useQuery({ queryKey: ['subprocessors'], queryFn: api.subprocessors, staleTime: Infinity })
  return (
    <div className="w-full max-w-2xl">
      <h1 className="text-lg font-semibold tracking-tight">Subprocessors</h1>
      <p className="mt-1 text-[13px] leading-relaxed text-muted">
        The companies this Emudoc deployment sends your data to, what for, and what each receives. The list is read from the
        deployment's own settings, so it changes when they do.
      </p>
      {list.isPending ? (
        <Loading />
      ) : list.isError ? (
        <div className="mt-5">
          <Notice tone="bad">{list.error.message}</Notice>
        </div>
      ) : !list.data.length ? (
        <div className="mt-5">
          <Notice tone="info">This deployment sends your data to no other company.</Notice>
        </div>
      ) : (
        <ul className="mt-5 divide-y divide-line rounded-xl border border-line bg-surface shadow-xs">
          {list.data.map((entry) => (
            <li key={`${entry.name} ${entry.purpose}`} className="px-5 py-4">
              <h2 className="text-sm font-medium">{entry.name}</h2>
              <dl className="mt-2 grid gap-x-4 gap-y-1 text-[13px] leading-relaxed sm:grid-cols-[7rem_1fr]">
                <dt className="text-muted">What for</dt>
                <dd>{entry.purpose}</dd>
                <dt className="text-muted">What it receives</dt>
                <dd>{entry.data}</dd>
              </dl>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
