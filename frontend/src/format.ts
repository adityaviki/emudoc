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
