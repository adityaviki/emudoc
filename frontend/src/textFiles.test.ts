import { describe, expect, it } from 'vitest'
import { TEXT_FILES, answerFileUrl, chatFileUrl } from './textFiles.ts'

describe('text files', () => {
  it('offers a Word document first, then a PDF and the Markdown', () => {
    expect(TEXT_FILES.map((file) => file.format)).toEqual(['docx', 'pdf', 'md'])
  })

  it('downloads an answer and a whole chat from their own addresses', () => {
    expect(answerFileUrl(3, 41, 'md')).toBe('/api/chats/3/messages/41/export?format=md')
    expect(chatFileUrl(3, 'pdf')).toBe('/api/chats/3/export?format=pdf')
  })
})
