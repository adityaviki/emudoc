import { Download, ExternalLink, FileCode, FileImage, FileSpreadsheet, FileText, FileType, Presentation } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { fileUrl, isImage, isPdf, isSheet } from '../api.ts'
import type { Document } from '../api.ts'
import type { MenuItem } from '../components/Menu.tsx'
import { scanPassed } from '../indexing.ts'

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
  slides: { icon: Presentation, color: 'text-file-slides', label: 'PowerPoint' },
  markdown: { icon: FileCode, color: 'text-muted', label: 'Markdown' },
  text: { icon: FileText, color: 'text-muted', label: 'Text' },
} satisfies Record<string, FileKind>

/** The kind of file a document is, from its MIME type (backend/app/api/documents.py). */
export function fileKind(document: Document): FileKind {
  if (isPdf(document)) return KINDS.pdf
  if (document.mime_type.startsWith('image/')) return KINDS.image
  if (isSheet(document)) return document.mime_type === 'text/csv' ? { ...KINDS.sheet, label: 'CSV' } : KINDS.sheet
  if (document.mime_type.endsWith('wordprocessingml.document')) return KINDS.word
  if (document.mime_type.endsWith('presentationml.presentation')) return KINDS.slides
  if (document.mime_type === 'text/markdown') return KINDS.markdown
  return KINDS.text
}

/**
 * How the original file is offered. An image opens in a new tab; any other file downloads, since
 * the API serves it as an attachment rather than let a browser's viewer run what a PDF can carry
 * (`download_file` in backend/app/api/documents.py).
 */
export function originalFile(document: Document) {
  const href = fileUrl(document.id)
  if (isImage(document)) {
    return { label: 'Open original file', icon: ExternalLink, link: { href, target: '_blank', rel: 'noreferrer' } }
  }
  return { label: 'Download original file', icon: Download, link: { href, download: '' } }
}

/** The menu entry for the original: offered once the malware scan has passed, never once it flagged the file. */
export function originalFileItem(document: Document): MenuItem {
  const { label, icon, link } = originalFile(document)
  return {
    label,
    icon,
    disabled: !scanPassed(document) || document.status === 'quarantined',
    // Followed as a click on the link would be, so a download leaves the page where it is.
    onSelect: () => {
      const anchor = window.document.createElement('a')
      Object.assign(anchor, link)
      anchor.click()
    },
  }
}
