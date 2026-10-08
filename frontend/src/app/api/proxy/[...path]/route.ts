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
 *
 * Stripped on the way back: `set-cookie`, `transfer-encoding` (and the rest
 * of the hop-by-hop set), plus `content-encoding` and `content-length` —
 * Node/undici transparently decompresses the upstream body for us (br/gzip),
 * so the bytes we hand to `new Response(upstream.body, …)` are already
 * plain. Forwarding the original `content-encoding: br` on a decoded body
 * makes the browser fail with ERR_CONTENT_DECODING_FAILED, which surfaces
 * to islands as `TypeError: Failed to fetch`. We also drop the client's
 * `accept-encoding` on the way UP so upstream is more likely to send
 * identity, keeping this belt-and-suspenders.
 */

import { readSessionCookie } from "@/lib/auth/bff"

const BACKEND = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8765"

export const dynamic = "force-dynamic"
// Vercel caps Hobby/Pro function duration; our upstream (Render free) can
// cold-start for 30–60s, so give this route the max we're allowed and
// bound the upstream fetch inside that budget.
export const maxDuration = 60

// Keep the upstream fetch inside the function budget. 55s leaves a few
// seconds of slack before Vercel kills the invocation so we can return a
// clean 504 instead of a terminated connection.
const UPSTREAM_TIMEOUT_MS = 55_000
// A single short probe is enough to nudge Render out of sleep; we don't
// care about the response — just the side-effect of waking the dyno. The
// daily-crawl workflow uses the same pattern.
const WARMUP_TIMEOUT_MS = 3_000

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
  // us set them. Also drop `accept-encoding` — if upstream brotli-compresses
  // the response, undici decodes it for us but we'd then be left echoing a
  // `content-encoding: br` header onto a plain body (see response loop
  // below). Asking upstream for identity sidesteps that mismatch entirely.
  for (const [k, v] of req.headers.entries()) {
    const lk = k.toLowerCase()
    if (
      lk === "host" ||
      lk === "cookie" ||
      lk === "authorization" ||
      lk === "accept-encoding" ||
      HOP_BY_HOP.has(lk)
    )
      continue
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
  const isCrawlRun = joinedPath === "crawl/run" && method === "POST"
  if (isCrawlRun) {
    const cronSecret = process.env.CRON_SECRET || ""
    if (cronSecret) headers.set("x-cron-secret", cronSecret)

    // Best-effort warm-up for Render free-tier cold starts. The dyno can
    // take 30–60s to come back from sleep; firing the actual POST at a
    // cold instance exceeds the function budget and the UI silently falls
    // back to Simulated. Hit /health first (same trick the daily-crawl
    // workflow uses) so the real request lands on a warm instance.
    try {
      await fetch(`${BACKEND.replace(/\/$/, "")}/health`, {
        method: "GET",
        signal: AbortSignal.timeout(WARMUP_TIMEOUT_MS),
        cache: "no-store",
      })
    } catch {
      // Ignore — this is best-effort. If the warm-up fails the main
      // request's own timeout + error handling will surface the state.
    }
  }
  const init: RequestInit = { method, headers, cache: "no-store" }
  if (method !== "GET" && method !== "HEAD") {
    // Pass the body through verbatim.
    init.body = await req.arrayBuffer()
    // duplex is required when a stream body is used; a buffer body is fine
    // without it but Node 20+ accepts the flag.
  }

  // Bound the upstream fetch so a slow cold start becomes a clean 504
  // we can toast on, not a terminated Vercel function returning HTML.
  const controller = new AbortController()
  const abortTimer = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS)
  let upstream: Response
  try {
    upstream = await fetch(target, { ...init, signal: controller.signal })
  } catch (e) {
    const err = e as { name?: string } | null
    const isTimeout = err?.name === "AbortError" || err?.name === "TimeoutError"
    if (isTimeout) {
      return Response.json(
        { detail: "backend waking up, try again" },
        { status: 504 },
      )
    }
    return Response.json({ detail: "backend unreachable", error: String(e) }, { status: 502 })
  } finally {
    clearTimeout(abortTimer)
  }

  const outHeaders = new Headers()
  for (const [k, v] of upstream.headers.entries()) {
    const lk = k.toLowerCase()
    // undici decompresses the upstream body for us, so `content-encoding`
    // and the upstream `content-length` no longer describe the bytes we're
    // about to send. Echoing them makes the browser try to br/gzip-decode a
    // plain body → ERR_CONTENT_DECODING_FAILED → `TypeError: Failed to
    // fetch` on the island. Let the runtime recompute the length.
    if (lk === "content-encoding" || lk === "content-length") continue
    if (HOP_BY_HOP.has(lk)) continue
    outHeaders.set(k, v)
  }
  return new Response(upstream.body, { status: upstream.status, headers: outHeaders })
}

export const GET = handler
export const POST = handler
export const PUT = handler
export const PATCH = handler
export const DELETE = handler
