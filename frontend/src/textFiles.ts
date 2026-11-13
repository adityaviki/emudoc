// The files a text that cites passages downloads as, an answer or a whole chat
// (backend/app/draft_export.py, backend/app/chat_export.py), and where an answer and a chat download from.

import type { TextFormat } from './api.ts'

/** The files a text downloads as, in the order the menus offer them, what each is for, and its words in the audit log. */
export const TEXT_FILES: { format: TextFormat; label: string; words: string }[] = [
  { format: 'docx', label: 'Word document, to edit', words: 'a Word document' },
  { format: 'pdf', label: 'PDF, to send as it is', words: 'a PDF' },
  { format: 'md', label: 'Markdown, as it is written', words: 'Markdown' },
]

/** Where an answer downloads as a file, headed by its question, its citations numbered notes naming the passages. */
export function answerFileUrl(chatId: number, messageId: number, format: TextFormat): string {
  return `/api/chats/${chatId}/messages/${messageId}/export?format=${format}`
}

/** Where a whole chat downloads as one file, each question over its answer, the citations numbered across it. */
export function chatFileUrl(chatId: number, format: TextFormat): string {
  return `/api/chats/${chatId}/export?format=${format}`
}
