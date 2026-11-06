import { create } from 'zustand'

type User = {
  id: string
  email: string
}

/** `admin` may change the library; `reader` may only browse, search and download. */
export type Role = 'admin' | 'reader'

type UserState = {
  user: User | null
  accessToken: string | null
  authLoading: boolean
  onboarded: boolean | null
  role: Role
  setUser: (user: User | null) => void
  setAccessToken: (token: string | null) => void
  setAuthLoading: (loading: boolean) => void
  setOnboarded: (onboarded: boolean) => void
  setRole: (role: Role) => void
  signOut: () => void
}

export const useUserStore = create<UserState>()((set) => ({
  user: null,
  accessToken: null,
  authLoading: true,
  onboarded: null,
  role: 'admin',
  setUser: (user) => set({ user }),
  setAccessToken: (accessToken) => set({ accessToken }),
  setAuthLoading: (authLoading) => set({ authLoading }),
  setOnboarded: (onboarded) => set({ onboarded }),
  setRole: (role) => set({ role }),
  signOut: () => set({ user: null, accessToken: null, authLoading: false, onboarded: null, role: 'admin' }),
}))
