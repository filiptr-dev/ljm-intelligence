"use client"

/**
 * Lane Rate Calculator — low/mid/high $/mi + evidence.
 *
 * Reuses `Panel`, `StatTile`, `Segmented` (no new design primitives). The
 * "Use in email draft" button deep-links into `/emails/compose` with a
 * pre-filled subject + body — the existing composer reads `?subject=…&body=…`
 * query params, so no composer changes are needed.
 */

import * as React from "react"
import Link from "next/link"
import { toast } from "sonner"

import { PageHeader, Panel, StatTile } from "@/components/app/ui"
import { Segmented } from "@/components/app/segmented"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { quoteLane, type RateQuoteIn, type RateQuoteOut } from "@/lib/api/rates"

const EQUIPMENT: { value: "V" | "R" | "F"; label: string }[] = [
  { value: "V", label: "Van" },
  { value: "R", label: "Reefer" },
  { value: "F", label: "Flatbed" },
]

export default function RatesPage() {
  const [originCity, setOriginCity] = React.useState("Dallas")
  const [originState, setOriginState] = React.useState("TX")
  const [destCity, setDestCity] = React.useState("Atlanta")
  const [destState, setDestState] = React.useState("GA")
  const [equipment, setEquipment] = React.useState<"V" | "R" | "F">("V")
  const [busy, setBusy] = React.useState(false)
  const [result, setResult] = React.useState<RateQuoteOut | null>(null)

  async function onQuote() {
    setBusy(true)
    try {
      const body: RateQuoteIn = {
        origin: { city: originCity, state: originState.toUpperCase() },
        dest: { city: destCity, state: destState.toUpperCase() },
        equipment,
      }
      const r = await quoteLane(body)
      if (!r) toast.error("Could not quote lane — try a different city pair.")
      else setResult(r)
    } catch (e) {
      toast.error(`Quote failed: ${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const composeHref = React.useMemo(() => {
    if (!result) return null
    const subject = `Rate quote: ${originCity}, ${originState} → ${destCity}, ${destState}`
    const miLow = result.rate_band.low as number
    const miMid = result.rate_band.mid as number
    const miHigh = result.rate_band.high as number
    const dLow = result.rate_band_dollars.low as number
    const dMid = result.rate_band_dollars.mid as number
    const dHigh = result.rate_band_dollars.high as number
    const body = [
      `Lane: ${originCity}, ${originState} → ${destCity}, ${destState}`,
      `Equipment: ${equipment}  ·  Miles: ${result.highway_miles} (highway × ${result.highway_factor})`,
      "",
      `Rate band:`,
      `  low:  $${miLow.toFixed(2)}/mi  (~$${dLow.toFixed(0)})`,
      `  mid:  $${miMid.toFixed(2)}/mi  (~$${dMid.toFixed(0)})`,
      `  high: $${miHigh.toFixed(2)}/mi  (~$${dHigh.toFixed(0)})`,
      "",
      ...result.evidence.map((e) => `- ${e}`),
    ].join("\n")
    const params = new URLSearchParams({ subject, body })
    return `/emails/compose?${params.toString()}`
  }, [result, originCity, originState, destCity, destState, equipment])

  return (
    <>
      <PageHeader
        eyebrow="Lane Rate Calculator"
        title="What should this lane pay?"
        description="Estimate $/mile for an origin → destination lane — distance, fuel, historical comps — and drop the quote straight into the email builder."
      />
      <div className="grid gap-5 md:grid-cols-2">
        <Panel title="Lane">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label htmlFor="o-city">Origin city</Label>
              <Input id="o-city" value={originCity} onChange={(e) => setOriginCity(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="o-state">State</Label>
              <Input id="o-state" value={originState} maxLength={2} onChange={(e) => setOriginState(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="d-city">Destination city</Label>
              <Input id="d-city" value={destCity} onChange={(e) => setDestCity(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="d-state">State</Label>
              <Input id="d-state" value={destState} maxLength={2} onChange={(e) => setDestState(e.target.value)} />
            </div>
          </div>
          <div className="mt-4 flex items-center gap-3">
            <Label>Equipment</Label>
            <Segmented value={equipment} onChange={setEquipment} options={EQUIPMENT} />
          </div>
          <div className="mt-4">
            <Button onClick={onQuote} disabled={busy}>
              {busy ? "Quoting…" : "Quote lane"}
            </Button>
          </div>
        </Panel>

        <Panel title="Band" description={result ? `${result.highway_miles} highway mi · factor ${result.highway_factor}` : "Enter a lane and quote."}>
          {result ? (
            <>
              <div className="grid grid-cols-3 gap-3">
                <StatTile label="Low" value={`$${(result.rate_band.low as number).toFixed(2)}/mi`} sub={`~$${(result.rate_band_dollars.low as number).toFixed(0)}`} />
                <StatTile label="Mid" value={`$${(result.rate_band.mid as number).toFixed(2)}/mi`} sub={`~$${(result.rate_band_dollars.mid as number).toFixed(0)}`} />
                <StatTile label="High" value={`$${(result.rate_band.high as number).toFixed(2)}/mi`} sub={`~$${(result.rate_band_dollars.high as number).toFixed(0)}`} />
              </div>
              <ul className="mt-4 space-y-1 text-xs text-muted-foreground">
                {result.evidence.map((e, i) => (
                  <li key={i}>· {e}</li>
                ))}
              </ul>
              {composeHref ? (
                <div className="mt-4">
                  <Link
                    href={composeHref}
                    className="inline-flex items-center rounded-sm border border-border bg-card px-3 py-1.5 text-sm hover:bg-muted"
                  >
                    Use in email draft →
                  </Link>
                </div>
              ) : null}
            </>
          ) : (
            <p className="text-sm text-muted-foreground">No quote yet.</p>
          )}
        </Panel>
      </div>

      {result && result.comps.length > 0 ? (
        <Panel title={`Historical comps (${result.comps_count})`} className="mt-5">
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="text-xs text-muted-foreground">
                <tr>
                  <th className="py-1 pr-3 text-left">Origin</th>
                  <th className="py-1 pr-3 text-left">Destination</th>
                  <th className="py-1 pr-3 text-right">Miles</th>
                  <th className="py-1 pr-3 text-right">Rate</th>
                  <th className="py-1 pr-3 text-right">$/mi</th>
                  <th className="py-1 pr-3 text-left">Pickup</th>
                </tr>
              </thead>
              <tbody>
                {result.comps.map((c, i) => (
                  <tr key={i} className="border-t">
                    <td className="py-1 pr-3">{c.origin}</td>
                    <td className="py-1 pr-3">{c.dest}</td>
                    <td className="py-1 pr-3 text-right num font-mono">{c.miles}</td>
                    <td className="py-1 pr-3 text-right num font-mono">${c.rate_usd.toFixed(0)}</td>
                    <td className="py-1 pr-3 text-right num font-mono">${c.rate_per_mile.toFixed(2)}</td>
                    <td className="py-1 pr-3 text-xs text-muted-foreground">{c.pickup_date ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : null}
    </>
  )
}
