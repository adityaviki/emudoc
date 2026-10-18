import { describe, expect, it } from 'vitest'
import { ApiError } from './api.ts'
import { framesOf, pageErrorOf, pagePath } from './errors.ts'

const ORIGIN = 'https://emudoc.example.com'
const TEXT = 'the termination clause says ninety days'

/** An error as the browser raised it, with the stack it wrote. */
function raised(error: Error, stack: string): Error {
  error.stack = stack
  return error
}

describe('pageErrorOf', () => {
  it('tells an error by its kind and where it was raised, never by its message', () => {
    const error = raised(
      new SyntaxError(`Unexpected token 'T', "${TEXT}" is not valid JSON`),
      [
        `SyntaxError: Unexpected token 'T', "${TEXT}" is not valid JSON`,
        '    at JSON.parse (<anonymous>)',
        `    at Xe (${ORIGIN}/assets/index-B2x9kQ.js:40:1234)`,
        `    at new Ab (${ORIGIN}/assets/index-B2x9kQ.js:1:99)`,
        `    at ${ORIGIN}/assets/pdf-Cq1.js:3:7`,
        `    at async Promise.all (${ORIGIN}/assets/index-B2x9kQ.js:2:5)`,
      ].join('\n'),
    )
    const report = pageErrorOf(error, ORIGIN, '/projects/12/documents/3')
    expect(report).toEqual({
      name: 'SyntaxError',
      status: null,
      frames: [
        { function: 'Xe', file: '/assets/index-B2x9kQ.js', line: 40, column: 1234 },
        { function: 'new Ab', file: '/assets/index-B2x9kQ.js', line: 1, column: 99 },
        { function: null, file: '/assets/pdf-Cq1.js', line: 3, column: 7 },
        { function: 'async Promise.all', file: '/assets/index-B2x9kQ.js', line: 2, column: 5 },
      ],
      path: '/projects/12/documents/3',
    })
    expect(JSON.stringify(report)).not.toContain('ninety')
  })

  it("names the API's refusal by its status", () => {
    const report = pageErrorOf(new ApiError(500, `Could not read ${TEXT}`), ORIGIN, '/')
    expect(report.name).toBe('ApiError')
    expect(report.status).toBe(500)
    expect(JSON.stringify(report)).not.toContain('ninety')
  })

  it('takes whatever was thrown, an error or not', () => {
    expect(pageErrorOf(TEXT, ORIGIN, '/')).toEqual({ name: 'Error', status: null, frames: [], path: '/' })
    const odd = new Error(TEXT)
    odd.name = TEXT
    expect(pageErrorOf(odd, ORIGIN, '/').name).toBe('Error')
  })
})

describe('framesOf', () => {
  it("reads Firefox's and Safari's frames too", () => {
    const stack = [`render/<@${ORIGIN}/assets/index-B2x9kQ.js:40:1234`, `@${ORIGIN}/assets/index-B2x9kQ.js:1:2`].join('\n')
    expect(framesOf(stack, ORIGIN)).toEqual([
      { function: 'render/<', file: '/assets/index-B2x9kQ.js', line: 40, column: 1234 },
      { function: null, file: '/assets/index-B2x9kQ.js', line: 1, column: 2 },
    ])
  })

  it("keeps the page's own built scripts alone", () => {
    const stack = [
      '    at a (https://other.example/assets/index.js:1:1)',
      '    at b (chrome-extension://abcdef/content.js:1:1)',
      `    at c (${ORIGIN}/src/App.tsx?t=1700000000:10:3)`,
      `    at d (${ORIGIN}/assets/index-B2x9kQ.js:5:6)`,
    ].join('\n')
    expect(framesOf(stack, ORIGIN)).toEqual([{ function: 'd', file: '/assets/index-B2x9kQ.js', line: 5, column: 6 }])
  })

  it('leaves out a function name that is more than code would make', () => {
    const stack = `    at ${TEXT} (${ORIGIN}/assets/index.js:1:1)`
    expect(framesOf(stack, ORIGIN)).toEqual([{ function: null, file: '/assets/index.js', line: 1, column: 1 }])
  })

  it('stops at fifty frames, as many as the server takes', () => {
    const stack = Array.from({ length: 80 }, (_, i) => `    at f${i} (${ORIGIN}/assets/index.js:${i}:1)`).join('\n')
    expect(framesOf(stack, ORIGIN)).toHaveLength(50)
  })
})

describe('pagePath', () => {
  it('keeps the words and ids of a path and writes anything else as *', () => {
    expect(pagePath('/projects/12/documents/3')).toBe('/projects/12/documents/3')
    expect(pagePath('/')).toBe('/')
    expect(pagePath('/notes/the%20termination%20clause/')).toBe('/notes/*/')
    expect(pagePath(`/${'a'.repeat(300)}`)).toHaveLength(200)
  })
})
