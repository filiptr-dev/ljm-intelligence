"use client"

/**
 * Map backend `BackendLead` → frontend `Lead`, filling the fields the crawler doesn't
 * carry (equipment defaults, single lane, size guess) so the existing lead-finder UI
 * treats real crawler rows exactly like the simulated pool.
 *
 * Why fill defaults instead of widening the Lead type: keeping the shape stable means
 * every existing table cell, badge and campaign template keeps working. The "Live"
 * badge on the page is what tells the user these are real rows.
 */

import * as React from "react"
import type { Lead, LeadKind } from "@/lib/data/types"
import type { BackendLead } from "@/lib/backend"

const STATE_ZONE: Record<string, string> = {
  // Northeast
  CT: "Northeast", ME: "Northeast", MA: "Northeast", NH: "Northeast", NJ: "Northeast",
  NY: "Northeast", PA: "Northeast", RI: "Northeast", VT: "Northeast",
  // Mid-Atlantic → grouped with Northeast/Southeast in the UI as needed
  DE: "Mid-Atlantic", DC: "Mid-Atlantic", MD: "Mid-Atlantic", VA: "Mid-Atlantic", WV: "Mid-Atlantic",
  // Southeast
  AL: "Southeast", AR: "Southeast", FL: "Southeast", GA: "Southeast", KY: "Southeast",
  LA: "Southeast", MS: "Southeast", NC: "Southeast", SC: "Southeast", TN: "Southeast",
  // Midwest
  IL: "Midwest", IN: "Midwest", MI: "Midwest", OH: "Midwest", WI: "Midwest",
  MO: "Midwest", IA: "Midwest", MN: "Midwest", OK: "Midwest",
}

export function backendLeadToFrontend(b: BackendLead): Lead {
  const kind: LeadKind = (b.kind as LeadKind) ?? "Broker"
  const registration = b.mc ? `MC-${b.mc}` : b.dot ? `DOT-${b.dot}` : b.id
  const zone = STATE_ZONE[b.state] ?? "US"
  const hqLabel = b.city ? `${b.city.replace(/\s+/g, " ").replace(/^./, (c) => c.toUpperCase())}, ${b.state}` : `${b.state}, US`
  const source = b.sources.includes("Gemini Search") ? "LinkedIn" : "FMCSA SAFER"
  return {
    id: b.id,
    kind,
    name: b.name,
    region: "US",
    country: "US",
    hq: hqLabel,
    zone,
    registration,
    contact: {
      name: "—",
      email: b.primary_email ?? "",
      phone: b.phone ?? "",
      title: kind === "Shipper" ? "Logistics" : "Dispatch",
    },
    equipment: ["Dry Van"],
    lanes: [{ origin: b.state, destination: "TBD" }],
    size: "Small",
    monthlyLoads: 0,
    source,
    discoveredAt: b.first_seen_at || new Date().toISOString(),
    emailVerified: !!b.primary_email,
    score: b.current_score ?? 0,
  }
}

/** Client hook: fetch real leads from the backend and expose them + a Live/Simulated flag. */
export function useBackendLeads(limit = 200): { real: Lead[]; live: boolean | null; error: string | null; reload: () => void } {
  const [real, setReal] = React.useState<Lead[]>([])
  const [live, setLive] = React.useState<boolean | null>(null)
  const [error, setError] = React.useState<string | null>(null)

  const reload = React.useCallback(async () => {
    try {
      const r = await fetch(`/api/crawler/leads?limit=${limit}`, { cache: "no-store" })
      if (!r.ok) {
        setLive(false); setError(`backend ${r.status}`); return
      }
      const j = (await r.json()) as { items: BackendLead[] }
      setReal((j.items ?? []).map(backendLeadToFrontend))
      setLive(true)
      setError(null)
    } catch (e) {
      setLive(false); setError(String(e))
    }
  }, [limit])

  React.useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    reload()
    const id = setInterval(reload, 60_000)
    return () => clearInterval(id)
  }, [reload])

  return { real, live, error, reload }
}
