import { FileSpreadsheet, FileText, Image } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { isImage, isSheet } from '../api.ts'
import type { Document } from '../api.ts'

/** The icon for a document's kind of file. */
export function fileIcon(document: Document): LucideIcon {
  if (isImage(document)) return Image
  if (isSheet(document)) return FileSpreadsheet
  return FileText
}
