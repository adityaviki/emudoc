import { useQuery } from '@tanstack/react-query'
import { useCallback, useMemo, useState } from 'react'
import { api, isIndexing } from './api.ts'
import type { Document, Folder } from './api.ts'

/** A project's documents, polled while any of them is still being indexed. */
export function useDocuments(projectId: number) {
  return useQuery({
    queryKey: ['documents', projectId],
    queryFn: () => api.documents(projectId),
    refetchInterval: (query) => (query.state.data?.some(isIndexing) ? 1500 : false),
  })
}

/**
 * Which optional services are usable, for the status line and the panes that depend on them.
 * The server asks the embedding endpoint at most once a minute, so asking it more often, or
 * again for every pane that opens, learns nothing.
 */
export function useStatus() {
  return useQuery({ queryKey: ['status'], queryFn: api.status, staleTime: 60_000, refetchInterval: 60_000 })
}

/** The version the API runs, for the user menu: asked as the menu opens, since a release changes it. */
export function useVersion() {
  return useQuery({ queryKey: ['health'], queryFn: api.health, staleTime: 60_000, select: (health) => health.version })
}

export function useFolders(projectId: number) {
  return useQuery({ queryKey: ['folders', projectId], queryFn: () => api.folders(projectId) })
}

/** The current edition of the terms of service and privacy policy; it changes only with a deployment. */
export function useLegal() {
  return useQuery({ queryKey: ['legal'], queryFn: api.legal, staleTime: Infinity })
}

export interface FolderTree {
  byId: Map<number, Folder>
  /** Subfolders of each folder (null for the root), sorted by name. */
  children: Map<number | null, Folder[]>
  /** The folders from the root down to this one; empty for the root. */
  pathTo(folderId: number | null): Folder[]
  /** The folder and every folder below it. */
  subtree(folderId: number): Set<number>
}

export const byName = <T extends { name: string }>(a: T, b: T) =>
  a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' })

export function useFolderTree(folders: Folder[] | undefined): FolderTree {
  return useMemo(() => {
    const byId = new Map<number, Folder>()
    const children = new Map<number | null, Folder[]>()
    for (const folder of folders ?? []) {
      byId.set(folder.id, folder)
      const siblings = children.get(folder.parent_id) ?? []
      siblings.push(folder)
      children.set(folder.parent_id, siblings)
    }
    for (const siblings of children.values()) siblings.sort(byName)

    return {
      byId,
      children,
      pathTo(folderId) {
        const path: Folder[] = []
        let current = folderId == null ? undefined : byId.get(folderId)
        while (current) {
          path.unshift(current)
          current = current.parent_id == null ? undefined : byId.get(current.parent_id)
        }
        return path
      },
      subtree(folderId) {
        const ids = new Set<number>()
        const stack = [folderId]
        while (stack.length) {
          const id = stack.pop()!
          ids.add(id)
          for (const child of children.get(id) ?? []) stack.push(child.id)
        }
        return ids
      },
    }
  }, [folders])
}

/** The documents directly inside a folder, by name. */
export function documentsIn(documents: Document[] | undefined, folderId: number | null): Document[] {
  return (documents ?? [])
    .filter((document) => document.folder_id === folderId)
    .sort((a, b) => byName({ name: a.filename }, { name: b.filename }))
}

/**
 * State mirrored to localStorage. Storage can be blocked or cleared, so reads fall back to the
 * initial value and writes may silently fail; `validate` guards against stale or foreign data.
 */
export function useLocalStorage<T>(key: string, initial: T, validate: (value: unknown) => value is T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const stored = localStorage.getItem(key)
      if (stored !== null) {
        const parsed: unknown = JSON.parse(stored)
        if (validate(parsed)) return parsed
      }
    } catch {
      // Unreadable storage: start from the initial value.
    }
    return initial
  })

  const set = useCallback(
    (next: T | ((current: T) => T), options: { persist?: boolean } = {}) => {
      setValue((current) => {
        const resolved = typeof next === 'function' ? (next as (current: T) => T)(current) : next
        if (options.persist !== false) {
          try {
            localStorage.setItem(key, JSON.stringify(resolved))
          } catch {
            // Storage can be full or blocked; the value still applies for this visit.
          }
        }
        return resolved
      })
    },
    [key],
  )
  return [value, set] as const
}
