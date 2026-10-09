"use client"

import * as React from "react"
import dynamic from "next/dynamic"
import { AlertTriangle, CheckCircle2, CircleDot, Wrench } from "lucide-react"
import { ChartSkeleton, EmptyChart } from "@/components/charts/primitives"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { timeAgo } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { TruckDetail } from "@/lib/api/fleet"
import { ExpiryBadge, StatusChip, ToneChip } from "./badges"
import { docLabel, EQUIPMENT_LABEL, fmtDate, miles, SEVERITY_ORDER } from "./format"

// deck.gl + maplibre are heavy and WebGL-only: only load them when a truck is opened, in the browser.
const TruckLocationMap = dynamic(() => import("@/components/charts/truck-location-map"), {
  ssr: false,
  loading: () => <ChartSkeleton className="h-56" />,
})

const usd = (v: number) => `$${Math.round(v).toLocaleString("en-US")}`
const usd2 = (v: number | null | undefined) => (v == null ? "—" : `$${v.toFixed(2)}`)

function Kpi({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="min-w-0 rounded-sm border border-border bg-card px-3 py-2.5">
      <div className="truncate text-[0.75rem] text-muted-foreground">{label}</div>
      <div className="mt-1 truncate text-xl leading-none font-semibold tracking-tight tabular-nums">{value}</div>
      {sub ? <div className="mt-1 truncate text-[0.72rem] text-muted-foreground">{sub}</div> : null}
    </div>
  )
}

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex min-w-0 items-baseline justify-between gap-3 border-b border-border/60 py-1.5 last:border-0">
      <dt className="shrink-0 text-[0.8rem] text-muted-foreground">{label}</dt>
      <dd className="min-w-0 truncate text-right text-[0.85rem] font-medium">{value}</dd>
    </div>
  )
}

const RESULT_TONE = { pass: "ok", conditional: "warn", fail: "bad" } as const
const SEVERITY_TONE = { critical: "bad", major: "warn", minor: "ok" } as const

export function TruckDetailView({ detail, wide = false }: { detail: TruckDetail; wide?: boolean }) {
  const { truck: t, kpis: k } = detail
  const isTruck = t.kind === "truck"
  const openDefects = detail.defects.filter((d) => d.status === "open").length
  const upcoming = detail.maintenance.filter((m) => !m.completed_at)
  const history = detail.maintenance.filter((m) => m.completed_at)
  const expiring = detail.documents.filter((d) => d.days_left <= 30).length

  return (
    <div className="min-w-0">
      <p className="mb-4 text-[0.9rem] leading-relaxed" data-testid="truck-summary">{detail.summary}</p>
      <Tabs defaultValue="overview" className="min-w-0">
        <TabsList variant="line" className="mb-3 w-full justify-start gap-0 overflow-x-auto border-b border-border">
          {[
            ["overview", "Overview"],
            ["inspections", `Inspections`],
            ["defects", openDefects ? `Defects (${openDefects})` : "Defects"],
            ["maintenance", "Maintenance"],
            ["documents", expiring ? `Documents (${expiring})` : "Documents"],
            ["performance", "Performance"],
          ].map(([v, label]) => (
            <TabsTrigger key={v} value={v} className="h-9 flex-none px-3">{label}</TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="overview" className="min-w-0 space-y-4">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            <Kpi label={`Hauls · ${k.window_days}d`} value={k.runs_count.toLocaleString("en-US")} />
            <Kpi label="Miles" value={k.miles.toLocaleString("en-US")} />
            <Kpi label="Revenue" value={usd(k.revenue_usd)} sub={`${usd2(k.dollar_per_mile)} per mile`} />
            <Kpi label="Cost per mile" value={usd2(k.cost_per_mile)} />
            <Kpi label="Margin" value={<span className={k.margin_usd < 0 ? "text-bad" : undefined}>{usd(k.margin_usd)}</span>} />
          </div>
          <div className={cn("grid gap-4", wide ? "lg:grid-cols-2" : "")}>
            <dl className="min-w-0 rounded-sm border border-border bg-card px-3 py-1">
              <Fact label="Equipment" value={t.equipment ? EQUIPMENT_LABEL[t.equipment] ?? t.equipment : "—"} />
              <Fact label="Make / model" value={[t.year, t.make, t.model].filter(Boolean).join(" ") || "—"} />
              <Fact label="VIN" value={<span className="font-mono text-xs">{t.vin ?? "—"}</span>} />
              <Fact label="Plate" value={t.plate ?? "—"} />
              {isTruck ? <Fact label="Odometer" value={miles(t.odometer_miles)} /> : null}
              {isTruck ? <Fact label="Driver" value={t.driver_name ?? "Unassigned"} /> : null}
              <Fact label="Home base" value={t.home_base_city ? `${t.home_base_city}, ${t.home_base_state}` : "—"} />
            </dl>
            <div className="min-w-0">
              {t.last_lat != null && t.last_lng != null ? (
                <>
                  <TruckLocationMap lat={t.last_lat} lng={t.last_lng} label={`Last known location of ${t.unit_number}`} />
                  <p className="mt-1.5 text-xs text-muted-foreground">
                    Last known location{t.last_seen_at ? `, seen ${timeAgo(t.last_seen_at)}` : ""}. Demo position: where its latest haul started.
                  </p>
                </>
              ) : (
                <EmptyChart title="No location yet" hint="This unit has not reported a position." className="h-56" />
              )}
            </div>
          </div>
        </TabsContent>

        <TabsContent value="inspections">
          {detail.inspections.length ? (
            <ol className="relative ml-2 space-y-4 border-l border-border pl-5">
              {detail.inspections.map((i) => {
                const found = detail.defects.filter((d) => d.inspection_id === i.id)
                return (
                  <li key={i.id} className="relative min-w-0">
                    <span className="absolute -left-[1.62rem] top-1 grid size-3 place-items-center rounded-full bg-card ring-2 ring-border">
                      <CircleDot className={cn("size-3", RESULT_TONE[i.result as keyof typeof RESULT_TONE] === "bad" ? "text-bad" : RESULT_TONE[i.result as keyof typeof RESULT_TONE] === "warn" ? "text-warn" : "text-good")} aria-hidden />
                    </span>
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="text-[0.9rem] font-semibold">{fmtDate(i.inspected_at)}</span>
                      <ToneChip tone={RESULT_TONE[i.result as keyof typeof RESULT_TONE] ?? "warn"}>{i.result}</ToneChip>
                      <span className="text-xs text-muted-foreground">
                        {i.inspector_name ?? "Unknown inspector"}{i.odometer_at_inspection != null ? ` · ${miles(i.odometer_at_inspection)}` : ""}
                      </span>
                    </div>
                    {i.notes ? <p className="mt-0.5 text-[0.85rem] text-muted-foreground">{i.notes}</p> : null}
                    {found.length ? (
                      <ul className="mt-1 space-y-0.5 text-[0.82rem]">
                        {found.map((d) => (
                          <li key={d.id} className="flex items-center gap-1.5">
                            <ToneChip tone={SEVERITY_TONE[d.severity as keyof typeof SEVERITY_TONE] ?? "warn"}>{d.severity}</ToneChip>
                            <span className="min-w-0 truncate">{d.title}</span>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </li>
                )
              })}
            </ol>
          ) : <EmptyChart title="No inspections on file" />}
        </TabsContent>

        <TabsContent value="defects" className="space-y-4">
          {detail.defects.length ? (
            SEVERITY_ORDER.map((sev) => {
              const rows = detail.defects.filter((d) => d.severity === sev).sort((a, b) => Number(b.status === "open") - Number(a.status === "open"))
              if (!rows.length) return null
              return (
                <section key={sev}>
                  <h3 className="eyebrow mb-1.5 capitalize">{sev}</h3>
                  <ul className="space-y-1.5">
                    {rows.map((d) => (
                      <li key={d.id} className="min-w-0 rounded-sm border border-border bg-card px-3 py-2">
                        <div className="flex items-center gap-2">
                          {d.status === "open" ? <AlertTriangle className={cn("size-4 shrink-0", sev === "critical" ? "text-bad" : "text-warn")} aria-hidden /> : <CheckCircle2 className="size-4 shrink-0 text-good" aria-hidden />}
                          <span className="min-w-0 flex-1 truncate text-[0.88rem] font-medium">{d.title}</span>
                          <ToneChip tone={d.status === "open" ? SEVERITY_TONE[sev] : "ok"}>{d.status === "open" ? "Open" : "Resolved"}</ToneChip>
                        </div>
                        {d.description ? <p className="mt-1 text-[0.82rem] text-muted-foreground">{d.description}</p> : null}
                        <p className="mt-1 text-[0.72rem] text-muted-foreground">
                          Reported {fmtDate(d.reported_at)}{d.resolved_at ? ` · resolved ${fmtDate(d.resolved_at)}` : ""}
                        </p>
                      </li>
                    ))}
                  </ul>
                </section>
              )
            })
          ) : <EmptyChart title="No defects on record" hint="Nothing has been reported for this unit." />}
        </TabsContent>

        <TabsContent value="maintenance" className="space-y-4">
          <section>
            <h3 className="eyebrow mb-1.5">Scheduled</h3>
            {upcoming.length ? (
              <ul className="space-y-1.5">
                {upcoming.map((m) => (
                  <li key={m.id} className="flex min-w-0 items-center gap-2 rounded-sm border border-border bg-card px-3 py-2">
                    <Wrench className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                    <span className="min-w-0 flex-1 truncate text-[0.88rem] font-medium">{m.title ?? m.kind}</span>
                    <span className="shrink-0 text-[0.82rem] text-muted-foreground">{fmtDate(m.scheduled_for)}</span>
                  </li>
                ))}
              </ul>
            ) : <p className="text-sm text-muted-foreground">Nothing scheduled.</p>}
          </section>
          <section>
            <h3 className="eyebrow mb-1.5">History</h3>
            {history.length ? (
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="h-8 px-2 text-xs">Date</TableHead>
                    <TableHead className="h-8 px-2 text-xs">Work</TableHead>
                    <TableHead className="h-8 px-2 text-right text-xs">Odometer</TableHead>
                    <TableHead className="h-8 px-2 text-right text-xs">Cost</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {history.map((m) => (
                    <TableRow key={m.id}>
                      <TableCell className="px-2 py-1.5 text-[0.85rem] whitespace-nowrap">{fmtDate(m.completed_at)}</TableCell>
                      <TableCell className="max-w-0 truncate px-2 py-1.5 text-[0.85rem]">{m.title ?? m.kind}</TableCell>
                      <TableCell className="px-2 py-1.5 text-right text-[0.85rem] tabular-nums">{m.odometer_at != null ? m.odometer_at.toLocaleString("en-US") : "—"}</TableCell>
                      <TableCell className="px-2 py-1.5 text-right text-[0.85rem] tabular-nums">{m.cost_usd != null ? usd(m.cost_usd) : "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : <p className="text-sm text-muted-foreground">No completed work on file.</p>}
          </section>
        </TabsContent>

        <TabsContent value="documents">
          {detail.documents.length ? (
            <ul className="space-y-1.5">
              {detail.documents.map((d) => (
                <li key={d.id} className="flex min-w-0 items-center gap-3 rounded-sm border border-border bg-card px-3 py-2">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[0.88rem] font-medium">{docLabel(d.kind)}</div>
                    <div className="truncate text-xs text-muted-foreground">
                      {d.number ?? "No number"} · expires {fmtDate(d.expires_on)}
                    </div>
                  </div>
                  <ExpiryBadge days={d.days_left} />
                </li>
              ))}
            </ul>
          ) : <EmptyChart title="No documents on file" />}
        </TabsContent>

        <TabsContent value="performance">
          {detail.recent_runs.length ? (
            <>
              <p className="mb-2 text-[0.82rem] text-muted-foreground">Its last {detail.recent_runs.length} hauls, newest first.</p>
              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="h-8 px-2 text-xs">Picked up</TableHead>
                    <TableHead className="h-8 px-2 text-xs">Lane</TableHead>
                    <TableHead className="h-8 px-2 text-right text-xs">Miles</TableHead>
                    <TableHead className="h-8 px-2 text-right text-xs">Revenue</TableHead>
                    <TableHead className="h-8 px-2 text-right text-xs">$/mi</TableHead>
                    <TableHead className="h-8 px-2 text-right text-xs">Margin</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {detail.recent_runs.map((r) => (
                    <TableRow key={r.id}>
                      <TableCell className="px-2 py-1.5 text-[0.82rem] whitespace-nowrap">{fmtDate(r.pickup_at)}</TableCell>
                      <TableCell className="min-w-[9rem] px-2 py-1.5 text-[0.82rem] leading-tight">{r.origin} → {r.dest}</TableCell>
                      <TableCell className="px-2 py-1.5 text-right text-[0.82rem] tabular-nums">{r.miles.toLocaleString("en-US")}</TableCell>
                      <TableCell className="px-2 py-1.5 text-right text-[0.82rem] tabular-nums">{usd(r.revenue_usd)}</TableCell>
                      <TableCell className="px-2 py-1.5 text-right text-[0.82rem] tabular-nums">{usd2(r.dollar_per_mile)}</TableCell>
                      <TableCell className={cn("px-2 py-1.5 text-right text-[0.82rem] whitespace-nowrap tabular-nums", r.margin_usd < 0 && "text-bad")}>
                        {usd(r.margin_usd)}{r.margin_pct != null ? ` · ${r.margin_pct.toFixed(0)}%` : ""}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </>
          ) : (
            <EmptyChart title="No hauls recorded" hint={isTruck ? "No completed runs are linked to this truck yet." : "Trailers do not carry their own run history."} />
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}

export function UnitHeading({ detail }: { detail: TruckDetail }) {
  const t = detail.truck
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
      <span className="font-display text-2xl leading-none font-bold">{t.unit_number}</span>
      <StatusChip status={t.status} />
      <span className="text-sm text-muted-foreground">{t.kind === "trailer" ? "Trailer" : "Truck"}{t.equipment ? ` · ${EQUIPMENT_LABEL[t.equipment] ?? t.equipment}` : ""}</span>
    </div>
  )
}
