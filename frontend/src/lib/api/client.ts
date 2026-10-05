/**
 * Typed API client — universal (safe in both Server Components / Route
 * Handlers and `"use client"` islands).
 *
 * Cookie-only auth. The JWT lives in an HttpOnly cookie this origin owns;
 * the browser never holds a bearer. Two execution paths share one typed
 * surface:
 *
 *  - **Server (SC / Route Handler / Server Action).** `baseUrl` is the
 *    direct FastAPI URL. We read the session cookie via `next/headers` and
 *    attach `Authorization: Bearer <jwt>` on each outgoing request.
 *  - **Browser island.** `baseUrl` is `/api/proxy`. The browser sends the
 *    HttpOnly cookie same-origin; the proxy BFF
 *    (`app/api/proxy/[...path]/route.ts`) reads it, attaches the bearer,
 *    and forwards to FastAPI. No bearer header ever lives in JS.
 *
 * The sister file `lib/api/server.ts` re-exports this `api` with an
 * `import "server-only"` guard for callers that specifically want the
 * build to reject a browser import. Types-only consumers import from
 * `./types`.
 *
 * Base URL (server mode): `API_URL` if set, else `NEXT_PUBLIC_API_URL`.
 * Timeout: 8s via `AbortSignal.timeout`.
 * Retry: one retry on 502/503/504 for GET/HEAD only.
 */

import createClient from "openapi-fetch"
import type { paths } from "./schema"
import { SESSION_COOKIE } from "@/lib/auth/bff"

const IS_SERVER = typeof window === "undefined"

const SERVER_BASE_URL = (process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, "")
const BROWSER_BASE_URL = "/api/proxy"
const baseUrl = IS_SERVER ? SERVER_BASE_URL : BROWSER_BASE_URL

export const apiConfigured = IS_SERVER ? SERVER_BASE_URL !== "" : true

// Render free-tier cold-starts routinely take 20–50s to boot FastAPI. The
// previous 8_000 ms budget aborted every first request after idle, which
// bubbled up as "timed out" in PROD while local never tripped (dev FastAPI
// is already warm). 35s is comfortably over p95 cold-start and still well
// under Vercel's hobby 60s route limit. Do not lower without a keep-alive
// cron in place.
const TIMEOUT_MS = 35_000
const RETRY_BACKOFF_MS = 400
const RETRY_STATUSES = new Set([502, 503, 504])

async function serverBearer(): Promise<string | null> {
  // Dynamic import so this module stays importable from the browser bundle
  // (where `next/headers` doesn't exist). Vite / webpack tree-shake drops
  // the branch, and in the browser the condition is false anyway.
  try {
    const { cookies } = await import("next/headers")
    const jar = await cookies()
    return jar.get(SESSION_COOKIE)?.value ?? null
  } catch {
    return null
  }
}

async function apiFetch(req: Request): Promise<Response> {
  const isRetryable = req.method === "GET" || req.method === "HEAD"

  const headers = new Headers(req.headers)
  if (IS_SERVER && !headers.has("authorization")) {
    const token = await serverBearer()
    if (token) headers.set("authorization", `Bearer ${token}`)
  }
  const prepared = new Request(req, { headers })

  async function once(): Promise<Response> {
    const timeout = AbortSignal.timeout(TIMEOUT_MS)
    const signal = prepared.signal ? AbortSignal.any([prepared.signal, timeout]) : timeout
    // On the browser, carry the HttpOnly session cookie to /api/proxy.
    const init: RequestInit = { signal }
    if (!IS_SERVER) init.credentials = "same-origin"
    return fetch(prepared.clone(), init)
  }

  try {
    const first = await once()
    if (!isRetryable || !RETRY_STATUSES.has(first.status)) return first
    await new Promise((r) => setTimeout(r, RETRY_BACKOFF_MS))
    return once()
  } catch (err) {
    // Retry once on AbortError for GET/HEAD — covers the window where the
    // FastAPI dyno woke up mid-request but didn't answer before TIMEOUT_MS.
    // Non-retryable methods or non-abort errors bubble up unchanged.
    if (!isRetryable) throw err
    const isAbort =
      err instanceof DOMException && err.name === "AbortError"
      || (typeof err === "object" && err !== null && (err as { name?: string }).name === "AbortError")
    if (!isAbort) throw err
    await new Promise((r) => setTimeout(r, RETRY_BACKOFF_MS * 2))
    return once()
  }
}

export const api = createClient<paths>({ baseUrl, fetch: apiFetch })

/** Raised when the API answered with a non-2xx and no `data` was returned. */
export class ApiRequestError extends Error {
  constructor(
    readonly status: number,
    readonly path: string,
    readonly detail?: unknown,
  ) {
    super(`API ${path} answered ${status}`)
  }
}
