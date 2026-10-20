// The library's last change, which it offers to undo at once: what the change says, and what
// puts it back. A deletion is undone from the project's trash (backend/app/trash.py), a move by
// moving each item back into the folder it was in, a rename by the name it had. Each is worked
// out from what the library held just before the change, so that nothing is asked for.

import type { Document, Folder } from '../api.ts'
import { movedToTrash } from '../trash.ts'

export interface MoveItems {
  folderIds: number[]
  documentIds: number[]
  targetId: number | null
}

export interface TrashItems {
  folderIds: number[]
  documentIds: number[]
  /** The documents inside the folders, as far as the library holds them, whose tabs close. */
  inFolders: number[]
  /** What the items are called, for saying where they went. */
  names: string[]
}

export type Change =
  | { kind: 'trash'; items: TrashItems }
  /** `back` is one move per folder the items came from. */
  | { kind: 'move'; back: MoveItems[]; names: string[]; to: string }
  | { kind: 'rename'; of: 'folder' | 'document'; id: number; from: string; to: string }

/**
 * A move as it can be undone, from the folders and the documents as they were before it, and
 * the project's name for its top. Null when an item or the target is not among them: an undo
 * that put back only some of the items would leave the others where the member did not look.
 */
export function moved(move: MoveItems, folders: Folder[], documents: Document[], top: string): Change | null {
  const to = move.targetId == null ? top : folders.find((folder) => folder.id === move.targetId)?.name
  if (to == null) return null
  const back = new Map<number | null, MoveItems>()
  const from = (targetId: number | null) => {
    const group = back.get(targetId) ?? { folderIds: [], documentIds: [], targetId }
    back.set(targetId, group)
    return group
  }
  const names: string[] = []
  for (const id of move.folderIds) {
    const folder = folders.find((each) => each.id === id)
    if (!folder) return null
    from(folder.parent_id).folderIds.push(id)
    names.push(folder.name)
  }
  for (const id of move.documentIds) {
    const document = documents.find((each) => each.id === id)
    if (!document) return null
    from(document.folder_id).documentIds.push(id)
    names.push(document.filename)
  }
  return { kind: 'move', back: [...back.values()], names, to }
}

/** A rename as it can be undone; null when the server kept the name it had (spaces aside, say). */
export function renamed(of: 'folder' | 'document', id: number, from: string | undefined, to: string): Change | null {
  return from == null || from === to ? null : { kind: 'rename', of, id, from, to }
}

/** What the library says of the change, beside the way to undo it. */
export function describeChange(change: Change): string {
  switch (change.kind) {
    case 'trash':
      return movedToTrash(change.items.names)
    case 'move':
      return change.names.length === 1
        ? `“${change.names[0]}” is now in “${change.to}”.`
        : `${change.names.length} items are now in “${change.to}”.`
    case 'rename':
      return `“${change.from}” is now called “${change.to}”.`
  }
}
