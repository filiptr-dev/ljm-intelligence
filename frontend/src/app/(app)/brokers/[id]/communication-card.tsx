"use client"

/**
 * Communication — real inbound+outbound threads for this broker.
 *
 * Server-matched by address OR corporate domain (see
 * ``brokers_service._load_communication``). Hidden when the list is
 * empty — a broker we've never corresponded with shouldn't get a
 * conversation card. Each row shows the honest signal we have: date,
 * from, subject, intent chip, sentiment, and when the backfill or
 * triage picked it up, a rate / lane hint.
 */

import * as React from "react"
import { Panel } from "@/components/app/ui"

export type CommunicationItem = {
  sent_at: string
  from_addr: string
  to_addr?: string | null
  subject: string
  intent?: string | null
  sentiment?: number | null
  rate_usd?: number | null
  lane_from?: string | null
  lane_to?: string | null
  direction: "inbound" | "outbound"
}

function formatDate(iso: string): string {
  if (!iso) return ""
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  return d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" })
}

function sentimentLabel(s: number | null | undefined): string | null {
  if (s == null) return null
  if (s >= 0.3) return "positive"
  if (s <= -0.3) return "negative"
  return "neutral"
}

export function CommunicationCard({ items }: { items: CommunicationItem[] }) {
  if (!items || items.length === 0) return null
  return (
    <Panel
      title="Communication"
      description={`${items.length} recent message${items.length === 1 ? "" : "s"}`}
    >
      <ul className="divide-y divide-border/40">
        {items.map((it, i) => (
          <li key={`${it.sent_at}-${i}`} className="py-2 text-sm">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span>{formatDate(it.sent_at)}</span>
                  <span className="rounded px-1 text-[0.65rem] uppercase tracking-wide bg-muted">
                    {it.direction}
                  </span>
                  {it.intent ? (
                    <span className="rounded px-1 text-[0.65rem] uppercase tracking-wide bg-muted">
                      {it.intent}
                    </span>
                  ) : null}
                  {sentimentLabel(it.sentiment) ? (
                    <span className="text-[0.65rem] text-muted-foreground">
                      · {sentimentLabel(it.sentiment)}
                    </span>
                  ) : null}
                </div>
                <div className="truncate font-medium">{it.subject || "(no subject)"}</div>
                <div className="truncate text-xs text-muted-foreground">
                  {it.direction === "inbound" ? "from " : "to "}
                  {it.direction === "inbound" ? it.from_addr : it.to_addr || "—"}
                </div>
              </div>
              <div className="shrink-0 text-right text-xs">
                {it.rate_usd != null ? (
                  <div className="font-mono">${Math.round(it.rate_usd).toLocaleString("en-US")}</div>
                ) : null}
                {it.lane_from && it.lane_to ? (
                  <div className="font-mono text-muted-foreground">
                    {it.lane_from} → {it.lane_to}
                  </div>
                ) : null}
              </div>
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  )
}
