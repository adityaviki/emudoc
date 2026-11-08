import { AlignLeft, FileImage } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { DocumentContent } from './api.ts'

/**
 * How a document shown as it looks is shown (backend/app/conversion.py): by the pages of its view, the PDF the
 * converter made of its file, or by its text, where a cited passage is marked and the outline sits beside.
 */
export type Shown = 'original' | 'text'

export const SHOWN_CHOICES: { value: Shown; label: string; icon: LucideIcon }[] = [
  { value: 'original', label: 'Original', icon: FileImage },
  { value: 'text', label: 'Text', icon: AlignLeft },
]

/** What the pages view is given of a document's view: its pages' sizes, and no text, which has no place on them. */
export const viewContent = (sizes: [number, number][]): DocumentContent => ({ blocks: [], page_sizes: sizes, view_page_sizes: [] })
