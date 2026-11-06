'use client'

import { useEffect, useRef, useState } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import { AppShell } from '@/components/layout/AppShell'
import { AuthProvider } from '@/components/auth/AuthProvider'
import { ChatDock } from '@/components/chat/ChatDock'
import { checkAuth } from '@/lib/auth-errors'
import { AuthUnavailable } from '@/components/auth/AuthUnavailable'
import { ONBOARDING_PREVIEW_ENABLED } from '@/lib/onboarding-preview'
import { apiUrl, isLocalMode as isLocal } from '@/lib/apiUrl'
import { useUserStore } from '@/stores'

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()

  if (ONBOARDING_PREVIEW_ENABLED && pathname === '/onboarding') {
    return <AppShell>{children}</AppShell>
  }

  if (isLocal) {
    return <LocalDashboard>{children}</LocalDashboard>
  }

  return <HostedDashboard>{children}</HostedDashboard>
}

/**
 * Local workspaces are single-user, but once exposed on a LAN they sit behind a shared
 * password: verify the session cookie before rendering anything that would 401.
 */
function LocalDashboard({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const pathname = usePathname()
  const [ready, setReady] = useState(false)
  const [unreachable, setUnreachable] = useState(false)
  const pathnameRef = useRef(pathname)

  useEffect(() => {
    pathnameRef.current = pathname
  }, [pathname])

  useEffect(() => {
    let cancelled = false
    setUnreachable(false)

    fetch(apiUrl('/v1/auth/status'), { credentials: 'include' })
      .then((res) => {
        if (!res.ok) throw new Error(`status ${res.status}`)
        return res.json() as Promise<{ required: boolean; authenticated: boolean; role?: string }>
      })
      .then((status) => {
        if (cancelled) return
        if (status.required && !status.authenticated) {
          const current = pathnameRef.current
          const returnTo = current && current !== '/wikis' ? `?returnTo=${encodeURIComponent(current)}` : ''
          router.replace(`/login${returnTo}`)
          return
        }
        useUserStore.getState().setRole(status.role === 'reader' ? 'reader' : 'admin')
        setReady(true)
      })
      .catch(() => {
        if (!cancelled) setUnreachable(true)
      })

    return () => { cancelled = true }
  }, [router])

  if (unreachable) {
    return (
      <AuthUnavailable
        title="Can’t reach the workspace"
        description="The workspace API did not answer. Check that the local server is still running, then reload this page."
      />
    )
  }
  if (!ready) return null

  return (
    <AuthProvider userId="local" email="local@localhost" accessToken="local">
      <AppShell>{children}</AppShell>
      <ChatDock />
    </AuthProvider>
  )
}

function HostedDashboard({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const pathname = usePathname()
  const [user, setUser] = useState<{ id: string; email: string; accessToken: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [authUnavailable, setAuthUnavailable] = useState(false)
  const pathnameRef = useRef(pathname)

  useEffect(() => {
    pathnameRef.current = pathname
  }, [pathname])

  useEffect(() => {
    let cancelled = false

    const bounceToLogin = async () => {
      try {
        const { createClient } = await import('@/lib/supabase/client')
        await createClient().auth.signOut({ scope: 'local' })
      } catch { /* signOut best-effort */ }
      const currentPath = pathnameRef.current
      const returnTo = currentPath !== '/wikis' ? `?returnTo=${encodeURIComponent(currentPath)}` : ''
      if (!cancelled) router.replace(`/login${returnTo}`)
    }

    import('@/lib/supabase/client').then(async ({ createClient }) => {
      if (cancelled) return
      const supabase = createClient()
      const auth = await checkAuth(supabase, bounceToLogin)
      if (cancelled) return
      if (auth.kind === 'signed_out') {
        await bounceToLogin()
        return
      }
      if (auth.kind === 'unavailable') {
        setAuthUnavailable(true)
        return
      }
      setUser({ id: auth.user.id, email: auth.user.email!, accessToken: auth.session.access_token })
      setLoading(false)
    }).catch(() => {
      if (!cancelled) setAuthUnavailable(true)
    })

    return () => { cancelled = true }
  }, [router])

  if (authUnavailable) return <AuthUnavailable />
  if (loading) return null

  return (
    <AuthProvider userId={user!.id} email={user!.email} accessToken={user!.accessToken}>
      <AppShell>{children}</AppShell>
    </AuthProvider>
  )
}
