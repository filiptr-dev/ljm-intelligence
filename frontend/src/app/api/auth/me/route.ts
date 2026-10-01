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

const BACKEND = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8765"
const COOKIE = "ljm_session"

export const dynamic = "force-dynamic"

export async function GET(req: Request) {
  const token = readCookie(req.headers.get("cookie"), COOKIE)
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
    const clear = [`${COOKIE}=`, "Path=/", "Max-Age=0", "HttpOnly", "Secure", "SameSite=Lax"].join("; ")
    return new Response(JSON.stringify({ authenticated: false }), {
      status: 401,
      headers: { "content-type": "application/json", "set-cookie": clear },
    })
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

function readCookie(header: string | null, name: string): string | null {
  if (!header) return null
  for (const part of header.split(";")) {
    const [k, ...rest] = part.trim().split("=")
    if (k === name) return decodeURIComponent(rest.join("="))
  }
  return null
}
