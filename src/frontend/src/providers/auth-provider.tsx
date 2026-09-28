import * as React from 'react'

import { api, type User } from '@/lib/api'

const STORAGE_KEY = 'cinema.auth.user'

interface AuthContextValue {
  isAuthenticated: boolean
  user: User | null
  /** Password is collected but not checked yet - the backend identifies users by email only. */
  login: (email: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = React.createContext<AuthContextValue | undefined>(undefined)

function readStoredUser(): User | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as User) : null
  } catch {
    return null
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = React.useState<User | null>(readStoredUser)

  const login = React.useCallback(async (email: string, _password: string) => {
    const loggedIn = await api.login(email)
    localStorage.setItem(STORAGE_KEY, JSON.stringify(loggedIn))
    setUser(loggedIn)
  }, [])

  const logout = React.useCallback(() => {
    localStorage.removeItem(STORAGE_KEY)
    setUser(null)
  }, [])

  const value = React.useMemo<AuthContextValue>(
    () => ({ isAuthenticated: user !== null, user, login, logout }),
    [user, login, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = React.useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider')
  return ctx
}
