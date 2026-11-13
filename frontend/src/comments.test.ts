import { describe, expect, it } from 'vitest'
import type { ActivityEvent, Thread } from './api.ts'
import {
  QUOTE_LENGTH,
  activityWords,
  grouped,
  matching,
  mentionAt,
  pieces,
  quoteWhere,
  quoted,
  stored,
  threadCounts,
  typed,
  uploadsWords,
  withMention,
} from './comments.ts'

const ann = { id: 1, name: 'Ann' }
const annLee = { id: 2, name: 'Ann Lee' }
const bob = { id: 3, name: 'Bob' }

describe('a comment and its mentions', () => {
  it('reads the members a comment mentions as their names, someone for an account gone', () => {
    expect(pieces('<@3> look at <@9>, please', [{ id: 3, name: 'Bob' }, { id: 9, name: null }])).toEqual([
      { mention: 3, name: 'Bob' },
      { text: ' look at ' },
      { mention: 9, name: null },
      { text: ', please' },
    ])
    expect(pieces('No one.', [])).toEqual([{ text: 'No one.' }])
  })

  it('writes the token of each name chosen from the list, the longer names first, and nothing for one typed by hand', () => {
    expect(stored('@Ann Lee and @Ann, ask @Bob', [ann, annLee])).toBe('<@2> and <@1>, ask @Bob')
    expect(stored('@Annabel is not Ann', [ann])).toBe('@Annabel is not Ann')
    expect(stored('Email @Ann.', [ann])).toBe('Email <@1>.')
  })

  it('shows a stored comment to be changed with its names, and the members it mentions chosen', () => {
    const shown = typed('<@1> and <@2>, again <@1>; <@9>', [
      { id: 1, name: 'Ann' },
      { id: 2, name: 'Ann Lee' },
      { id: 9, name: null },
    ])
    expect(shown.text).toBe('@Ann and @Ann Lee, again @Ann; <@9>')
    expect(shown.chosen).toEqual([ann, annLee])
    expect(stored(shown.text, shown.chosen)).toBe('<@1> and <@2>, again <@1>; <@9>')
  })

  it('finds the "@" the caret is after, and the names that start with what follows it', () => {
    expect(mentionAt('Ask @an', 7)).toEqual({ start: 4, query: 'an' })
    expect(mentionAt('@', 1)).toEqual({ start: 0, query: '' })
    expect(mentionAt('Ask @Ann Le', 11)).toEqual({ start: 4, query: 'Ann Le' })
    expect(mentionAt('mail@example', 12)).toBeNull()
    expect(mentionAt('Ask @an\nnext', 12)).toBeNull()
    expect(matching([ann, annLee, bob], 'an').map((member) => member.name)).toEqual(['Ann', 'Ann Lee'])
    expect(matching([ann, annLee, bob], 'lee').map((member) => member.name)).toEqual(['Ann Lee'])
    expect(matching([ann, annLee, bob], '')).toHaveLength(3)
    expect(withMention('Ask @an now', 4, 7, annLee)).toEqual({ text: 'Ask @Ann Lee  now', caret: 13 })
  })

  it('quotes the words chosen on one line, cut short at a word', () => {
    expect(quoted('  three\n months’ notice ')).toBe('three months’ notice')
    const long = quoted('word '.repeat(QUOTE_LENGTH))
    expect(long.length).toBeLessThanOrEqual(QUOTE_LENGTH)
    expect(long.endsWith('word…')).toBe(true)
  })
})

describe('a thread', () => {
  const thread = (fields: Partial<Thread>): Thread => ({
    id: 1,
    document_id: 4,
    passage_id: 8,
    quote: 'The fee',
    page: 2,
    version: 1,
    created_at: '2026-10-31T09:00:00Z',
    resolved_at: null,
    resolver: null,
    comments: [],
    ...fields,
  })

  it('says where its quote was, and in which version once the document has another', () => {
    expect(quoteWhere(thread({}), 1)).toBe('page 2')
    expect(quoteWhere(thread({ passage_id: null }), 2)).toBe('page 2, version 1')
    expect(quoteWhere(thread({ page: null, passage_id: null }), 3)).toBe('version 1')
    expect(quoteWhere(thread({ page: null }), 1)).toBeNull()
  })

  it('counts the open and the resolved', () => {
    expect(threadCounts([thread({}), thread({ resolved_at: '2026-10-31T10:00:00Z' }), thread({})])).toEqual({ open: 2, resolved: 1 })
  })
})

describe('the activity', () => {
  const event = (fields: Partial<ActivityEvent>): ActivityEvent => ({
    id: 1,
    action: 'document_uploaded',
    occurred_at: '2026-10-31T09:00:00Z',
    actor: 'Ann',
    automation: null,
    via: null,
    filename: 'inv-1.pdf',
    document_id: 4,
    folder: null,
    documents: null,
    thread_id: null,
    reply: false,
    review: null,
    assignee: null,
    names: [],
    name: null,
    questions: null,
    target_id: null,
    ...fields,
  })
  const sentence = (fields: Partial<ActivityEvent>) => {
    const words = activityWords(event(fields))
    return `${words.who ?? ''}${words.before}${words.target ?? ''}${words.after}`
  }

  it('words each thing done by who did it and what it was about, never what a document or a comment says', () => {
    expect(sentence({})).toBe('Ann uploaded inv-1.pdf')
    expect(sentence({ actor: null, via: 'email' })).toBe('inv-1.pdf arrived by email')
    expect(sentence({ via: 'google' })).toBe('inv-1.pdf arrived from Google Drive')
    expect(sentence({ action: 'document_version_uploaded' })).toBe('Ann gave a new version of inv-1.pdf')
    expect(sentence({ action: 'document_deleted', actor: null })).toBe('A former member deleted inv-1.pdf')
    expect(sentence({ action: 'folder_restored', filename: null, folder: 'March', documents: 3 })).toBe(
      'Ann restored the folder March with 3 documents',
    )
    expect(sentence({ action: 'document_review_changed', review: 'disputed' })).toBe('Ann disputed inv-1.pdf')
    expect(sentence({ action: 'document_review_changed', review: 'to_review', actor: null, automation: 'New invoices' })).toBe(
      'The automation “New invoices” put inv-1.pdf in review',
    )
    expect(sentence({ action: 'document_review_changed', review: null })).toBe('Ann took inv-1.pdf out of review')
    expect(sentence({ action: 'document_assigned', assignee: 'Bob' })).toBe('Ann assigned inv-1.pdf to Bob')
    expect(sentence({ action: 'document_assigned' })).toBe('Ann left inv-1.pdf assigned to nobody')
    expect(sentence({ action: 'comment_added', thread_id: 5 })).toBe('Ann commented on inv-1.pdf')
    expect(sentence({ action: 'comment_added', reply: true })).toBe('Ann replied on inv-1.pdf')
    expect(sentence({ action: 'thread_resolved' })).toBe('Ann resolved a thread on inv-1.pdf')
    expect(sentence({ action: 'cross_checks_run', filename: null, names: ['Totals', 'Dates'] })).toBe('Ann ran the cross-checks Totals, Dates')
    expect(sentence({ action: 'question_set_run', filename: null, name: 'Daily', questions: 2 })).toBe(
      'Ann ran the question set Daily, 2 questions',
    )
    expect(sentence({ action: 'chat_shared', filename: null })).toBe('Ann shared a chat with the project')
  })

  it('gathers a run of uploads by one member, or one way in, within ten minutes', () => {
    const items = grouped([
      event({ id: 6, action: 'comment_added' }),
      event({ id: 5, occurred_at: '2026-10-31T09:09:00Z' }),
      event({ id: 4, occurred_at: '2026-10-31T09:05:00Z' }),
      event({ id: 3, occurred_at: '2026-10-31T08:56:00Z' }),
      event({ id: 2, occurred_at: '2026-10-31T08:40:00Z' }),
      event({ id: 1, occurred_at: '2026-10-31T08:39:00Z', actor: 'Bob' }),
    ])
    expect(items.map((item) => ('uploads' in item ? item.uploads.map((e) => e.id) : item.event.id))).toEqual([6, [5, 4, 3], 2, 1])
    const [, run] = items
    expect('uploads' in run && uploadsWords(run.uploads)).toBe('Ann uploaded 3 documents')
    expect(uploadsWords([event({ via: 'email' }), event({ via: 'email' })])).toBe('2 documents arrived by email')
  })
})
