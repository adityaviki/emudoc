import { describe, expect, it } from 'vitest'
import { chosenFolder, droppedEntries, readEntries } from './dropped.ts'

// The browser's entries as far as a drop is read through them.
const fileEntry = (name: string, unreadable = false) =>
  ({
    name,
    isFile: true,
    isDirectory: false,
    file: (resolve: (file: File) => void, reject: (error: Error) => void) =>
      unreadable ? reject(new Error('Gone.')) : resolve(new File(['x'], name)),
  }) as unknown as FileSystemEntry

// A folder whose reader answers its children two at a time, then with nothing, as Chromium's does a hundred.
const folderEntry = (name: string, children: FileSystemEntry[]) =>
  ({
    name,
    isFile: false,
    isDirectory: true,
    createReader: () => {
      let next = 0
      return {
        readEntries: (resolve: (entries: FileSystemEntry[]) => void) => {
          resolve(children.slice(next, next + 2))
          next += 2
        },
      }
    },
  }) as unknown as FileSystemEntry

const transfer = (items: object[]) => ({ items, files: [] }) as unknown as DataTransfer

describe('a drop', () => {
  it('is taken by its entries where the browser offers them', () => {
    const entry = fileEntry('a.md')
    const dropped = transfer([
      { kind: 'string', webkitGetAsEntry: () => null },
      { kind: 'file', webkitGetAsEntry: () => entry },
    ])
    expect(droppedEntries(dropped)).toEqual([entry])
    expect(droppedEntries(transfer([{ kind: 'file' }]))).toBeNull()
    expect(droppedEntries(transfer([]))).toBeNull()
  })

  it('is read through its folders, each file with the folders it is in', async () => {
    const dropped = [
      fileEntry('notes.md'),
      folderEntry('Invoices', [
        fileEntry('a.pdf'),
        folderEntry('2026', [fileEntry('b.pdf'), fileEntry('c.pdf'), fileEntry('d.pdf')]),
        folderEntry('.git', [fileEntry('config')]),
        fileEntry('locked.pdf', true),
      ]),
    ]
    const { items, unread } = await readEntries(dropped)
    expect(items.map(({ file, folder }) => [folder, file.name])).toEqual([
      ['', 'notes.md'],
      ['Invoices', 'a.pdf'],
      ['Invoices/2026', 'b.pdf'],
      ['Invoices/2026', 'c.pdf'],
      ['Invoices/2026', 'd.pdf'],
    ])
    expect(unread).toEqual(['Invoices/locked.pdf'])
  })
})

describe('a folder chosen in the picker', () => {
  it('gives each file the folders it is in, the chosen one first', () => {
    const at = (path: string) => {
      const file = new File(['x'], path.split('/').at(-1) ?? path)
      Object.defineProperty(file, 'webkitRelativePath', { value: path })
      return file
    }
    const items = chosenFolder([at('Invoices/a.pdf'), at('Invoices/2026/b.pdf'), new File(['x'], 'c.pdf')])
    expect(items.map(({ file, folder }) => [folder, file.name])).toEqual([
      ['Invoices', 'a.pdf'],
      ['Invoices/2026', 'b.pdf'],
      ['', 'c.pdf'],
    ])
  })
})
