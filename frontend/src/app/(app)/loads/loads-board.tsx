"use client"

/**
 * Loads board — rows from every enabled source, source chip per vendor.
 *
 * Zero AI scoring; this is a plain list ordered by posted_at. The six source
 * chips (ai_page, paste, dat, chr, loadboard123, truckstop) always render so
 * the owner can see which vendors are turned off without opening Settings.
 */

import * as React from "react"
import { Panel } from "@/components/app/ui"
import { listLoadSources, listLoads, type LoadRow, type LoadSourceRow } from "@/lib/api/connectors"

const SOURCE_LABEL: Record<string, string> = {
  ai_page: "AI pages",
  paste: "Paste",
  dat: "DAT",
  chr: "CHR",
  loadboard123: "123LB",
  truckstop: "Truckstop",
}

export function LoadsBoard() {
  const [rows, setRows] = React.useState<LoadRow[]>([])
  const [sources, setSources] = React.useState<LoadSourceRow[]>([])
  const [fetchError, setFetchError] = React.useState(false)

  React.useEffect(() => {
    void (async () => {
      try {
        const [l, s] = await Promise.all([listLoads(50), listLoadSources()])
        setRows(l)
        setSources(s)
      } catch {
        // Mirror connectors-panel's pattern: swallow + surface a one-line
        // signal to the operator. Reloading the page is the current escape
        // route — a retry button is deliberately out of scope for v1.
        setFetchError(true)
      }
    })()
  }, [])

  return (
    <div className="space-y-4">
      {fetchError && (
        <p className="text-sm text-red-600">Could not load sources</p>
      )}
      <Panel title="Sources">
        <div className="flex flex-wrap gap-2">
          {sources.map((s) => (
            <span
              key={s.kind}
              className={
                "rounded px-2 py-0.5 text-xs " +
                (s.enabled ? "bg-green-500/10 text-green-700" : "bg-muted text-muted-foreground")
              }
              title={s.reason ?? ""}
            >
              {SOURCE_LABEL[s.kind] ?? s.kind}
            </span>
          ))}
        </div>
      </Panel>
      <Panel title="Live loads">
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No loads yet. Enable at least one vendor in Settings → Load sources, then click Refresh.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="text-xs text-muted-foreground">
                <tr>
                  <th className="py-1 pr-3 text-left">Source</th>
                  <th className="py-1 pr-3 text-left">Broker</th>
                  <th className="py-1 pr-3 text-left">Origin</th>
                  <th className="py-1 pr-3 text-left">Destination</th>
                  <th className="py-1 pr-3 text-left">Equipment</th>
                  <th className="py-1 pr-3 text-right">Rate</th>
                  <th className="py-1 pr-3 text-right">Miles</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="border-t">
                    <td className="py-2 pr-3 text-xs">{SOURCE_LABEL[r.source] ?? r.source}</td>
                    <td className="py-2 pr-3">{r.broker_name}</td>
                    <td className="py-2 pr-3">
                      {r.origin_city ?? "—"}
                      {r.origin_state ? `, ${r.origin_state}` : ""}
                    </td>
                    <td className="py-2 pr-3">
                      {r.dest_city ?? "—"}
                      {r.dest_state ? `, ${r.dest_state}` : ""}
                    </td>
                    <td className="py-2 pr-3">{r.equipment ?? "—"}</td>
                    <td className="py-2 pr-3 text-right">
                      {r.rate_usd != null ? `$${r.rate_usd.toLocaleString()}` : "—"}
                    </td>
                    <td className="py-2 pr-3 text-right">{r.miles ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  )
}
