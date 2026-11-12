// Every change the library makes. Renames and moves show at once and are undone if the server
// refuses them; the lists are fetched again afterwards either way. A deletion puts things in the project's trash (backend/app/trash.py). The library offers to
// take back the last deletion, move or rename at once (changes.ts) through the mutation that made
// it, so that an undo shows at once too and is put back if the server refuses it. An upload
// goes in as many requests as the server's limits ask (uploads.ts), its progress kept apart for
// its panel, which then follows its documents' indexing until it is dismissed or the next upload
// takes its place; begun offline it waits for the connection, and one cut off on its way offers
// the files not sent again (`uploadCutOff` in connection.ts) rather than show a refusal.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { Unreachable, api } from '../api.ts'
import type { Document, DocumentDetailsInput, Folder, ReviewInput, UploadItem, UploadResult } from '../api.ts'
import { factTablesKey } from '../factTable.ts'
import { useStatus } from '../hooks.ts'
import { unfinishedStages } from '../indexing.ts'
import { reviewsKey } from '../reviews.ts'
import { exportWords, reprocessedWords } from '../selection.ts'
import { trashKey } from '../trash.ts'
import { givenWords, versionsKey } from '../versions.ts'
import { moved, renamed } from './changes.ts'
import type { Change, MoveItems, TrashItems } from './changes.ts'
import { documentKey, editListings, findListed, listed, listingsKey, restore, snapshotListings } from './listing.ts'
import { documentTypesKey } from './organizing.ts'
import { useWorkspace } from './state.ts'
import {
  DEFAULT_LIMITS,
  NOTHING_TO_UPLOAD,
  UPLOAD_UNDER_WAY,
  UploadProgress,
  UploadStopped,
  followedKey,
  isAbort,
  screen,
  sendInBatches,
} from './uploads.ts'

/** An undo puts things back and offers no undo of its own. */
interface Undoing {
  undoing?: boolean
}

/** What an upload sends, and the folder it goes into; `resent` for the files one cut off did not send. */
export interface UploadRequest {
  items: UploadItem[]
  folderId: number | null
  resent?: boolean
}

/** An upload sent, whose documents the panel follows: its uploads on the server, and the folder it went into. */
export interface Followed {
  uploads: number[]
  folderId: number | null
}

export function useLibraryMutations() {
  const queryClient = useQueryClient()
  const workspace = useWorkspace()
  const projectId = workspace.project.id
  const [notice, setNotice] = useState<string | null>(null)
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
  // The upload the panel follows, whose documents may be indexed again or deleted.
  const uploadsKey = [...followedKey(projectId)]
  const fail = (error: Error) => setNotice(error.message)
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

  // What a deletion and its undoing touch: the library, the counts, the open documents and the
  // trash.
  const afterTrash = () => invalidate(foldersKey, documentsKey, projectKey, openKey, uploadsKey, [...trashKey(projectId)])

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

  const limits = useStatus().data?.uploads ?? DEFAULT_LIMITS
  const [progress] = useState(() => new UploadProgress())
  // The upload's requests, which Stop aborts, and whether one is under way: a drop's folders are
  // read before it starts, by when the render's `isPending` may be old.
  const sending = useRef<AbortController | null>(null)
  const underWay = useRef(false)
  const [followed, setFollowed] = useState<Followed | null>(null)
  // The files sent again after a cut are followed with those that went in before it.
  const follow = (uploads: number[], { folderId, resent }: UploadRequest) => {
    if (!uploads.length) return
    setFollowed((before) => ({ uploads: [...(resent && before ? before.uploads : []), ...uploads], folderId }))
  }
  const upload = useMutation({
    mutationFn: ({ items, folderId }: UploadRequest) => {
      const controller = new AbortController()
      sending.current = controller
      const send = (batch: UploadItem[], onProgress: (loaded: number, total: number) => void) =>
        api.upload(projectId, batch, folderId, { onProgress, signal: controller.signal })
      return sendInBatches(items, limits, send, progress.set)
    },
    onMutate: () => {
      underWay.current = true
    },
    onSuccess: (result, request) => {
      setRejected((left) => [...left, ...result.rejected])
      follow(result.uploads, request)
    },
    // One that reached no server, or that its member stopped, stays the mutation's error, which
    // the library offers to send again or says was stopped; what went in before is followed.
    onError: (error, request) => {
      const stopped = error instanceof UploadStopped ? error : null
      if (stopped) {
        setRejected((left) => [...left, ...stopped.result.rejected])
        follow(stopped.result.uploads, request)
      }
      const cause = stopped?.error ?? error
      if (!(cause instanceof Unreachable) && !isAbort(cause)) fail(cause)
    },
    onSettled: () => {
      underWay.current = false
      sending.current = null
      progress.set(null)
      invalidate(documentsKey, foldersKey, projectKey)
    },
  })

  /**
   * Uploads what was dropped or chosen into the folder: what cannot go is listed at once, in
   * place of what an upload before left out, and the rest is sent. Answers why nothing was when
   * another upload is under way, which this one does not join, or nothing is left to send.
   */
  const startUpload = (items: UploadItem[], folderId: number | null, unread: string[] = []): string | null => {
    if (underWay.current) return UPLOAD_UNDER_WAY
    const screened = screen(items, limits, unread)
    setRejected(screened.left)
    setFollowed(null)
    if (!screened.items.length) return screened.left.length ? null : NOTHING_TO_UPLOAD
    underWay.current = true
    upload.mutate({ items: screened.items, folderId })
    return null
  }

  const reprocess = useMutation({
    mutationFn: (id: number) => api.reprocessDocument(id, 'parse'),
    onError: fail,
    onSettled: (_data, _error, id) => invalidate(documentsKey, [...documentKey(id)], projectKey, uploadsKey),
  })

  // "Index the rest": a partly indexed document from the first stage it went without, keeping
  // what it has; for the project, each of its partly indexed documents so (the server picks them).
  const indexRest = useMutation({
    mutationFn: (document: Document) => api.reprocessDocument(document.id, unfinishedStages(document)[0]),
    onError: fail,
    onSettled: (_data, _error, document) => invalidate(documentsKey, [...documentKey(document.id)], projectKey, uploadsKey),
  })

  const indexProjectRest = useMutation({
    mutationFn: () => api.reprocessProject(projectId),
    onError: fail,
    onSettled: () => invalidate(documentsKey, openKey, projectKey, uploadsKey),
  })

  // The documents chosen together: each indexed again from parse, or, for "Index the rest", the partly indexed among
  // them each from the stage it went without; the library says how many, and those left being indexed.
  const reprocessMany = useMutation({
    mutationFn: ({ ids, rest }: { ids: number[]; rest: boolean }) => api.reprocessDocuments(projectId, ids, rest ? null : 'parse'),
    onSuccess: (result, { rest }) => setNotice(reprocessedWords(result, rest)),
    onError: fail,
    onSettled: () => invalidate(documentsKey, openKey, projectKey, uploadsKey),
  })

  // An export of the chosen documents alone, which the worker makes and the projects page lists with the project's.
  const exportChosen = useMutation({
    mutationFn: (ids: number[]) => api.exportProject(projectId, ids),
    onSuccess: (_made, ids) => setNotice(exportWords(ids.length)),
    onError: fail,
    onSettled: () => invalidate([...projectKey, 'exports']),
  })

  // "Upload a new version…" from a file's menu: the library says what became of it.
  const version = useUploadVersion(projectId)
  const uploadVersion = (document: Document, file: File) =>
    version.mutate({ id: document.id, file }, { onSuccess: (given) => setNotice(givenWords(given)), onError: fail })

  return {
    notice,
    setNotice,
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
    startUpload,
    uploadProgress: progress,
    stopUpload: () => sending.current?.abort(),
    followed,
    clearFollowed: () => setFollowed(null),
    reprocess,
    indexRest,
    indexProjectRest,
    reprocessMany,
    exportChosen,
    uploadVersion,
  }
}

export type LibraryMutations = ReturnType<typeof useLibraryMutations>

/**
 * Move a document's review or assign it (backend/app/reviews.py), then show the document as the server keeps it
 * wherever the page holds it, and ask again for what lists reviews: the reviews view and the facts tables.
 */
export function useReviewChange(projectId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, change }: { id: number; change: ReviewInput }) => api.review(id, change),
    onSuccess: (document) => {
      editListings(queryClient, projectId, (documents) => documents.map((held) => (held.id === document.id ? document : held)))
      queryClient.setQueryData(documentKey(document.id), document)
      void queryClient.invalidateQueries({ queryKey: reviewsKey(projectId) })
      void queryClient.invalidateQueries({ queryKey: factTablesKey(projectId) })
    },
  })
}

/**
 * Set a document's details as the member's, or take what the reading found again (backend/app/details.py), then show the
 * document so wherever the page holds it, and ask again for the listings, whose date order reads them, and the project's
 * types.
 */
export function useDetailsChange(projectId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, change }: { id: number; change: DocumentDetailsInput }) => api.changeDetails(id, change),
    onSuccess: (document) => {
      editListings(queryClient, projectId, (documents) => documents.map((held) => (held.id === document.id ? document : held)))
      queryClient.setQueryData(documentKey(document.id), document)
      for (const queryKey of [listingsKey(projectId), documentTypesKey(projectId)]) void queryClient.invalidateQueries({ queryKey })
    },
  })
}

/**
 * Give a document a new file (backend/app/versions.py), then show the document as the server keeps it, waiting to
 * read the new file, wherever the page holds it, and ask again for its versions and what counts its project's documents.
 */
export function useUploadVersion(projectId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, file }: { id: number; file: File }) => api.uploadVersion(id, file),
    onSuccess: (document) => {
      editListings(queryClient, projectId, (documents) => documents.map((held) => (held.id === document.id ? document : held)))
      queryClient.setQueryData(documentKey(document.id), document)
    },
    onSettled: (_data, _error, { id }) => {
      for (const queryKey of [listingsKey(projectId), documentKey(id), versionsKey(id), ['project', projectId], followedKey(projectId)]) {
        void queryClient.invalidateQueries({ queryKey })
      }
    },
  })
}
