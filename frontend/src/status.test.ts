import { describe, expect, it } from 'vitest'
import type { SystemStatus } from './api.ts'
import { describeServices } from './status.ts'

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
  currency: 'USD',
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
  currency: 'USD',
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
