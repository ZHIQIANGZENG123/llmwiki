// Central API base. Local mode uses a same-origin path so the Next server proxies /api to
// FastAPI: the session cookie stays same-origin, and one build works from any LAN address.
export const isLocalMode = process.env.NEXT_PUBLIC_MODE === 'local'

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || (isLocalMode ? '/api' : 'http://localhost:8000')

/** Join an API path onto the configured base, tolerating a trailing slash on the base. */
export function apiUrl(path: string): string {
  return `${API_BASE.replace(/\/+$/, '')}${path}`
}

/** Absolute URL of an API path; relative bases resolve against the current origin. */
export function absoluteApiUrl(path: string): string {
  const url = apiUrl(path)
  if (!url.startsWith('/')) return url
  return typeof window === 'undefined' ? url : `${window.location.origin}${url}`
}
