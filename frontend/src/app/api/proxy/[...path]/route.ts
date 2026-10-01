/**
 * Generic BFF proxy — the one and only client-island data path.
 *
 * Client islands (`"use client"`) can no longer read the backend JWT: it
 * lives in the HttpOnly `ljm_session` cookie this origin plants at login,
 * and nothing in `document.cookie` reveals it. This handler is how an
 * island reaches the FastAPI backend: the browser hits
 * `/api/proxy/<backend-path>` same-origin, we read the HttpOnly cookie,
 * attach `Authorization: Bearer <jwt>` upstream, and stream the response
 * back. The browser never holds the token.
 *
 * All HTTP verbs are supported (we delegate `GET` / `POST` / `PUT` / `PATCH`
 * / `DELETE` to one `handler`). Query strings pass through. Request bodies
 * stream through as raw bytes — no JSON re-serialisation, so file uploads
 * and arbitrary content-types work. Response headers are copied minus
 * `set-cookie` / `transfer-encoding` which would be meaningless on the
 * echoed response.
 *
 * Keep this handler stupid: no business logic, no shape rewriting. The
 * typed `openapi-fetch` client on the server + the islands' direct fetches
 * both talk to the same backend contract.
 */

import { readSessionCookie } from "@/lib/auth/bff"

const BACKEND = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8765"

export const dynamic = "force-dynamic"

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailers",
  "transfer-encoding",
  "upgrade",
  "set-cookie",
])

async function handler(req: Request, ctx: { params: Promise<{ path: string[] }> }): Promise<Response> {
  const { path } = await ctx.params
  const token = readSessionCookie(req)
  if (!token) {
    return Response.json({ detail: "unauthenticated" }, { status: 401 })
  }
  const url = new URL(req.url)
  const target = `${BACKEND.replace(/\/$/, "")}/${(path || []).join("/")}${url.search}`

  const headers = new Headers()
  // Forward safe request headers; drop host / cookie / authorization and let
  // us set them.
  for (const [k, v] of req.headers.entries()) {
    const lk = k.toLowerCase()
    if (lk === "host" || lk === "cookie" || lk === "authorization" || HOP_BY_HOP.has(lk)) continue
    headers.set(k, v)
  }
  headers.set("authorization", `Bearer ${token}`)

  const method = req.method.toUpperCase()

  // Server-side secret injection — a tiny, well-known allowlist.
  //
  // `POST /crawl/run` on the FastAPI backend is gated by `X-Cron-Secret`
  // (not the bearer) — it's the owner trigger that used to live in the
  // deleted `/api/crawler/run` BFF route. The secret is a server-only env
  // var; the browser session cookie proves the user is authenticated, and
  // we attach the secret here so an island can call `api.POST("/crawl/run")`
  // through the generic proxy without the UI ever learning the secret.
  const joinedPath = (path || []).join("/")
  if (joinedPath === "crawl/run" && method === "POST") {
    const cronSecret = process.env.CRON_SECRET || ""
    if (cronSecret) headers.set("x-cron-secret", cronSecret)
  }
  const init: RequestInit = { method, headers, cache: "no-store" }
  if (method !== "GET" && method !== "HEAD") {
    // Pass the body through verbatim.
    init.body = await req.arrayBuffer()
    // duplex is required when a stream body is used; a buffer body is fine
    // without it but Node 20+ accepts the flag.
  }

  let upstream: Response
  try {
    upstream = await fetch(target, init)
  } catch (e) {
    return Response.json({ detail: "backend unreachable", error: String(e) }, { status: 502 })
  }

  const outHeaders = new Headers()
  for (const [k, v] of upstream.headers.entries()) {
    if (HOP_BY_HOP.has(k.toLowerCase())) continue
    outHeaders.set(k, v)
  }
  return new Response(upstream.body, { status: upstream.status, headers: outHeaders })
}

export const GET = handler
export const POST = handler
export const PUT = handler
export const PATCH = handler
export const DELETE = handler
