"use client"

import { useEffect, useState } from "react"
import { Panel } from "@/components/app/ui"
import * as inbox from "@/lib/api/inbox"

/**
 * Broker inbox cards — relationship health + response time, fetched by the
 * broker's email domain. One client fetch pair; silently absent if the
 * broker has no email on file.
 */
export function InboxCards({ email }: { email: string | null | undefined }) {
  const domain = (email || "").split("@")[1]?.toLowerCase() ?? ""
  const [rel, setRel] = useState<inbox.RelationshipHealth | null>(null)
  const [rt, setRt] = useState<inbox.ResponseTimeStats | null>(null)

  useEffect(() => {
    if (!domain) return
    let cancelled = false
    ;(async () => {
      try {
        const [r, t] = await Promise.all([inbox.relationship(domain), inbox.responseTime(domain)])
        if (!cancelled) {
          setRel(r)
          setRt(t)
        }
      } catch {
        /* silent — no data is a valid state for a brand-new broker */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [domain])

  if (!domain) return null

  return (
    <div className="mb-5 grid gap-4 lg:grid-cols-2">
      <Panel title="Relationship health" description={`Composite score 0–100 for @${domain}.`}>
        {rel ? (
          <>
            <div className="text-3xl font-bold">{rel.health_score}</div>
            <div className="mt-1 text-xs text-muted-foreground">
              {rel.inbound} inbound · {rel.outbound} outbound · avg sentiment {rel.avg_sentiment.toFixed(2)}
            </div>
            <div className="mt-2 flex gap-4 text-xs">
              <span>👍 praise: {rel.praise}</span>
              <span>⚠ complaints: {rel.complaints}</span>
            </div>
          </>
        ) : (
          <div className="text-sm text-muted-foreground">No inbox data for this broker yet.</div>
        )}
      </Panel>
      <Panel title="Response time" description="Median minutes to reply, ours vs theirs.">
        {rt ? (
          <div className="grid grid-cols-2 gap-4">
            <div>
              <div className="text-xs text-muted-foreground">Ours (median)</div>
              <div className="text-2xl font-semibold">{rt.ours_median_minutes ?? "—"}m</div>
              <div className="text-xs text-muted-foreground">{rt.ours_count} replies</div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">Theirs (median)</div>
              <div className="text-2xl font-semibold">{rt.theirs_median_minutes ?? "—"}m</div>
              <div className="text-xs text-muted-foreground">{rt.theirs_count} replies</div>
            </div>
          </div>
        ) : (
          <div className="text-sm text-muted-foreground">No response-time data yet.</div>
        )}
      </Panel>
    </div>
  )
}
