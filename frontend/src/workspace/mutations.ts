// Every change the library makes. Renames and moves show at once and are undone if the server
// refuses them; the lists are fetched again afterwards either way. A refusal by the plan (a full
// plan, or an organisation past it and so read-only) is shown as the way past it, and a deletion
// asks again what the organisation holds past its plan, since deleting is how it gets within it.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, isPlanLimit } from '../api.ts'
import type { Document, Folder, PlanRefusal, UploadResult } from '../api.ts'
import { ME_KEY } from '../auth.ts'
import { documentKey, editListings, listingsKey, restore, snapshotListings } from './listing.ts'
import { useWorkspace } from './state.ts'

export interface MoveItems {
  folderIds: number[]
  documentIds: number[]
  targetId: number | null
}

export function useLibraryMutations() {
  const queryClient = useQueryClient()
  const workspace = useWorkspace()
  const projectId = workspace.project.id
  const [notice, setNotice] = useState<string | null>(null)
  // A change the organisation's plan refused: shown as the way past its limit.
  const [planLimit, setPlanLimit] = useState<PlanRefusal | null>(null)
  const [rejected, setRejected] = useState<UploadResult['rejected']>([])

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
    mutationFn: ({ id, name }: { id: number; name: string }) => api.updateFolder(id, { name }),
    onMutate: async ({ id, name }) => {
      const previous = await snapshot<Folder[]>(foldersKey)
      editFolders((folders) => folders.map((folder) => (folder.id === id ? { ...folder, name } : folder)))
      return { previous }
    },
    onError: (error, _variables, context) => {
      queryClient.setQueryData(foldersKey, context?.previous)
      fail(error)
    },
    onSettled: () => invalidate(foldersKey),
  })

  const renameDocument = useMutation({
    mutationFn: ({ id, filename }: { id: number; filename: string }) => api.updateDocument(id, { filename }),
    onMutate: async ({ id, filename }) => {
      const previous = await snapshotListings(queryClient, projectId)
      const open = await snapshot<Document>([...documentKey(id)])
      editDocuments([id], (document) => ({ ...document, filename }))
      return { previous, open }
    },
    onError: (error, { id }, context) => {
      restore(queryClient, context?.previous)
      queryClient.setQueryData(documentKey(id), context?.open)
      fail(error)
    },
    onSettled: (_data, _error, { id }) => invalidate(documentsKey, [...documentKey(id)]),
  })

  const moveItems = useMutation({
    mutationFn: ({ folderIds, documentIds, targetId }: MoveItems) =>
      Promise.all([
        ...folderIds.map((id) => api.updateFolder(id, { parent_id: targetId })),
        ...documentIds.map((id) => api.updateDocument(id, { folder_id: targetId })),
      ]),
    onMutate: async ({ folderIds, documentIds, targetId }) => {
      const previous = {
        folders: await snapshot<Folder[]>(foldersKey),
        documents: await snapshotListings(queryClient, projectId),
      }
      editFolders((folders) =>
        folders.map((folder) => (folderIds.includes(folder.id) ? { ...folder, parent_id: targetId } : folder)),
      )
      // Gone from the folder they were in at once; the target's listing shows them in its order
      // when it is fetched again.
      editListings(queryClient, projectId, (documents) => documents.filter((d) => !documentIds.includes(d.id)))
      return { previous }
    },
    onError: (error, _variables, context) => {
      queryClient.setQueryData(foldersKey, context?.previous.folders)
      restore(queryClient, context?.previous.documents)
      fail(error)
    },
    onSettled: () => invalidate(foldersKey, documentsKey, openKey),
  })

  const deleteFolder = useMutation({
    // `documentIds` are the ones the library holds; the tabs of the others close when their
    // documents are asked for again and found gone.
    mutationFn: ({ id }: { id: number; documentIds: number[] }) => api.deleteFolder(id),
    onMutate: ({ documentIds }) => workspace.closeTabs(documentIds),
    onError: fail,
    onSettled: () => invalidate(foldersKey, documentsKey, projectKey, openKey, [...ME_KEY]),
  })

  const deleteDocuments = useMutation({
    mutationFn: (ids: number[]) => Promise.all(ids.map((id) => api.deleteDocument(id))),
    onMutate: async (ids) => {
      const previous = await snapshotListings(queryClient, projectId)
      editDocuments(ids, () => null)
      workspace.closeTabs(ids)
      return { previous }
    },
    onError: (error, _variables, context) => {
      restore(queryClient, context?.previous)
      fail(error)
    },
    onSettled: () => invalidate(documentsKey, foldersKey, projectKey, [...ME_KEY]),
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
    createFolder,
    renameFolder,
    renameDocument,
    moveItems,
    deleteFolder,
    deleteDocuments,
    upload,
    reprocess,
    enrichAll,
  }
}

export type LibraryMutations = ReturnType<typeof useLibraryMutations>
