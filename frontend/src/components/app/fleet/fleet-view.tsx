"use client"

import * as React from "react"
import { Segmented } from "@/components/app/segmented"
import { PageHeader, Panel } from "@/components/app/ui"
import { EmptyChart } from "@/components/charts/primitives"
import { cn } from "@/lib/utils"
import type { AlertStrip, FleetList } from "@/lib/api/fleet"
import { AlertsStrip } from "./alerts-strip"
import { EQUIPMENT_LABEL, EQUIPMENT_ORDER, STATUS_LABEL, STATUS_ORDER } from "./format"
import { TruckDrawer } from "./truck-drawer"
import { sortRows, TrucksTable, type Sort, type SortKey } from "./trucks-table"

type Kind = "trucks" | "trailers"

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "inline-flex h-7 items-center gap-1.5 rounded-sm border px-2.5 text-[0.8rem] font-medium whitespace-nowrap transition-colors",
        active ? "border-asphalt bg-asphalt text-white" : "border-input bg-background text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  )
}

export function FleetView({ initial }: { initial: { list: FleetList; alerts: AlertStrip } }) {
  const { list, alerts } = initial
  const [kind, setKind] = React.useState<Kind>("trucks")
  const [status, setStatus] = React.useState<string | null>(null)
  const [equipment, setEquipment] = React.useState<string | null>(null)
  const [sort, setSort] = React.useState<Sort>({ key: "unit", dir: "asc" })
  const [openId, setOpenId] = React.useState<number | null>(null)

  const all = kind === "trucks" ? list.trucks : list.trailers
  const counts = React.useMemo(() => {
    const c: Record<string, number> = {}
    for (const r of all) c[r.status] = (c[r.status] ?? 0) + 1
    return c
  }, [all])
  const rows = React.useMemo(
    () => sortRows(all.filter((r) => (!status || r.status === status) && (!equipment || r.equipment === equipment)), sort),
    [all, status, equipment, sort],
  )
  const onSort = (key: SortKey) => setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }))
  const switchKind = (k: Kind) => { setKind(k); setStatus(null); setEquipment(null) }

  return (
    <>
      <PageHeader
        eyebrow="Tools"
        title="Fleet"
        description={
          <>
            Your trucks and trailers, their inspections, defects, maintenance and paperwork, next to what each one earns.
            {list.source === "demo" ? " Showing demo data until your fleet software is connected." : null}
          </>
        }
      />
      <AlertsStrip alerts={alerts} onOpen={setOpenId} />
      <Panel
        title={kind === "trucks" ? "Trucks" : "Trailers"}
        description={`${rows.length} of ${all.length} shown`}
        action={
          <Segmented<Kind>
            value={kind}
            onChange={switchKind}
            options={[
              { value: "trucks", label: `Trucks ${list.trucks.length}` },
              { value: "trailers", label: `Trailers ${list.trailers.length}` },
            ]}
          />
        }
        bodyClassName="p-0"
      >
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border px-4 py-3">
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by status">
            <Chip active={status === null} onClick={() => setStatus(null)}>All {all.length}</Chip>
            {STATUS_ORDER.map((s) => (
              <Chip key={s} active={status === s} onClick={() => setStatus(status === s ? null : s)}>
                {STATUS_LABEL[s]} <span className="tabular-nums opacity-70">{counts[s] ?? 0}</span>
              </Chip>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-1.5 md:ml-auto" role="group" aria-label="Filter by equipment">
            <Chip active={equipment === null} onClick={() => setEquipment(null)}>Any equipment</Chip>
            {EQUIPMENT_ORDER.map((e) => (
              <Chip key={e} active={equipment === e} onClick={() => setEquipment(equipment === e ? null : e)}>{EQUIPMENT_LABEL[e]}</Chip>
            ))}
          </div>
        </div>
        {rows.length ? (
          <TrucksTable rows={rows} sort={sort} onSort={onSort} onOpen={setOpenId} selectedId={openId} />
        ) : (
          <div className="p-4"><EmptyChart title="Nothing matches these filters" hint="Clear a status or equipment chip to see the rest of the fleet." /></div>
        )}
      </Panel>
      <TruckDrawer id={openId} onClose={() => setOpenId(null)} />
    </>
  )
}
