"use client"

/**
 * Loads board — deduped group rows (one lane per entry, multi-source badges).
 *
 * The backend groups by (broker, origin_state, dest_state, pickup_date,
 * equipment) and returns one `LoadGroupOut` per group. Status flips (new ↔
 * contacted ↔ booked ↔ lost) touch every underlying row atomically through
 * `POST /loads/{group_hash}/status`. The Take-it drawer exposes Call /
 * Email broker / status buttons so an operator never leaves the row.
 */

import * as React from "react"
import { toast } from "sonner"
import { Panel } from "@/components/app/ui"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Loader2, Mail, Phone, Package, X } from "lucide-react"
import type { ContactOption } from "@/components/app/email-composer"
import { SingleEmailBuilder } from "@/components/app/single-email-builder"
import { singleSendAdapter, type EmailSendAdapter } from "@/lib/api/email-send"
import {
  listLoadSources,
  listLoads,
  postLoadsPaste,
  postLoadGroupStatus,
  postLoadGroupInquiry,
  type LoadGroupRow,
  type LoadSourceRow,
  type StatusIn,
} from "@/lib/api/connectors"

const SOURCE_LABEL: Record<string, string> = {
  ai_page: "AI pages",
  paste: "Paste",
  dat: "DAT",
  chr: "CHR",
  loadboard123: "123LB",
  truckstop: "Truckstop",
  inbox: "Inbox",
  demo: "Demo",
  demo2: "Demo",
}

type StatusValue = StatusIn["status"]
type FilterMode = "active" | "all"

function statusPillClass(s: string): string {
  switch (s) {
    case "new":
      return "bg-blue-500/10 text-blue-700"
    case "contacted":
      return "bg-amber-500/10 text-amber-700"
    case "booked":
      return "bg-green-500/10 text-green-700"
    case "lost":
      return "bg-muted text-muted-foreground"
    default:
      return "bg-muted text-muted-foreground"
  }
}

type ComposeState = {
  hash: string
  subject: string
  body: string
  recipient: ContactOption
}

export function LoadsBoard() {
  const [groups, setGroups] = React.useState<LoadGroupRow[]>([])
  const [sources, setSources] = React.useState<LoadSourceRow[]>([])
  const [fetchError, setFetchError] = React.useState(false)
  const [filter, setFilter] = React.useState<FilterMode>("active")
  const [busy, setBusy] = React.useState<string | null>(null)
  const [drawerGroup, setDrawerGroup] = React.useState<LoadGroupRow | null>(null)
  const [pasteText, setPasteText] = React.useState("")
  const [compose, setCompose] = React.useState<ComposeState | null>(null)

  const refresh = React.useCallback(async () => {
    try {
      const [g, s] = await Promise.all([listLoads(200), listLoadSources()])
      setGroups(g)
      setSources(s)
      setFetchError(false)
    } catch {
      setFetchError(true)
    }
  }, [])

  React.useEffect(() => {
    void refresh()
  }, [refresh])

  async function onStatus(group: LoadGroupRow, status: StatusValue) {
    setBusy(`status-${group.group_hash}-${status}`)
    try {
      const r = await postLoadGroupStatus(group.group_hash, status)
      if (!r) {
        toast.error("Status update failed")
      } else {
        toast.success(`→ ${status} (${r.rows_updated} row${r.rows_updated === 1 ? "" : "s"})`)
      }
      await refresh()
      setDrawerGroup(null)
    } finally {
      setBusy(null)
    }
  }

  async function onEmailBroker(group: LoadGroupRow) {
    setBusy(`inq-${group.group_hash}`)
    try {
      const r = await postLoadGroupInquiry(group.group_hash)
      if (!r) {
        toast.error("Could not build inquiry")
        return
      }
      if (!r.broker_email) {
        toast.error("No broker email on file")
        return
      }
      const broker = (group.broker ?? {}) as { name?: string }
      const origin = (group.origin ?? {}) as { state?: string | null }
      const name = broker.name ?? r.broker_email
      setCompose({
        hash: group.group_hash,
        subject: r.subject,
        body: r.body,
        recipient: {
          id: `load:${group.group_hash}`,
          kind: "broker",
          name,
          contactName: name,
          email: r.broker_email,
          // Display-only, same as the broker page's synthesised recipient.
          region: (origin.state ?? "") as ContactOption["region"],
          sub: `Broker · ${origin.state ?? "load inquiry"}`,
        },
      })
      setDrawerGroup(null)
    } finally {
      setBusy(null)
    }
  }

  // Wrap the shared adapter so the group flips to "contacted" only after a
  // successful send (the builder has no "sent" callback; we don't edit it).
  const composeAdapter = React.useMemo<EmailSendAdapter | null>(() => {
    if (!compose) return null
    const hash = compose.hash
    return {
      kind: "single",
      send: async (p) => {
        const res = await singleSendAdapter.send(p)
        if (res.ok) {
          let flipped = false
          try {
            flipped = !!(await postLoadGroupStatus(hash, "contacted"))
          } catch {
            flipped = false
          }
          if (!flipped) toast.error("Sent, but status update failed")
          void refresh()
          setCompose(null)
        }
        return res
      },
    }
  }, [compose, refresh])

  async function onPaste() {
    if (!pasteText.trim()) return
    setBusy("paste")
    try {
      const items = await postLoadsPaste(pasteText)
      toast.success(`Paste parsed — ${items.length} group${items.length === 1 ? "" : "s"}`)
      setPasteText("")
      await refresh()
    } catch {
      toast.error("Paste failed")
    } finally {
      setBusy(null)
    }
  }

  const visibleGroups = React.useMemo(() => {
    if (filter === "all") return groups
    return groups.filter((g) => g.status === "new" || g.status === "contacted")
  }, [groups, filter])

  // Yellow chip rule: a source whose most recent agent run ended with
  // `login_challenge` wants operator attention — the backend surfaces that
  // under `reason="login_challenge"` on the source row (same strict string
  // the agent writes to `agent_runs.status`).
  function sourceChipClass(s: LoadSourceRow): string {
    if (s.reason === "login_challenge") return "bg-yellow-500/15 text-yellow-800"
    return s.enabled
      ? "bg-green-500/10 text-green-700"
      : "bg-muted text-muted-foreground"
  }

  return (
    <div className="space-y-4">
      {fetchError && (
        <p className="text-sm text-red-600">Could not load sources — try again.</p>
      )}
      <Panel title="Sources">
        <div className="flex flex-wrap gap-2">
          {sources.map((s) => (
            <span
              key={s.kind}
              className={`rounded px-2 py-0.5 text-xs ${sourceChipClass(s)}`}
              title={s.reason ?? ""}
            >
              {SOURCE_LABEL[s.kind] ?? s.kind}
              {s.reason === "login_challenge" ? " · login needed" : ""}
            </span>
          ))}
        </div>
      </Panel>

      <Panel
        title="Paste a lane"
        description="Paste any broker email, Slack blurb, or spreadsheet row. The AI turns it into a load row and dedupes against existing lanes."
      >
        <div className="flex flex-col gap-2">
          <textarea
            value={pasteText}
            onChange={(e) => setPasteText(e.target.value)}
            placeholder="Example: Dallas, TX → Atlanta, GA. 48ft reefer, pickup Thu 10/9, $2,400. Broker Acme Logistics, mike@acme.co, 555-0101."
            className="min-h-[80px] w-full rounded-md border bg-background p-2 text-sm font-mono"
          />
          <div>
            <Button onClick={onPaste} disabled={busy === "paste" || !pasteText.trim()}>
              {busy === "paste" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Package className="h-4 w-4" />}
              Parse paste
            </Button>
          </div>
        </div>
      </Panel>

      <Panel
        title={`Loads (${visibleGroups.length})`}
        description={filter === "active" ? "Showing new + contacted. Toggle Show all to see booked/lost." : "Showing every status."}
      >
        <div className="mb-2 flex items-center gap-2">
          <Button
            size="sm"
            variant={filter === "active" ? "default" : "outline"}
            onClick={() => setFilter("active")}
          >
            Active
          </Button>
          <Button
            size="sm"
            variant={filter === "all" ? "default" : "outline"}
            onClick={() => setFilter("all")}
          >
            Show all
          </Button>
        </div>

        {visibleGroups.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No loads to show. Paste a lane above, or enable a source in Settings → Load boards.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="text-xs text-muted-foreground">
                <tr>
                  <th className="py-1 pr-3 text-left">Broker</th>
                  <th className="py-1 pr-3 text-left">Lane</th>
                  <th className="py-1 pr-3 text-left">Pickup</th>
                  <th className="py-1 pr-3 text-left">Equipment</th>
                  <th className="py-1 pr-3 text-right">Rate</th>
                  <th className="py-1 pr-3 text-left">Sources</th>
                  <th className="py-1 pr-3 text-left">Status</th>
                  <th className="py-1 pr-3" />
                </tr>
              </thead>
              <tbody>
                {visibleGroups.map((g) => {
                  const broker = (g.broker ?? {}) as {
                    name?: string
                    email?: string | null
                    phone?: string | null
                  }
                  const origin = (g.origin ?? {}) as { state?: string | null; city?: string | null }
                  const dest = (g.dest ?? {}) as { state?: string | null; city?: string | null }
                  const pickup = g.pickup_date ? String(g.pickup_date).slice(0, 10) : "—"
                  return (
                    <tr key={g.group_hash} className="border-t">
                      <td className="py-2 pr-3 font-medium">{broker.name ?? "—"}</td>
                      <td className="py-2 pr-3">
                        {(origin.city ?? "") || "?"}{origin.state ? `, ${origin.state}` : ""} →{" "}
                        {(dest.city ?? "") || "?"}{dest.state ? `, ${dest.state}` : ""}
                      </td>
                      <td className="py-2 pr-3">{pickup}</td>
                      <td className="py-2 pr-3">{g.equipment ?? "—"}</td>
                      <td className="py-2 pr-3 text-right">
                        {g.rate_usd != null ? `$${Number(g.rate_usd).toLocaleString()}` : "—"}
                      </td>
                      <td className="py-2 pr-3">
                        <div className="flex flex-wrap gap-1">
                          {(g.sources ?? []).map((src, i) => {
                            const s = src as { kind?: string; count?: number }
                            return (
                              <span
                                key={`${g.group_hash}-${s.kind}-${i}`}
                                className="rounded bg-green-500/10 px-1.5 py-0.5 text-[11px] text-green-700"
                              >
                                {SOURCE_LABEL[s.kind ?? ""] ?? s.kind}
                                {(s.count ?? 1) > 1 ? ` ×${s.count}` : ""}
                              </span>
                            )
                          })}
                          {g.is_demo && (
                            <span className="rounded bg-purple-500/15 px-1.5 py-0.5 text-[11px] text-purple-700">
                              Demo
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="py-2 pr-3">
                        <span className={`rounded px-2 py-0.5 text-xs ${statusPillClass(g.status)}`}>
                          {g.status}
                        </span>
                      </td>
                      <td className="py-2 pr-3 text-right">
                        <Button size="sm" variant="outline" onClick={() => setDrawerGroup(g)}>
                          Take it
                        </Button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {compose && composeAdapter && (
        <div className="fixed inset-0 z-[60] flex items-start justify-center overflow-y-auto bg-black/40 p-4" role="dialog" aria-modal="true">
          <div className="w-full max-w-3xl rounded-md border bg-background p-4 shadow-xl">
            <div className="mb-2 flex justify-end">
              <Button size="sm" variant="ghost" onClick={() => setCompose(null)} aria-label="Close">
                <X className="h-4 w-4" />
              </Button>
            </div>
            <SingleEmailBuilder
              contacts={[]}
              backHref="/loads"
              backLabel="Back to loads"
              initialRecipient={compose.recipient}
              initialToId={compose.recipient.id}
              initialSubject={compose.subject}
              initialBody={compose.body}
              onSend={composeAdapter}
            />
          </div>
        </div>
      )}

      {drawerGroup && (
        <TakeItDrawer
          group={drawerGroup}
          busy={busy}
          onClose={() => setDrawerGroup(null)}
          onStatus={onStatus}
          onEmail={onEmailBroker}
        />
      )}
    </div>
  )
}

function TakeItDrawer({
  group,
  busy,
  onClose,
  onStatus,
  onEmail,
}: {
  group: LoadGroupRow
  busy: string | null
  onClose: () => void
  onStatus: (g: LoadGroupRow, s: StatusValue) => void | Promise<void>
  onEmail: (g: LoadGroupRow) => void | Promise<void>
}) {
  const broker = (group.broker ?? {}) as {
    name?: string
    email?: string | null
    phone?: string | null
  }
  const phone = broker.phone ?? null
  const email = broker.email ?? null

  return (
    <div className="fixed inset-0 z-50 flex" role="dialog" aria-modal="true">
      <div
        className="flex-1 bg-black/40"
        onClick={onClose}
        aria-hidden
      />
      <div className="w-full max-w-md overflow-y-auto border-l bg-background p-5 shadow-xl">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Take it</h2>
          <Button size="sm" variant="ghost" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>
        <p className="mb-3 text-sm text-muted-foreground">
          Group <code className="font-mono text-xs">{group.group_hash.slice(0, 10)}…</code> · status{" "}
          <strong>{group.status}</strong>
        </p>
        <div className="space-y-2 text-sm">
          <p>
            <strong>Broker:</strong> {broker.name ?? "—"}
          </p>
          <p>
            <strong>Phone:</strong> {phone ?? "—"}
          </p>
          <p>
            <strong>Email:</strong> {email ?? "—"}
          </p>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          {phone ? (
            <a
              href={`tel:${phone}`}
              className="inline-flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            >
              <Phone className="h-4 w-4" /> Call
            </a>
          ) : (
            <Button variant="outline" disabled>
              <Phone className="h-4 w-4" /> Call (no phone)
            </Button>
          )}
          <Button variant="outline" onClick={() => onEmail(group)} disabled={busy === `inq-${group.group_hash}`}>
            {busy === `inq-${group.group_hash}` ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Mail className="h-4 w-4" />
            )}
            Email broker
          </Button>
        </div>
        <div className="mt-5">
          <Label>Status</Label>
          <div className="mt-2 flex flex-wrap gap-2">
            {(["new", "contacted", "booked", "lost"] as StatusValue[]).map((s) => {
              const key = `status-${group.group_hash}-${s}`
              return (
                <Button
                  key={s}
                  size="sm"
                  variant={group.status === s ? "default" : "outline"}
                  disabled={busy === key}
                  onClick={() => onStatus(group, s)}
                >
                  {busy === key ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
                  {s}
                </Button>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
