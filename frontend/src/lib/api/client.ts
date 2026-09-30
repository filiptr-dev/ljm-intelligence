/**
 * Typed API client — first module of the bb-unikoop `lib/api/` pattern.
 *
 * Mirrors ~/Desktop/bbunikoop-demo/frontend/lib/api/client.ts on purpose: same
 * openapi-fetch shape, same base-URL resolution split, same retry semantics.
 * The second migration (call-list → capacity → emails → settings) will be
 * copy-paste, not a re-design.
 *
 * Base URL
 *   - Browser: `NEXT_PUBLIC_API_URL` (must be public — it's read from JS).
 *   - Server (Route Handlers / Server Components): `API_URL` if set, else the
 *     public var. The split keeps the door open for a shorter internal
 *     hostname later; today both usually resolve to the same URL.
 *
 * Timeout: 8s via AbortSignal.timeout — plan gate Q4.
 *
 * Retry: exactly one retry on 502/503/504 after a 400ms backoff, GETs only.
 * POSTs never retry — a duplicate promote is a duplicate promote (see the
 * shipper-finder plan §"Rules that apply"). openapi-fetch's `fetch` hook
 * receives the finalised `Request` object, whose `method` we honour.
 *
 * Notes
 *   - `openapi-fetch` calls this fetch with a Request; we clone it before each
 *     attempt because a Request body is a one-shot stream.
 *   - We compose signals with `AbortSignal.any` so a caller's abort still wins
 *     over our timeout.
 */

import createClient from "openapi-fetch"
import type { paths } from "./schema"

const RAW_BASE_URL =
  (typeof window === "undefined" && process.env.API_URL) ||
  process.env.NEXT_PUBLIC_API_URL ||
  ""

// Trim a trailing slash so `${baseUrl}${path}` never produces `//foo`.
const baseUrl = RAW_BASE_URL.replace(/\/$/, "")

export const apiConfigured = baseUrl !== ""

const TIMEOUT_MS = 8_000
const RETRY_BACKOFF_MS = 400
const RETRY_STATUSES = new Set([502, 503, 504])

async function apiFetch(req: Request): Promise<Response> {
  const isRetryable = req.method === "GET" || req.method === "HEAD"

  async function once(): Promise<Response> {
    const timeout = AbortSignal.timeout(TIMEOUT_MS)
    const signal = req.signal
      ? AbortSignal.any([req.signal, timeout])
      : timeout
    // A Request body is a one-shot stream — clone before every attempt.
    return fetch(req.clone(), { signal })
  }

  const first = await once()
  if (!isRetryable || !RETRY_STATUSES.has(first.status)) return first
  await new Promise((r) => setTimeout(r, RETRY_BACKOFF_MS))
  return once()
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
