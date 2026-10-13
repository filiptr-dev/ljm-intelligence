"use client"

import { useEffect, useState } from "react"
import { getBackfillProgress, type BackfillProgressRow } from "@/lib/api/inbox-backfill"

/**
 * Gradual historical-import progress — one row per allowed mailbox,
 * showing how far back the nightly ``mail_backfill_step`` has reached.
 */
export function MailBackfillProgress() {
  const [rows, setRows] = useState<BackfillProgressRow[]>([])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const r = await getBackfillProgress()
        if (!cancelled) setRows(r)
      } catch {
        /* ignore */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  if (rows.length === 0) return null
  return (
    <section className="rounded border border-border bg-muted/30 p-3 text-xs text-muted-foreground">
      <div className="mb-1 font-semibold text-foreground">Historical import</div>
      <ul className="space-y-0.5">
        {rows.map((r) => (
          <li key={r.mailbox} className="flex items-center justify-between gap-3">
            <span className="font-mono">{r.mailbox}</span>
            <span>{summarise(r)}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

function summarise(r: BackfillProgressRow): string {
  const through = r.backfilled_through_at ? new Date(r.backfilled_through_at) : null
  const floor = r.floor_at ? new Date(r.floor_at) : null
  if (!through) return "not started"
  const date = through.toISOString().slice(0, 10)
  if (!floor) return `backfilled to ${date}`
  const total = (Date.now() - floor.getTime()) / (1000 * 60 * 60 * 24 * 30)
  const done = (Date.now() - through.getTime()) / (1000 * 60 * 60 * 24 * 30)
  const totalMonths = Math.max(1, Math.round(total))
  const doneMonths = Math.max(0, Math.round(done))
  return `backfilled to ${date} — ${Math.min(doneMonths, totalMonths)} months of ${totalMonths}`
}
