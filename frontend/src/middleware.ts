/**
 * Edge middleware — redirects unauthenticated visits to /login.
 *
 * This is a UX guard, not a security guard. The real fence is `Depends(current_user)`
 * on every protected FastAPI route. The middleware exists so a user who
 * loses their session cookie doesn't get a half-broken dashboard full of
 * 401 flashes before the client-side session gate kicks in.
 *
 * Allowlist:
 *  - `/login`         — the only page that must render unauthenticated.
 *  - `/api/auth/*`    — the three BFF auth routes (login/logout/me).
 *  - `/_next/*`       — Next assets.
 *  - `/favicon*`      — asset.
 *
 * Everything else: if there's no `ljm_session` cookie, redirect to
 * `/login?next=<path>`. We don't try to validate the cookie here (that
 * would require importing JWT libs into the edge runtime) — the backend
 * answers 401 on a bad token, which the session gate handles.
 */

import { NextRequest, NextResponse } from "next/server"

const COOKIE = "ljm_session"

const PUBLIC_PREFIXES = ["/login", "/api/auth/", "/_next/", "/favicon"]

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl
  if (PUBLIC_PREFIXES.some((p) => pathname === p || pathname.startsWith(p))) {
    return NextResponse.next()
  }
  const token = req.cookies.get(COOKIE)?.value
  if (token) return NextResponse.next()
  const url = req.nextUrl.clone()
  url.pathname = "/login"
  url.search = `?next=${encodeURIComponent(pathname + req.nextUrl.search)}`
  return NextResponse.redirect(url)
}

export const config = {
  // Skip static assets; the matcher mirrors the Next 15/16 recommendation.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)"],
}
