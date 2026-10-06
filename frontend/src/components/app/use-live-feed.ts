"use client"

import * as React from "react"
import { getLiveFeed, type LiveFeedItem } from "@/lib/api/analysis"

const POLL_MS = 60_000

/**
 * Rolling 12h of real activity for `<LiveFeed />`, polled every 60s.
 *
 * Mirrors `useTodayCounters` — starts empty, keeps the last successful
 * snapshot on failure (never invents events). Replaces the client-side
 * `createRng` + `setInterval` fiction that `EngineProvider` used to run.
 */
export function useLiveFeed(limit = 25): LiveFeedItem[] {
  const [items, setItems] = React.useState<LiveFeedItem[]>([])
  React.useEffect(() => {
    const ctrl = new AbortController()
    const load = () =>
      getLiveFeed(limit, ctrl.signal)
        .then((r) => setItems(r.items))
        .catch(() => {})
    load()
    const id = setInterval(load, POLL_MS)
    return () => {
      clearInterval(id)
      ctrl.abort()
    }
  }, [limit])
  return items
}
