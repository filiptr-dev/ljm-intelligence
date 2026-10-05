"use client"

import * as React from "react"
import { getTopbarCounters } from "@/lib/api/analysis"

export type TodayCounters = { scanned: number; found: number; sent: number; replies: number }

const ZERO: TodayCounters = { scanned: 0, found: 0, sent: 0, replies: 0 }
const POLL_MS = 60_000

/**
 * Today's (ET) real counters for the tenant — `GET /analysis/topbar-counters`.
 *
 * Starts at zero and only ever shows what the backend returned: no baseline,
 * no client-side tick-up. Re-polls every minute so a crawl or send that lands
 * while the page is open shows up without a reload. A failed poll keeps the
 * last real numbers rather than inventing new ones.
 */
export function useTodayCounters(): TodayCounters {
  const [counters, setCounters] = React.useState<TodayCounters>(ZERO)
  React.useEffect(() => {
    const ctrl = new AbortController()
    const load = () =>
      getTopbarCounters(ctrl.signal)
        .then(({ scanned, found, sent, replies }) => setCounters({ scanned, found, sent, replies }))
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
