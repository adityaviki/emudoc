// Every change the library makes. Renames and moves show at once and are undone if the server
// refuses them; the lists are fetched again afterwards either way.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api.ts'
import type { Document, Folder, UploadResult } from '../api.ts'
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
  const [rejected, setRejected] = useState<UploadResult['rejected']>([])

  const documentsKey = ['documents', projectId]
  const foldersKey = ['folders', projectId]
  const projectKey = ['project', projectId]
  const fail = (error: Error) => setNotice(error.message)
  const invalidate = (...keys: unknown[][]) => {
    for (const queryKey of keys) queryClient.invalidateQueries({ queryKey })
  }
  async function snapshot<T>(queryKey: unknown[]): Promise<T | undefined> {
    await queryClient.cancelQueries({ queryKey })
    return queryClient.getQueryData<T>(queryKey)
  }
  const editFolders = (change: (folders: Folder[]) => Folder[]) =>
    queryClient.setQueryData<Folder[]>(foldersKey, (folders) => folders && change(folders))
  const editDocuments = (change: (documents: Document[]) => Document[]) =>
    queryClient.setQueryData<Document[]>(documentsKey, (documents) => documents && change(documents))

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
      const previous = await snapshot<Document[]>(documentsKey)
      editDocuments((documents) => documents.map((d) => (d.id === id ? { ...d, filename } : d)))
      return { previous }
    },
    onError: (error, _variables, context) => {
      queryClient.setQueryData(documentsKey, context?.previous)
      fail(error)
    },
    onSettled: () => invalidate(documentsKey),
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
        documents: await snapshot<Document[]>(documentsKey),
      }
      editFolders((folders) =>
        folders.map((folder) => (folderIds.includes(folder.id) ? { ...folder, parent_id: targetId } : folder)),
      )
      editDocuments((documents) =>
        documents.map((d) => (documentIds.includes(d.id) ? { ...d, folder_id: targetId } : d)),
      )
      return { previous }
    },
    onError: (error, _variables, context) => {
      queryClient.setQueryData(foldersKey, context?.previous.folders)
      queryClient.setQueryData(documentsKey, context?.previous.documents)
      fail(error)
    },
    onSettled: () => invalidate(foldersKey, documentsKey),
  })

  const deleteFolder = useMutation({
    mutationFn: ({ id }: { id: number; documentIds: number[] }) => api.deleteFolder(id),
    onMutate: ({ documentIds }) => workspace.closeTabs(documentIds),
    onError: fail,
    onSettled: () => invalidate(foldersKey, documentsKey, projectKey),
  })

  const deleteDocuments = useMutation({
    mutationFn: (ids: number[]) => Promise.all(ids.map((id) => api.deleteDocument(id))),
    onMutate: async (ids) => {
      const previous = await snapshot<Document[]>(documentsKey)
      editDocuments((documents) => documents.filter((d) => !ids.includes(d.id)))
      workspace.closeTabs(ids)
      return { previous }
    },
    onError: (error, _variables, context) => {
      queryClient.setQueryData(documentsKey, context?.previous)
      fail(error)
    },
    onSettled: () => invalidate(documentsKey, projectKey),
  })

  const upload = useMutation({
    mutationFn: ({ files, folderId }: { files: File[]; folderId: number | null }) =>
      api.upload(projectId, files, folderId),
    onSuccess: (result) => setRejected(result.rejected),
    onError: fail,
    onSettled: () => invalidate(documentsKey, projectKey),
  })

  const reprocess = useMutation({
    mutationFn: (id: number) => api.reprocessDocument(id, 'parse'),
    onError: fail,
    onSettled: () => invalidate(documentsKey),
  })

  const enrichAll = useMutation({
    mutationFn: () => api.reprocessProject(projectId, 'enrich'),
    onError: fail,
    onSettled: () => invalidate(documentsKey),
  })

  return {
    notice,
    setNotice,
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
