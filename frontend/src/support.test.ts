import { describe, expect, it } from 'vitest'
import type { Me } from './api.ts'
import { formatDateTime } from './format.ts'
import { leaveWords, viewWords } from './support.ts'

const ACME = { name: 'Acme' } as Me['organization']
const ENDS = '2026-10-21T15:05:00Z'

describe("support's access", () => {
  it('says whether support may see the account here, and until when', () => {
    expect(leaveWords({ organization: ACME, support_until: null })).toBe("Emudoc's support cannot see your account in Acme.")
    expect(leaveWords({ organization: ACME, support_until: ENDS })).toBe(
      `Emudoc's support may see your account in Acme until ${formatDateTime(ENDS)}, without changing anything.`,
    )
  })

  it("names the member whose account a view shows, by name or else address, and none for the member's own", () => {
    const member = { name: 'Bob', email: 'bob@example.com', organization: ACME, support_view: null }
    expect(viewWords(member)).toBeNull()
    const viewed = { ...member, support_view: { ends_at: ENDS } }
    expect(viewWords(viewed)).toMatch(/^You are viewing Bob's account in Acme as Emudoc's support, read only, until .+\.$/)
    expect(viewWords({ ...viewed, name: '' })).toMatch(/^You are viewing bob@example\.com's account/)
  })
})
