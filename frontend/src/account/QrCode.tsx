import { useMemo } from 'react'
import { encode } from 'uqr'

/** A QR code for a short text, such as the otpauth URL an authenticator app scans. */
export function QrCode({ value, label, className = 'size-40' }: { value: string; label: string; className?: string }) {
  const { size, path } = useMemo(() => {
    const code = encode(value, { border: 2, ecc: 'M' })
    let path = ''
    code.data.forEach((row, y) => {
      row.forEach((dark, x) => {
        if (dark) path += `M${x} ${y}h1v1h-1z`
      })
    })
    return { size: code.size, path }
  }, [value])
  // Scanners want dark modules on a light ground, so this stays black on white in dark mode too.
  return (
    <svg viewBox={`0 0 ${size} ${size}`} shapeRendering="crispEdges" role="img" aria-label={label} className={`rounded-md bg-white ${className}`}>
      <path d={path} fill="#000" />
    </svg>
  )
}
