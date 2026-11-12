import { describe, expect, it } from 'vitest'
import type { Me, OrganizationShareLink, ShareLink, SharedDocument } from './api.ts'
import { formatDate } from './format.ts'
import {
  SHARE_DAY_CHOICES,
  describeLink,
  linkAbout,
  linkKey,
  linksHeld,
  mayRevoke,
  offersLinks,
  shareLinksKey,
  sharedWords,
  tokenOf,
  viewLeft,
} from './shareLinks.ts'

const link: ShareLink = {
  id: 4,
  kind: 'document',
  document_id: 9,
  message_id: null,
  user_id: 7,
  made_by: 'Ada',
  created_at: '2026-10-25T09:00:00Z',
  expires_at: '2026-11-01T09:00:00Z',
  password: false,
  views: 0,
  last_viewed_at: null,
}

function member(role: Me['role'], organization: Partial<Me['organization']> = {}): Me {
  return {
    id: 8,
    role,
    organization: { flags_on: ['share_links'], share_links: true, ...organization },
  } as Me
}

describe('share links', () => {
  it('offers the days a link may run, a week first', () => {
    expect(SHARE_DAY_CHOICES).toEqual([
      { value: 7, label: '7 days' },
      { value: 1, label: '1 day' },
      { value: 30, label: '30 days' },
    ])
  })

  it('says who made a link, until when it runs, its password and how often it was opened', () => {
    expect(describeLink(link)).toBe(`Made by Ada · runs until ${formatDate(link.expires_at)} · not opened yet`)
    const opened = { ...link, password: true, views: 1, last_viewed_at: '2026-10-26T10:00:00Z' }
    expect(describeLink(opened)).toBe(
      `Made by Ada · runs until ${formatDate(link.expires_at)} · with a password · opened once, last ${formatDate(opened.last_viewed_at)}`,
    )
    expect(describeLink({ ...opened, views: 3 })).toContain('opened 3 times')
  })

  it('offers links to a role that may change the project, while they are rolled out', () => {
    expect(offersLinks(member('member'))).toBe(true)
    expect(offersLinks(member('viewer'))).toBe(false)
    expect(offersLinks(member('owner', { flags_on: [] }))).toBe(false)
  })

  it('says why no link can be made: the organisation turned them off', () => {
    expect(linksHeld(member('member'))).toBeNull()
    expect(linksHeld(member('member', { share_links: false }))).toBe('off')
  })

  it('lets its maker, and the owners and admins, revoke a link', () => {
    expect(mayRevoke(link, { ...member('member'), id: 7 })).toBe(true)
    expect(mayRevoke(link, member('member'))).toBe(false)
    expect(mayRevoke(link, member('admin'))).toBe(true)
  })

  it("names what a link of the organisation's shows, an answer by no words of its chat", () => {
    const listed: OrganizationShareLink = { ...link, filename: 'msa.pdf', project_id: 2, project_name: 'Contracts' }
    expect(linkAbout(listed)).toBe('msa.pdf in Contracts')
    expect(linkAbout({ ...listed, kind: 'answer', document_id: null, message_id: 31, filename: null })).toBe(
      'An answer of a chat in Contracts',
    )
  })

  it('keeps a document’s links and an answer’s apart', () => {
    expect(linkKey(link)).toEqual(shareLinksKey('document', 9))
    expect(linkKey({ ...link, kind: 'answer', document_id: null, message_id: 9 })).toEqual(['share-links', 'answer', 9])
  })

  it('finds the token in the fragment of a link', () => {
    expect(tokenOf('#token=Ab3d-x_9')).toBe('Ab3d-x_9')
    expect(tokenOf('')).toBe('')
    expect(tokenOf('#other=1')).toBe('')
  })

  it('tells the visitor who shared the document, and when the view ends', () => {
    const shared = {
      shared_by: 'Ada Lovelace',
      organization: 'Acme',
      expires_at: '2026-11-01T09:00:00Z',
      view_ends_at: '2026-10-25T10:00:00Z',
    } as SharedDocument
    expect(sharedWords(shared)).toBe(`Shared by Ada Lovelace of Acme, until ${formatDate(shared.expires_at)}.`)
    const now = new Date('2026-10-25T09:30:00Z').getTime()
    expect(viewLeft(shared, now)).toBe(30 * 60 * 1000)
    expect(viewLeft(shared, now + 3_600_000)).toBe(0)
  })
})
