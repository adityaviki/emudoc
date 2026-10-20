// The time zones a member may choose for the dates in the mail Emudoc sends them (`time_zone`
// in backend/app/models.py, which the server checks against the IANA database). The pages date
// everything in the browser's own zone whatever was chosen.

/** The zone the browser runs in, '' when it names none. */
export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone ?? ''
  } catch {
    return ''
  }
}

/**
 * The zones to offer, UTC first and the rest by name. The browser's list leaves out UTC and
 * holds one name for each zone, which need not be the one another browser stored (Chrome lists
 * India's as Asia/Calcutta, Firefox as Asia/Kolkata), so the stored zone and the browser's own
 * are added when it lacks them.
 */
export function timeZoneChoices(stored: string, browser: string, listed: readonly string[] = listedZones()): string[] {
  const zones = new Set(listed)
  for (const zone of [stored, browser]) if (zone) zones.add(zone)
  zones.delete('UTC')
  return ['UTC', ...[...zones].sort()]
}

/** A zone as a person reads it: America/New York. */
export const zoneLabel = (zone: string) => zone.replaceAll('_', ' ')

function listedZones(): string[] {
  try {
    return Intl.supportedValuesOf('timeZone')
  } catch {
    return []
  }
}
