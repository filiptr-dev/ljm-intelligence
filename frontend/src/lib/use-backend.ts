"use client"

import * as React from "react"

/** Poll `/api/crawler/health` so the UI can flip a Live/Simulated badge honestly.
 *
 *  Design note: this only asks about the backend's state — it never dispatches a crawl
 *  or fetches leads on its own. The consumer decides what to do with `live=false`
 *  (per the plan: fall back to simulated with an amber badge, never silently degrade).
 */
export function useBackendHealth(intervalMs = 30_000) {
  const [live, setLive] = React.useState<boolean | null>(null)

  const ping = React.useCallback(async () => {
    try {
      const r = await fetch("/api/crawler/health", { cache: "no-store" })
      if (!r.ok) return setLive(false)
      const j = (await r.json()) as { ok?: boolean }
      setLive(!!j.ok)
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
      const r = await fetch("/api/crawler/status", { cache: "no-store" })
      if (!r.ok) return
      setRun((await r.json()) as LatestRun)
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
      await fetch(`/api/crawler/run?limit=${limit}`, { method: "POST" })
      await fetchLatest()
    } finally {
      setRunning(false)
    }
  }, [fetchLatest])

  return { run, running, trigger }
}
