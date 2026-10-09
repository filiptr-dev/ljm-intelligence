"use client"

/**
 * Login — the only unauthenticated page in the app.
 *
 * Thin UI: posts to `/api/auth/login` via the SessionProvider, then redirects
 * to `?next=…` (preserved from the middleware redirect) or `/`. Error
 * messages are the raw `detail` field from FastAPI — "invalid credentials"
 * on bad password, "backend unreachable" on BFF failure.
 */

import { useSearchParams } from "next/navigation"
import { useEffect, useRef, useState } from "react"
import { useSession } from "@/lib/auth/session"

export default function LoginPage() {
  const params = useSearchParams()
  const rawNext = params.get("next") || "/"
  // Same-origin paths only (blocks `//evil.com` open redirects).
  const next = rawNext.startsWith("/") && !rawNext.startsWith("//") ? rawNext : "/"
  const { status, login } = useSession()
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const redirecting = useRef(false)

  // Hard navigation, not router.replace(): the Next client router cache may
  // hold the proxy's earlier "/ -> /login" redirect (from a prefetch or the
  // first unauthenticated visit), so a soft nav lands back on /login until a
  // manual refresh. A full load always re-requests with the fresh cookie.
  function go() {
    if (redirecting.current) return
    redirecting.current = true
    window.location.replace(next)
  }

  useEffect(() => {
    if (status === "authenticated") go()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, next])

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const res = await login(email, password)
      if (!res.ok) {
        setError(res.detail || "login failed")
        return
      }
      go()
    } finally {
      if (!redirecting.current) setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm space-y-4 rounded-lg border border-border bg-card p-6 shadow-sm"
      >
        <div>
          <h1 className="text-xl font-semibold">LJM Intelligence</h1>
          <p className="text-sm text-muted-foreground">Sign in to continue.</p>
        </div>
        <div className="space-y-1">
          <label htmlFor="email" className="text-sm font-medium">
            Email
          </label>
          <input
            id="email"
            type="text"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded border border-input bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <div className="space-y-1">
          <label htmlFor="password" className="text-sm font-medium">
            Password
          </label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded border border-input bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        {error && (
          <div role="alert" className="text-sm text-destructive">
            {error}
          </div>
        )}
        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  )
}
