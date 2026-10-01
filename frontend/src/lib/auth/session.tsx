"use client"

/**
 * Session state — the one place client code reads "who's logged in."
 *
 * Cookie-only auth: the browser never holds the JWT. The HttpOnly
 * `ljm_session` cookie is the single source of truth for the browser's
 * authority; every data call goes through same-origin BFF routes
 * (`/api/auth/*` + `/api/proxy/<path>`) that read the cookie and attach
 * `Authorization: Bearer <jwt>` server-side. This module therefore has
 * no token to cache — it only mirrors `authenticated` and the user
 * profile, both derived from a single `/api/auth/me` round-trip.
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

export function SessionProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SessionState["status"]>("loading")
  const [user, setUser] = useState<SessionUser | null>(null)

  const reload = useCallback(async () => {
    try {
      const r = await fetch("/api/auth/me", { cache: "no-store", credentials: "same-origin" })
      if (!r.ok) {
        setUser(null)
        setStatus("unauthenticated")
        return
      }
      const data = (await r.json()) as { user: SessionUser }
      setUser(data.user ?? null)
      setStatus("authenticated")
    } catch {
      setUser(null)
      setStatus("unauthenticated")
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reload()
  }, [reload])

  const login = useCallback(
    async (email: string, password: string) => {
      const r = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email, password }),
        credentials: "same-origin",
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
      const data = (await r.json()) as { user?: SessionUser }
      if (data.user) {
        setUser(data.user)
        setStatus("authenticated")
      } else {
        await reload()
      }
      return { ok: true }
    },
    [reload],
  )

  const logout = useCallback(async () => {
    try {
      await fetch("/api/auth/logout", {
        method: "POST",
        cache: "no-store",
        credentials: "same-origin",
      })
    } catch {
      // ignore — the cookie clear is still issued by the BFF.
    }
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

/**
 * Back-compat shim. Pre-cookie-only, callers used this to grab the JWT in
 * JS and inject it as a bearer header. There is no JWT in JS anymore —
 * every data call goes same-origin and the BFF reads the HttpOnly cookie.
 *
 * @deprecated Use `fetch("/api/proxy/<backend-path>", { credentials: "same-origin" })`
 *   from the browser, or the server-only typed `api` from `@/lib/api/client`
 *   from a Server Component / Route Handler.
 */
export async function ensureAccessToken(): Promise<string | null> {
  return null
}

/** @deprecated See `ensureAccessToken` note. */
export function getAccessToken(): string | null {
  return null
}
