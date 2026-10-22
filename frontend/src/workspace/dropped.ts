// What a drop onto the library or a folder chosen in the picker holds, as items to upload: each
// file with the folders it is in, so that the server files it the same way (`paths` in
// backend/app/api/documents.py). A drop's folders are read through the browser's entries
// (`webkitGetAsEntry`), a chosen folder's files carry their path (`webkitRelativePath`).

import type { UploadItem } from '../api.ts'

/**
 * The entries a drop holds, taken while its event lasts, since the browser empties its items
 * after; null where the browser offers none, which then hands a folder over as an empty file.
 */
export function droppedEntries(transfer: DataTransfer): FileSystemEntry[] | null {
  const items = [...transfer.items].filter((item) => item.kind === 'file')
  if (!items.length || typeof items[0].webkitGetAsEntry !== 'function') return null
  const entries = items.map((item) => item.webkitGetAsEntry()).filter((entry) => entry !== null)
  return entries.length ? entries : null
}

/**
 * The files the entries hold, folders read through, each with the folders it is in from the
 * drop's own, and the paths of those the browser could not read. A hidden folder (".git") is
 * not read, since nothing in it would be uploaded.
 */
export async function readEntries(entries: FileSystemEntry[]): Promise<{ items: UploadItem[]; unread: string[] }> {
  const items: UploadItem[] = []
  const unread: string[] = []
  async function walk(entry: FileSystemEntry, folder: string) {
    const path = folder ? `${folder}/${entry.name}` : entry.name
    try {
      if (entry.isFile) items.push({ file: await fileOf(entry as FileSystemFileEntry), folder })
      else if (entry.isDirectory && !entry.name.startsWith('.')) {
        for (const child of await childrenOf(entry as FileSystemDirectoryEntry)) await walk(child, path)
      }
    } catch {
      unread.push(path)
    }
  }
  for (const entry of entries) await walk(entry, '')
  return { items, unread }
}

const fileOf = (entry: FileSystemFileEntry) => new Promise<File>((resolve, reject) => entry.file(resolve, reject))

// A folder's reader answers a batch at a time (Chromium's of 100) until an empty one.
async function childrenOf(directory: FileSystemDirectoryEntry): Promise<FileSystemEntry[]> {
  const reader = directory.createReader()
  const children: FileSystemEntry[] = []
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject))
    if (!batch.length) return children
    children.push(...batch)
  }
}

/** The files of a folder chosen in the picker, each with the folders it is in, the chosen one's first. */
export function chosenFolder(files: File[]): UploadItem[] {
  return files.map((file) => {
    const path = file.webkitRelativePath || file.name
    return { file, folder: path.slice(0, Math.max(0, path.lastIndexOf('/'))) }
  })
}
