// A project's draft templates as the drafts' Templates view words and writes them (backend/app/draft_templates.py,
// backend/app/api/drafts.py): what each says, the form's state and what it sends, what it still lacks, and what the
// view asks before a kind gets its built-in template back.

import type { DraftKind, DraftSection, DraftTemplate, DraftTemplateInput } from './api.ts'
import type { Question } from './components/Confirm.tsx'
import { KIND_LABELS } from './drafts.ts'

export const draftTemplatesKey = (projectId: number) => ['draft-templates', projectId] as const

/** A template's ceilings, as the server holds them (MAX_DRAFT_SECTIONS and the rest in backend/app/schemas.py). */
export const MAX_SECTIONS = 20
export const TONE_CHARS = 500
export const HEADING_CHARS = 100
export const SECTION_CHARS = 500
export const GUIDANCE_CHARS = 2000

export const NEW_SECTION: DraftSection = { heading: '', description: '', required: true }

/** Where a kind's template comes from, as its row's chip says. */
export const originWords = (template: Pick<DraftTemplate, 'built_in'>) => (template.built_in ? 'Built in' : 'The project’s own')

/** A template's parts in a line, by name, those a draft may leave out marked. */
export function sectionsWords(template: Pick<DraftTemplate, 'sections'>): string {
  if (template.sections.length === 0) return 'No parts of its own: the kind’s layout alone.'
  return template.sections.map((section) => (section.required ? section.heading : `${section.heading} (where it applies)`)).join(' · ')
}

/** What the form starts from: the template as it is, a built-in one to become the project's own. */
export function templateFormOf(template: DraftTemplate): DraftTemplateInput {
  return { tone: template.tone, sections: template.sections.map((section) => ({ ...section })), guidance: template.guidance }
}

const blank = (section: DraftSection) => !section.heading.trim() && !section.description.trim()

/** What the form sends: each text trimmed, and a part left empty left out. */
export function templateInputOf(form: DraftTemplateInput): DraftTemplateInput {
  return {
    tone: form.tone.trim(),
    sections: form.sections
      .filter((section) => !blank(section))
      .map((section) => ({ heading: section.heading.trim(), description: section.description.trim(), required: section.required })),
    guidance: form.guidance.trim(),
  }
}

/** What the form lacks before it is saved, in a sentence, or null. */
export function templateProblem(form: DraftTemplateInput): string | null {
  const unnamed = form.sections.findIndex((section) => !blank(section) && !section.heading.trim())
  if (unnamed >= 0) return `Name part ${unnamed + 1}, or remove it.`
  const seen = new Set<string>()
  for (const { heading } of templateInputOf(form).sections) {
    if (seen.has(heading.toLowerCase())) return `Two parts are named “${heading}”. Give each part a name of its own.`
    seen.add(heading.toLowerCase())
  }
  return null
}

/** What the view asks before the project's own template of a kind goes, the built-in one taking its place. */
export function builtInAgainQuestion(kind: DraftKind): Question {
  const name = KIND_LABELS[kind].toLowerCase()
  return {
    title: `Use the built-in ${name} template again?`,
    body: `The project’s own ${name} template goes for good, and the next ${name} is written to the built-in one. The drafts written already stay as they are.`,
    action: 'Use the built-in one',
  }
}

/** What the new draft's form says of the template its kind is written to, when it is the project's own. */
export function writtenToWords(template: DraftTemplate | undefined): string | null {
  return template && !template.built_in ? 'Written to the project’s own template for this kind.' : null
}
