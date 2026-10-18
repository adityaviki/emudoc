// What the header says of the two optional services (ServiceStatus in App.tsx). The server
// words a problem for whoever reads the status (`status` in backend/app/main.py): in
// development the developer, who runs the app, gets the models' names and what to change; a
// deployment's members get what they cannot do for now, and no names.

import type { SystemStatus } from './api.ts'

export interface ServiceLine {
  label: string
  ok: boolean
  /** The sentence the line's tooltip shows. */
  detail: string
}

/** A model and the one asked when it cannot answer: "a (b when it cannot)". */
function withFallback(model: string, fallback: string | null) {
  return fallback ? `${model} (${fallback} when it cannot)` : model
}

function describeModels(llm: SystemStatus['llm']): string {
  if (!llm.available) return llm.error ?? 'The AI model is unavailable.'
  if (!llm.agent_model || !llm.index_model) return 'Questions are answered, and documents get summaries and facts.'
  return `Answers with ${withFallback(llm.agent_model, llm.agent_fallback_model)}, indexes with ${withFallback(llm.index_model, llm.index_fallback_model)}`
}

function describeSearch(embeddings: SystemStatus['embeddings']): string {
  if (!embeddings.ok) return embeddings.error ?? 'Search matches keywords only.'
  if (!embeddings.model) return 'Search finds passages by their meaning as well as by their words.'
  return `Semantic search with ${embeddings.model}`
}

/** The AI model and semantic search, each usable or not, with what that means. */
export function describeServices({ llm, embeddings }: SystemStatus): ServiceLine[] {
  return [
    { label: 'AI model', ok: llm.available, detail: describeModels(llm) },
    { label: 'Semantic search', ok: embeddings.ok, detail: describeSearch(embeddings) },
  ]
}
