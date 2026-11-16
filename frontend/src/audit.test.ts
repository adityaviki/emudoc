import { describe, expect, it } from 'vitest'
import { auditExportUrl, auditQuery } from './api.ts'
import type { AuditAction, AuditEvent } from './api.ts'
import { ACTIONS, KINDS, NO_CHOICES, actionLabel, actionsOf, describeDetail, filterOf, whoDid } from './audit.ts'
import { formatDate, formatDateTime } from './format.ts'
import type { AuditKind } from './audit.ts'

const event = (action: AuditAction, detail: AuditEvent['detail'] = {}, fields: Partial<AuditEvent> = {}): AuditEvent => ({
  id: 1,
  occurred_at: '2026-10-21T09:30:00Z',
  action,
  user_id: 7,
  user_name: 'Ada',
  user_email: 'ada@example.com',
  ip: '203.0.113.7',
  project_id: null,
  project_name: null,
  target_type: null,
  target_id: null,
  detail,
  ...fields,
})

describe('the actions', () => {
  it('files every action under one of the kinds the filter offers', () => {
    const kinds = Object.keys(KINDS) as AuditKind[]
    const filed = kinds.flatMap(actionsOf)
    expect(filed.toSorted()).toEqual(Object.keys(ACTIONS).toSorted())
    expect(new Set(filed).size).toBe(filed.length)
    for (const kind of kinds) expect(actionsOf(kind).length).toBeGreaterThan(0)
  })

  it('names an action a page loaded before a release does not know by its name', () => {
    expect(actionLabel('project_exported')).toBe('Exported a project')
    expect(actionLabel('report_scheduled' as AuditAction)).toBe('report scheduled')
  })
})

describe('the filter', () => {
  it('asks for nothing until something is chosen', () => {
    expect(filterOf(NO_CHOICES)).toEqual({ actions: [], userId: null, projectId: null, since: null, until: null })
    expect(auditQuery(filterOf(NO_CHOICES)).toString()).toBe('')
    expect(auditExportUrl(filterOf(NO_CHOICES))).toBe('/api/audit/export')
  })

  it('asks for a kind as its actions, and one action alone', () => {
    expect(filterOf({ ...NO_CHOICES, what: 'kind:chats' }).actions).toEqual([
      'chat_shared',
      'chat_unshared',
      'chat_exported',
      'answer_exported',
      'chat_deleted',
      'chat_restored',
      'chat_purged',
    ])
    expect(filterOf({ ...NO_CHOICES, what: 'sign_in' }).actions).toEqual(['sign_in'])
    const query = auditQuery(filterOf({ ...NO_CHOICES, what: 'kind:chats', who: '7', project: '12' }))
    expect(query.getAll('action')).toHaveLength(7)
    expect([query.get('user_id'), query.get('project_id')]).toEqual(['7', '12'])
  })

  it('runs the days from the first midnight until the one after the last, in the browser’s zone', () => {
    const { since, until } = filterOf({ ...NO_CHOICES, from: '2026-10-01', to: '2026-10-03' })
    const first = new Date(since ?? '')
    const after = new Date(until ?? '')
    expect([first.getFullYear(), first.getMonth(), first.getDate(), first.getHours(), first.getMinutes()]).toEqual([2026, 9, 1, 0, 0])
    expect([after.getMonth(), after.getDate(), after.getHours()]).toEqual([9, 4, 0])
    // The last day of a month runs into the next.
    expect(new Date(filterOf({ ...NO_CHOICES, to: '2026-10-31' }).until ?? '').getMonth()).toBe(10)
    expect(auditExportUrl(filterOf({ ...NO_CHOICES, from: '2026-10-01' }))).toMatch(/^\/api\/audit\/export\?since=2026-/)
  })
})

describe('a row', () => {
  it('names who did it, the app, or a member whose account is gone', () => {
    expect(whoDid(event('sign_in'))).toBe('Ada')
    expect(whoDid(event('sign_in', {}, { user_name: '' }))).toBe('ada@example.com')
    expect(whoDid(event('sample_added', {}, { user_id: null, user_name: null, user_email: null }))).toBe('Emudoc')
    expect(whoDid(event('sign_in', {}, { user_name: null, user_email: null }))).toBe('A former member')
    const nobody = { user_id: null, user_name: null, user_email: null }
    expect(whoDid(event('share_link_viewed', {}, nobody))).toBe('Someone with the link')
    expect(whoDid(event('share_link_downloaded', {}, nobody))).toBe('Someone with the link')
  })

  it('words its detail from the names and settings it holds', () => {
    expect(describeDetail(event('document_uploaded', { filename: 'msa.pdf', size_bytes: 1200 }))).toBe('msa.pdf')
    expect(describeDetail(event('project_exported', { name: 'Contracts' }, { project_name: 'Contracts' }))).toBe('')
    expect(describeDetail(event('project_exported', { name: 'Contracts', documents: 3 }, { project_name: 'Contracts' }))).toBe('3 chosen documents')
    const bob = new Map([[9, 'Bob']])
    expect(describeDetail(event('role_changed', { from: 'member', to: 'admin' }, { target_type: 'user', target_id: 9 }), bob)).toBe(
      'Bob · Member → Admin',
    )
    expect(describeDetail(event('ownership_transferred', { from: 'admin' }, { target_type: 'user', target_id: 4 }), bob)).toBe(
      'a former member · was Admin',
    )
    expect(describeDetail(event('invitation_sent', { role: 'viewer', again: true }))).toBe('Viewer')
    const due = '2026-11-04T12:00:00+00:00'
    expect(describeDetail(event('organization_deletion_requested', { due_at: due }))).toBe(`for ${formatDate(due)}`)
    expect(describeDetail(event('plan_changed', { from: 'free', to: 'team' }))).toBe('Free → Team')
    expect(describeDetail(event('plan_changed', { from: 'free', to: 'team', operator: 7 }))).toBe("Free → Team · by Emudoc's operators")
    expect(describeDetail(event('document_requeued', { filename: 'msa.pdf', stage: 'parse', operator: 7 }))).toBe("msa.pdf · by Emudoc's operators")
    // Queued again from the Administration page, the row is the admin's, with no word of operators.
    expect(describeDetail(event('document_requeued', { filename: 'msa.pdf', stage: 'parse' }))).toBe('msa.pdf')
    expect(describeDetail(event('provider_switched', { provider: 'google', off: true }))).toBe('google off')
    expect(describeDetail(event('provider_switched', { provider: 'google', off: false }))).toBe('google on again')
    // A feature flag turned on for the organisation, then back to its rollout, which leaves it off.
    expect(describeDetail(event('flag_changed', { flag: 'extraction_templates', from: null, to: true, on: true, operator: 7 }))).toBe(
      "extraction templates: on · by Emudoc's operators",
    )
    expect(describeDetail(event('flag_changed', { flag: 'extraction_templates', from: true, to: null, on: false }))).toBe(
      'extraction templates: as Emudoc rolls it out, off',
    )
    // A project's extraction template made, changed (its kind too) and deleted, by its fields' names.
    expect(describeDetail(event('template_created', { name: 'Supplier invoice', doc_type: 'invoice', fields: ['invoice_number', 'iban'] }))).toBe(
      'Supplier invoice · for invoice documents, 2 fields',
    )
    const changed = { name: 'Supplier invoice', doc_type: 'bill', added: ['vat_number'], removed: [], changed: ['iban'], from: 'invoice', to: 'bill' }
    expect(describeDetail(event('template_changed', changed))).toBe('Supplier invoice · for invoice → bill documents, added vat_number, changed iban')
    expect(describeDetail(event('template_deleted', { name: 'Supplier invoice', doc_type: 'invoice' }))).toBe('Supplier invoice · for invoice documents')
    // A facts table downloaded: its kind, the file and how it was kept, never a condition's value or the words.
    expect(describeDetail(event('facts_exported', { kind: 'invoice', format: 'csv' }))).toBe('invoice facts as a CSV file')
    const asked = { kind: 'invoice', format: 'xlsx', sort: 'total_amount', order: 'desc', conditions: ['total_amount:min', 'vendor_name:contains', 'total_amount:max'], words: true, unchecked: true }
    expect(describeDetail(event('facts_exported', asked))).toBe(
      'invoice facts as an Excel workbook, sorted by total amount, descending, kept by total amount and vendor name, rows holding the words looked for, those with unchecked facts',
    )
    // A cross-check made or changed: the kinds and fields it compares and what changed, never a value.
    const rule = { name: 'Prices', left: 'invoice.unit_price', right: 'price list.unit_price', comparison: 'equal' }
    expect(describeDetail(event('cross_check_created', rule))).toBe('Prices · invoice unit price against price list unit price')
    expect(describeDetail(event('cross_check_changed', { ...rule, changed: ['title', 'comparison'] }))).toBe(
      'Prices · invoice unit price against price list unit price, changed its name, the comparison',
    )
    // A run of cross-checks: the rules it ran by their names, and whether a report was asked for.
    expect(describeDetail(event('cross_checks_run', { names: ['Prices', 'Totals'], report: true }))).toBe(
      '2 cross-checks: Prices, Totals, with a discrepancy report',
    )
    expect(describeDetail(event('check_report_requested', { names: ['Prices'] }))).toBe('Prices')
    // An automation: what it watches and runs, by names, never a value; and a run it made names it.
    const automation = { name: 'New invoices', kind: 'invoice', folder: 'Inbox', checks: null, report: false, audience: 'maker', recheck: false, enabled: true }
    expect(describeDetail(event('automation_created', automation))).toBe('New invoices · invoice documents in Inbox, running every cross-check')
    const turnedOff = { ...automation, kind: null, folder: null, checks: ['Totals'], enabled: false, changed: ['watches', 'enabled'] }
    expect(describeDetail(event('automation_changed', turnedOff))).toBe(
      'New invoices · any document anywhere, running Totals, off, changed what it watches, on or off',
    )
    // A question set, in a row from before question sets went: how many questions and when it ran, never a question;
    // a run says what ran it.
    const questions = { name: 'Monday checks', questions: 3, schedule: 'on Mondays at 08:00', arrivals: true, audience: 'project', enabled: true }
    expect(describeDetail(event('question_set_created', questions))).toBe('Monday checks · 3 questions, on Mondays at 08:00, as documents arrive')
    expect(describeDetail(event('question_set_changed', { ...questions, schedule: null, enabled: false, changed: ['schedule', 'enabled'] }))).toBe(
      'Monday checks · 3 questions, as documents arrive, off, changed its schedule, on or off',
    )
    expect(describeDetail(event('question_set_run', { ...questions, trigger: 'arrivals', arrived: 2 }))).toBe(
      'Monday checks · 3 questions, after 2 documents arrived',
    )
    expect(describeDetail(event('question_report_deleted', { name: 'Monday checks', questions: 1 }))).toBe('Monday checks · 1 question')
    // A filing rule: what it looks at and does, by the type, the field, the folder and the tag, never a fact's words.
    const filing = { condition: 'type', type: 'invoice', field: null, action: 'by_month', folder: 'Invoices', tag: null, enabled: true }
    expect(describeDetail(event('filing_rule_created', filing))).toBe('invoice documents, by month under Invoices')
    const retagged = { ...filing, condition: 'fact', type: null, field: 'supplier_name', action: 'tag', folder: null, tag: 'Acme', enabled: false, changed: ['condition', 'action'] }
    expect(describeDetail(event('filing_rule_changed', retagged))).toBe(
      'documents by their supplier name, tagged “Acme”, off, changed what it looks at, what it does',
    )
    expect(describeDetail(event('filing_rule_deleted', { ...filing, condition: 'any', type: null, action: 'folder', folder: null }))).toBe(
      'any document, into the top of the library',
    )
    expect(describeDetail(event('cross_checks_run', { names: ['Totals'], report: false, automation: 'New invoices' }))).toBe(
      'Totals, by the automation “New invoices”',
    )
    // A draft, of the rows from before drafts became files of My files: its kind, what it was about counted, and how it changed, never what it says.
    expect(describeDetail(event('draft_created', { kind: 'dispute_letter', documents: 1 }))).toBe('Dispute letter, about 1 document')
    expect(describeDetail(event('draft_created', { kind: 'summary_memo', documents: 0 }))).toBe('Summary memo, about what the model finds')
    expect(describeDetail(event('draft_created', { kind: 'reply_email', documents: 1, chat: 4 }))).toBe('Reply email, about 1 document, from a chat')
    expect(describeDetail(event('draft_changed', { kind: 'report', version: 3 }))).toBe('Report, saved as version 3')
    expect(describeDetail(event('draft_changed', { kind: 'report', rewritten: true }))).toBe('Report, written again by the model')
    expect(describeDetail(event('draft_deleted', { kind: 'reply_email' }))).toBe('Reply email')
    expect(describeDetail(event('draft_exported', { kind: 'dispute_letter', version: 2, format: 'pdf' }))).toBe('Dispute letter, version 2 as a PDF')
    expect(describeDetail(event('draft_exported', { kind: 'report', version: 1, format: 'md' }))).toBe('Report, version 1 as Markdown')
    expect(describeDetail(event('draft_template_created', { kind: 'dispute_letter', sections: ['Date', 'Reference'] }))).toBe(
      'for dispute letters, 2 parts',
    )
    expect(
      describeDetail(event('draft_template_changed', { kind: 'reply_email', added: ['Reference'], removed: [], changed: ['Closing'], tone: true, guidance: false })),
    ).toBe('for reply emails, added Reference, changed Closing, its tone changed')
    expect(describeDetail(event('draft_template_deleted', { kind: 'report' }))).toBe('for reports')
    expect(describeDetail(event('mailbox_created', { senders: 2, folder: 'Inbox' }))).toBe('from 2 senders and its members, into Inbox')
    expect(describeDetail(event('mailbox_created', { senders: 0, folder: null }))).toBe('from its members alone, into the project’s root')
    expect(describeDetail(event('mailbox_changed', { changed: ['address'], senders: 1 }))).toBe('a new address, from 1 sender and its members')
    expect(
      describeDetail(event('mailbox_changed', { changed: ['senders'], senders: 3, added: 2, removed: 1, folder: 'Inbox' })),
    ).toBe('from 3 senders and its members, 2 added, 1 removed')
    expect(describeDetail(event('mailbox_deleted', { senders: 3 }))).toBe('')
    expect(describeDetail(event('document_uploaded', { filename: 'INV-7.pdf', size_bytes: 10, by_email: true }))).toBe('INV-7.pdf · by email')
    // A connected folder, and the documents its syncs bring and put in the trash.
    expect(describeDetail(event('connector_created', { provider: 'google' }))).toBe('Google Drive')
    expect(describeDetail(event('connector_changed', { provider: 'google', changed: ['folder'], folder: 'Invoices', target: null }))).toBe(
      'Google Drive, Invoices, into the project’s root',
    )
    expect(describeDetail(event('connector_changed', { provider: 'google', changed: ['grant'], folder: 'Invoices' }))).toBe(
      'Google Drive, connected again, Invoices',
    )
    expect(describeDetail(event('connector_deleted', { provider: 'dropbox', folder: 'Scans' }))).toBe('Dropbox, Scans')
    expect(describeDetail(event('document_uploaded', { filename: 'INV-7.pdf', size_bytes: 10, by_connector: 'google' }))).toBe(
      'INV-7.pdf · from Google Drive',
    )
    expect(describeDetail(event('document_deleted', { filename: 'INV-7.pdf', by_connector: 'google' }))).toBe('INV-7.pdf · gone from Google Drive')
    expect(describeDetail(event('document_deleted', { filename: 'INV-7.pdf', by_connector: 'google', replaced: true }))).toBe(
      'INV-7.pdf · replaced by a new version from Google Drive',
    )
    // A document's new version, its name before when it took another type's, and an earlier one downloaded.
    expect(describeDetail(event('document_version_uploaded', { filename: 'msa.pdf', size_bytes: 10, version: 2, from: 'msa.docx' }))).toBe(
      'msa.pdf · was msa.docx · version 2',
    )
    expect(describeDetail(event('document_version_uploaded', { filename: 'Notes.md', size_bytes: 10, version: 3, by_connector: 'dropbox' }))).toBe(
      'Notes.md · version 3 · from Dropbox',
    )
    expect(describeDetail(event('document_downloaded', { filename: 'msa.docx', version: 1 }))).toBe('msa.docx · version 1')
    // A fact checked or corrected: the document and the field, never what it says.
    expect(describeDetail(event('fact_corrected', { filename: 'acme-10.pdf', field: 'total_amount' }))).toBe('acme-10.pdf · total amount')
    // A member's leave for support, support's view of their account, and what was done in it.
    const until = '2026-10-28T09:30:00+00:00'
    expect(describeDetail(event('support_access_granted', { days: 7, until }))).toBe(`until ${formatDateTime(until)}`)
    const view = event('support_session_started', { operator: 3, ends_at: until }, { user_id: null, target_type: 'user', target_id: 9 })
    expect(describeDetail(view, bob)).toBe(`Bob · until ${formatDateTime(until)} · by Emudoc's operators`)
    expect(describeDetail(event('document_downloaded', { filename: 'msa.pdf', support: 3 }))).toBe(
      "msa.pdf · by Emudoc's support, viewing their account",
    )
    expect(describeDetail(event('sign_in', { method: 'google' }))).toBe('through Google')
    expect(describeDetail(event('sign_in', { second_factor: 'recovery_code' }))).toBe('with a recovery code')
    expect(describeDetail(event('sign_in_failed', { reason: 'code' }))).toBe('wrong code')
    expect(describeDetail(event('joining_changed', { domains: 'acme.com', role: 'member' }))).toBe('Member · acme.com')
    expect(describeDetail(event('pack_bought', { unit: 'pages', quantity: 1000, month: '2026-10' }))).toBe('1,000 pages')
    expect(describeDetail(event('entitlements_changed', { entitlement: 'pages_per_month', from: 200, to: null, agreed: true }))).toBe(
      'pages per month · 200 → none',
    )
    expect(describeDetail(event('sign_out'))).toBe('')
    // A project's name is said once, as the row's project.
    expect(describeDetail(event('sample_added', { name: 'Sample', documents: 8 }, { project_name: 'Sample' }))).toBe('')
    expect(describeDetail(event('project_deleted', { name: 'Old', documents: 3 }))).toBe('Old')
  })

  it('words an API key, and names the key a member acted through', () => {
    expect(describeDetail(event('api_key_created', { name: 'Sync', scope: 'write', days: 90 }))).toBe(
      'Sync · reads and writes · for 90 days',
    )
    expect(describeDetail(event('api_key_created', { name: 'Sync', scope: 'read', days: null }))).toBe(
      'Sync · reads only · until revoked',
    )
    expect(describeDetail(event('api_key_rolled', { name: 'Sync' }))).toBe('Sync')
    const through = { filename: 'msa.pdf', api_key: { id: 3, name: 'Sync' } }
    expect(describeDetail(event('document_uploaded', through))).toBe('msa.pdf · with the API key Sync')
  })

  it('words an answer or a chat downloaded by what the file held, never its words', () => {
    expect(actionLabel('answer_exported')).toBe('Downloaded an answer')
    expect(describeDetail(event('answer_exported', { answer: 31, format: 'pdf' }))).toBe('as a PDF')
    expect(describeDetail(event('chat_exported', { answers: 1, format: 'md' }))).toBe('1 answer, as Markdown')
    expect(describeDetail(event('chat_exported', { answers: 4, format: 'docx' }))).toBe('4 answers, as a Word document')
  })

  it('words a file of My files by its kind and version, and says when the agent wrote it', () => {
    expect(describeDetail(event('my_file_added', { kind: 'docx', size_bytes: 9 }))).toBe('a Word document')
    expect(describeDetail(event('my_file_added', { kind: 'docx', by_model: true, chat: 4 }))).toBe('a Word document, by the agent in a chat')
    expect(describeDetail(event('my_file_added', { kind: 'docx', by_model: true, writing: 'dispute_letter' }))).toBe(
      'a Word document, by the agent: a dispute letter',
    )
    expect(describeDetail(event('my_file_version_added', { kind: 'docx', version: 3, by_model: true }))).toBe(
      'a Word document, version 3, by the agent in a chat',
    )
  })

  it('words a share link by its document, its days and its password', () => {
    const made = { filename: 'msa.pdf', link: 4, days: 1, password: true }
    expect(describeDetail(event('share_link_created', made))).toBe('msa.pdf · for 1 day · with a password')
    expect(describeDetail(event('share_link_created', { ...made, days: 7, password: false }))).toBe('msa.pdf · for 7 days')
    expect(describeDetail(event('share_link_viewed', { filename: 'msa.pdf', link: 4 }))).toBe('msa.pdf')
    expect(describeDetail(event('share_link_created', { answer: 31, link: 5, days: 30, password: false }))).toBe(
      'an answer · for 30 days',
    )
    expect(describeDetail(event('share_link_revoked', { answer: 31, link: 5 }))).toBe('an answer')
    expect(describeDetail(event('settings_changed', { setting: 'share_links', from: true, to: false }))).toBe(
      'Share links turned off',
    )
  })

  it("words a webhook by its address's server alone, and what changed", () => {
    const added = { host: 'hooks.example.com', events: ['document.ready', 'document.failed'] }
    expect(describeDetail(event('webhook_created', added))).toBe('hooks.example.com, document.ready, document.failed')
    const changed = { host: 'hooks.example.com', to_host: 'n8n.example.com', from: ['document.ready'], to: ['check_run.completed'] }
    expect(describeDetail(event('webhook_changed', changed))).toBe(
      'hooks.example.com, moved to n8n.example.com, document.ready → check_run.completed',
    )
    expect(describeDetail(event('webhook_changed', { host: 'hooks.example.com', enabled: false }))).toBe('hooks.example.com, paused')
    expect(describeDetail(event('webhook_rolled', { host: 'hooks.example.com' }))).toBe('hooks.example.com')
    expect(actionLabel('webhook_rolled')).toBe("Rolled a webhook's secret")
  })

  it("words a document's review by its states and whom it is assigned to, by the members' names", () => {
    const names = new Map([[9, 'Bob']])
    const moved = (from: string | null, to: string | null) =>
      describeDetail(event('document_review_changed', { filename: 'inv-1.pdf', from, to }), names)
    expect(moved(null, 'to_review')).toBe('inv-1.pdf · not in review → To review')
    expect(moved('to_review', 'approved')).toBe('inv-1.pdf · To review → Approved')
    expect(moved('disputed', null)).toBe('inv-1.pdf · Disputed → not in review')
    const assigned = (from: number | null, to: number | null) =>
      describeDetail(event('document_assigned', { filename: 'inv-1.pdf', from, to }), names)
    expect(assigned(null, 9)).toBe('inv-1.pdf · to Bob')
    expect(assigned(9, 12)).toBe('inv-1.pdf · to a former member')
    expect(assigned(9, null)).toBe('inv-1.pdf · assigned to nobody, was Bob')
    const automatic = { filename: 'inv-2.pdf', from: null, to: 'to_review', automation: 'New invoices' }
    expect(describeDetail(event('document_review_changed', automatic, { user_id: null }))).toBe(
      'inv-2.pdf · not in review → To review · by the automation “New invoices”',
    )
  })

  it("words a comment, from before comments went, by its document, never its words, and whose an owner or admin deleted", () => {
    const names = new Map([[9, 'Bob']])
    expect(describeDetail(event('comment_added', { filename: 'terms.txt', thread: 3, reply: false }))).toBe('terms.txt')
    expect(describeDetail(event('comment_added', { filename: 'terms.txt', thread: 3, reply: true }))).toBe('terms.txt · a reply')
    const deleted = (author: number | null) => describeDetail(event('comment_deleted', { filename: 'terms.txt', thread: 3, author }), names)
    expect(deleted(7)).toBe('terms.txt')
    expect(deleted(9)).toBe('terms.txt · Bob’s comment')
    expect(deleted(12)).toBe('terms.txt · a former member’s comment')
    expect(deleted(null)).toBe('terms.txt')
    expect(describeDetail(event('thread_resolved', { filename: 'terms.txt', thread: 3 }))).toBe('terms.txt')
    expect(actionLabel('thread_reopened')).toBe('Opened a thread of comments again')
  })

  it('words a setting by what it is', () => {
    const setting = (detail: AuditEvent['detail']) => describeDetail(event('settings_changed', detail))
    expect(setting({ setting: 'language', from: null, to: 'german' })).toBe('Language: the default → German')
    expect(setting({ setting: 'chat_days', from: 30, to: 7 })).toBe('Chats kept: 30 days after the last question → 7 days after the last question')
    expect(setting({ setting: 'session_days', from: null, to: 1 })).toBe('Sign in again: every day')
    expect(setting({ setting: 'session_days', from: 7, to: null })).toBe('Sign in again: as Emudoc asks')
    expect(setting({ setting: 'mfa_required', from: false, to: true })).toBe('Two-factor sign-in required')
    expect(setting({ setting: 'mfa_required', from: true, to: false })).toBe('Two-factor sign-in no longer required')
    expect(setting({ setting: 'audit_days', from: 365, to: null })).toBe('Audit log kept: 365 days → until deleted')
    expect(setting({ setting: 'notices', kind: 'checks_run', way: 'email', from: true, to: false })).toBe(
      'An automation ran the cross-checks, by email: off',
    )
    // A kind no longer told, chosen before comments went.
    expect(setting({ setting: 'notices', kind: 'mentioned', way: 'app', from: true, to: false })).toBe(
      'You are mentioned in a comment, in the app: off',
    )
  })
})
