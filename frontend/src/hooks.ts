import { onlineManager, useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { api } from './api.ts'
import type { Folder } from './api.ts'

/**
 * Which optional services are usable, for the status line and the panes that depend on them.
 * The server asks the embedding endpoint at most once a minute, so asking it more often, or
 * again for every pane that opens, learns nothing.
 */
export function useStatus() {
  return useQuery({ queryKey: ['status'], queryFn: api.status, staleTime: 60_000, refetchInterval: 60_000 })
}

/**
 * Whether the browser is online, as TanStack Query's `onlineManager` has it from the browser's
 * `online` and `offline` events: while it is not, the queries and mutations wait, an upload among
 * them, and the page says so.
 */
export function useOnline(): boolean {
  return useSyncExternalStore(onlineManager.subscribe, () => onlineManager.isOnline())
}

/** The version the API runs, for the user menu: asked as the menu opens, since a release changes it. */
export function useVersion() {
  return useQuery({ queryKey: ['health'], queryFn: api.health, staleTime: 60_000, select: (health) => health.version })
}

/**
 * The page's title while it prints, which the browser heads the sheets with and names a PDF saved
 * from the print after, for a page that prints a document; the app's own again once printed.
 */
export function usePrintTitle(title: string) {
  useEffect(() => {
    let before: string | null = null
    function start() {
      before = window.document.title
      window.document.title = title
    }
    function end() {
      if (before !== null) window.document.title = before
      before = null
    }
    window.addEventListener('beforeprint', start)
    window.addEventListener('afterprint', end)
    return () => {
      window.removeEventListener('beforeprint', start)
      window.removeEventListener('afterprint', end)
    }
  }, [title])
}

/**
 * Names a page anyone reads (the help, the terms, the sign-in pages) in the browser's tab and to a
 * search engine, which reads the title and the description the page shows once its script ran:
 * "Plans and usage · Emudoc help", with the article's summary. The page's own again once it goes.
 */
export function usePageTitle(title: string | undefined, description?: string) {
  useEffect(() => {
    if (!title) return
    const meta = window.document.querySelector<HTMLMetaElement>('meta[name="description"]')
    const before = { title: window.document.title, description: meta?.content }
    window.document.title = title
    if (meta && description) meta.content = description
    return () => {
      window.document.title = before.title
      if (meta && before.description !== undefined) meta.content = before.description
    }
  }, [title, description])
}

export function useFolders(projectId: number) {
  return useQuery({ queryKey: ['folders', projectId], queryFn: () => api.folders(projectId) })
}

/** The current edition of the terms of service and privacy policy; it changes only with a deployment. */
export function useLegal() {
  return useQuery({ queryKey: ['legal'], queryFn: api.legal, staleTime: Infinity })
}

/** The help pages and where members write for help; they change only with a deployment. */
export function useHelp() {
  return useQuery({ queryKey: ['help'], queryFn: api.help, staleTime: Infinity })
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
