// What the page tells the app of an error it rendered into (POST /api/page-errors, from
// components/ErrorBoundary.tsx; backend/app/tracking.py): the error's kind, the API's status
// when the error was the API's refusal, where in the page's own scripts it was raised, and the
// page's path. Never the error's message, which may quote what the page showed (a JSON parse
// error quotes the text it could not read), nor the page's query, which holds the library's
// search words. Each part keeps to what the server takes (backend/app/schemas.py), since a part
// outside it would have the whole report refused.

import { ApiError } from './api.ts'
import type { PageError, PageFrame } from './api.ts'

const KIND = /^[A-Za-z_$][A-Za-z0-9_$.]{0,99}$/
const FUNCTION = /^(?:new |async )?[A-Za-z0-9_$.<>[\]/-]{1,190}$/
const SCRIPT = /^\/assets\/[A-Za-z0-9._-]{1,185}\.js$/
const SEGMENT = /^[A-Za-z0-9_.~-]+$/
const MAX_FRAMES = 50
const MAX_PATH = 200
// A frame as Chrome and Edge write it, "    at fn (https://host/assets/index.js:1:2)" or
// "    at https://host/assets/index.js:1:2", and as Firefox and Safari do, "fn@https://…:1:2".
const V8_FRAME = /^\s*at (?:(.+?) \()?(.+?):(\d+):(\d+)\)?$/
const OTHER_FRAME = /^(.*?)@(.+?):(\d+):(\d+)$/

/** The report of `error`, raised on the page at `pathname` of `origin`. */
export function pageErrorOf(error: unknown, origin: string, pathname: string): PageError {
  const kind = error instanceof ApiError ? 'ApiError' : error instanceof Error ? error.name : ''
  return {
    name: KIND.test(kind) ? kind : 'Error',
    status: error instanceof ApiError ? error.status : null,
    frames: error instanceof Error && error.stack ? framesOf(error.stack, origin) : [],
    path: pagePath(pathname),
  }
}

/**
 * The frames of `stack` in the page's own built scripts, the innermost first: a frame in the
 * browser's own code, an extension's or another origin's says nothing of the app, and in
 * development the scripts are not built, so there are none.
 */
export function framesOf(stack: string, origin: string): PageFrame[] {
  const frames: PageFrame[] = []
  for (const line of stack.split('\n')) {
    const match = V8_FRAME.exec(line) ?? OTHER_FRAME.exec(line)
    const file = match && ownScript(match[2], origin)
    if (!match || !file) continue
    const name = match[1] ?? ''
    frames.push({ function: FUNCTION.test(name) ? name : null, file, line: Number(match[3]), column: Number(match[4]) })
    if (frames.length === MAX_FRAMES) break
  }
  return frames
}

/** The path with every segment that is not a plain word or id written as `*`. */
export function pagePath(pathname: string): string {
  const path = pathname
    .split('/')
    .map((segment) => (segment === '' || SEGMENT.test(segment) ? segment : '*'))
    .join('/')
  return path.startsWith('/') ? path.slice(0, MAX_PATH) : '/'
}

function ownScript(url: string, origin: string): string | null {
  try {
    const parsed = new URL(url)
    return parsed.origin === origin && SCRIPT.test(parsed.pathname) ? parsed.pathname : null
  } catch {
    return null
  }
}
