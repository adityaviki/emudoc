import { describe, expect, it } from 'vitest'
import { ApiError, Unreachable } from './api.ts'
import { describeConnection, uploadCutOff, uploadWaiting } from './connection.ts'

describe('describeConnection', () => {
  it('says nothing online, and offline what waits for the connection', () => {
    expect(describeConnection(true)).toBeNull()
    expect(describeConnection(false)).toEqual({
      label: 'Offline',
      detail:
        'Your browser is offline. The page keeps what it showed; uploads start, and answers being written go on showing, once the connection is back.',
    })
  })
})

describe('uploadWaiting', () => {
  it('counts the files that wait', () => {
    expect(uploadWaiting(1)).toBe('Your browser is offline: the upload of 1 file starts once the connection is back.')
    expect(uploadWaiting(1200)).toBe('Your browser is offline: the upload of 1,200 files starts once the connection is back.')
  })
})

describe('uploadCutOff', () => {
  it('words an upload that reached no server, whether the browser knew it was offline or not', () => {
    expect(uploadCutOff(new Unreachable(false), 3)).toBe(
      'The upload of 3 files did not get through. Emudoc did not answer. Check your connection, then try again.',
    )
    expect(uploadCutOff(new Unreachable(true), 1)).toBe(
      'The upload of 1 file did not get through. Your browser is offline. Try again once the connection is back.',
    )
  })

  it('leaves a refusal to the notice every other one is shown in', () => {
    expect(uploadCutOff(new ApiError(413, 'Upload at most 50 files at once.'), 60)).toBeNull()
    expect(uploadCutOff(new ApiError(502, 'Bad Gateway'), 3)).toBeNull()
    expect(uploadCutOff(null, 3)).toBeNull()
  })
})
