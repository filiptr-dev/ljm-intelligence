"use client"

/**
 * Session state — the one place client code reads "who's logged in."
 *
 * On mount we fetch `/api/auth/me`. The BFF reads the HttpOnly cookie, calls
 * the backend `/auth/me`, and (on success) echoes the user shape AND the
 * access token back to JS so the typed openapi-fetch client can inject
 * `Authorization: Bearer …` on every data call. We stash that token in a
 * module-level ref so the client (which is instantiated once at import
 * time) can reach it without threading context through every call site.
 *
 * `useSession()` is the component-facing hook. `getAccessToken()` is the
 * module-level accessor used by `lib/api/client.ts`.
 */

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react"

export interface SessionUser {
  id: string
  email: string
  name: string
  role: string
}

interface SessionState {
  status: "loading" | "authenticated" | "unauthenticated"
  user: SessionUser | null
  login: (email: string, password: string) => Promise<{ ok: boolean; detail?: string }>
  logout: () => Promise<void>
  reload: () => Promise<void>
}

const SessionContext = createContext<SessionState | null>(null)

// Module-level token ref so the typed API client can read it without a hook.
let _accessToken: string | null = null
export function getAccessToken(): string | null {
  return _accessToken
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SessionState["status"]>("loading")
  const [user, setUser] = useState<SessionUser | null>(null)

  const reload = useCallback(async () => {
    try {
      const r = await fetch("/api/auth/me", { cache: "no-store" })
      if (!r.ok) {
        _accessToken = null
        setUser(null)
        setStatus("unauthenticated")
        return
      }
      const data = (await r.json()) as { user: SessionUser; access_token: string }
      _accessToken = data.access_token
      setUser(data.user)
      setStatus("authenticated")
    } catch {
      _accessToken = null
      setUser(null)
      setStatus("unauthenticated")
    }
  }, [])

  useEffect(() => {
    // Session bootstrap: hit /api/auth/me once on mount. The lint rule
    // "setState in effect" is designed to catch cascading re-renders from
    // sync setState; the state updates inside `reload` happen on a resolved
    // Promise and are the whole point of the effect (synchronising external
    // state — the HttpOnly cookie — into React).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reload()
  }, [reload])

  const login = useCallback(async (email: string, password: string) => {
    const r = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, password }),
    })
    if (!r.ok) {
      let detail = `login failed (${r.status})`
      try {
        const body = (await r.json()) as { detail?: string }
        if (typeof body.detail === "string") detail = body.detail
      } catch {
        // ignore
      }
      return { ok: false, detail }
    }
    const data = (await r.json()) as { user: SessionUser; access_token: string }
    _accessToken = data.access_token
    setUser(data.user)
    setStatus("authenticated")
    return { ok: true }
  }, [])

  const logout = useCallback(async () => {
    try {
      await fetch("/api/auth/logout", { method: "POST" })
    } catch {
      // ignore — the cookie is cleared regardless
    }
    _accessToken = null
    setUser(null)
    setStatus("unauthenticated")
  }, [])

  const value: SessionState = { status, user, login, logout, reload }
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionState {
  const ctx = useContext(SessionContext)
  if (!ctx) throw new Error("useSession must be used inside <SessionProvider>")
  return ctx
}
