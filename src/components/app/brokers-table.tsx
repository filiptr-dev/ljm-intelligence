"use client"

import * as React from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { Search, Send } from "lucide-react"
import { Plate } from "@/components/brand/marks"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { SEGMENTS, type Segment } from "@/lib/analytics"
import type { Region } from "@/lib/data/geo"
import { money, pct } from "@/lib/format"
import { cn } from "@/lib/utils"
import { HealthPill, SEGMENT_COLOR, SegmentBadge, Sparkline } from "./ui"

export type BrokerRow = {
  id: string
  name: string
  region: Region
  country: string
  hq: string
  registration: string
  contact: string
  segment: Segment
  booked: number
  rejected: number
  winRate: number
  revenue: number
  health: number
  healthDelta: number
  daysSinceLast: number
  monthly: number[]
  paymentIssues: number
}

const SORTS: Record<string, { label: string; fn: (a: BrokerRow, b: BrokerRow) => number }> = {
  health: { label: "Health score", fn: (a, b) => b.health - a.health },
  loads: { label: "Loads booked", fn: (a, b) => b.booked - a.booked },
  rejections: { label: "Most rejections", fn: (a, b) => b.rejected - a.rejected },
  recent: { label: "Last contact", fn: (a, b) => a.daysSinceLast - b.daysSinceLast },
  payment: { label: "Overdue invoices", fn: (a, b) => b.paymentIssues - a.paymentIssues || b.booked - a.booked },
}

export function BrokersTable({ rows, initialSegment, initialSort }: { rows: BrokerRow[]; initialSegment: string; initialSort: string }) {
  const router = useRouter()
  const [segment, setSegment] = React.useState<string>(initialSegment)
  const [region, setRegion] = React.useState<"all" | Region>("all")
  const [q, setQ] = React.useState("")
  const [sort, setSort] = React.useState(SORTS[initialSort] ? initialSort : "health")
  const [selected, setSelected] = React.useState<Set<string>>(new Set())

  const filtered = React.useMemo(
    () =>
      rows
        .filter((r) => segment === "All" || r.segment === segment)
        .filter((r) => region === "all" || r.region === region)
        .filter((r) => !q || `${r.name} ${r.hq} ${r.contact} ${r.registration}`.toLowerCase().includes(q.toLowerCase()))
        .sort(SORTS[sort].fn),
    [rows, segment, region, q, sort],
  )

  const counts = React.useMemo(() => {
    const c: Record<string, number> = { All: rows.length }
    rows.forEach((r) => (c[r.segment] = (c[r.segment] ?? 0) + 1))
    return c
  }, [rows])

  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })
  const allOn = filtered.length > 0 && filtered.every((r) => selected.has(r.id))

  return (
    <div className="rounded-sm border border-border bg-card">
      <div className="flex flex-wrap gap-1 border-b border-border p-2">
        {["All", ...SEGMENTS].map((sg) => (
          <button
            key={sg}
            onClick={() => setSegment(sg)}
            className={cn(
              "flex h-8 items-center gap-2 rounded-sm px-3 text-sm font-medium transition-colors",
              segment === sg ? "bg-asphalt text-white" : "hover:bg-muted",
            )}
          >
            {sg !== "All" ? <span className="size-2 rounded-[2px]" style={{ background: SEGMENT_COLOR[sg as Segment] }} /> : null}
            {sg}
            <span className={cn("num font-mono text-xs", segment === sg ? "text-[#b9bcc2]" : "text-muted-foreground")}>{counts[sg] ?? 0}</span>
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
        <div className="relative w-full sm:w-72">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search broker, city, MC / VAT…" className="pl-8" />
        </div>
        <Select value={region} onValueChange={(v) => setRegion((v ?? "all") as "all" | Region)}>
          <SelectTrigger className="w-36"><SelectValue>{region === "all" ? "US & Europe" : region === "US" ? "United States" : "Europe"}</SelectValue></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">US &amp; Europe</SelectItem>
            <SelectItem value="US">United States</SelectItem>
            <SelectItem value="EU">Europe</SelectItem>
          </SelectContent>
        </Select>
        <Select value={sort} onValueChange={(v) => setSort(v ?? "health")}>
          <SelectTrigger className="w-48"><span className="text-muted-foreground">Sort:</span> <SelectValue>{SORTS[sort].label}</SelectValue></SelectTrigger>
          <SelectContent>
            {Object.entries(SORTS).map(([k, v]) => (
              <SelectItem key={k} value={k}>{v.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="ml-auto flex items-center gap-3">
          <span className="text-sm text-muted-foreground">{filtered.length} brokers</span>
          <Button
            disabled={!selected.size}
            onClick={() => router.push(`/outreach?audience=existing&ids=${[...selected].join(",")}&campaign=reengage`)}
            className="font-semibold"
          >
            <Send /> Email {selected.size || ""} selected
          </Button>
        </div>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-10 pl-4">
              <Checkbox
                checked={allOn}
                onCheckedChange={(v) => setSelected(v ? new Set(filtered.map((r) => r.id)) : new Set())}
                aria-label="Select all"
              />
            </TableHead>
            <TableHead>Broker</TableHead>
            <TableHead>Segment</TableHead>
            <TableHead>Health</TableHead>
            <TableHead className="text-right">Loads</TableHead>
            <TableHead className="text-right">Win rate</TableHead>
            <TableHead className="text-right">Revenue</TableHead>
            <TableHead className="text-right">Last contact</TableHead>
            <TableHead className="pr-4">12-month activity</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {filtered.map((r) => (
            <TableRow key={r.id} data-state={selected.has(r.id) ? "selected" : undefined} className="data-[state=selected]:bg-accent">
              <TableCell className="pl-4">
                <Checkbox checked={selected.has(r.id)} onCheckedChange={() => toggle(r.id)} aria-label={`Select ${r.name}`} />
              </TableCell>
              <TableCell className="max-w-[320px]">
                <Link href={`/brokers/${r.id}`} className="block truncate font-semibold hover:underline">{r.name}</Link>
                <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
                  <Plate region={r.region} value={r.registration.replace(/^[A-Z]{2}(?=\d)/, "")} country={r.country} className="scale-90 origin-left" />
                  <span className="truncate">{r.hq} · {r.contact}</span>
                </div>
              </TableCell>
              <TableCell><SegmentBadge segment={r.segment} /></TableCell>
              <TableCell><HealthPill value={r.health} delta={r.healthDelta} /></TableCell>
              <TableCell className="num text-right font-mono">{r.booked}</TableCell>
              <TableCell className="num text-right font-mono">{r.booked + r.rejected ? pct(r.winRate) : "–"}</TableCell>
              <TableCell className="num text-right font-mono">{money(r.revenue, r.region, true)}</TableCell>
              <TableCell className={cn("num text-right font-mono", r.daysSinceLast > 90 && "text-bad")}>{r.daysSinceLast}d</TableCell>
              <TableCell className="pr-4"><Sparkline values={r.monthly} color={SEGMENT_COLOR[r.segment]} /></TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
