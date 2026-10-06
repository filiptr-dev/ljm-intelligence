"use client"

import * as React from "react"
import { getTopbarCounters } from "@/lib/api/analysis"

export type MonitoringStatus = "none" | "running" | "idle_recent" | "idle_stale" | "error"

export type TodayCounters = {
  scanned: number
  found: number
  sent: number
  replies: number
  lastCrawlAt: string | null
  lastCrawlStatus: MonitoringStatus
}

const ZERO: TodayCounters = {
  scanned: 0,
  found: 0,
  sent: 0,
  replies: 0,
  lastCrawlAt: null,
  lastCrawlStatus: "none",
}
const POLL_MS = 60_000

/**
 * Today's (ET) real counters for the tenant — `GET /analysis/topbar-counters`.
 *
 * One poll feeds both the number row and the top-bar pill (`lastCrawlAt` /
 * `lastCrawlStatus`), so they can never drift against each other. Starts at
 * zero + `none`; a failed poll keeps the last real snapshot rather than
 * inventing new numbers.
 */
export function useTodayCounters(): TodayCounters {
  const [counters, setCounters] = React.useState<TodayCounters>(ZERO)
  React.useEffect(() => {
    const ctrl = new AbortController()
    const load = () =>
      getTopbarCounters(ctrl.signal)
        .then((c) =>
          setCounters({
            scanned: c.scanned,
            found: c.found,
            sent: c.sent,
            replies: c.replies,
            lastCrawlAt: c.last_crawl_at,
            lastCrawlStatus: c.last_crawl_status as MonitoringStatus,
          }),
        )
        .catch(() => {})
    load()
    const id = setInterval(load, POLL_MS)
    return () => {
      clearInterval(id)
      ctrl.abort()
    }
  }, [])
  return counters
}
