import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Check, Copy, Mail, Plus, X } from 'lucide-react'
import { useId, useRef, useState } from 'react'
import { api, mayChange } from '../api.ts'
import type { Mailbox, MailboxInput } from '../api.ts'
import { useMe } from '../auth.ts'
import { folderChoices } from '../automations.ts'
import { Confirm } from '../components/Confirm.tsx'
import { Button, EmptyState, FIELD, IconButton, Loading, Notice, SelectField } from '../components/ui.tsx'
import { useFolders } from '../hooks.ts'
import { NEW_ADDRESS, TAKE_AWAY, mailboxKey, outcomeWords, senderEntry, senderProblem, sendersWords, sentWords } from '../mailbox.ts'
import { useWorkspace } from './state.ts'

/**
 * The project's address, in the library's place (backend/app/inbound.py): mail to it from the
 * senders its members allow becomes its documents. Whoever may change the project gives it the
 * address, lists whom it takes mail from and where what arrives goes, and gives it a new one or
 * takes it away; everyone who sees the project reads it and what became of the messages it
 * received lately.
 */
export function MailboxView() {
  const workspace = useWorkspace()
  const { project } = workspace
  const changes = mayChange(useMe().role) && !project.sample
  const queryClient = useQueryClient()
  const mailbox = useQuery({ queryKey: mailboxKey(project.id), queryFn: () => api.mailbox(project.id) })
  const [refused, setRefused] = useState<string | null>(null)
  const [asking, setAsking] = useState<'address' | 'away' | null>(null)
  // Taking the address away takes its buttons with the focus: the keyboard goes on from the heading.
  const heading = useRef<HTMLHeadingElement>(null)
  const fail = (error: Error) =>
    setRefused(error.message)
  // A change answers the address as it is now, which the view shows at once.
  const changed = {
    onMutate: () => setRefused(null),
    onSuccess: (saved: Mailbox) => queryClient.setQueryData(mailboxKey(project.id), saved),
    onError: fail,
  }
  const save = useMutation({ mutationFn: (body: MailboxInput) => api.setMailbox(project.id, body), ...changed })
  const renew = useMutation({ mutationFn: () => api.newMailAddress(project.id), ...changed })
  const takeAway = useMutation({
    mutationFn: () => api.deleteMailbox(project.id),
    onMutate: () => {
      setRefused(null)
      heading.current?.focus()
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: mailboxKey(project.id) }),
    onError: fail,
  })
  const busy = save.isPending || renew.isPending || takeAway.isPending
  const data = mailbox.data

  return (
    <div className="flex h-full min-h-0 flex-col bg-page">
      <div className="flex h-11 shrink-0 items-center gap-1.5 border-b border-line bg-surface px-2">
        <IconButton label="Back to the library" onClick={() => workspace.goToFolder(null)}>
          <ArrowLeft className="size-4" />
        </IconButton>
        <h2 ref={heading} tabIndex={-1} className="flex-1 text-[13px] font-semibold">
          Email in
        </h2>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
        {refused && (
          <Notice tone="bad">
            <div className="flex items-start justify-between gap-3">
              <span>{refused}</span>
              <IconButton label="Dismiss" onClick={() => setRefused(null)} className="p-0.5!">
                <X className="size-3.5" />
              </IconButton>
            </div>
          </Notice>
        )}
        {mailbox.isPending && <Loading />}
        {mailbox.isError && <Notice tone="bad">Could not load the project’s address: {mailbox.error.message}</Notice>}
        {data && !data.available && (
          <EmptyState icon={<Mail />} title="Email in is not set up yet">
            This deployment does not receive mail yet, so a project cannot have an address. Ask whoever runs it.
          </EmptyState>
        )}
        {data && data.available && !data.address && (
          <EmptyState icon={<Mail />} title="Send documents to this project by email">
            <p>
              Give the project an address of its own: what you and the senders you allow send to it becomes its documents,
              each attachment a file and the message’s text one too.
            </p>
            {changes && (
              <Button variant="primary" className="mt-3" disabled={busy} onClick={() => save.mutate({ senders: [], folder_id: null })}>
                Give it an address
              </Button>
            )}
          </EmptyState>
        )}
        {data?.address && <Address mailbox={data} changes={changes} busy={busy} onAsk={setAsking} />}
        {data?.address && <Senders mailbox={data} changes={changes} busy={busy} onSave={(body) => save.mutate(body)} />}
        {data && data.messages.length > 0 && <Messages mailbox={data} />}
      </div>
      {asking && (
        <Confirm
          {...(asking === 'address' ? NEW_ADDRESS : TAKE_AWAY)}
          danger={asking === 'away'}
          onConfirm={() => (asking === 'address' ? renew.mutate() : takeAway.mutate())}
          onClose={() => setAsking(null)}
        />
      )}
    </div>
  )
}

function Address({
  mailbox,
  changes,
  busy,
  onAsk,
}: {
  mailbox: Mailbox
  changes: boolean
  busy: boolean
  onAsk: (question: 'address' | 'away') => void
}) {
  const [copied, setCopied] = useState(false)
  const address = mailbox.address ?? ''
  async function copy() {
    try {
      await navigator.clipboard.writeText(address)
      setCopied(true)
    } catch {
      // The clipboard can be unavailable; the address is on screen to copy by hand.
    }
  }
  return (
    <section aria-labelledby="mail-address" className="space-y-2">
      <h3 id="mail-address" className="text-xs font-medium text-muted">
        The project’s address
      </h3>
      <p className="break-all rounded-md border border-line bg-surface px-3 py-2 font-mono text-[13px]">{address}</p>
      <div className="flex flex-wrap gap-2">
        <Button icon={copied ? <Check className="size-4" /> : <Copy className="size-4" />} onClick={() => void copy()}>
          {copied ? 'Copied' : 'Copy the address'}
        </Button>
        {changes && (
          <>
            <Button disabled={busy} onClick={() => onAsk('address')}>
              New address
            </Button>
            <Button variant="danger" disabled={busy} onClick={() => onAsk('away')}>
              Take it away
            </Button>
          </>
        )}
      </div>
    </section>
  )
}

function Senders({
  mailbox,
  changes,
  busy,
  onSave,
}: {
  mailbox: Mailbox
  changes: boolean
  busy: boolean
  onSave: (body: MailboxInput) => void
}) {
  const { project } = useWorkspace()
  const folders = useFolders(project.id)
  const id = useId()
  const [typed, setTyped] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const { senders } = mailbox
  const full = senders.length >= mailbox.max_senders

  function add() {
    const found = senderProblem(typed, senders)
    if (found) {
      setProblem(found)
      return
    }
    setProblem(null)
    setTyped('')
    onSave({ senders: [...senders, senderEntry(typed)], folder_id: mailbox.folder_id })
  }

  return (
    <>
      <section aria-labelledby={`${id}-senders`} className="space-y-2">
        <h3 id={`${id}-senders`} className="text-xs font-medium text-muted">
          Who may send to it
        </h3>
        <p className="text-[13px] text-muted">{sendersWords(senders)}</p>
        <p className="text-xs text-muted">
          A message counts as its sender’s only when their mail service proves it (DMARC or DKIM); anything else is turned
          away, and nothing is sent back.
        </p>
        {senders.length > 0 && (
          <ul aria-label="Senders" className="space-y-0.5">
            {senders.map((sender) => (
              <li key={sender} className="flex items-center gap-2 rounded-md px-2 py-1 hover:bg-sunken">
                <span className="min-w-0 flex-1 truncate text-[13px]">{sender}</span>
                {changes && (
                  <IconButton
                    label={`Remove ${sender}`}
                    disabled={busy}
                    onClick={() => onSave({ senders: senders.filter((other) => other !== sender), folder_id: mailbox.folder_id })}
                  >
                    <X className="size-4" />
                  </IconButton>
                )}
              </li>
            ))}
          </ul>
        )}
        {changes && !full && (
          <form
            className="flex items-start gap-2"
            onSubmit={(event) => {
              event.preventDefault()
              add()
            }}
          >
            <div className="min-w-0 flex-1">
              <label htmlFor={`${id}-sender`} className="sr-only">
                An address or a domain
              </label>
              <input
                id={`${id}-sender`}
                className={FIELD}
                value={typed}
                maxLength={320}
                placeholder="ap@supplier.com or supplier.com"
                aria-describedby={problem ? `${id}-problem` : undefined}
                aria-invalid={problem ? true : undefined}
                onChange={(event) => setTyped(event.target.value)}
              />
              {problem && (
                <p id={`${id}-problem`} className="mt-1 text-xs text-bad">
                  {problem}
                </p>
              )}
            </div>
            <Button type="submit" icon={<Plus className="size-4" />} disabled={busy}>
              Add
            </Button>
          </form>
        )}
      </section>
      <section aria-labelledby={`${id}-folder`} className="space-y-2">
        <h3 id={`${id}-folder`} className="sr-only">
          Where it goes
        </h3>
        {changes ? (
          <SelectField
            label="What arrives goes into"
            value={mailbox.folder_id ?? ''}
            disabled={busy || !folders.data}
            onChange={(event) => onSave({ senders, folder_id: event.target.value ? Number(event.target.value) : null })}
          >
            <option value="">The project’s root</option>
            {folderChoices(folders.data ?? []).map((folder) => (
              <option key={folder.id} value={folder.id}>
                {folder.label}
              </option>
            ))}
          </SelectField>
        ) : (
          <p className="text-[13px] text-muted">What arrives goes into {mailbox.folder ?? 'the project’s root'}.</p>
        )}
      </section>
    </>
  )
}

function Messages({ mailbox }: { mailbox: Mailbox }) {
  const id = useId()
  return (
    <section aria-labelledby={`${id}-messages`} className="space-y-2">
      <h3 id={`${id}-messages`} className="text-xs font-medium text-muted">
        Received lately
      </h3>
      <p className="text-xs text-muted">What became of each message is kept {mailbox.days} days; its documents stay in the library.</p>
      <ul aria-label="Messages received" className="space-y-1.5">
        {mailbox.messages.map((message) => (
          <li key={message.id} className="rounded-md border border-line bg-surface px-3 py-2 text-[13px]">
            <div className="flex items-baseline justify-between gap-3">
              <p className="min-w-0 truncate font-medium">{message.subject || '(no subject)'}</p>
              <p className={`shrink-0 text-xs ${message.outcome === 'kept' ? 'text-good' : message.outcome === 'refused' ? 'text-bad' : 'text-warn'}`}>
                {outcomeWords(message)}
              </p>
            </div>
            <p className="truncate text-xs text-muted">{sentWords(message)}</p>
            {message.reason && <p className="mt-1 text-xs text-muted">{message.reason}</p>}
            {message.rejected.length > 0 && (
              <ul aria-label="Files not kept" className="mt-1 space-y-0.5 text-xs text-muted">
                {message.rejected.map((file, index) => (
                  <li key={index}>
                    {file.filename}: {file.reason}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
