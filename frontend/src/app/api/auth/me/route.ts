/**
 * Session bootstrap BFF — validates the HttpOnly session cookie against
 * FastAPI `/auth/me` and returns `{authenticated, user}` only.
 *
 * The access token no longer crosses the BFF→browser boundary (cookie-only
 * auth): the JWT stays in the HttpOnly cookie, and every data call from the
 * browser goes through `/api/proxy/<path>` which pulls the token out of the
 * cookie server-side. The client never holds a bearer, so there's nothing
 * to leak to XSS and nothing to echo back here.
 *
 * On 401 from FastAPI we clear the cookie so the next request from this
 * browser doesn't re-send a dead token.
 */

import { readSessionCookie, SESSION_COOKIE } from "@/lib/auth/bff"

const BACKEND = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8765"

export const dynamic = "force-dynamic"

export async function GET(req: Request) {
  const token = readSessionCookie(req)
  if (!token) {
    return Response.json({ authenticated: false }, { status: 401 })
  }
  let upstream: Response
  try {
    upstream = await fetch(`${BACKEND}/auth/me`, {
      headers: { authorization: `Bearer ${token}` },
      cache: "no-store",
    })
  } catch (e) {
    return Response.json({ detail: "backend unreachable", error: String(e) }, { status: 502 })
  }
  if (upstream.status === 401) {
    const base = [`${SESSION_COOKIE}=`, "Path=/", "Max-Age=0", "HttpOnly", "SameSite=Lax"]
    const headers = new Headers({ "content-type": "application/json" })
    headers.append("set-cookie", base.join("; "))
    headers.append("set-cookie", [...base, "Secure"].join("; "))
    return new Response(JSON.stringify({ authenticated: false }), { status: 401, headers })
  }
  const text = await upstream.text()
  if (!upstream.ok) {
    return new Response(text, { status: upstream.status, headers: { "content-type": "application/json" } })
  }
  let user: unknown
  try {
    user = JSON.parse(text)
  } catch {
    return Response.json({ detail: "bad backend response" }, { status: 502 })
  }
  // NOTE: `access_token` deliberately omitted. The browser must not learn it.
  return Response.json({ authenticated: true, user })
}
