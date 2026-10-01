"use client"

/**
 * Session state — the one place client code reads "who's logged in."
 *
 * The architecture
 * ----------------
 * The browser's authority on "who am I" is the HttpOnly `ljm_session` cookie,
 * which the login BFF plants and the `/api/auth/me` BFF reads to echo the
 * backend JWT back to JS. We hold the token in `window.__ljmAccessToken` — a
 * single property on the global object — because module-level `let` state is
 * unreliable under Next.js App Router + Turbopack HMR: on a client-side route
 * change the dev server can re-execute client modules, which resets any
 * module-level `let` back to its initializer. (Verified via a Playwright
 * reproduction: after login + nav to /settings the typed client saw a null
 * token even though `login()` had just set it.) The `window` property
 * survives route changes because the global object isn't re-created.
 *
 * The typed openapi-fetch client (`lib/api/client.ts`) doesn't read that
 * property directly either — it calls `ensureAccessToken()`, which:
 *   1. Returns the window token if we have one.
 *   2. Otherwise, calls `/api/auth/me` ONCE (deduped via an in-flight
 *      Promise), stashes the token on `window`, returns it.
 *   3. Returns null if /me 401s (unauthenticated).
 *
 * This way there's exactly one place that answers "give me a bearer right
 * now" and it's always correct — no race, no stale module state. The cookie
 * is the ground truth; `window.__ljmAccessToken` is a fast-path cache.
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

// Global token store. We tag the window object so there's one authoritative
// slot per browser tab, no matter how many times client modules are re-evaled.
interface TokenHost {
  __ljmAccessToken?: string | null
  __ljmTokenInFlight?: Promise<string | null> | null
}
function host(): TokenHost | null {
  return typeof window === "undefined" ? null : (window as unknown as TokenHost)
}

/** Synchronous peek — returns the cached token or null. For code that needs
 *  the token NOW and knows the caller will have hydrated it (e.g. after
 *  `ensureAccessToken` resolves). */
export function getAccessToken(): string | null {
  return host()?.__ljmAccessToken ?? null
}

/** Set the cached token (or clear it with `null`). Used by `login()` + the
 *  SessionProvider bootstrap + `logout()`. */
function setCachedToken(token: string | null) {
  const h = host()
  if (!h) return
  h.__ljmAccessToken = token
}

/**
 * The single source of truth for "give me a bearer right now."
 *
 * Returns the cached token if we have one. If not, calls /api/auth/me once
 * (deduped via `__ljmTokenInFlight`) and caches the result. Returns null if
 * the browser isn't authenticated — the caller lets the backend 401 and the
 * middleware/SessionGate handles the redirect to /login.
 */
export async function ensureAccessToken(): Promise<string | null> {
  const h = host()
  if (!h) return null
  if (typeof h.__ljmAccessToken === "string" && h.__ljmAccessToken.length > 0) {
    return h.__ljmAccessToken
  }
  if (h.__ljmTokenInFlight) return h.__ljmTokenInFlight
  h.__ljmTokenInFlight = (async () => {
    try {
      const r = await fetch("/api/auth/me", { cache: "no-store", credentials: "same-origin" })
      if (!r.ok) {
        setCachedToken(null)
        return null
      }
      const data = (await r.json()) as { access_token?: string }
      const t = data.access_token ?? null
      setCachedToken(t)
      return t
    } catch {
      setCachedToken(null)
      return null
    } finally {
      // Clear the in-flight slot so a subsequent call after an expired cache
      // kicks off a fresh fetch instead of returning the (now-settled) promise.
      const h2 = host()
      if (h2) h2.__ljmTokenInFlight = null
    }
  })()
  return h.__ljmTokenInFlight
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SessionState["status"]>("loading")
  const [user, setUser] = useState<SessionUser | null>(null)

  const reload = useCallback(async () => {
    try {
      const r = await fetch("/api/auth/me", { cache: "no-store" })
      if (!r.ok) {
        setCachedToken(null)
        setUser(null)
        setStatus("unauthenticated")
        return
      }
      const data = (await r.json()) as { user: SessionUser; access_token: string }
      setCachedToken(data.access_token)
      setUser(data.user)
      setStatus("authenticated")
    } catch {
      setCachedToken(null)
      setUser(null)
      setStatus("unauthenticated")
    }
  }, [])


  useEffect(() => {
    // Session bootstrap: hit /api/auth/me once on mount. State updates land
    // in the resolved Promise, not the effect body.
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
    setCachedToken(data.access_token)
    setUser(data.user)
    setStatus("authenticated")
    return { ok: true }
  }, [])

  const logout = useCallback(async () => {
    // Null the cache FIRST so any concurrent typed-client call grabs no bearer
    // instead of the one we're about to invalidate server-side.
    setCachedToken(null)
    try {
      await fetch("/api/auth/logout", { method: "POST", cache: "no-store" })
    } catch {
      // ignore — the cookie is cleared regardless
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
