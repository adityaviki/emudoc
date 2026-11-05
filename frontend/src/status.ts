// What the header says of the background work when no worker takes it (ServiceStatus in
// App.tsx), and why a question is held. The server words a problem for whoever reads the status
// (`status` in backend/app/main.py): in development the developer, who runs the app, gets what
// to change; a deployment's members get what they cannot do for now.

import type { SystemStatus } from './api.ts'

export interface ServiceLine {
  label: string
  ok: boolean
  /** The sentence the line's tooltip shows. */
  detail: string
}

// Said when the server gives no sentence of its own.
const WORK_STOPPED = 'Background work is not running, so documents wait to be indexed and questions cannot be asked.'

/**
 * The header's lines: the background work, only while no worker runs, since a worker that runs
 * is no news. The AI model and search by meaning have none: the Ask tab and the search say
 * when they are out of reach.
 */
export function describeServices({ workers }: SystemStatus): ServiceLine[] {
  return workers.running ? [] : [{ label: 'Background work', ok: false, detail: workers.error ?? WORK_STOPPED }]
}

/**
 * Why a question cannot be asked now, from `/api/status`: no model to answer it, or no worker to
 * run the answer, which would otherwise wait unanswered. Null when it can, and while the status
 * is not known.
 */
export function askHeld(status: SystemStatus | undefined): string | null {
  if (!status) return null
  if (!status.llm.available) return status.llm.error ?? 'The AI model is unavailable.'
  if (!status.workers.running) return status.workers.error ?? WORK_STOPPED
  return null
}
