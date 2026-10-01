/**
 * Logout BFF — clears the first-party session cookie. The backend JWT is
 * stateless so there's nothing to revoke server-side in v1 (dropped per plan
 * amendments; a revocation table is the obvious upgrade when staff accounts
 * land). We still call the backend `/auth/logout` best-effort so a future
 * server-side revocation lands transparently when it ships.
 *
 * Belt-and-braces clear: we emit TWO `Set-Cookie` headers — one with `Secure`,
 * one without — so a stale cookie from a prior deploy that stored it with a
 * different attribute set is matched and deleted regardless. Cookies are keyed
 * on (name, domain, path) so this never duplicates; the browser just gets
 * two no-op deletes if only one matches.
 */

import { readSessionCookie, SESSION_COOKIE } from "@/lib/auth/bff"

const BACKEND = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8765"

export const dynamic = "force-dynamic"

export async function POST(req: Request) {
  const token = readSessionCookie(req)
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
  const base = [`${SESSION_COOKIE}=`, "Path=/", "Max-Age=0", "HttpOnly", "SameSite=Lax"]
  const clears = [base.join("; "), [...base, "Secure"].join("; ")]
  const headers = new Headers({ "content-type": "application/json" })
  for (const c of clears) headers.append("set-cookie", c)
  return new Response(JSON.stringify({ ok: true }), { status: 200, headers })
}
