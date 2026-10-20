import { describe, expect, it } from 'vitest'
import { browserTimeZone, timeZoneChoices, zoneLabel } from './timeZones.ts'

describe('timeZoneChoices', () => {
  it('offers UTC first and the listed zones by name', () => {
    expect(timeZoneChoices('', '', ['Europe/Berlin', 'America/New_York', 'UTC'])).toEqual([
      'UTC',
      'America/New_York',
      'Europe/Berlin',
    ])
  })

  it('keeps the stored zone and the one the browser runs in when the list names them otherwise', () => {
    const choices = timeZoneChoices('Asia/Kolkata', 'Asia/Calcutta', ['Asia/Tokyo'])
    expect(choices).toEqual(['UTC', 'Asia/Calcutta', 'Asia/Kolkata', 'Asia/Tokyo'])
    expect(timeZoneChoices('Asia/Tokyo', 'Asia/Tokyo', ['Asia/Tokyo'])).toEqual(['UTC', 'Asia/Tokyo'])
  })

  it('reads the list from the runtime', () => {
    const choices = timeZoneChoices('', browserTimeZone())
    expect(choices[0]).toBe('UTC')
    expect(choices.length).toBeGreaterThan(300)
    expect(choices).toContain('Europe/Berlin')
  })
})

describe('zoneLabel', () => {
  it('spells the underscores as spaces', () => {
    expect(zoneLabel('America/Port_of_Spain')).toBe('America/Port of Spain')
  })
})
