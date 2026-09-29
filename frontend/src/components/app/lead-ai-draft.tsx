"use client"

/**
 * One-click AI draft for a real crawled lead — the "zero typing" flow.
 *
 * When it opens: fires POST /api/email/draft immediately for the current tone, then
 * shows subject + branded HTML preview. Tone chips regenerate. Send is simulated
 * (persists locally via the existing `useEngine().sendEmail` so campaigns UI keeps
 * working), plus a toast; no real SMTP wiring here.
 */

import * as React from "react"
import { toast } from "sonner"
import { RefreshCw, Send, Sparkles } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { EMAIL_TONES, STANCE_LABEL, TONE_LABEL, type EmailDraft, type EmailTone } from "@/lib/backend"
import type { Lead } from "@/lib/data/types"
import { cn } from "@/lib/utils"

/**
 * Anything the dialog can draft for. `request` is sent to /api/email/draft as-is
 * (either `{ lead_id }` for a stored crawler lead or `{ lead: {...} }` inline).
 */
export type DraftTarget = {
  id: string
  name: string
  email: string
  request: { lead_id: string } | { lead: Record<string, unknown> }
}

type Props = {
  /** a Lead from the lead finder; converted to a DraftTarget */
  lead?: Lead | null
  /** or any other target, e.g. an existing broker with its sentiment data */
  target?: DraftTarget | null
  open: boolean
  onOpenChange: (v: boolean) => void
  onSent?: (draft: EmailDraft) => void
}

function leadToTarget(lead: Lead): DraftTarget {
  const isRealLead = /^(MC|DOT|DOMAIN)-/.test(lead.id)
  return {
    id: lead.id,
    name: lead.name,
    email: lead.contact.email,
    request: isRealLead
      ? { lead_id: lead.id }
      : {
          lead: {
            id: lead.id,
            name: lead.name,
            kind: lead.kind,
            state: lead.country,
            city: lead.hq,
            contact_name: lead.contact.name,
            primary_email: lead.contact.email,
          },
        },
  }
}

export function LeadAIDraftDialog({ lead, target: targetProp, open, onOpenChange, onSent }: Props) {
  const [tone, setTone] = React.useState<EmailTone>("professional")
  const [draft, setDraft] = React.useState<EmailDraft | null>(null)
  const [loading, setLoading] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const target = React.useMemo(() => targetProp ?? (lead ? leadToTarget(lead) : null), [targetProp, lead])

  const generate = React.useCallback(async (t: EmailTone) => {
    if (!target) return
    setLoading(true)
    setError(null)
    try {
      const body = { ...target.request, tone: t }
      const r = await fetch("/api/email/draft", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
      if (!r.ok) throw new Error(`draft ${r.status}`)
      setDraft((await r.json()) as EmailDraft)
    } catch (e) {
      setError(String(e))
      // Never leave the dialog blank; the backend already falls back to a template on Gemini errors.
    } finally {
      setLoading(false)
    }
  }, [target])

  React.useEffect(() => {
    if (open && target) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setDraft(null)
      void generate(tone)
    }
    // Regenerate only when re-opened; tone chips call generate() explicitly.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, target?.id])

  const send = () => {
    if (!draft || !target) return
    // Simulated send — no SMTP by design. Store the draft on the toast so the user can
    // see it landed; the campaigns UI keeps working with the existing engine pipeline.
    onSent?.(draft)
    toast.success(`Email sent (simulated) → ${target.email || "no address on file"}`, {
      description: `${TONE_LABEL[draft.tone]} tone · ${draft.source === "gemini" ? "AI-written" : "template"}`,
    })
    onOpenChange(false)
  }

  const noEmail = !target?.email

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="font-display text-xl flex items-center gap-2">
            <Sparkles className="size-4 text-chart-2" />
            One-click email · {target?.name}
          </DialogTitle>
          <DialogDescription>
            {loading
              ? "Drafting with AI…"
              : draft?.source === "gemini"
                ? "Written by Gemini from this broker's real data. Tap a tone to regenerate. Edit anything before sending."
                : "Written from the LJM template (Gemini unavailable). Tap a tone to regenerate."}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">Tone:</span>
          {EMAIL_TONES.map((t) => (
            <button
              key={t}
              type="button"
              disabled={loading}
              onClick={() => { setTone(t); void generate(t) }}
              className={cn(
                "min-h-9 rounded-full border border-border px-3 py-1 text-sm font-semibold transition",
                tone === t ? "bg-asphalt text-white" : "bg-card hover:bg-muted",
              )}
            >
              {TONE_LABEL[t]}
            </button>
          ))}
          <Button
            size="sm"
            variant="outline"
            disabled={loading}
            onClick={() => void generate(tone)}
            className="ml-auto"
          >
            <RefreshCw className={cn("size-3.5", loading && "animate-spin")} /> Regenerate
          </Button>
        </div>

        {draft?.stance ? (
          <div className="rounded-sm border-l-4 border-safety bg-accent px-3 py-2 text-sm">
            <span className="eyebrow mr-2 text-foreground">Angle</span>
            {STANCE_LABEL[draft.stance]}
          </div>
        ) : null}

        {error ? (
          <div className="rounded-sm border border-warn bg-warn/10 p-2 text-xs text-warn">
            Couldn&apos;t reach the AI ({error}). Showing the last successful draft.
          </div>
        ) : null}

        <div className="space-y-3">
          <div>
            <div className="eyebrow mb-1">Subject</div>
            <input
              className="w-full rounded-sm border border-border bg-background px-3 py-2 text-sm"
              value={draft?.subject ?? ""}
              onChange={(e) => setDraft((d) => (d ? { ...d, subject: e.target.value } : d))}
              placeholder={loading ? "Writing…" : ""}
            />
          </div>
          <div>
            <div className="eyebrow mb-1">Body (plain text)</div>
            <textarea
              className="min-h-[180px] w-full rounded-sm border border-border bg-background px-3 py-2 font-mono text-sm"
              value={draft?.body ?? ""}
              onChange={(e) => setDraft((d) => (d ? { ...d, body: e.target.value } : d))}
              placeholder={loading ? "Writing…" : ""}
            />
          </div>
          <div>
            <div className="eyebrow mb-1">Branded preview (what the recipient sees)</div>
            <div className="rounded-sm border border-border bg-muted p-2">
              <iframe
                title="Branded email preview"
                srcDoc={draft?.body_html ?? ""}
                className="h-[420px] w-full rounded-sm border-0 bg-white"
                sandbox=""
              />
            </div>
          </div>
        </div>

        <DialogFooter className="flex-col-reverse gap-2 sm:flex-row">
          {noEmail ? (
            <span className="text-xs text-warn">No email on file for this lead. Add a contact before sending.</span>
          ) : null}
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button
            className="min-h-11 font-semibold"
            disabled={!draft || loading || noEmail}
            onClick={send}
          >
            <Send /> Send (simulated)
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
