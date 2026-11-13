// A project's writing templates as My files' Templates view words and writes them (backend/app/draft_templates.py,
// backend/app/api/draft_templates.py): how the agent writes each kind of document into a member's My files, what each
// template says, the form's state and what it sends, what it still lacks, and what the view asks before a kind gets its
// built-in template back.

import type { DraftKind, DraftSection, DraftTemplate, DraftTemplateInput } from './api.ts'
import type { Question } from './components/Confirm.tsx'

export const draftTemplatesKey = (projectId: number) => ['draft-templates', projectId] as const

/** Each kind the agent writes to a template, in the order the templates are listed, with what it is. */
export const DRAFT_KINDS: { value: DraftKind; label: string; hint: string }[] = [
  { value: 'reply_email', label: 'Reply email', hint: 'An answer to an email the project holds, point by point.' },
  {
    value: 'dispute_letter',
    label: 'Dispute letter',
    hint: 'A letter to whoever issued a document, each figure disputed beside what it should be.',
  },
  { value: 'summary_memo', label: 'Summary memo', hint: 'The documents summed up for a colleague, and what to do next.' },
  { value: 'comparison_table', label: 'Comparison table', hint: 'The documents side by side, a column for each field that matters.' },
  { value: 'report', label: 'Report', hint: 'What the documents show, under headings, with conclusions.' },
]

export const KIND_LABELS = Object.fromEntries(DRAFT_KINDS.map((kind) => [kind.value, kind.label])) as Record<DraftKind, string>

/** A kind in the middle of a sentence, with its article: "a dispute letter", "an …". */
export function kindWords(kind: DraftKind): string {
  const name = KIND_LABELS[kind].toLowerCase()
  return `${/^[aeiou]/.test(name) ? 'an' : 'a'} ${name}`
}

/** A template's ceilings, as the server holds them (MAX_DRAFT_SECTIONS and the rest in backend/app/schemas.py). */
export const MAX_SECTIONS = 20
export const TONE_CHARS = 500
export const HEADING_CHARS = 100
export const SECTION_CHARS = 500
export const GUIDANCE_CHARS = 2000

export const NEW_SECTION: DraftSection = { heading: '', description: '', required: true }

/** Where a kind's template comes from, as its row's chip says. */
export const originWords = (template: Pick<DraftTemplate, 'built_in'>) => (template.built_in ? 'Built in' : 'The project’s own')

/** A template's parts in a line, by name, those a document may leave out marked. */
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
    body: `The project’s own ${name} template goes for good, and the next ${name} is written to the built-in one. The files written already stay as they are.`,
    action: 'Use the built-in one',
  }
}
