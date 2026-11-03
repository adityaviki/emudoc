// The library's ways of reading a project as the page holds them (backend/app/api/tags.py, filing.py, favorites.py): the
// project's tags and filing rules, the reader's favourites, and the changes to them.
// Tagging a document or several chosen together and starring one show at once and are put back if the server refuses;
// every listing a change may touch is asked for again either way.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api.ts'
import type { Document, FilingRuleInput, TagColor } from '../api.ts'
import { retagged } from '../selection.ts'
import type { TagsChange } from '../selection.ts'
import { documentKey, editListings, listingsKey, placeListingsKey, restore, snapshotListings } from './listing.ts'

export const tagsKey = (projectId: number) => ['tags', projectId] as const
export const filingRulesKey = (projectId: number) => ['filing-rules', projectId] as const
export const favoritesKey = (projectId: number) => ['favorites', projectId] as const
export const documentTypesKey = (projectId: number) => ['document-types', projectId] as const

export const useTags = (projectId: number) => useQuery({ queryKey: tagsKey(projectId), queryFn: () => api.tags(projectId) })
export const useFilingRules = (projectId: number) =>
  useQuery({ queryKey: filingRulesKey(projectId), queryFn: () => api.filingRules(projectId) })
export const useDocumentTypes = (projectId: number) =>
  useQuery({ queryKey: documentTypesKey(projectId), queryFn: () => api.documentTypes(projectId) })

/** The ids of the documents the reader starred, as a set. */
export function useFavorites(projectId: number): Set<number> {
  const favorites = useQuery({ queryKey: favoritesKey(projectId), queryFn: () => api.favorites(projectId) })
  return new Set(favorites.data?.document_ids)
}

/** Tagging a document or several and starring one, each shown at once wherever the page holds it, and put back if refused. */
export function useDocumentMarks(projectId: number, fail: (error: Error) => void) {
  const queryClient = useQueryClient()
  const tag = useMutation({
    mutationFn: ({ id, tagIds }: { id: number; tagIds: number[] }) => api.tagDocument(id, tagIds),
    onMutate: async ({ id, tagIds }) => {
      const previous = await snapshotListings(queryClient, projectId)
      const open = queryClient.getQueryData<Document>(documentKey(id))
      const change = (document: Document) => (document.id === id ? { ...document, tag_ids: tagIds } : document)
      editListings(queryClient, projectId, (documents) => documents.map(change))
      if (open) queryClient.setQueryData(documentKey(id), change(open))
      return { previous, open }
    },
    onError: (error, { id }, context) => {
      restore(queryClient, context?.previous)
      queryClient.setQueryData(documentKey(id), context?.open)
      fail(error)
    },
    // The tags' counts, and the place that lists by a tag.
    onSettled: (_data, _error, { id }) => {
      for (const queryKey of [listingsKey(projectId), documentKey(id), tagsKey(projectId)]) void queryClient.invalidateQueries({ queryKey })
    },
  })
  // Several at once: each keeps what the change does not name, as the server keeps it.
  const tagMany = useMutation({
    mutationFn: ({ ids, change }: { ids: number[]; change: TagsChange }) => api.tagDocuments(projectId, ids, change.add, change.remove),
    onMutate: async ({ ids, change }) => {
      const previous = await snapshotListings(queryClient, projectId)
      const chosen = new Set(ids)
      const open = ids.map((id) => [id, queryClient.getQueryData<Document>(documentKey(id))] as const)
      const edit = (document: Document) => (chosen.has(document.id) ? { ...document, tag_ids: retagged(document.tag_ids, change) } : document)
      editListings(queryClient, projectId, (documents) => documents.map(edit))
      for (const [id, document] of open) if (document) queryClient.setQueryData(documentKey(id), edit(document))
      return { previous, open }
    },
    onError: (error, _variables, context) => {
      restore(queryClient, context?.previous)
      for (const [id, document] of context?.open ?? []) queryClient.setQueryData(documentKey(id), document)
      fail(error)
    },
    onSettled: (_data, _error, { ids }) => {
      for (const queryKey of [listingsKey(projectId), tagsKey(projectId), ...ids.map(documentKey)]) void queryClient.invalidateQueries({ queryKey })
    },
  })
  const star = useMutation({
    mutationFn: ({ id, starred }: { id: number; starred: boolean }) => (starred ? api.starDocument(id) : api.unstarDocument(id)),
    onMutate: async ({ id, starred }) => {
      const key = favoritesKey(projectId)
      await queryClient.cancelQueries({ queryKey: key })
      const previous = queryClient.getQueryData<{ document_ids: number[] }>(key)
      const others = (previous?.document_ids ?? []).filter((held) => held !== id)
      queryClient.setQueryData(key, { document_ids: starred ? [id, ...others] : others })
      return { previous }
    },
    onError: (error, _variables, context) => {
      queryClient.setQueryData(favoritesKey(projectId), context?.previous)
      fail(error)
    },
    onSettled: () => {
      for (const queryKey of [favoritesKey(projectId), placeListingsKey(projectId, { favorites: true })]) {
        void queryClient.invalidateQueries({ queryKey })
      }
    },
  })
  return { tag, tagMany, star }
}

/** Making, changing and deleting the project's tags and filing rules, each asked for again with what it touches. */
export function useOrganizing(projectId: number) {
  const queryClient = useQueryClient()
  const invalidate = (...keys: readonly unknown[][]) => {
    for (const queryKey of keys) void queryClient.invalidateQueries({ queryKey })
  }
  // A tag renamed shows on every document by its new name; one deleted comes off them, and takes its rules with it.
  const tagTouched = () => invalidate([...tagsKey(projectId)], [...listingsKey(projectId)], ['document'], [...filingRulesKey(projectId)])
  const createTag = useMutation({
    mutationFn: (body: { name: string; color: TagColor }) => api.createTag(projectId, body),
    onSettled: () => invalidate([...tagsKey(projectId)]),
  })
  const updateTag = useMutation({
    mutationFn: ({ id, ...body }: { id: number; name?: string; color?: TagColor }) => api.updateTag(id, body),
    onSettled: tagTouched,
  })
  const deleteTag = useMutation({ mutationFn: (id: number) => api.deleteTag(id), onSettled: tagTouched })

  const rulesTouched = () => invalidate([...filingRulesKey(projectId)])
  const saveRule = useMutation({
    mutationFn: ({ id, rule }: { id: number | null; rule: FilingRuleInput }) =>
      id == null ? api.createFilingRule(projectId, rule) : api.updateFilingRule(id, rule),
    onSettled: rulesTouched,
  })
  const deleteRule = useMutation({ mutationFn: (id: number) => api.deleteFilingRule(id), onSettled: rulesTouched })

  return { createTag, updateTag, deleteTag, saveRule, deleteRule }
}
