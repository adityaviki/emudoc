// Who the form for a project's access lists, and what it saves (backend/app/access.py): the
// organisation's members, the owners and admins apart, since they see every project whoever is
// chosen, and the asker who is neither held among a private project's members, since the
// server refuses a choice that would leave them out of it.

import { seesEveryProject } from './api.ts'
import type { Member, Role } from './api.ts'

export interface AccessRow {
  member: Member
  /** An owner or admin, who sees the project whoever is chosen, so is offered no choice. */
  always: boolean
  /** Chosen to see the project while it is private. */
  chosen: boolean
  /** The asker, who is no owner or admin and so stays among its members. */
  held: boolean
}

/** The members in the order given, each with what the form shows of them. */
export function accessRows(members: Member[], chosen: ReadonlySet<number>, me: { id: number; role: Role }): AccessRow[] {
  return members.map((member) => {
    const held = member.user_id === me.id && !seesEveryProject(me.role)
    return { member, always: seesEveryProject(member.role), chosen: held || chosen.has(member.user_id), held }
  })
}

/**
 * The ids a private project is saved with: those chosen, and the asker when they would lose it
 * otherwise. Owners and admins chosen before stay chosen, though the form offers them no box,
 * so that one made a member later keeps the project.
 */
export function chosenIds(chosen: ReadonlySet<number>, me: { id: number; role: Role }): number[] {
  const ids = new Set(chosen)
  if (!seesEveryProject(me.role)) ids.add(me.id)
  return [...ids].sort((a, b) => a - b)
}
