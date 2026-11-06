import { apiUrl } from '@/lib/apiUrl'

/**
 * Clear the local-mode session cookie.
 *
 * Local mode used to skip this entirely — it only reset the client store, so the signed
 * cookie stayed valid and a reload logged the user straight back in.
 */
export async function logoutLocal(): Promise<void> {
  try {
    await fetch(apiUrl('/v1/auth/logout'), { method: 'POST', credentials: 'include' })
  } catch {
    // A failed logout still clears client state below; the cookie expires on its own.
  }
}
