import { FileCode, FileImage, FileSpreadsheet, FileText, FileType } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { isImage, isPdf, isSheet } from '../api.ts'
import type { Document } from '../api.ts'

/** How a kind of file is drawn: its icon, the colour class for it, and a short name. */
export interface FileKind {
  icon: LucideIcon
  color: string
  label: string
}

const KINDS = {
  pdf: { icon: FileText, color: 'text-file-pdf', label: 'PDF' },
  word: { icon: FileType, color: 'text-file-word', label: 'Word' },
  sheet: { icon: FileSpreadsheet, color: 'text-file-sheet', label: 'Spreadsheet' },
  image: { icon: FileImage, color: 'text-file-image', label: 'Image' },
  markdown: { icon: FileCode, color: 'text-muted', label: 'Markdown' },
  text: { icon: FileText, color: 'text-muted', label: 'Text' },
} satisfies Record<string, FileKind>

/** The kind of file a document is, from its MIME type (backend/app/api/documents.py). */
export function fileKind(document: Document): FileKind {
  if (isPdf(document)) return KINDS.pdf
  if (isImage(document)) return KINDS.image
  if (isSheet(document)) return document.mime_type === 'text/csv' ? { ...KINDS.sheet, label: 'CSV' } : KINDS.sheet
  if (document.mime_type.endsWith('wordprocessingml.document')) return KINDS.word
  if (document.mime_type === 'text/markdown') return KINDS.markdown
  return KINDS.text
}
