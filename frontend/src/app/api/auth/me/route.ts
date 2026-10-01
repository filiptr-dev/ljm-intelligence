/**
 * Session bootstrap BFF — reads the HttpOnly session cookie and (a) proxies
 * it to FastAPI `/auth/me` to validate, (b) echoes the token back to the
 * browser JS so the typed `openapi-fetch` client can inject `Authorization`
 * on every data call.
 *
 * Returning the token to JS memory re-exposes it to XSS; that's an accepted
 * trade-off for v1 (same posture as the plan's "bearer in JS memory + first-
 * party cookie" option B). A stricter upgrade would proxy every data route
 * through BFFs, trading that for a lot of handler code.
 */

import { readSessionCookie, SESSION_COOKIE } from "@/lib/auth/bff"

const BACKEND = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8765"

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
    // Expired / revoked / mis-issued token → clear the cookie so the next
    // request from this browser doesn't re-attempt the same dead token.
    // Match the login route's Secure attribute so the clear takes.
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
  return Response.json({ authenticated: true, user, access_token: token })
}
