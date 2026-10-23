// A project's extraction templates as the library's Templates view words and writes them
// (backend/app/extraction.py, backend/app/api/templates.py): what each says, the form's draft
// and what it sends, and what the view asks before deleting one or reading the documents again.

import type { ExtractionTemplate, FactType, TemplateInput } from './api.ts'
import type { Question } from './components/Confirm.tsx'

export const templatesKey = (projectId: number) => ['templates', projectId] as const

/** Each type a field may be, as the form offers it. */
export const FACT_TYPES: Record<FactType, string> = {
  text: 'Text',
  number: 'Number',
  date: 'Date',
  money: 'Amount of money',
}

/** How a template's examples are written in one line of the form. */
export const EXAMPLES_APART = ';'

const count = (n: number, noun: string) => `${n} ${noun}${n === 1 ? '' : 's'}`

/** A field's name as its facts are named: what the server makes of it (normalize_fact_name in backend/app/indexing/enrich.py). */
export function factName(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 100)
}

/** A kind of document as templates name it, in lowercase words (kind_of in backend/app/extraction.py). */
export function kindOf(text: string): string {
  return text.replace(/[\s_]+/g, ' ').trim().toLowerCase().slice(0, 100)
}

/** A field as the form holds it, its examples in one line. */
export interface FieldDraft {
  name: string
  type: FactType
  description: string
  required: boolean
  examples: string
}

export interface TemplateDraft {
  doc_type: string
  title: string
  description: string
  fields: FieldDraft[]
}

export const NEW_FIELD: FieldDraft = { name: '', type: 'text', description: '', required: false, examples: '' }

/** What the form starts from: nothing for a new template, else the template as it is, a built-in one to become the project's own. */
export function draftOf(template: ExtractionTemplate | null): TemplateDraft {
  if (!template) return { doc_type: '', title: '', description: '', fields: [{ ...NEW_FIELD }] }
  return {
    doc_type: template.doc_type,
    title: template.title,
    description: template.description,
    fields: template.fields.map((field) => ({ ...field, examples: field.examples.join(`${EXAMPLES_APART} `) })),
  }
}

/** What the form sends: everything trimmed, the examples split apart, a field with nothing in it left out. */
export function inputOf(draft: TemplateDraft): TemplateInput {
  return {
    doc_type: draft.doc_type.trim(),
    title: draft.title.trim(),
    description: draft.description.trim(),
    fields: draft.fields
      .filter((field) => field.name.trim() || field.description.trim() || field.examples.trim())
      .map((field) => ({
        name: field.name.trim(),
        type: field.type,
        description: field.description.trim(),
        required: field.required,
        examples: field.examples
          .split(EXAMPLES_APART)
          .map((example) => example.trim())
          .filter(Boolean),
      })),
  }
}

/** What stops the draft from being saved as it stands, in the form's words; null when nothing does. The server checks the same. */
export function draftProblem(draft: TemplateDraft): string | null {
  const input = inputOf(draft)
  if (!input.title) return 'Give the template a name.'
  if (!kindOf(input.doc_type)) return 'Name the kind of document the template is for.'
  if (input.fields.length === 0) return 'Add a field for the template to read.'
  const seen = new Set<string>()
  for (const field of input.fields) {
    const name = factName(field.name)
    if (!name) return field.name ? `The field “${field.name}” needs a letter or a digit in its name.` : 'Name each field.'
    if (seen.has(name)) return `Two fields are named ${name}. Give each field a name of its own.`
    seen.add(name)
  }
  return null
}

/** The line under a template's name: the kind it is for, its fields, the documents last read for it, those being read and those its last readings changed. */
export function templateWords(template: ExtractionTemplate): string {
  const required = template.fields.filter((field) => field.required).length
  const parts = [`For ${template.doc_type} documents`, `${count(template.fields.length, 'field')}${required ? `, ${required} required` : ''}`]
  if (template.documents) parts.push(`${count(template.documents, 'document')} read for it`)
  if (template.reading) parts.push(`${template.reading} being read`)
  if (template.changed) parts.push(`${template.changed} changed when read again`)
  return parts.join(' · ')
}

/** Where a template comes from, for its chip: Emudoc's, or the project's own in the place of Emudoc's. */
export function originOf(template: ExtractionTemplate): string | null {
  if (template.built_in) return 'Built in'
  return template.replaces_built_in ? 'Replaces the built-in one' : null
}

/** What deleting a project's own template asks, and what becomes of its kind. */
export function deletionQuestion(template: ExtractionTemplate): Question {
  const read = template.replaces_built_in ? `for the built-in ${template.doc_type} template` : 'without a template'
  return {
    title: `Delete the template “${template.title}”?`,
    body: `The project's ${template.doc_type} documents are read ${read} once they are read again; until then they keep the facts read for this one. This cannot be undone.`,
    action: 'Delete it',
  }
}

/** Why the documents cannot be read again just now. */
export const MODEL_AWAY = 'The AI model is unavailable just now: read them again once it is back.'
