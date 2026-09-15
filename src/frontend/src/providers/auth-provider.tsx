import * as React from 'react'

const STORAGE_KEY = 'cinema.auth.token'

interface AuthContextValue {
  isAuthenticated: boolean
  /**
   * Placeholder until the real backend exists. Stores a fake token so
   * routing/guard logic can be built now; swap the body for a real API call
   * later without touching anything that consumes this context.
   */
  login: (email: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = React.createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = React.useState<string | null>(() =>
    localStorage.getItem(STORAGE_KEY),
  )

  const login = React.useCallback(async (email: string, _password: string) => {
    // TODO: replace with a real POST /auth/login once the backend exists.
    const fakeToken = `dev.${btoa(email)}`
    localStorage.setItem(STORAGE_KEY, fakeToken)
    setToken(fakeToken)
  }, [])

  const logout = React.useCallback(() => {
    localStorage.removeItem(STORAGE_KEY)
    setToken(null)
  }, [])

  const value = React.useMemo<AuthContextValue>(
    () => ({ isAuthenticated: token !== null, login, logout }),
    [token, login, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = React.useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider')
  return ctx
}
