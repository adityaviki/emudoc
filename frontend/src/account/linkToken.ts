import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'

/**
 * The token of the one-time link that opened the page. The link carries it in the fragment
 * (`#token=…`, `_link` in backend/app/api/auth.py), which the browser never sends, so it lands in
 * no server's or proxy's log and in no Referer; a link mailed before that carries it in the query.
 * Either way it leaves the address at once, so that the history and a copied address hold none.
 */
export function useLinkToken(): string {
  const location = useLocation()
  const navigate = useNavigate()
  const [token] = useState(
    () =>
      new URLSearchParams(location.hash.slice(1)).get('token') ??
      new URLSearchParams(location.search).get('token') ??
      '',
  )
  useEffect(() => {
    if (location.hash || location.search) navigate(location.pathname, { replace: true })
  }, [location, navigate])
  return token
}
