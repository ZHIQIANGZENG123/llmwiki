import type { Metadata } from 'next'
import { LocalLoginForm } from './LocalLoginForm'
import { LoginForm } from './LoginForm'

export const metadata: Metadata = {
  title: 'Sign In | LLM Wiki',
  description: 'Sign in to LLM Wiki to manage your knowledge bases and wikis.',
  openGraph: {
    title: 'Sign In | LLM Wiki',
    description: 'Sign in to LLM Wiki to manage your knowledge bases and wikis.',
  },
}

export default function LoginPage() {
  // Local workspaces authenticate with one shared password, not Supabase/Google.
  if (process.env.NEXT_PUBLIC_MODE === 'local') return <LocalLoginForm />
  return <LoginForm />
}
