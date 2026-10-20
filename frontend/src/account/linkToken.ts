import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'

/**
 * The token of the one-time link that opened the page. The link carries it in the fragment
 * (`#token=…`, `mailed_link` in backend/app/api/auth.py), which the browser never sends, so it
 * lands in no server's or proxy's log and in no Referer; a link mailed before that carries it in
 * the query. Either way it leaves the address at once, so that the history and a copied address
 * hold none.
 *
 * A page that sends the user away to sign in first and expects them back, as an invitation's
 * does, passes `keep`: the token is kept in the tab's session storage under that name, and found
 * there when the page opens again without one, until `forgetLinkToken` drops it.
 */
export function useLinkToken(keep?: string): string {
  const location = useLocation()
  const navigate = useNavigate()
  const [token] = useState(() => {
    const found =
      new URLSearchParams(location.hash.slice(1)).get('token') ?? new URLSearchParams(location.search).get('token')
    if (!keep) return found ?? ''
    try {
      if (found) sessionStorage.setItem(keep, found)
      return found ?? sessionStorage.getItem(keep) ?? ''
    } catch {
      // Storage can be blocked: the token still works on this visit.
      return found ?? ''
    }
  })
  useEffect(() => {
    if (location.hash || location.search) navigate(location.pathname, { replace: true })
  }, [location, navigate])
  return token
}

/** Drop a token `useLinkToken(keep)` kept, once its link has done its work. */
export function forgetLinkToken(keep: string) {
  try {
    sessionStorage.removeItem(keep)
  } catch {
    // Nothing was kept where nothing can be.
  }
}
