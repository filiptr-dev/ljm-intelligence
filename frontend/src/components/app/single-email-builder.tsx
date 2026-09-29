"use client"

/**
 * Full-page single-recipient email composer. Reuses the shared
 * MessageFields / DesignFields / PreviewPanel from the campaign builder,
 * so this looks and feels identical to /outreach — just for one person.
 *
 * Reached from:
 *   • /brokers/[id] "Draft email"  → /emails/compose?broker=<id>
 *   • /messages "New email"        → /emails/compose  (recipient picker up top)
 */

import * as React from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { toast } from "sonner"
import { CalendarClock, Search, Send, Wand2, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import type { EmailPurpose, OutreachDraft, OutreachTone } from "@/lib/ai/types"
import type { Segment } from "@/lib/analytics"
import { PURPOSE_LABEL, type Recipient } from "@/lib/campaigns/types"
import { LEAD_KIND_LABEL } from "@/lib/data/types"
import { nowMs } from "@/lib/use-now"
import { cn } from "@/lib/utils"
import { streamInto, type WriteStyle } from "./ai-writer"
import type { ContactOption } from "./email-composer"
import { DesignFields, MessageFields, PreviewPanel, Step } from "./email-builder-parts"
import { renderTemplate } from "./email-preview"
import { useBackendLeads } from "@/lib/backend-leads"
import { DEFAULT_DESIGN, toRecipient, useEngine, type EmailDesign } from "./engine"
import { Segmented } from "./segmented"
import { RegionTag, SegmentBadge } from "./ui"

const PURPOSES: { value: EmailPurpose; hint: string }[] = [
  { value: "truck_available", hint: "A truck is free on their lane" },
  { value: "quote_followup", hint: "Chase a quote you sent" },
  { value: "send_rate", hint: "Answer a rate request" },
  { value: "check_in", hint: "Reconnect with a quiet broker" },
  { value: "rate_update", hint: "Tell them about lower rates" },
  { value: "thank_you", hint: "After a delivered load" },
  { value: "intro", hint: "First email to a new company" },
]

const SUGGESTED: Record<Segment, EmailPurpose> = {
  "Core partners": "truck_available",
  Growing: "truck_available",
  "Price shoppers": "rate_update",
  Dormant: "check_in",
  Occasional: "check_in",
}

function atNine(daysAhead: number) {
  const d = new Date()
  d.setDate(d.getDate() + daysAhead)
  d.setHours(9, 0, 0, 0)
  return d
}
const toLocalInput = (d: Date) => {
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}
const formatWhen = (ms: number) =>
  new Date(ms).toLocaleString("en-US", { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })

function suggestPurpose(c?: ContactOption): EmailPurpose {
  if (!c) return "truck_available"
  if (c.kind === "lead") return "intro"
  if (c.segment) return SUGGESTED[c.segment]
  return "check_in"
}

export type SingleEmailBuilderProps = {
  contacts: ContactOption[]
  /** Preselected recipient id (from ?broker=…). */
  initialToId?: string
  /** Optional preselected purpose (from ?purpose=…). */
  initialPurpose?: EmailPurpose
  /** Where the "back" link points, and where we return after sending. */
  backHref: string
  backLabel: string
}

export function SingleEmailBuilder({ contacts, initialToId, initialPurpose, backHref, backLabel }: SingleEmailBuilderProps) {
  const router = useRouter()
  const { liveLeads, sendEmail } = useEngine()
  const { real: realLeads } = useBackendLeads(200)

  const all = React.useMemo<ContactOption[]>(
    () => [
      ...realLeads.map((l) => ({ ...toRecipient(l), sub: `${LEAD_KIND_LABEL[l.kind]} · new lead · ${l.hq}` })),
      ...liveLeads.map((l) => ({ ...toRecipient(l), sub: `${LEAD_KIND_LABEL[l.kind]} · new lead · ${l.hq}` })),
      ...contacts,
    ],
    [realLeads, liveLeads, contacts],
  )

  const [toId, setToId] = React.useState(initialToId)
  const to = all.find((c) => c.id === toId)
  const [q, setQ] = React.useState("")

  const [purpose, setPurpose] = React.useState<EmailPurpose>(initialPurpose ?? suggestPurpose(to))
  const [tone, setTone] = React.useState<OutreachTone>("friendly")
  const [custom, setCustom] = React.useState(false)
  const [brief, setBrief] = React.useState("")
  const [understood, setUnderstood] = React.useState<string[]>([])
  const [subject, setSubject] = React.useState("")
  const [body, setBody] = React.useState("")
  const [writing, setWriting] = React.useState(false)

  const [design, setDesign] = React.useState<EmailDesign>(DEFAULT_DESIGN)
  const [previewIdx, setPreviewIdx] = React.useState(0)
  const [mobile, setMobile] = React.useState(false)

  const [when, setWhen] = React.useState<"now" | "tomorrow" | "scheduled">("now")
  const [scheduleAt, setScheduleAt] = React.useState("")

  const style: WriteStyle = custom ? "custom" : tone
  const setStyle = (v: WriteStyle) => {
    setCustom(v === "custom")
    if (v !== "custom") setTone(v)
  }

  const write = React.useCallback(async () => {
    if (!to) return
    setWriting(true)
    try {
      const res = await fetch("/api/ai/draft", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          campaign: purpose, tone,
          brief: custom ? brief : undefined,
          region: to.region, equipment: [], lanes: [],
        }),
      })
      const draft = (await res.json()) as OutreachDraft
      // one recipient, so bake in the personal fields — the text reads as final
      setSubject(renderTemplate(draft.subject, to))
      setUnderstood(draft.understood ?? [])
      await streamInto(renderTemplate(draft.body, to), setBody)
    } finally {
      setWriting(false)
    }
  }, [to, purpose, tone, custom, brief])

  // redraft when recipient/purpose/tone changes (custom brief waits for "Write it for me")
  const writeRef = React.useRef(write)
  React.useEffect(() => { writeRef.current = write }, [write])
  React.useEffect(() => {
    if (!toId || custom) return
    writeRef.current()
  }, [toId, purpose, tone, custom])

  const results = React.useMemo(() => {
    const s = q.trim().toLowerCase()
    const hits = s
      ? all.filter((c) => `${c.name} ${c.contactName} ${c.email} ${c.lane ?? ""} ${c.sub}`.toLowerCase().includes(s))
      : all.filter((c) => c.kind === "broker")
    return hits.slice(0, 8)
  }, [q, all])

  const sendAt = when === "now" ? undefined : when === "tomorrow" ? atNine(1).getTime() : scheduleAt ? new Date(scheduleAt).getTime() : undefined
  const canSend = !!to && !!subject && !!body && !writing && !(when === "scheduled" && !scheduleAt)

  const send = () => {
    if (!to) return
    const at = sendAt ?? nowMs()
    sendEmail({
      recipient: to, purpose, tone, brief: custom ? brief : undefined,
      subject, body, at, design,
    })
    toast.success(when === "now" ? `Email sent to ${to.contactName}` : `Email scheduled for ${formatWhen(at)}`, {
      description: "You'll see when it's opened and what they reply.",
    })
    router.push(backHref)
  }

  const recipients = to ? [to as Recipient] : []

  return (
    <>
      <Link href={backHref} className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        ← {backLabel}
      </Link>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,520px)]">
        <div className="min-w-0 space-y-5">
          <Step n={1} title="To">
            {to ? (
              <div className="flex items-center gap-3 rounded-sm border border-border bg-background px-3 py-2">
                <RegionTag region={to.region} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-semibold">{to.name}</span>
                    {to.segment ? <SegmentBadge segment={to.segment} /> : null}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">{to.contactName} · {to.email}{to.lane ? ` · ${to.lane}` : ""}</div>
                </div>
                <Button variant="ghost" size="icon-sm" onClick={() => setToId(undefined)} aria-label="Change recipient"><X /></Button>
              </div>
            ) : (
              <div>
                <div className="relative">
                  <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                  <Input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search a broker, shipper or lead by name, contact, email or lane…" className="h-10 pl-9" />
                </div>
                <ul className="mt-1.5 divide-y divide-border rounded-sm border border-border">
                  {results.map((c) => (
                    <li key={c.id}>
                      <button
                        type="button"
                        onClick={() => {
                          setToId(c.id)
                          setPurpose(initialPurpose ?? suggestPurpose(c))
                          setQ("")
                        }}
                        className="flex w-full items-center gap-3 px-3 py-2 text-left text-sm hover:bg-muted/60"
                      >
                        <RegionTag region={c.region} />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate font-medium">{c.name}</span>
                          <span className="block truncate text-xs text-muted-foreground">{c.contactName} · {c.sub}</span>
                        </span>
                        {c.segment ? <SegmentBadge segment={c.segment} /> : null}
                      </button>
                    </li>
                  ))}
                  {!results.length ? <li className="p-4 text-center text-sm text-muted-foreground">No company matches “{q}”.</li> : null}
                </ul>
              </div>
            )}
          </Step>

          <Step n={2} title="What is it about?">
            <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
              {PURPOSES.map((p) => (
                <button
                  key={p.value}
                  type="button"
                  onClick={() => setPurpose(p.value)}
                  className={cn(
                    "rounded-sm border px-2.5 py-1.5 text-left transition-colors",
                    purpose === p.value ? "border-asphalt bg-asphalt text-white" : "border-border bg-card hover:border-asphalt",
                  )}
                >
                  <span className="block text-sm font-semibold">{PURPOSE_LABEL[p.value]}</span>
                  <span className={cn("block truncate text-[0.7rem]", purpose === p.value ? "text-white/70" : "text-muted-foreground")}>{p.hint}</span>
                </button>
              ))}
            </div>
          </Step>

          <Step
            n={3}
            title="Message"
            action={
              <Button variant="outline" size="sm" onClick={write} disabled={writing || !to}>
                <Wand2 className={writing ? "animate-pulse" : undefined} /> {writing ? "Writing…" : "Rewrite with AI"}
              </Button>
            }
          >
            <MessageFields
              typeSlot={
                <div className="space-y-1.5">
                  <Label>Purpose</Label>
                  <div className="flex h-9 items-center rounded-sm border border-border bg-background px-3 text-sm">
                    {PURPOSE_LABEL[purpose]}
                  </div>
                </div>
              }
              style={style}
              setStyle={setStyle}
              custom={custom}
              brief={brief}
              setBrief={setBrief}
              onWrite={write}
              writing={writing}
              understood={understood}
              subject={subject}
              setSubject={setSubject}
              body={body}
              setBody={setBody}
              preview={to}
              fieldsHint={to ? `${to.name}'s own details, so it reads as a personal email` : "the recipient's own details"}
              bodyHint="Personal fields are filled in when the email is sent."
            />
          </Step>

          <Step n={4} title="Design">
            <DesignFields design={design} setDesign={setDesign} />
          </Step>

          <Step n={5} title="Sending">
            <div className="space-y-1.5">
              <Label>When</Label>
              <Segmented
                value={when}
                onChange={(v) => {
                  setWhen(v)
                  if (v === "scheduled" && !scheduleAt) setScheduleAt(toLocalInput(atNine(1)))
                }}
                className="flex w-full [&>button]:flex-1"
                options={[{ value: "now", label: "Now" }, { value: "tomorrow", label: "Tomorrow 9:00" }, { value: "scheduled", label: "Pick date" }]}
              />
              {when === "scheduled" ? <Input type="datetime-local" value={scheduleAt} onChange={(e) => setScheduleAt(e.target.value)} /> : null}
            </div>
          </Step>
        </div>

        <div className="min-w-0 space-y-4 xl:sticky xl:top-20 xl:self-start">
          <div className="rounded-sm border-2 border-asphalt bg-card p-4">
            <div className="flex flex-wrap items-center gap-3">
              <div className="min-w-0 flex-1 text-sm">
                <div className="truncate font-semibold">
                  {to ? `To ${to.contactName}` : "No recipient yet"}
                </div>
                <div className="text-xs text-muted-foreground" suppressHydrationWarning>
                  {to ? `${to.email}` : "Pick a company on the left"}
                  {sendAt ? ` · ${formatWhen(sendAt)}` : when === "now" ? " · sends now" : ""}
                </div>
              </div>
              <Button size="lg" className="font-semibold" disabled={!canSend} onClick={send}>
                {when === "now" ? <Send /> : <CalendarClock />} {when === "now" ? "Send email" : "Schedule email"}
              </Button>
            </div>
          </div>
          <PreviewPanel
            subject={subject}
            body={body}
            design={design}
            recipients={recipients}
            previewIdx={previewIdx}
            setPreviewIdx={setPreviewIdx}
            mobile={mobile}
            setMobile={setMobile}
          />
        </div>
      </div>
    </>
  )
}
