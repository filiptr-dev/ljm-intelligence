"use client"

/**
 * Backhaul Finder — rank nearby brokers/shippers by headed-home score.
 *
 * "Draft intro" deep-links into the existing `/emails/compose` with a
 * recipient and a short pre-filled subject + body. No new email code.
 */

import * as React from "react"
import Link from "next/link"
import { toast } from "sonner"

import { PageHeader, Panel } from "@/components/app/ui"
import { Segmented } from "@/components/app/segmented"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { findBackhauls, type BackhaulIn, type BackhaulOut } from "@/lib/api/rates"

const EQUIPMENT: { value: "V" | "R" | "F" | "ANY"; label: string }[] = [
  { value: "ANY", label: "Any" },
  { value: "V", label: "Van" },
  { value: "R", label: "Reefer" },
  { value: "F", label: "Flatbed" },
]

function introHref(opts: {
  email: string | null
  phone: string | null
  city: string
  state: string
  dropCity: string
  dropState: string
  expectedRatePerMile: number | null
}): string {
  const subject = `Backhaul out of ${opts.dropCity}, ${opts.dropState}`
  const bodyLines = [
    `Hi — we have a truck emptying in ${opts.dropCity}, ${opts.dropState} and are looking for a return load headed your way (${opts.city}, ${opts.state}).`,
    opts.expectedRatePerMile ? `Target rate ≈ $${opts.expectedRatePerMile.toFixed(2)}/mi.` : "",
    "What do you have?",
  ].filter(Boolean)
  const params = new URLSearchParams({ subject, body: bodyLines.join("\n\n") })
  if (opts.email) params.set("to", opts.email)
  return `/emails/compose?${params.toString()}`
}

export default function BackhaulPage() {
  const [dropCity, setDropCity] = React.useState("Chicago")
  const [dropState, setDropState] = React.useState("IL")
  const [homeState, setHomeState] = React.useState("TX")
  const [radius, setRadius] = React.useState("150")
  const [equipment, setEquipment] = React.useState<"V" | "R" | "F" | "ANY">("ANY")
  const [busy, setBusy] = React.useState(false)
  const [result, setResult] = React.useState<BackhaulOut | null>(null)

  async function onSearch() {
    setBusy(true)
    try {
      const body: BackhaulIn = {
        drop: { city: dropCity, state: dropState.toUpperCase() },
        home_state: homeState.toUpperCase(),
        equipment,
        radius_miles: Math.max(25, Math.min(500, Number(radius) || 150)),
      }
      const r = await findBackhauls(body)
      if (!r) toast.error("Could not search — try widening the radius.")
      else setResult(r)
    } catch (e) {
      toast.error(`Search failed: ${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Backhaul Finder"
        title="Don't come back empty"
        description="Given a delivery city, surface brokers and shippers nearby who might have your return load — ranked by headed-home score."
      />
      <div className="grid gap-5 md:grid-cols-[1fr_2fr]">
        <Panel title="Where are you dropping?">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label htmlFor="drop-city">Drop city</Label>
              <Input id="drop-city" value={dropCity} onChange={(e) => setDropCity(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="drop-state">State</Label>
              <Input id="drop-state" maxLength={2} value={dropState} onChange={(e) => setDropState(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="home-state">Home state</Label>
              <Input id="home-state" maxLength={2} value={homeState} onChange={(e) => setHomeState(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="radius">Radius (mi)</Label>
              <Input id="radius" type="number" min={25} max={500} value={radius} onChange={(e) => setRadius(e.target.value)} />
            </div>
          </div>
          <div className="mt-4 flex items-center gap-3">
            <Label>Equipment</Label>
            <Segmented value={equipment} onChange={setEquipment} options={EQUIPMENT} />
          </div>
          <div className="mt-4">
            <Button onClick={onSearch} disabled={busy}>
              {busy ? "Searching…" : "Find backhauls"}
            </Button>
          </div>
        </Panel>

        <Panel title={result ? `Candidates (${result.candidates.length})` : "Candidates"} description={result ? result.evidence.join(" · ") : "Pick a drop city."}>
          {result && result.candidates.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead className="text-xs text-muted-foreground">
                  <tr>
                    <th className="py-1 pr-3 text-left">Name</th>
                    <th className="py-1 pr-3 text-left">City</th>
                    <th className="py-1 pr-3 text-right">Dist</th>
                    <th className="py-1 pr-3 text-right">Score</th>
                    <th className="py-1 pr-3 text-right">$/mi</th>
                    <th className="py-1 pr-3 text-left">Contact</th>
                    <th className="py-1 pr-3 text-left">Last contacted</th>
                    <th className="py-1 pr-3" />
                  </tr>
                </thead>
                <tbody>
                  {result.candidates.map((c) => (
                    <tr key={c.lead_id} className="border-t">
                      <td className="py-1 pr-3 font-medium">{c.name}</td>
                      <td className="py-1 pr-3">{c.city}, {c.state}</td>
                      <td className="py-1 pr-3 text-right num font-mono">{c.distance_miles} mi</td>
                      <td className="py-1 pr-3 text-right num font-mono">{c.headed_home_score}{c.toward_home ? " ↩" : ""}</td>
                      <td className="py-1 pr-3 text-right num font-mono">{c.expected_rate_per_mile !== null && c.expected_rate_per_mile !== undefined ? `$${c.expected_rate_per_mile.toFixed(2)}` : "—"}</td>
                      <td className="py-1 pr-3 text-xs">
                        {c.has_phone ? <span className="mr-1 rounded bg-muted px-1">📞</span> : null}
                        {c.has_email ? <span className="rounded bg-muted px-1">✉</span> : null}
                      </td>
                      <td className="py-1 pr-3 text-xs text-muted-foreground">{c.last_contacted_at ? new Date(c.last_contacted_at).toLocaleDateString() : "never"}</td>
                      <td className="py-1 pr-3 text-right">
                        <Link
                          href={introHref({
                            email: c.email,
                            phone: c.phone,
                            city: c.city,
                            state: c.state,
                            dropCity,
                            dropState: dropState.toUpperCase(),
                            expectedRatePerMile: c.expected_rate_per_mile ?? null,
                          })}
                          className="inline-flex items-center rounded-sm border border-border bg-card px-2 py-1 text-xs hover:bg-muted"
                        >
                          Draft intro
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              {result ? "No candidates in the radius. Try widening it or picking a different drop city." : "No search yet."}
            </p>
          )}
        </Panel>
      </div>
    </>
  )
}
