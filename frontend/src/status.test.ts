import { describe, expect, it } from 'vitest'
import type { SystemStatus } from './api.ts'
import { askHeld, describeServices } from './status.ts'

// As the development backend answers: the models named, a problem with what to change.
const development: SystemStatus = {
  llm: {
    available: true,
    agent_model: 'google:gemini-3.5-flash-lite',
    index_model: 'google:gemini-3.5-flash-lite',
    agent_fallback_model: 'anthropic:claude-haiku-5-5',
    index_fallback_model: null,
    error: null,
  },
  embeddings: { ok: true, model: 'nomic-embed-text', error: null },
  workers: { running: true, error: null },
  currency: 'USD',
  uploads: { files: 100, request_bytes: 500 * 1024 * 1024, file_bytes: 100 * 1024 * 1024, extensions: ['.pdf'] },
}

// As a deployment answers its members: no names, and a problem as what cannot be done.
const deployment: SystemStatus = {
  llm: {
    available: true,
    agent_model: null,
    index_model: null,
    agent_fallback_model: null,
    index_fallback_model: null,
    error: null,
  },
  embeddings: { ok: true, model: null, error: null },
  workers: { running: true, error: null },
  currency: 'USD',
  uploads: { files: 100, request_bytes: 500 * 1024 * 1024, file_bytes: 100 * 1024 * 1024, extensions: ['.pdf'] },
}

describe('describeServices', () => {
  it('names the models and their fallbacks to the developer', () => {
    expect(describeServices(development)).toEqual([
      {
        label: 'AI model',
        ok: true,
        detail:
          'Answers with google:gemini-3.5-flash-lite (anthropic:claude-haiku-5-5 when it cannot), indexes with google:gemini-3.5-flash-lite',
      },
      { label: 'Semantic search', ok: true, detail: 'Semantic search with nomic-embed-text' },
    ])
  })

  it("tells a deployment's members what works, naming nothing of how it is set up", () => {
    const [models, search] = describeServices(deployment)
    expect(models).toEqual({
      label: 'AI model',
      ok: true,
      detail: 'Questions are answered, and documents get summaries and facts.',
    })
    expect(search.detail).toBe('Search finds passages by their meaning as well as by their words.')
  })

  it("shows the server's sentence for what is off, adding nothing to it", () => {
    const off: SystemStatus = {
      ...development,
      llm: { ...development.llm, available: false, error: 'Set GOOGLE_API_KEY in .env… Then restart the backend.' },
      embeddings: { ok: false, model: 'nomic-embed-text', error: 'Embedding endpoint unreachable at http://localhost:11434/v1. Search falls back to keywords.' },
    }
    expect(describeServices(off)).toEqual([
      { label: 'AI model', ok: false, detail: 'Set GOOGLE_API_KEY in .env… Then restart the backend.' },
      {
        label: 'Semantic search',
        ok: false,
        detail: 'Embedding endpoint unreachable at http://localhost:11434/v1. Search falls back to keywords.',
      },
    ])

    const unavailable: SystemStatus = {
      ...deployment,
      llm: { ...deployment.llm, available: false, error: 'The AI model is unavailable just now.' },
      embeddings: { ok: false, model: null, error: 'Search matches keywords only just now.' },
    }
    expect(describeServices(unavailable).map((line) => line.detail)).toEqual([
      'The AI model is unavailable just now.',
      'Search matches keywords only just now.',
    ])
  })
})

describe('the background work', () => {
  const stopped: SystemStatus = {
    ...deployment,
    workers: { running: false, error: "Emudoc's background work is not running just now." },
  }

  it('is a line of the header only while no worker runs, in the server’s words', () => {
    expect(describeServices(deployment).map((line) => line.label)).toEqual(['AI model', 'Semantic search'])
    expect(describeServices(stopped)[2]).toEqual({
      label: 'Background work',
      ok: false,
      detail: "Emudoc's background work is not running just now.",
    })
  })

  it('holds a question with no model to answer it or no worker to run the answer', () => {
    expect(askHeld(undefined)).toBeNull()
    expect(askHeld(deployment)).toBeNull()
    expect(askHeld(stopped)).toBe("Emudoc's background work is not running just now.")
    // The model first: without it there is no answer, worker or not.
    const neither: SystemStatus = { ...stopped, llm: { ...deployment.llm, available: false, error: 'The AI model is unavailable just now.' } }
    expect(askHeld(neither)).toBe('The AI model is unavailable just now.')
  })
})
