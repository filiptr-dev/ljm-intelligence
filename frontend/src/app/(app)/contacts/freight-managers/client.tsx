"use client"

/**
 * Freight-managers client island — pagination + "Email campaign" modal.
 *
 * Dry-run toggles ON by default so the first click never actually sends.
 * The modal confirms the recipient list before switching to a real send.
 */

import * as React from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import {
  type Contact, listFreightManagers, runCampaign,
} from "@/lib/api/contacts"

type Props = {
  initial: { items: Contact[]; next_cursor: string | null }
}

export default function FreightManagersClient({ initial }: Props) {
  const [items, setItems] = React.useState<Contact[]>(initial.items)
  const [cursor, setCursor] = React.useState<string | null>(initial.next_cursor)
  const [loading, setLoading] = React.useState(false)
  const [open, setOpen] = React.useState(false)
  const [dryRun, setDryRun] = React.useState(true)
  const [tone, setTone] = React.useState<"professional" | "friendly" | "direct" | "persuasive">("professional")
  const [preview, setPreview] = React.useState<{ email: string; name: string | null }[] | null>(null)
  const [sending, setSending] = React.useState(false)

  const loadMore = async () => {
    if (!cursor || loading) return
    setLoading(true)
    try {
      const next = await listFreightManagers(cursor, 50)
      setItems((prev) => [...prev, ...(next.items ?? [])])
      setCursor(next.next_cursor ?? null)
    } finally {
      setLoading(false)
    }
  }

  const openCampaign = () => {
    setDryRun(true)
    setPreview(null)
    setOpen(true)
  }

  const run = async () => {
    setSending(true)
    try {
      const res = await runCampaign({ tone, dry_run: dryRun, limit: 100 })
      if (dryRun) {
        setPreview(res.recipients.map((r) => ({ email: r.email, name: r.name ?? null })))
        toast.success(`Dry run — ${res.recipients.length} recipients`)
      } else {
        toast.success(`Enqueued ${res.enqueued} send jobs`)
        setOpen(false)
      }
    } catch (e) {
      toast.error(String(e))
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="text-sm text-muted-foreground">{items.length} contacts</div>
        <Button onClick={openCampaign} className="font-semibold">Email campaign</Button>
      </div>

      {items.length === 0 ? (
        <div className="rounded-sm border border-dashed border-border bg-muted p-6 text-sm text-muted-foreground">
          No freight-manager contacts yet. Open a lead and run <strong>Refresh contacts</strong> to populate.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-sm border border-border bg-card">
          <table className="min-w-full text-sm">
            <thead className="bg-muted text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-3 py-2 text-left">Name</th>
                <th className="px-3 py-2 text-left">Title</th>
                <th className="px-3 py-2 text-left">Lead</th>
                <th className="px-3 py-2 text-left">Email</th>
                <th className="px-3 py-2 text-left">Source</th>
                <th className="px-3 py-2 text-left">Evidence</th>
              </tr>
            </thead>
            <tbody>
              {items.map((c) => (
                <tr key={c.id} className="border-t border-border">
                  <td className="px-3 py-2 font-medium">{c.name ?? "—"}</td>
                  <td className="px-3 py-2">{c.title ?? "—"}</td>
                  <td className="px-3 py-2 text-xs text-muted-foreground">{c.lead_id}</td>
                  <td className="px-3 py-2">{c.email ?? <span className="text-warn">no email</span>}</td>
                  <td className="px-3 py-2 text-xs">{c.source ?? "—"}</td>
                  <td className="px-3 py-2 text-xs">
                    {c.source_url ? <a className="underline" href={c.source_url} target="_blank" rel="noreferrer">link</a> : "—"}
                    {" · sighted ×"}{c.evidence_count}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {cursor ? (
        <Button variant="outline" onClick={loadMore} disabled={loading}>
          {loading ? "Loading…" : "Load more"}
        </Button>
      ) : null}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>Freight-manager campaign</DialogTitle>
            <DialogDescription>
              Sends one AI-drafted email per contact in the segment. Dry-run previews recipients without sending.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3">
            <div className="flex items-center gap-2 text-sm">
              <label className="font-medium">Tone:</label>
              <select
                className="rounded-sm border border-border bg-background px-2 py-1 text-sm"
                value={tone}
                onChange={(e) => setTone(e.target.value as typeof tone)}
              >
                <option value="professional">Professional</option>
                <option value="friendly">Friendly</option>
                <option value="direct">Direct</option>
                <option value="persuasive">Persuasive</option>
              </select>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
              Dry run (don&apos;t actually send)
            </label>
            {preview ? (
              <div className="max-h-48 overflow-y-auto rounded-sm border border-border bg-muted p-2 text-xs">
                <div className="eyebrow mb-1">Would send to {preview.length}</div>
                {preview.map((p, i) => (
                  <div key={i}>{p.name ?? ""} &lt;{p.email}&gt;</div>
                ))}
              </div>
            ) : null}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)} disabled={sending}>Close</Button>
            <Button onClick={run} disabled={sending} className="font-semibold">
              {dryRun ? "Preview recipients" : "Send campaign"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
