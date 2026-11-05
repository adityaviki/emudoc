// The library's ways of reading a project as the page holds them (backend/app/api/favorites.py, query.py): the reader's
// favourites and the types of the project's documents. Starring a document shows at once and is put back if the server
// refuses; the favourites' listing is asked for again either way.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api.ts'
import { placeListingsKey } from './listing.ts'

export const favoritesKey = (projectId: number) => ['favorites', projectId] as const
export const documentTypesKey = (projectId: number) => ['document-types', projectId] as const

export const useDocumentTypes = (projectId: number) =>
  useQuery({ queryKey: documentTypesKey(projectId), queryFn: () => api.documentTypes(projectId) })

/** The ids of the documents the reader starred, as a set. */
export function useFavorites(projectId: number): Set<number> {
  const favorites = useQuery({ queryKey: favoritesKey(projectId), queryFn: () => api.favorites(projectId) })
  return new Set(favorites.data?.document_ids)
}

/** Starring a document, shown at once wherever the page holds it, and put back if refused. */
export function useDocumentMarks(projectId: number, fail: (error: Error) => void) {
  const queryClient = useQueryClient()
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
  return { star }
}
