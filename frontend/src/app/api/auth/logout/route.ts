/**
 * Logout BFF — clears the first-party session cookie. The backend JWT is
 * stateless so there's nothing to revoke server-side in v1 (dropped per plan
 * amendments; a revocation table is the obvious upgrade when staff accounts
 * land). We still call the backend `/auth/logout` best-effort so a future
 * server-side revocation lands transparently when it ships.
 */

const BACKEND = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8765"
const COOKIE = "ljm_session"

export const dynamic = "force-dynamic"

export async function POST(req: Request) {
  const token = readCookie(req.headers.get("cookie"), COOKIE)
  if (token) {
    try {
      await fetch(`${BACKEND}/auth/logout`, {
        method: "POST",
        headers: { authorization: `Bearer ${token}` },
        cache: "no-store",
      })
    } catch {
      // Best-effort; the local cookie clear below is the authoritative action.
    }
  }
  // Max-Age=0 instructs the browser to delete immediately.
  const clear = [`${COOKIE}=`, "Path=/", "Max-Age=0", "HttpOnly", "Secure", "SameSite=Lax"].join("; ")
  return new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { "content-type": "application/json", "set-cookie": clear },
  })
}

function readCookie(header: string | null, name: string): string | null {
  if (!header) return null
  for (const part of header.split(";")) {
    const [k, ...rest] = part.trim().split("=")
    if (k === name) return decodeURIComponent(rest.join("="))
  }
  return null
}
