import { describe, expect, it } from 'vitest'
import type { Member, Role } from './api.ts'
import { accessRows, chosenIds } from './projectAccess.ts'

const member = (user_id: number, role: Role): Member => ({
  user_id,
  name: `User ${user_id}`,
  email: `user${user_id}@example.com`,
  role,
  joined_at: '2026-10-20T12:00:00Z',
})

const members = [member(1, 'owner'), member(2, 'admin'), member(3, 'member'), member(4, 'viewer')]

describe('the access form', () => {
  it('offers no choice for the owners and admins, who see every project', () => {
    const rows = accessRows(members, new Set([4]), { id: 1, role: 'owner' })
    expect(rows.map((row) => [row.member.user_id, row.always, row.chosen, row.held])).toEqual([
      [1, true, false, false],
      [2, true, false, false],
      [3, false, false, false],
      [4, false, true, false],
    ])
  })

  it('holds a member who asks among the chosen, and saves them so', () => {
    const me = { id: 3, role: 'member' as const }
    const own = accessRows(members, new Set(), me).find((row) => row.member.user_id === 3)
    expect(own).toMatchObject({ chosen: true, held: true })
    expect(chosenIds(new Set([4]), me)).toEqual([3, 4])
  })

  it('leaves an admin who asks free, and keeps those chosen before whatever their role now', () => {
    const me = { id: 2, role: 'admin' as const }
    expect(accessRows(members, new Set(), me)[1].held).toBe(false)
    expect(chosenIds(new Set([4, 2]), me)).toEqual([2, 4])
    expect(chosenIds(new Set(), me)).toEqual([])
  })
})
