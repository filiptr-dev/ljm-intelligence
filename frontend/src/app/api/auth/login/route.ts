/**
 * Login BFF — forwards credentials to the FastAPI `/auth/login`, then (on 200)
 * sets a first-party HttpOnly session cookie on the Vercel origin so the
 * browser carries session identity across reloads without ever persisting the
 * JWT in `localStorage` (which is XSS-readable).
 *
 * Why BFF: the API is on `*.onrender.com` (different registrable domain), so
 * a cross-site auth cookie there would be fragile (SameSite=None required,
 * Safari ITP, public-suffix). A first-party cookie here owned by this
 * origin is the simplest robust answer. We reuse `NEXT_PUBLIC_API_URL`
 * server-side (readable in both contexts) — no new env var per plan
 * amendment 1.
 *
 * Cookie attributes: HttpOnly + Secure + SameSite=Lax, always. Modern Chrome
 * (M89+), Firefox, and Safari all treat `http://localhost` and `http://127.0.0.1`
 * as potentially-trustworthy origins and accept `Secure` cookies there — a
 * stricter dev default is safer than special-casing production.
 */

import { SESSION_COOKIE } from "@/lib/auth/bff"

const BACKEND = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8765"
// MF2 — shared secret between this BFF and FastAPI's rate-limiter trust layer.
// On a BFF every request to the backend arrives from Vercel's IP pool; a
// 5/min per-IP limit on that IP would lock every user out globally. The
// backend only trusts the forwarded ``X-LJM-Client-IP`` header when this
// secret matches, so an attacker who guesses the header name alone can't
// bypass the per-IP guard. Server-only env var — never exposed to the
// browser.
const PROXY_SECRET = process.env.LJM_PROXY_SECRET || ""
const COOKIE_MAX_AGE_S = 60 * 60 * 24 * 7 // 7d — matches backend auth_access_ttl_days default.

function clientIpFrom(req: Request): string {
  // Vercel's edge adds ``x-forwarded-for`` with the real client IP as the
  // leftmost entry (one hop upstream from this server). We prefer the
  // more explicit ``x-real-ip`` when present.
  const real = req.headers.get("x-real-ip")
  if (real) return real.trim()
  const xff = req.headers.get("x-forwarded-for")
  if (xff) {
    const first = xff.split(",")[0]?.trim()
    if (first) return first
  }
  return ""
}

export const dynamic = "force-dynamic"

export async function POST(req: Request) {
  const body = await req.text()
  const headers: Record<string, string> = { "content-type": "application/json" }
  const clientIp = clientIpFrom(req)
  if (PROXY_SECRET && clientIp) {
    headers["x-ljm-client-ip"] = clientIp
    headers["x-ljm-proxy-secret"] = PROXY_SECRET
  }
  let upstream: Response
  try {
    upstream = await fetch(`${BACKEND}/auth/login`, {
      method: "POST",
      headers,
      body,
      cache: "no-store",
    })
  } catch (e) {
    return Response.json({ detail: "backend unreachable", error: String(e) }, { status: 502 })
  }

  const text = await upstream.text()
  if (!upstream.ok) {
    return new Response(text, {
      status: upstream.status,
      headers: { "content-type": "application/json" },
    })
  }

  let data: { access_token?: string; expires_in?: number } = {}
  try {
    data = JSON.parse(text)
  } catch {
    return Response.json({ detail: "bad backend response" }, { status: 502 })
  }

  if (!data.access_token) {
    return Response.json({ detail: "no access_token in response" }, { status: 502 })
  }

  const maxAge = Math.min(data.expires_in ?? COOKIE_MAX_AGE_S, COOKIE_MAX_AGE_S)
  const cookie = [
    `${SESSION_COOKIE}=${encodeURIComponent(data.access_token)}`,
    "Path=/",
    `Max-Age=${maxAge}`,
    "HttpOnly",
    "Secure",
    "SameSite=Lax",
  ].join("; ")

  // Fetch user profile with the fresh token so the browser can render the
  // signed-in chrome without ever learning the token itself.
  let user: unknown = null
  try {
    const me = await fetch(`${BACKEND}/auth/me`, {
      headers: { authorization: `Bearer ${data.access_token}` },
      cache: "no-store",
    })
    if (me.ok) user = await me.json()
  } catch {
    // Non-fatal: the SessionProvider will refetch /api/auth/me on mount.
  }

  // NOTE: `access_token` is intentionally stripped from the response body.
  // The browser never sees the JWT (cookie-only auth).
  return new Response(JSON.stringify({ ok: true, user }), {
    status: 200,
    headers: {
      "content-type": "application/json",
      "set-cookie": cookie,
    },
  })
}
