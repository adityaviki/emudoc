// Every change the library makes. Renames and moves show at once and are undone if the server
// refuses them; the lists are fetched again afterwards either way. A refusal by the plan (a full
// plan, or an organisation past it and so read-only) is shown as the way past it, and a deletion
// asks again what the organisation holds past its plan, since deleting is how it gets within it.
// A deletion puts things in the project's trash (backend/app/trash.py). The library offers to
// take back the last deletion, move or rename at once (changes.ts) through the mutation that made
// it, so that an undo shows at once too and is put back if the server refuses it.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, isPlanLimit } from '../api.ts'
import type { Document, Folder, PlanRefusal, UploadResult } from '../api.ts'
import { ME_KEY } from '../auth.ts'
import { trashKey } from '../trash.ts'
import { moved, renamed } from './changes.ts'
import type { Change, MoveItems, TrashItems } from './changes.ts'
import { documentKey, editListings, findListed, listed, listingsKey, restore, snapshotListings } from './listing.ts'
import { useWorkspace } from './state.ts'

/** An undo puts things back and offers no undo of its own. */
interface Undoing {
  undoing?: boolean
}

export function useLibraryMutations() {
  const queryClient = useQueryClient()
  const workspace = useWorkspace()
  const projectId = workspace.project.id
  const [notice, setNotice] = useState<string | null>(null)
  // A change the organisation's plan refused: shown as the way past its limit.
  const [planLimit, setPlanLimit] = useState<PlanRefusal | null>(null)
  const [rejected, setRejected] = useState<UploadResult['rejected']>([])
  // The last deletion, move or rename, which the library offers to undo until the next one or a
  // dismissal.
  const [last, setLast] = useState<Change | null>(null)

  // Every folder's listing: a document may be on a page of any of them.
  const documentsKey = [...listingsKey(projectId)]
  // Every open document; a deleted folder's are found gone by asking.
  const openKey = ['document']
  const foldersKey = ['folders', projectId]
  const projectKey = ['project', projectId]
  const fail = (error: Error) =>
    isPlanLimit(error) ? setPlanLimit({ message: error.message, limit: error.limit }) : setNotice(error.message)
  const invalidate = (...keys: unknown[][]) => {
    for (const queryKey of keys) queryClient.invalidateQueries({ queryKey })
  }
  async function snapshot<T>(queryKey: unknown[]): Promise<T | undefined> {
    await queryClient.cancelQueries({ queryKey })
    return queryClient.getQueryData<T>(queryKey)
  }
  // A change begun takes the offer to undo the one before; an undo keeps it until it is done.
  const begin = ({ undoing }: Undoing) => {
    if (!undoing) setLast(null)
  }
  const offer = (change: Change | null, { undoing }: Undoing) => {
    if (!undoing) setLast(change)
  }
  const editFolders = (change: (folders: Folder[]) => Folder[]) =>
    queryClient.setQueryData<Folder[]>(foldersKey, (folders) => folders && change(folders))
  /** Change the documents wherever they are held: the listings, and each one open by itself. */
  const editDocuments = (ids: number[], change: (document: Document) => Document | null) => {
    editListings(queryClient, projectId, (documents) =>
      documents.flatMap((document) => (ids.includes(document.id) ? (change(document) ?? []) : [document])),
    )
    for (const id of ids) {
      queryClient.setQueryData<Document>(documentKey(id), (document) => (document && change(document)) ?? document)
    }
  }

  const createFolder = useMutation({
    mutationFn: ({ name, parentId }: { name: string; parentId: number | null }) =>
      api.createFolder(projectId, { name, parent_id: parentId }),
    onSuccess: (folder) => editFolders((folders) => [...folders, folder]),
    onError: fail,
    onSettled: () => invalidate(foldersKey),
  })

  const renameFolder = useMutation({
    mutationFn: ({ id, name }: { id: number; name: string } & Undoing) => api.updateFolder(id, { name }),
    onMutate: async (variables) => {
      const { id, name } = variables
      begin(variables)
      const previous = await snapshot<Folder[]>(foldersKey)
      editFolders((folders) => folders.map((folder) => (folder.id === id ? { ...folder, name } : folder)))
      return { previous, from: previous?.find((folder) => folder.id === id)?.name }
    },
    // The name as the server keeps it, its spaces tidied.
    onSuccess: (folder, variables, context) => offer(renamed('folder', folder.id, context.from, folder.name), variables),
    onError: (error, _variables, context) => {
      queryClient.setQueryData(foldersKey, context?.previous)
      fail(error)
    },
    onSettled: () => invalidate(foldersKey),
  })

  const renameDocument = useMutation({
    mutationFn: ({ id, filename }: { id: number; filename: string } & Undoing) => api.updateDocument(id, { filename }),
    onMutate: async (variables) => {
      const { id, filename } = variables
      begin(variables)
      const previous = await snapshotListings(queryClient, projectId)
      const open = await snapshot<Document>([...documentKey(id)])
      const from = (findListed(queryClient, projectId, id) ?? open)?.filename
      editDocuments([id], (document) => ({ ...document, filename }))
      return { previous, open, from }
    },
    // The name as the server keeps it, with the extension it had.
    onSuccess: (document, variables, context) =>
      offer(renamed('document', document.id, context.from, document.filename), variables),
    onError: (error, { id }, context) => {
      restore(queryClient, context?.previous)
      queryClient.setQueryData(documentKey(id), context?.open)
      fail(error)
    },
    onSettled: (_data, _error, { id }) => invalidate(documentsKey, [...documentKey(id)]),
  })

  const moveItems = useMutation({
    mutationFn: ({ folderIds, documentIds, targetId }: MoveItems & Undoing) =>
      Promise.all([
        ...folderIds.map((id) => api.updateFolder(id, { parent_id: targetId })),
        ...documentIds.map((id) => api.updateDocument(id, { folder_id: targetId })),
      ]),
    onMutate: async (variables) => {
      const { folderIds, documentIds, targetId } = variables
      begin(variables)
      const previous = {
        folders: await snapshot<Folder[]>(foldersKey),
        documents: await snapshotListings(queryClient, projectId),
      }
      const held = previous.documents.flatMap(([, listing]) => listed(listing))
      const change = moved(variables, previous.folders ?? [], held, workspace.project.name)
      editFolders((folders) =>
        folders.map((folder) => (folderIds.includes(folder.id) ? { ...folder, parent_id: targetId } : folder)),
      )
      // Gone from the folder they were in at once; the target's listing shows them in its order
      // when it is fetched again.
      editListings(queryClient, projectId, (documents) => documents.filter((d) => !documentIds.includes(d.id)))
      return { previous, change }
    },
    onSuccess: (_data, variables, context) => offer(context.change, variables),
    onError: (error, _variables, context) => {
      queryClient.setQueryData(foldersKey, context?.previous.folders)
      restore(queryClient, context?.previous.documents)
      fail(error)
    },
    onSettled: () => invalidate(foldersKey, documentsKey, openKey),
  })

  // What a deletion and its undoing touch: the library, the counts, the open documents, the
  // trash and what the organisation holds against its plan.
  const afterTrash = () => invalidate(foldersKey, documentsKey, projectKey, openKey, [...trashKey(projectId)], [...ME_KEY])

  const trashItems = useMutation({
    // A folder's documents that the library does not hold close their tabs once they are asked
    // for again and found gone.
    mutationFn: ({ folderIds, documentIds }: TrashItems) =>
      Promise.all([...folderIds.map((id) => api.deleteFolder(id)), ...documentIds.map((id) => api.deleteDocument(id))]),
    onMutate: async ({ documentIds, inFolders }) => {
      setLast(null)
      const previous = await snapshotListings(queryClient, projectId)
      editDocuments(documentIds, () => null)
      workspace.closeTabs([...documentIds, ...inFolders])
      return { previous }
    },
    onSuccess: (_data, items) => setLast({ kind: 'trash', items }),
    onError: (error, _variables, context) => {
      restore(queryClient, context?.previous)
      fail(error)
    },
    onSettled: afterTrash,
  })

  const untrash = useMutation({
    mutationFn: ({ folderIds, documentIds }: TrashItems) =>
      Promise.all([
        ...folderIds.map((id) => api.restore('folder', id)),
        ...documentIds.map((id) => api.restore('document', id)),
      ]),
    onError: fail,
    onSettled: afterTrash,
  })

  // Each step reports its own refusal; the offer goes once the whole change is put back, unless
  // another change took its place meanwhile. A move back is one move per folder the items came
  // from, one after another, so that a refusal stops the rest.
  const undo = useMutation({
    mutationFn: async (change: Change) => {
      switch (change.kind) {
        case 'trash':
          await untrash.mutateAsync(change.items)
          return
        case 'move':
          for (const back of change.back) await moveItems.mutateAsync({ ...back, undoing: true })
          return
        case 'rename':
          if (change.of === 'folder') await renameFolder.mutateAsync({ id: change.id, name: change.from, undoing: true })
          else await renameDocument.mutateAsync({ id: change.id, filename: change.from, undoing: true })
      }
    },
    onSuccess: (_data, change) => setLast((current) => (current === change ? null : current)),
  })

  const upload = useMutation({
    mutationFn: ({ files, folderId }: { files: File[]; folderId: number | null }) =>
      api.upload(projectId, files, folderId),
    onSuccess: (result) => setRejected(result.rejected),
    onError: fail,
    onSettled: () => invalidate(documentsKey, foldersKey, projectKey),
  })

  const reprocess = useMutation({
    mutationFn: (id: number) => api.reprocessDocument(id, 'parse'),
    onError: fail,
    onSettled: (_data, _error, id) => invalidate(documentsKey, [...documentKey(id)]),
  })

  const enrichAll = useMutation({
    mutationFn: () => api.reprocessProject(projectId, 'enrich'),
    onError: fail,
    onSettled: () => invalidate(documentsKey, openKey, projectKey),
  })

  return {
    notice,
    setNotice,
    planLimit,
    clearPlanLimit: () => setPlanLimit(null),
    rejected,
    clearRejected: () => setRejected([]),
    last,
    clearLast: () => setLast(null),
    createFolder,
    renameFolder,
    renameDocument,
    moveItems,
    trashItems,
    undo,
    upload,
    reprocess,
    enrichAll,
  }
}

export type LibraryMutations = ReturnType<typeof useLibraryMutations>
