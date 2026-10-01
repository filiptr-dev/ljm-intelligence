"use client"

import * as React from "react"
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

  const trigger = React.useCallback(async (limit = 25) => {
    setRunning(true)
    try {
      // The proxy injects `X-Cron-Secret` for POST /crawl/run from a server-
      // only env var; the browser never learns the secret. See
      // `app/api/proxy/[...path]/route.ts`.
      await api.POST("/crawl/run", { params: { query: { trigger: "on_demand", limit } } })
      await fetchLatest()
    } finally {
      setRunning(false)
    }
  }, [fetchLatest])

  return { run, running, trigger }
}
