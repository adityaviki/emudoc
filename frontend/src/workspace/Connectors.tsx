import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ChevronRight, FolderSync, RefreshCw, X } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { api, mayChange } from '../api.ts'
import type { Connector, ConnectorProvider, Connectors, RemoteFolder } from '../api.ts'
import { useMe } from '../auth.ts'
import { folderChoices } from '../library.ts'
import { Confirm } from '../components/Confirm.tsx'
import { Button, EmptyState, IconButton, Loading, Notice, SelectField } from '../components/ui.tsx'
import { DISCONNECT, connectError, connectedWords, connectorsKey, keptWords, moreRejected, standingWords } from '../connectors.ts'
import { useFolders } from '../hooks.ts'
import { listingsKey } from './listing.ts'
import { useWorkspace } from './state.ts'

/**
 * The project's connected folders, in the library's place (backend/app/connectors/): a folder of a member's Google
 * Drive, Dropbox or OneDrive whose files the project keeps in step with. Whoever may change the project connects one,
 * the browser going to the service for their consent and coming back here, then chooses its folder there and where
 * its files go; they sync it now, change where its files go and disconnect it, and its member connects it again once
 * the service took its access back. Everyone who sees the project reads how each stands.
 */
export function ConnectorsView() {
  const workspace = useWorkspace()
  const { project } = workspace
  const me = useMe()
  const changes = mayChange(me.role) && !project.sample
  const queryClient = useQueryClient()
  // Ask again while a sync runs, to show it done and what it brought.
  const connectors = useQuery({
    queryKey: connectorsKey(project.id),
    queryFn: () => api.connectors(project.id),
    refetchInterval: (query) => (query.state.data?.connectors.some((connector) => connector.syncing) ? 3000 : false),
  })
  const [params, setParams] = useSearchParams()
  // Why the service sent the browser back with nothing connected, read once from the address the callback sent it to.
  const [failed, setFailed] = useState(() => connectError(params.get('connect_error')))
  useEffect(() => {
    if (params.has('connect_error')) {
      setParams(
        (next) => {
          next.delete('connect_error')
          return next
        },
        { replace: true },
      )
    }
  }, [params, setParams])
  const [refused, setRefused] = useState<string | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const fail = (error: Error) =>
    setRefused(error.message)
  const connect = useMutation({
    mutationFn: ({ provider, connectorId }: { provider: ConnectorProvider; connectorId: number | null }) =>
      api.connect(project.id, provider, connectorId),
    onMutate: () => setRefused(null),
    onSuccess: ({ url }) => window.location.assign(url),
    onError: fail,
  })

  // A sync that ended brought documents the library shows: its listings are asked again.
  const syncing = connectors.data?.connectors.some((connector) => connector.syncing) ?? false
  const wasSyncing = useRef(syncing)
  useEffect(() => {
    if (wasSyncing.current && !syncing) {
      void queryClient.invalidateQueries({ queryKey: listingsKey(project.id) })
      void queryClient.invalidateQueries({ queryKey: ['folders', project.id] })
    }
    wasSyncing.current = syncing
  }, [syncing, project.id, queryClient])

  const data = connectors.data
  const leaving = connect.isPending || connect.isSuccess
  const room = data ? data.connectors.length < data.max_connectors : false

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <div className="flex h-11 shrink-0 items-center gap-1.5 border-b border-line bg-surface px-2">
        <IconButton label="Back to the library" onClick={() => workspace.goToFolder(null)}>
          <ArrowLeft className="size-4" />
        </IconButton>
        <h2 ref={heading} tabIndex={-1} className="flex-1 text-[13px] font-semibold">
          Connected folders
        </h2>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
        {failed && <Dismissible text={failed} onDismiss={() => setFailed(null)} />}
        {refused && <Dismissible text={refused} onDismiss={() => setRefused(null)} />}
        {connectors.isPending && <Loading />}
        {connectors.isError && <Notice tone="bad">Could not load the connected folders: {connectors.error.message}</Notice>}
        {data && data.connectors.length === 0 && (
          <EmptyState icon={<FolderSync />} title="Keep the project in step with a folder">
            <p>
              Connect a folder of your Google Drive, Dropbox or OneDrive: its files become the project’s documents, a file
              changed there is read again, and one taken out of it goes to the project’s trash.
            </p>
            {data.services.length === 0 && <p className="mt-2">No service is set up on this deployment yet. Ask whoever runs it.</p>}
          </EmptyState>
        )}
        {data && data.services.length > 0 && changes && room && (
          <div className="flex flex-wrap gap-2">
            {data.services.map((service) => (
              <Button
                key={service.provider}
                variant={data.connectors.length ? 'secondary' : 'primary'}
                icon={<FolderSync className="size-4" />}
                disabled={leaving}
                onClick={() => connect.mutate({ provider: service.provider, connectorId: null })}
              >
                Connect {service.label}
              </Button>
            ))}
          </div>
        )}
        {data?.connectors.map((connector) => (
          <ConnectorCard
            key={connector.id}
            connector={connector}
            data={data}
            changes={changes}
            leaving={leaving}
            onConnectAgain={() => connect.mutate({ provider: connector.provider, connectorId: connector.id })}
            onRefused={fail}
            onGone={() => heading.current?.focus()}
          />
        ))}
      </div>
    </div>
  )
}

function Dismissible({ text, onDismiss }: { text: string; onDismiss: () => void }) {
  return (
    <Notice tone="bad">
      <div className="flex items-start justify-between gap-3">
        <span>{text}</span>
        <IconButton label="Dismiss" onClick={onDismiss} className="p-0.5!">
          <X className="size-3.5" />
        </IconButton>
      </div>
    </Notice>
  )
}

function ConnectorCard({
  connector,
  data,
  changes,
  leaving,
  onConnectAgain,
  onRefused,
  onGone,
}: {
  connector: Connector
  data: Connectors
  changes: boolean
  leaving: boolean
  onConnectAgain: () => void
  onRefused: (error: Error) => void
  onGone: () => void
}) {
  const { project } = useWorkspace()
  const folders = useFolders(project.id)
  const queryClient = useQueryClient()
  const id = useId()
  const [asking, setAsking] = useState(false)
  const key = connectorsKey(project.id)
  // A change answers the connector as it is now, which the view shows at once.
  const saved = (next: Connector) =>
    queryClient.setQueryData<Connectors>(key, (old) =>
      old ? { ...old, connectors: old.connectors.map((each) => (each.id === next.id ? next : each)) } : old,
    )
  const target = useMutation({
    mutationFn: (folderId: number | null) => api.setConnector(project.id, connector.id, { target_folder_id: folderId }),
    onSuccess: saved,
    onError: onRefused,
  })
  const sync = useMutation({ mutationFn: () => api.syncConnector(project.id, connector.id), onSuccess: saved, onError: onRefused })
  const disconnect = useMutation({
    mutationFn: () => api.disconnect(project.id, connector.id),
    onMutate: onGone,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: key }),
    onError: onRefused,
  })
  const busy = target.isPending || sync.isPending || disconnect.isPending
  const tone = connector.status === 'paused' || connector.problem ? 'text-warn' : connector.syncing ? 'text-muted' : 'text-good'

  return (
    <section aria-labelledby={`${id}-name`} className="space-y-2 rounded-md border border-line bg-surface px-3 py-2.5">
      <div className="flex items-baseline justify-between gap-3">
        <h3 id={`${id}-name`} className="min-w-0 truncate text-[13px] font-medium">
          {connector.label}
          {connector.folder ? `: ${connector.folder}` : ''}
        </h3>
        <p className={`shrink-0 text-xs ${tone}`}>{standingWords(connector)}</p>
      </div>
      <p className="text-xs text-muted">{connectedWords(connector)}</p>
      {connector.problem && <p className="text-xs text-warn">{connector.problem}</p>}
      {connector.status === 'choosing' && connector.mine && changes && (
        <FolderChooser connector={connector} onChosen={saved} onRefused={onRefused} />
      )}
      {connector.status !== 'choosing' && <p className="text-[13px] text-muted">{keptWords(connector, data.interval_minutes)}</p>}
      {connector.status !== 'choosing' && changes && (
        <SelectField
          label="Its files go into"
          value={target.isPending ? (target.variables ?? '') : (connector.target_folder_id ?? '')}
          disabled={busy || !folders.data}
          onChange={(event) => target.mutate(event.target.value ? Number(event.target.value) : null)}
        >
          <option value="">The project’s root</option>
          {folderChoices(folders.data ?? []).map((folder) => (
            <option key={folder.id} value={folder.id}>
              {folder.label}
            </option>
          ))}
        </SelectField>
      )}
      {changes && (
        <div className="flex flex-wrap gap-2">
          {connector.status === 'active' && (
            <Button icon={<RefreshCw className="size-4" />} disabled={busy || connector.syncing} onClick={() => sync.mutate()}>
              Sync now
            </Button>
          )}
          {connector.status === 'paused' && connector.mine && (
            <Button variant="primary" disabled={busy || leaving} onClick={onConnectAgain}>
              Connect again
            </Button>
          )}
          <Button variant="danger" disabled={busy} onClick={() => setAsking(true)}>
            Disconnect
          </Button>
        </div>
      )}
      {connector.rejected.length > 0 && (
        <details className="text-xs text-muted">
          <summary className="cursor-pointer">
            {connector.rejected_count === 1 ? '1 file is not a document' : `${connector.rejected_count.toLocaleString()} files are not documents`}
          </summary>
          <ul aria-label="Files not kept" className="mt-1 space-y-0.5">
            {connector.rejected.map((file) => (
              <li key={file.path}>
                {file.path}: {file.reason}
              </li>
            ))}
            {moreRejected(connector) && <li>{moreRejected(connector)}</li>}
          </ul>
        </details>
      )}
      {asking && <Confirm {...DISCONNECT} onConfirm={() => disconnect.mutate()} onClose={() => setAsking(false)} />}
    </section>
  )
}

/**
 * The member's own folders at the service, browsed from where the service starts down to the one to keep the project
 * in step with, and where its files go in the project.
 */
function FolderChooser({
  connector,
  onChosen,
  onRefused,
}: {
  connector: Connector
  onChosen: (connector: Connector) => void
  onRefused: (error: Error) => void
}) {
  const { project } = useWorkspace()
  const folders = useFolders(project.id)
  const id = useId()
  // The folders browsed into, from where the service starts; empty there.
  const [trail, setTrail] = useState<RemoteFolder[]>([])
  const [targetId, setTargetId] = useState<number | null>(null)
  const here = trail.at(-1) ?? null
  const listing = useQuery({
    queryKey: ['remote-folders', connector.id, here?.ref ?? null],
    queryFn: () => api.remoteFolders(project.id, connector.id, here?.ref ?? null),
  })
  const choose = useMutation({
    mutationFn: (folder: RemoteFolder) => api.setConnector(project.id, connector.id, { folder_ref: folder.ref, target_folder_id: targetId }),
    onSuccess: onChosen,
    onError: onRefused,
  })

  return (
    <div className="space-y-2">
      <nav aria-label={`Folders of ${connector.label}`} className="flex flex-wrap items-center gap-1 text-xs">
        <button type="button" className="text-accent hover:underline" onClick={() => setTrail([])}>
          {connector.label}
        </button>
        {trail.map((folder, index) => (
          <span key={folder.ref} className="flex items-center gap-1">
            <ChevronRight aria-hidden className="size-3 text-muted" />
            {index === trail.length - 1 ? (
              <span aria-current="location">{folder.name}</span>
            ) : (
              <button type="button" className="text-accent hover:underline" onClick={() => setTrail(trail.slice(0, index + 1))}>
                {folder.name}
              </button>
            )}
          </span>
        ))}
      </nav>
      {listing.isPending && <Loading />}
      {listing.isError && <p className="text-xs text-bad">{listing.error.message}</p>}
      {listing.data && (
        <ul aria-label={here ? `Folders in ${here.name}` : `Where ${connector.label} starts`} className="space-y-0.5">
          {listing.data.folders.length === 0 && <li className="px-2 py-1 text-xs text-muted">No folders in it.</li>}
          {listing.data.folders.map((folder) => (
            <li key={folder.ref}>
              <button
                type="button"
                className="flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-[13px] hover:bg-sunken"
                onClick={() => setTrail([...trail, folder])}
              >
                <span className="min-w-0 flex-1 truncate">{folder.name}</span>
                <ChevronRight aria-hidden className="size-4 text-muted" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <SelectField
        id={`${id}-target`}
        label="Its files go into"
        value={targetId ?? ''}
        disabled={!folders.data}
        onChange={(event) => setTargetId(event.target.value ? Number(event.target.value) : null)}
      >
        <option value="">The project’s root</option>
        {folderChoices(folders.data ?? []).map((folder) => (
          <option key={folder.id} value={folder.id}>
            {folder.label}
          </option>
        ))}
      </SelectField>
      {here?.choosable && (
        <Button variant="primary" disabled={choose.isPending} onClick={() => choose.mutate(here)}>
          Sync {here.name}
        </Button>
      )}
    </div>
  )
}
