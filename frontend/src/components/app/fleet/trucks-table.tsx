"use client"

import * as React from "react"
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { cn } from "@/lib/utils"
import type { TruckRow } from "@/lib/api/fleet"
import { ExpiryBadge, StatusChip, ToneChip } from "./badges"
import { EQUIPMENT_LABEL, STATUS_ORDER } from "./format"

export type SortKey = "unit" | "status" | "equipment" | "driver" | "odometer" | "miles" | "revenue" | "cost" | "expiry" | "defects"
export type Sort = { key: SortKey; dir: "asc" | "desc" }

const num = (v: number) => v.toLocaleString("en-US")
const usd = (v: number) => `$${Math.round(v).toLocaleString("en-US")}`

const VALUE: Record<SortKey, (r: TruckRow) => string | number | null> = {
  unit: (r) => r.unit_number,
  status: (r) => STATUS_ORDER.indexOf(r.status as (typeof STATUS_ORDER)[number]),
  equipment: (r) => r.equipment ?? "",
  driver: (r) => r.driver_name ?? "",
  odometer: (r) => r.odometer_miles,
  miles: (r) => r.miles_30d,
  revenue: (r) => r.revenue_30d_usd,
  cost: (r) => r.cost_per_mile_30d_usd,
  expiry: (r) => r.next_doc_expiry?.days_left ?? null,
  defects: (r) => r.open_critical_defects * 100 + r.open_defects,
}

export function sortRows(rows: TruckRow[], { key, dir }: Sort): TruckRow[] {
  const get = VALUE[key]
  const sign = dir === "asc" ? 1 : -1
  return [...rows].sort((a, b) => {
    const x = get(a), y = get(b)
    if (x === null && y === null) return 0
    if (x === null) return 1 // blanks always sink to the bottom
    if (y === null) return -1
    const c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "en", { numeric: true })
    return c * sign || a.unit_number.localeCompare(b.unit_number, "en", { numeric: true })
  })
}

const COLS: { key: SortKey; label: string; align?: "right"; className?: string }[] = [
  { key: "unit", label: "Unit" },
  { key: "status", label: "Status" },
  { key: "equipment", label: "Equipment" },
  { key: "driver", label: "Driver", className: "hidden lg:table-cell" },
  { key: "odometer", label: "Odometer", align: "right" },
  { key: "miles", label: "Miles · 30d", align: "right" },
  { key: "revenue", label: "Revenue · 30d", align: "right" },
  { key: "cost", label: "Cost/mi", align: "right" },
  { key: "expiry", label: "Next expiry" },
  { key: "defects", label: "Defects" },
]

export function TrucksTable({
  rows, sort, onSort, onOpen, selectedId,
}: { rows: TruckRow[]; sort: Sort; onSort: (k: SortKey) => void; onOpen: (id: number) => void; selectedId: number | null }) {
  return (
    <Table className="min-w-0" data-testid="fleet-table">
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          {COLS.map((c) => {
            const active = sort.key === c.key
            return (
              <TableHead
                key={c.key}
                aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
                className={cn("h-9 px-2 text-[0.72rem]", c.align === "right" && "text-right", c.className)}
              >
                <button
                  type="button"
                  onClick={() => onSort(c.key)}
                  className={cn(
                    "inline-flex items-center gap-1 font-semibold whitespace-nowrap hover:text-foreground",
                    active ? "text-foreground" : "text-muted-foreground",
                  )}
                >
                  {c.label}
                  {active ? (sort.dir === "asc" ? <ArrowUp className="size-3" aria-hidden /> : <ArrowDown className="size-3" aria-hidden />) : <ChevronsUpDown className="size-3 opacity-40" aria-hidden />}
                </button>
              </TableHead>
            )
          })}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r) => (
          <TableRow
            key={r.id}
            data-state={selectedId === r.id ? "selected" : undefined}
            tabIndex={0}
            role="button"
            aria-label={`Open ${r.unit_number}`}
            onClick={() => onOpen(r.id)}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(r.id) } }}
            className="cursor-pointer focus-visible:bg-muted/60 focus-visible:outline-none"
          >
            <TableCell className="px-2 py-2">
              <div className="font-mono text-[0.85rem] font-semibold">{r.unit_number}</div>
              <div className="truncate text-xs text-muted-foreground">{[r.year, r.make, r.model].filter(Boolean).join(" ")}</div>
            </TableCell>
            <TableCell className="px-2 py-2"><StatusChip status={r.status} /></TableCell>
            <TableCell className="px-2 py-2 text-[0.85rem]">{r.equipment ? EQUIPMENT_LABEL[r.equipment] ?? r.equipment : "—"}</TableCell>
            <TableCell className="hidden truncate px-2 py-2 text-[0.85rem] lg:table-cell">{r.driver_name ?? <span className="text-muted-foreground">Unassigned</span>}</TableCell>
            <TableCell className="px-2 py-2 text-right text-[0.85rem] tabular-nums">{r.odometer_miles == null ? "—" : num(r.odometer_miles)}</TableCell>
            <TableCell className="px-2 py-2 text-right text-[0.85rem] tabular-nums">{r.runs_30d ? num(r.miles_30d) : "—"}</TableCell>
            <TableCell className="px-2 py-2 text-right text-[0.85rem] tabular-nums">{r.runs_30d ? usd(r.revenue_30d_usd) : "—"}</TableCell>
            <TableCell className="px-2 py-2 text-right text-[0.85rem] tabular-nums">{r.cost_per_mile_30d_usd == null ? "—" : `$${r.cost_per_mile_30d_usd.toFixed(2)}`}</TableCell>
            <TableCell className="px-2 py-2">
              {r.next_doc_expiry ? <ExpiryBadge days={r.next_doc_expiry.days_left} /> : <span className="text-muted-foreground">—</span>}
            </TableCell>
            <TableCell className="px-2 py-2">
              {r.open_critical_defects ? (
                <ToneChip tone="bad">{r.open_critical_defects} critical</ToneChip>
              ) : r.open_defects ? (
                <ToneChip tone="warn">{r.open_defects} open</ToneChip>
              ) : (
                <span className="text-muted-foreground">None</span>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
