// A project's connected folders as the library's "Connected folders" words them
// (backend/app/connectors/, backend/app/api/connectors.py): how each stands, when it syncs, why a
// consent connected nothing, and what the view asks before it disconnects one.

import type { Connector, ConnectorProvider } from './api.ts'
import type { Question } from './components/Confirm.tsx'
import { formatDateTime } from './format.ts'

export const connectorsKey = (projectId: number) => ['connectors', projectId] as const

/** Each service in the members' words, those this deployment offers none of among them, for the audit log. */
export const SERVICE_NAMES: Record<ConnectorProvider, string> = {
  google: 'Google Drive',
  dropbox: 'Dropbox',
  onedrive: 'OneDrive',
}

/**
 * Why the browser came back from the service with nothing connected, by the code the server sent it back with
 * (CONSENT_FAILURES in backend/app/connectors/base.py); null for a code it does not send.
 */
export function connectError(code: string | null): string | null {
  switch (code) {
    case 'cancelled':
      return 'The connection was cancelled, so nothing was connected.'
    case 'expired':
      return 'The connection took too long or was started in another tab. Connect again.'
    case 'refused':
      return 'The service did not accept the connection. Connect again.'
    case 'scope':
      return 'The service was connected without leave to see your files, which it asks for on a line of its own. Connect again, and tick it.'
    case 'lasting':
      return 'The service gave no lasting access to your files, so nothing was connected. Connect again, and allow Emudoc to keep reading them.'
    case 'setup':
      return 'The service refused this deployment’s connection with it. Ask whoever runs Emudoc to look at it.'
    case 'unreachable':
      return 'The service did not answer. Connect again in a moment.'
    case 'not_allowed':
      return 'You no longer may change this project, so nothing was connected.'
    default:
      return null
  }
}

/** How a connector stands, in a few words: "Synced 24 Oct 2026, 10:02", "Syncing…", "Paused". */
export function standingWords(connector: Connector): string {
  if (connector.status === 'choosing') return connector.mine ? 'Choose its folder' : `${connector.member} is choosing its folder`
  if (connector.status === 'paused') return 'Paused'
  if (connector.syncing) return 'Syncing…'
  if (connector.problem) return 'Not synced in full'
  return connector.synced_at ? `Synced ${formatDateTime(connector.synced_at)}` : 'Waiting for its first sync'
}

/** What a connector holds and when it syncs next, in a sentence. */
export function keptWords(connector: Connector, intervalMinutes: number): string {
  const kept = connector.kept === 1 ? '1 document' : `${connector.kept.toLocaleString()} documents`
  const into = connector.target ? `in ${connector.target}` : 'in the project’s root'
  if (connector.status !== 'active') return `${kept} ${into}.`
  return `${kept} ${into}, synced every ${intervalMinutes} minutes.`
}

/** Who connected it, from which account. */
export function connectedWords(connector: Connector): string {
  const who = connector.mine ? 'You' : connector.member
  return connector.account ? `${who} connected it as ${connector.account}.` : `${who} connected it.`
}

/** How many files are no document, when more were left out than the list shows. */
export function moreRejected(connector: Connector): string | null {
  const more = connector.rejected_count - connector.rejected.length
  return more > 0 ? `and ${more.toLocaleString()} more` : null
}

export const DISCONNECT: Question = {
  title: 'Disconnect the folder?',
  body: 'It will be synced no more, and Emudoc gives back its access to the service. The documents it brought stay in the library.',
  action: 'Disconnect',
}
