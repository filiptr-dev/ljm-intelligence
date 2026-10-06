"use client"

import * as React from "react"
import { api } from "@/lib/api/client"

/**
 * Read + write the real `settings.auto_outreach_enabled` flag.
 *
 * The Lead-Finder page's auto-outreach switch used to flip a client-only
 * `autoOutreach` state (part of the engine simulation). The real flag was
 * already honest in the Settings page (`settings-client.tsx` — PUT
 * `/settings`); plan 2026-10-06 rewires the Leads switch to the same
 * endpoint so both pages drive the same source of truth.
 *
 * Starts `false` (safe default). A failing read keeps the local state
 * false and does not swallow the Save error.
 */
export function useAutoOutreach(): [boolean, (v: boolean) => Promise<void>] {
  const [enabled, setEnabled] = React.useState(false)

  React.useEffect(() => {
    let cancelled = false
    api.GET("/settings", {}).then(({ data }) => {
      if (cancelled || !data) return
      setEnabled(Boolean((data as { auto_outreach_enabled?: boolean }).auto_outreach_enabled))
    }).catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  const save = React.useCallback(async (next: boolean) => {
    // Optimistic flip, revert on failure.
    setEnabled(next)
    try {
      const { response } = await api.PUT("/settings", {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        body: { auto_outreach_enabled: next } as any,
      })
      if (!response.ok) {
        setEnabled(!next)
      }
    } catch {
      setEnabled(!next)
    }
  }, [])

  return [enabled, save]
}
