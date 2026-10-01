/**
 * Shared BFF helpers — one place that knows the session cookie's name and how
 * to turn it into an upstream `Authorization: Bearer <jwt>` header.
 *
 * Every `/api/*` Route Handler that proxies to the FastAPI backend uses these
 * two functions. Repeating the readCookie / authHeaders boilerplate per route
 * is how the `/api/settings` proxy originally shipped WITHOUT auth forwarding
 * for weeks — one copy, one place to notice, one place to fix.
 *
 * The cookie name lives here as the single source of truth; keep it in sync
 * with `middleware.ts` + the `/api/auth/*` routes (search for `SESSION_COOKIE`).
 */

export const SESSION_COOKIE = "ljm_session"

/** Pull one named cookie's value out of a raw `Cookie:` header. */
export function readSessionCookie(req: Request): string | null {
  const header = req.headers.get("cookie")
  if (!header) return null
  for (const part of header.split(";")) {
    const [k, ...rest] = part.trim().split("=")
    if (k === SESSION_COOKIE) return decodeURIComponent(rest.join("="))
  }
  return null
}

/**
 * Build the `HeadersInit` for an upstream fetch: merges caller-supplied headers
 * with `Authorization: Bearer <session jwt>` when a session cookie is present.
 * When there's no cookie we send the upstream call anyway and let the backend
 * answer 401 — the typed client / BFF caller surfaces that as a toast / redirect.
 */
export function authHeaders(req: Request, extra: HeadersInit = {}): HeadersInit {
  const token = readSessionCookie(req)
  const h: Record<string, string> = { ...(extra as Record<string, string>) }
  if (token) h.authorization = `Bearer ${token}`
  return h
}
