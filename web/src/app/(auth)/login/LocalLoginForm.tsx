'use client'

import { Suspense, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { apiUrl } from '@/lib/apiUrl'
import { safeReturnPath } from '@/lib/returnTo'

function LocalLoginFormInner() {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const router = useRouter()
  const searchParams = useSearchParams()
  const returnTo = searchParams.get('returnTo')

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError('')

    try {
      const res = await fetch(apiUrl('/v1/auth/login'), {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      })
      if (res.status === 401) {
        setError('Incorrect password.')
        return
      }
      if (!res.ok) {
        setError(`Sign in failed (${res.status}).`)
        return
      }
      router.replace(safeReturnPath(returnTo))
      router.refresh()
    } catch {
      setError('Could not reach the workspace. Check that the server is running.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-8">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center">
          <h1 className="text-2xl font-bold">Sign in to LLM Wiki</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            This workspace is shared on your network, so it needs the workspace password.
          </p>
        </div>

        <form onSubmit={handleLogin} className="space-y-4">
          <div>
            <label htmlFor="workspace-password" className="block text-sm font-medium">
              Workspace password
            </label>
            <input
              id="workspace-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 block w-full rounded-lg border border-input bg-background px-3 py-2 text-sm"
              required
            />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <button
            type="submit"
            disabled={loading || !password}
            className="w-full rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
          >
            {loading ? 'Signing in...' : 'Sign in'}
          </button>
        </form>

        <p className="text-center text-xs text-muted-foreground/60">
          The password is the <code className="font-mono">LOCAL_AUTH_TOKEN</code> set when the
          workspace server was started. Ask whoever runs it if you do not have it.
        </p>
      </div>
    </div>
  )
}

export function LocalLoginForm() {
  return (
    <Suspense>
      <LocalLoginFormInner />
    </Suspense>
  )
}
