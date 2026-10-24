import type { APIRequestContext } from '@playwright/test'

// The stand-in for the model and the embeddings endpoint (e2e/standin.py), which
// playwright.config.ts starts on this port.
export const STANDIN_PORT = 18001
const STANDIN = `http://127.0.0.1:${STANDIN_PORT}`

/**
 * Hold the model's next turn of an answer until `releaseTurns`, or a minute: an answer then waits
 * on the model, as on one that thinks for long, for as long as a step needs it to.
 */
export async function holdNextTurn(request: APIRequestContext) {
  expect200(await request.post(`${STANDIN}/hold`))
}

/** How many held turns are waiting for their release: an answer that reached the model while a turn was held. */
export async function heldTurns(request: APIRequestContext): Promise<number> {
  const response = await request.get(`${STANDIN}/held`)
  expect200(response)
  return ((await response.json()) as { waiting: number }).waiting
}

/** Let the held turns answer. */
export async function releaseTurns(request: APIRequestContext) {
  expect200(await request.post(`${STANDIN}/release`))
}

/**
 * Refuse the next embeddings request any of whose texts holds the word, which the app does not
 * try again: the document it was for is left partly indexed, found by its words alone.
 */
export async function refuseEmbeddings(request: APIRequestContext, containing: string) {
  expect200(await request.post(`${STANDIN}/refuse-embeddings`, { data: { containing } }))
}

function expect200(response: { ok(): boolean; url(): string; status(): number }) {
  if (!response.ok()) throw new Error(`${response.url()} answered ${response.status()}.`)
}
