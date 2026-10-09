"use client"

import * as React from "react"
import { ChevronDown } from "lucide-react"
import { cn } from "@/lib/utils"
import type { AlertStrip } from "@/lib/api/fleet"
import { ExpiryBadge, ToneChip } from "./badges"
import { fmtDate, when } from "./format"

type Item = { key: string; truckId: number; label: React.ReactNode }

/**
 * Three plain-sentence cards (same tone as the lanes statements). The sentence is written on the
 * server from the same rows, so it can't disagree with the list under "Show which".
 */
export function AlertsStrip({ alerts, onOpen }: { alerts: AlertStrip; onOpen: (truckId: number) => void }) {
  const text = (k: string) => alerts.statements.find((s) => s.key === k)
  const cards: { key: string; title: string; tone: "bad" | "warn"; sentence: string; items: Item[]; empty: boolean }[] = [
    {
      key: "docs", title: "Documents", empty: !alerts.expiring_docs.length,
      tone: alerts.expiring_docs.some((d) => d.days_left <= 7) ? "bad" : "warn",
      sentence: text("docs")?.text ?? "",
      items: alerts.expiring_docs.map((d) => ({
        key: `d${d.id}`, truckId: d.truck_id,
        label: <><span className="font-mono text-xs font-semibold">{d.unit_number}</span><ExpiryBadge days={d.days_left} kind={d.kind} /></>,
      })),
    },
    {
      key: "defects", title: "Critical defects", empty: !alerts.critical_defects.length, tone: "bad",
      sentence: text("defects")?.text ?? "",
      items: alerts.critical_defects.map((d) => ({
        key: `f${d.id}`, truckId: d.truck_id,
        label: <><span className="font-mono text-xs font-semibold">{d.unit_number}</span><span className="min-w-0 truncate">{d.title}</span></>,
      })),
    },
    {
      key: "maintenance", title: "Maintenance this week", empty: !alerts.maintenance_due.length, tone: "warn",
      sentence: text("maintenance")?.text ?? "",
      items: alerts.maintenance_due.map((m) => ({
        key: `m${m.id}`, truckId: m.truck_id,
        label: (
          <>
            <span className="font-mono text-xs font-semibold">{m.unit_number}</span>
            <span className="min-w-0 truncate">{m.title ?? m.kind}</span>
            <ToneChip tone={m.days_until <= 1 ? "bad" : "warn"}>{when(m.days_until)} · {fmtDate(m.scheduled_for)}</ToneChip>
          </>
        ),
      })),
    },
  ]
  return (
    <div className="mb-5 grid gap-3 lg:grid-cols-3" data-testid="fleet-alerts">
      {cards.map(({ key, ...c }) => <AlertCard key={key} {...c} onOpen={onOpen} />)}
    </div>
  )
}

function AlertCard({
  title, tone, sentence, items, empty, onOpen,
}: { title: string; tone: "bad" | "warn"; sentence: string; items: Item[]; empty: boolean; onOpen: (id: number) => void }) {
  const [open, setOpen] = React.useState(false)
  return (
    <div className={cn("min-w-0 rounded-sm border-l-2 bg-muted/40 px-3 py-2.5", empty ? "border-good" : tone === "bad" ? "border-bad" : "border-warn")}>
      <div className="eyebrow mb-0.5">{title}</div>
      <p className="text-[0.88rem] leading-relaxed">{sentence}</p>
      {!empty ? (
        <>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground"
          >
            {open ? "Hide" : "Show which"}
            <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} aria-hidden />
          </button>
          {open ? (
            <ul className="mt-2 space-y-1">
              {items.map((it) => (
                <li key={it.key}>
                  <button
                    type="button"
                    onClick={() => onOpen(it.truckId)}
                    className="flex w-full min-w-0 items-center gap-2 rounded-sm px-1.5 py-1 text-left text-[0.82rem] hover:bg-background"
                  >
                    {it.label}
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </>
      ) : null}
    </div>
  )
}
