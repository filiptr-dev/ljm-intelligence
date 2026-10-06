"use client"

import * as React from "react"
import { getCampaignStatus, type CampaignStatusItem } from "@/lib/api/analysis"
import type { Campaign } from "@/lib/campaigns/types"

export type CampaignStatusMap = Map<string, CampaignStatusItem>

/**
 * Backfills a single campaign's recipients with real `sent_at` / `replied_at`
 * timestamps from the backend. Opens and wins are never returned — no table
 * backs them (plan 2026-10-06, gate amendment).
 *
 * The hook is deliberately simple: one `POST /analysis/campaign-status` per
 * campaign shown, keyed by `(id, recipient-emails joined)`. Dashboards with
 * many campaigns call `useCampaignsStatus` instead to batch per-view.
 */
export function useCampaignStatus(campaign: Campaign | null | undefined): CampaignStatusMap {
  const [map, setMap] = React.useState<CampaignStatusMap>(() => new Map())

  const key = React.useMemo(() => {
    if (!campaign) return null
    const emails = campaign.recipients.map((r) => r.email).filter(Boolean)
    if (!emails.length) return null
    return { id: campaign.id, createdAt: new Date(campaign.createdAt).toISOString(), emails }
  }, [campaign])

  React.useEffect(() => {
    if (!key) return
    const ctrl = new AbortController()
    getCampaignStatus(key.createdAt, key.emails, ctrl.signal)
      .then((r) => {
        const next: CampaignStatusMap = new Map()
        for (const item of r.items) next.set(item.email.toLowerCase(), item)
        setMap(next)
      })
      .catch(() => {})
    return () => ctrl.abort()
  }, [key])

  return map
}

/** Batch per-campaign variant for the Campaigns dashboard. One request per
 *  campaign (keeps the backend endpoint small and composable). */
export function useCampaignsStatus(campaigns: Campaign[]): Map<string, CampaignStatusMap> {
  const [all, setAll] = React.useState<Map<string, CampaignStatusMap>>(() => new Map())
  // Stable key: ids + createdAt + recipient counts (don't re-fetch on every render).
  const sig = React.useMemo(
    () =>
      campaigns
        .map((c) => `${c.id}:${c.createdAt}:${c.recipients.length}`)
        .join("|"),
    [campaigns],
  )
  React.useEffect(() => {
    const ctrl = new AbortController()
    const next = new Map<string, CampaignStatusMap>()
    Promise.all(
      campaigns.map(async (c) => {
        const emails = c.recipients.map((r) => r.email).filter(Boolean)
        if (!emails.length) {
          next.set(c.id, new Map())
          return
        }
        try {
          const r = await getCampaignStatus(new Date(c.createdAt).toISOString(), emails, ctrl.signal)
          const m: CampaignStatusMap = new Map()
          for (const item of r.items) m.set(item.email.toLowerCase(), item)
          next.set(c.id, m)
        } catch {
          next.set(c.id, new Map())
        }
      }),
    ).then(() => {
      if (!ctrl.signal.aborted) setAll(new Map(next))
    })
    return () => ctrl.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig])
  return all
}
