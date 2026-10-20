export function formatDate(value: string | null): string {
  if (!value) return ''
  return new Date(value).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

/** Parse a Markdown pipe table into rows of cells, or return null if the text is not one. */
export function parsePipeTable(text: string): string[][] | null {
  const lines = text.split('\n').filter((line) => line.trim())
  if (lines.length < 2 || !lines.every((line) => line.trim().startsWith('|'))) return null
  const rows = lines
    // The separator row under the header holds only dashes, colons and pipes.
    .filter((line) => !/^[\s|:-]+$/.test(line))
    .map((line) =>
      line
        .trim()
        .replace(/^\|/, '')
        .replace(/\|$/, '')
        .split(/(?<!\\)\|/)
        .map((cell) => cell.trim().replaceAll('\\|', '|')),
    )
  return rows.length ? rows : null
}

/** A size in the unit that reads best, as the server words a plan's (`size_label` in backend/app/entitlements.py): 100 MB, 2.5 GB. */
export function formatSize(bytes: number): string {
  for (const [unit, scale] of [['GB', 1e9], ['MB', 1e6], ['kB', 1e3]] as const) {
    if (bytes >= scale) {
      const value = bytes / scale
      return `${value.toLocaleString('en', { maximumFractionDigits: value >= 10 ? 0 : 1 })} ${unit}`
    }
  }
  return `${bytes} bytes`
}

export function formatDateTime(value: string): string {
  return new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}
