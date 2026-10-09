"use client"

/**
 * Load Profit Calculator — rate - (fuel + driver + tolls) = margin.
 *
 * One service call per submit (`/rates/profit`). Verdict chip is driven by
 * the backend's thresholds so the UI never drifts from the formula.
 */

import * as React from "react"
import { toast } from "sonner"

import { PageHeader, Panel, StatTile } from "@/components/app/ui"
import { Segmented } from "@/components/app/segmented"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { scoreProfit, type ProfitIn, type ProfitOut } from "@/lib/api/rates"
import { cn } from "@/lib/utils"

const EQUIPMENT: { value: "V" | "R" | "F"; label: string }[] = [
  { value: "V", label: "Van" },
  { value: "R", label: "Reefer" },
  { value: "F", label: "Flatbed" },
]

function VerdictChip({ v }: { v: "green" | "tight" | "red" }) {
  const tone =
    v === "green" ? "bg-green-500/10 text-green-700"
    : v === "tight" ? "bg-amber-500/10 text-amber-700"
    : "bg-red-500/10 text-red-700"
  const label = v === "green" ? "Take it" : v === "tight" ? "Tight" : "Walk away"
  return <span className={cn("inline-flex items-center rounded px-2 py-0.5 text-sm font-medium", tone)}>{label}</span>
}

export default function ProfitPage() {
  const [rate, setRate] = React.useState("2200")
  const [loaded, setLoaded] = React.useState("800")
  const [deadhead, setDeadhead] = React.useState("50")
  const [mpg, setMpg] = React.useState("6.5")
  const [originState, setOriginState] = React.useState("TX")
  const [destState, setDestState] = React.useState("GA")
  const [equipment, setEquipment] = React.useState<"V" | "R" | "F">("V")
  const [busy, setBusy] = React.useState(false)
  const [result, setResult] = React.useState<ProfitOut | null>(null)

  async function onScore() {
    setBusy(true)
    try {
      const body: ProfitIn = {
        rate_usd: Number(rate),
        loaded_miles: Number(loaded),
        deadhead_miles: Number(deadhead) || 0,
        mpg: Number(mpg) || 6.5,
        equipment,
        origin_state: originState.toUpperCase(),
        dest_state: destState.toUpperCase(),
      }
      const r = await scoreProfit(body)
      if (!r) toast.error("Could not score — check the inputs.")
      else setResult(r)
    } catch (e) {
      toast.error(`Score failed: ${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Load Profit Calculator"
        title="Should you take this load?"
        description="Rate minus fuel, driver pay, tolls, and deadhead — the margin and a clear go/no-go verdict."
      />
      <div className="grid gap-5 md:grid-cols-2">
        <Panel title="Offer">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label htmlFor="rate">Rate (USD)</Label>
              <Input id="rate" type="number" min={0} step={1} value={rate} onChange={(e) => setRate(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="loaded">Loaded miles</Label>
              <Input id="loaded" type="number" min={0} step={1} value={loaded} onChange={(e) => setLoaded(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="deadhead">Deadhead miles</Label>
              <Input id="deadhead" type="number" min={0} step={1} value={deadhead} onChange={(e) => setDeadhead(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="mpg">Truck MPG</Label>
              <Input id="mpg" type="number" min={0.1} step={0.1} value={mpg} onChange={(e) => setMpg(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="o-state">Origin state</Label>
              <Input id="o-state" maxLength={2} value={originState} onChange={(e) => setOriginState(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="d-state">Dest state</Label>
              <Input id="d-state" maxLength={2} value={destState} onChange={(e) => setDestState(e.target.value)} />
            </div>
          </div>
          <div className="mt-4 flex items-center gap-3">
            <Label>Equipment</Label>
            <Segmented value={equipment} onChange={setEquipment} options={EQUIPMENT} />
          </div>
          <div className="mt-4">
            <Button onClick={onScore} disabled={busy}>
              {busy ? "Scoring…" : "Score load"}
            </Button>
          </div>
        </Panel>

        <Panel title="Breakdown">
          {result ? (
            <>
              <div className="grid grid-cols-2 gap-3">
                <StatTile
                  label="Margin"
                  value={`${result.margin_pct.toFixed(1)}%`}
                  sub={<VerdictChip v={result.verdict} />}
                />
                <StatTile label="Net" value={`$${result.net.toFixed(0)}`} sub={`of $${result.rate_usd.toFixed(0)} offered`} />
              </div>
              <table className="mt-4 w-full text-sm">
                <tbody>
                  <tr className="border-t"><td className="py-1 text-muted-foreground">Fuel</td><td className="py-1 text-right num font-mono">${result.fuel_cost.toFixed(2)}</td></tr>
                  <tr className="border-t"><td className="py-1 text-muted-foreground">Driver pay</td><td className="py-1 text-right num font-mono">${result.driver_cost.toFixed(2)}</td></tr>
                  <tr className="border-t"><td className="py-1 text-muted-foreground">Tolls{result.toll_corridor ? ` (${result.toll_corridor})` : ""}</td><td className="py-1 text-right num font-mono">${result.toll_cost.toFixed(2)}</td></tr>
                  <tr className="border-t font-semibold"><td className="py-1">Total cost</td><td className="py-1 text-right num font-mono">${result.total_cost.toFixed(2)}</td></tr>
                </tbody>
              </table>
              {result.diesel_stale ? (
                <p className="mt-3 rounded px-2 py-1 text-xs bg-amber-500/10 text-amber-700">
                  Diesel row is stale — margin treats fuel as a rough floor.
                </p>
              ) : null}
              <ul className="mt-3 space-y-1 text-xs text-muted-foreground">
                {result.evidence.map((e, i) => (
                  <li key={i}>· {e}</li>
                ))}
              </ul>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">Punch in an offer and score.</p>
          )}
        </Panel>
      </div>
    </>
  )
}
