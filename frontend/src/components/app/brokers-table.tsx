"use client"

import * as React from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { ArrowDown, ArrowUp, ArrowUpDown, Search, Send } from "lucide-react"
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
import { Segmented } from "./segmented"

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

type HeaderKey = "name" | "health" | "loads" | "winRate" | "revenue" | "recent"
type HeaderSort = { key: HeaderKey; dir: "desc" | "asc" }

/** 1st click "desc" = the most useful direction (A→Z for names, most-recent for last contact, highest→lowest for numerics). */
const HEADER_SORTS: Record<HeaderKey, { desc: (a: BrokerRow, b: BrokerRow) => number; asc: (a: BrokerRow, b: BrokerRow) => number }> = {
  name: {
    desc: (a, b) => a.name.localeCompare(b.name),
    asc: (a, b) => b.name.localeCompare(a.name),
  },
  health: { desc: (a, b) => b.health - a.health, asc: (a, b) => a.health - b.health },
  loads: { desc: (a, b) => b.booked - a.booked, asc: (a, b) => a.booked - b.booked },
  winRate: { desc: (a, b) => b.winRate - a.winRate, asc: (a, b) => a.winRate - b.winRate },
  revenue: { desc: (a, b) => b.revenue - a.revenue, asc: (a, b) => a.revenue - b.revenue },
  // "most recent first" = smallest daysSinceLast first
  recent: { desc: (a, b) => a.daysSinceLast - b.daysSinceLast, asc: (a, b) => b.daysSinceLast - a.daysSinceLast },
}

const ACTIVITY_MONTHS = [12, 7, 3] as const
type ActivityMonths = (typeof ACTIVITY_MONTHS)[number]

export function BrokersTable({ rows, initialSegment, initialSort }: { rows: BrokerRow[]; initialSegment: string; initialSort: string }) {
  const router = useRouter()
  const [segment, setSegment] = React.useState<string>(initialSegment)
  const [q, setQ] = React.useState("")
  const [sort, setSort] = React.useState(SORTS[initialSort] ? initialSort : "health")
  const [headerSort, setHeaderSort] = React.useState<HeaderSort | null>(null)
  const [activityMonths, setActivityMonths] = React.useState<ActivityMonths>(12)
  const [selected, setSelected] = React.useState<Set<string>>(new Set())

  const filtered = React.useMemo(
    () => {
      const base = rows
        .filter((r) => segment === "All" || r.segment === segment)
        .filter((r) => !q || `${r.name} ${r.hq} ${r.contact} ${r.registration}`.toLowerCase().includes(q.toLowerCase()))
      const fn = headerSort ? HEADER_SORTS[headerSort.key][headerSort.dir] : SORTS[sort].fn
      return base.sort(fn)
    },
    [rows, segment, q, sort, headerSort],
  )

  // 1st click: desc. 2nd: asc. 3rd: clear (back to default Select sort).
  const cycleHeader = (key: HeaderKey) => {
    setHeaderSort((cur) => {
      if (!cur || cur.key !== key) return { key, dir: "desc" }
      if (cur.dir === "desc") return { key, dir: "asc" }
      return null
    })
  }

  const onSelectSort = (v: string | null) => {
    setSort(v ?? "health")
    setHeaderSort(null) // Select takes over; clear the header override.
  }

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
        <Select value={sort} onValueChange={onSelectSort}>
          <SelectTrigger className="w-48">
            <span className="text-muted-foreground">Sort:</span>{" "}
            <SelectValue>{headerSort ? "Custom" : SORTS[sort].label}</SelectValue>
          </SelectTrigger>
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
            <TableHead><SortHeader label="Broker" k="name" headerSort={headerSort} onClick={cycleHeader} /></TableHead>
            <TableHead>Segment</TableHead>
            <TableHead><SortHeader label="Health" k="health" headerSort={headerSort} onClick={cycleHeader} /></TableHead>
            <TableHead className="text-right"><SortHeader label="Loads" k="loads" headerSort={headerSort} onClick={cycleHeader} align="right" /></TableHead>
            <TableHead className="text-right"><SortHeader label="Win rate" k="winRate" headerSort={headerSort} onClick={cycleHeader} align="right" /></TableHead>
            <TableHead className="text-right"><SortHeader label="Revenue" k="revenue" headerSort={headerSort} onClick={cycleHeader} align="right" /></TableHead>
            <TableHead className="text-right"><SortHeader label="Last contact" k="recent" headerSort={headerSort} onClick={cycleHeader} align="right" /></TableHead>
            <TableHead className="pr-4">
              <div className="flex items-center justify-between gap-2">
                <span>{activityMonths}-month activity</span>
                <Segmented
                  value={String(activityMonths) as "12" | "7" | "3"}
                  onChange={(v) => setActivityMonths(Number(v) as ActivityMonths)}
                  options={ACTIVITY_MONTHS.map((m) => ({ value: String(m) as "12" | "7" | "3", label: String(m) }))}
                  className="h-6 p-0.5"
                />
              </div>
            </TableHead>
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
              <TableCell className="pr-4"><Sparkline values={r.monthly.slice(-activityMonths)} color={SEGMENT_COLOR[r.segment]} /></TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

/** Clickable / keyboardable sort header. Renders a real <button> so tab + enter/space work. */
function SortHeader({
  label,
  k,
  headerSort,
  onClick,
  align = "left",
}: {
  label: string
  k: HeaderKey
  headerSort: HeaderSort | null
  onClick: (k: HeaderKey) => void
  align?: "left" | "right"
}) {
  const active = headerSort?.key === k
  const dir = active ? headerSort!.dir : null
  const Icon = dir === "desc" ? ArrowDown : dir === "asc" ? ArrowUp : ArrowUpDown
  const ariaLabel = dir ? `${label}, sorted ${dir === "desc" ? "descending" : "ascending"}` : `${label}, click to sort`
  return (
    <button
      type="button"
      onClick={() => onClick(k)}
      aria-label={ariaLabel}
      className={cn(
        "-mx-1 inline-flex w-full items-center gap-1 rounded-sm px-1 py-0.5 text-left transition-colors hover:text-foreground",
        active ? "text-foreground" : "text-muted-foreground",
        align === "right" && "justify-end text-right",
      )}
    >
      <span>{label}</span>
      <Icon className={cn("size-3.5 shrink-0", active ? "opacity-100" : "opacity-40")} aria-hidden />
    </button>
  )
}
