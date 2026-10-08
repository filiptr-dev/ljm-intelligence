"use client"

import * as React from "react"
import { toast } from "sonner"
import { api } from "@/lib/api/client"

/** Poll `/health` through the typed client so the UI can flip a Live/Simulated
 *  badge honestly. Universal `openapi-fetch` client sends the request to
 *  `/api/proxy/health` from the browser (same-origin, HttpOnly cookie rides
 *  along) — no `/api/crawler/*` BFF needed.
 *
 *  Design note: this only asks about the backend's state — it never dispatches
 *  a crawl or fetches leads on its own. The consumer decides what to do with
 *  `live=false` (per the plan: fall back to simulated with an amber badge,
 *  never silently degrade).
 */
export function useBackendHealth(intervalMs = 30_000) {
  const [live, setLive] = React.useState<boolean | null>(null)

  const ping = React.useCallback(async () => {
    try {
      const { data, response } = await api.GET("/health", {})
      if (!response.ok || !data) return setLive(false)
      setLive(!!data.ok)
    } catch {
      setLive(false)
    }
  }, [])

  React.useEffect(() => {
    // Poll deliberately — the setState inside `ping` is the whole point of the hook.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    ping()
    const id = setInterval(ping, intervalMs)
    return () => clearInterval(id)
  }, [ping, intervalMs])

  return { live, recheck: ping }
}

export type LatestRun = {
  id: string
  status: "queued" | "running" | "done" | "error"
  started_at: string
  finished_at: string | null
  counts: Record<string, number>
} | null

export function useLatestRun(pollMs = 4000) {
  const [run, setRun] = React.useState<LatestRun>(null)
  const [running, setRunning] = React.useState(false)

  const fetchLatest = React.useCallback(async () => {
    try {
      const { data, response } = await api.GET("/crawl/latest", {})
      if (!response.ok) return
      setRun((data as LatestRun) ?? null)
    } catch {}
  }, [])

  React.useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchLatest()
    const active = run?.status === "queued" || run?.status === "running"
    const id = setInterval(fetchLatest, active ? 2000 : pollMs)
    return () => clearInterval(id)
  }, [fetchLatest, pollMs, run?.status])

  const trigger = React.useCallback(async (limit?: number): Promise<number | null> => {
    setRunning(true)
    try {
      // The proxy injects `X-Cron-Secret` for POST /crawl/run from a server-
      // only env var; the browser never learns the secret. See
      // `app/api/proxy/[...path]/route.ts`.
      //
      // Backend may return `{run_id, status, job_id}` when the queue is
      // installed — the pipeline runs in the worker, not inside this request.
      // Either way the `CrawlRun` row progresses queued → running → done,
      // and `useLatestRun` keeps polling `/crawl/latest`. The `job_id` is
      // handed to the caller so they can wire `useJobStatus` for the
      // procrastinate-level failure signal (job stuck in failed before the
      // CrawlRun row got an update).
      const resp = await api.POST(
        "/crawl/run",
        // `limit` omitted = the paginator's own caps (same as the daily cron)
        { params: { query: limit === undefined ? { trigger: "on_demand" } : { trigger: "on_demand", limit } } },
      )
      if (!resp.response.ok) {
        // Surface the backend-waking vs real-error distinction the proxy
        // encodes in the status + detail. Up to now this silently returned
        // null and the UI fell back to Simulated with no explanation.
        const status = resp.response.status
        const detail =
          (resp.error as { detail?: string } | undefined)?.detail
          ?? (status === 504
            ? "backend waking up, try again"
            : status === 502
              ? "backend unreachable"
              : `request failed (${status})`)
        toast.error(`Crawl didn't start — ${detail}`)
        return null
      }
      await fetchLatest()
      const data = resp.data as { job_id?: number | null } | undefined
      return data?.job_id ?? null
    } catch (e) {
      // Network-layer / aborted fetch. Still better than a silent slip to
      // Simulated — tell the operator a real request failed.
      toast.error(
        `Crawl didn't start — ${
          (e as { message?: string } | null)?.message || "network error"
        }`,
      )
      return null
    } finally {
      setRunning(false)
    }
  }, [fetchLatest])

  return { run, running, trigger }
}


/** Poll /jobs/{id} until the worker reports a terminal status. The hook
 *  is intentionally minimal — a caller passes `onDone` to refetch its own
 *  data when the job succeeds, and `onFail` to show an error.
 */
export type JobStatus = "todo" | "doing" | "succeeded" | "failed" | "cancelled" | "aborted"

export function useJobStatus(
  jobId: number | null,
  opts: { pollMs?: number; onDone?: () => void; onFail?: (err: string | null) => void } = {},
) {
  const { pollMs = 2000, onDone, onFail } = opts
  const [status, setStatus] = React.useState<JobStatus | null>(null)
  const [lastError, setLastError] = React.useState<string | null>(null)
  const terminalRef = React.useRef(false)

  React.useEffect(() => {
    if (jobId == null) {
      // Reset is required when the caller clears the id; the lint
      // guidance applies to render-driving setStates, not to clean-up.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setStatus(null)
      terminalRef.current = false
      return
    }
    terminalRef.current = false
    let alive = true

    const tick = async () => {
      try {
        const { data, response } = await api.GET("/jobs/{job_id}", {
          params: { path: { job_id: jobId } },
        })
        if (!alive || !response.ok || !data) return
        const s = data.status as JobStatus
        setStatus(s)
        setLastError(data.last_error ?? null)
        if (s === "succeeded" || s === "failed" || s === "cancelled" || s === "aborted") {
          terminalRef.current = true
          if (s === "succeeded") onDone?.()
          if (s === "failed" || s === "cancelled" || s === "aborted") onFail?.(data.last_error ?? null)
        }
      } catch {
        // transient — the next tick will retry
      }
    }

    tick()
    const id = setInterval(() => {
      if (terminalRef.current) return
      tick()
    }, pollMs)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [jobId, pollMs, onDone, onFail])

  return { status, lastError, running: status === "todo" || status === "doing" }
}
