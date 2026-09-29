"use client"

import * as React from "react"
import { toast } from "sonner"
import { CalendarClock, Search, Send, Wand2, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import type { EmailPurpose, OutreachDraft, OutreachTone } from "@/lib/ai/types"
import type { Segment } from "@/lib/analytics"
import { PURPOSE_LABEL, type Recipient } from "@/lib/campaigns/types"
import { LEAD_KIND_LABEL } from "@/lib/data/types"
import { nowMs } from "@/lib/use-now"
import { cn } from "@/lib/utils"
import { BriefBox, streamInto, StylePicker, type WriteStyle } from "./ai-writer"
import { renderTemplate } from "./email-preview"
import { toRecipient, useEngine } from "./engine"
import { Segmented } from "./segmented"
import { RegionTag, SegmentBadge } from "./ui"

/** Someone you can email: an existing broker or a company the crawler found. */
export type ContactOption = Recipient & { segment?: Segment; sub: string }

export type ComposerInit = { to?: string; purpose?: EmailPurpose; brief?: string; afterDays?: number }

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

const EXAMPLES = [
  "Tell them we have a reefer free in Dallas tomorrow and ask if they have anything going to Chicago",
  "Short thank-you for the last load, mention our 98% on-time record and ask for next week's plan",
  "Friendly check-in, we haven't worked together since summer, offer 10% off the next load",
  "Ask for a quick call about a weekly dedicated truck on their main lane",
]

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

export function EmailComposer({
  open, onOpenChange, contacts, init, onSent,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  contacts: ContactOption[]
  init: ComposerInit
  onSent?: (id: string) => void
}) {
  const { liveLeads, sendEmail } = useEngine()
  const all = React.useMemo<ContactOption[]>(
    () => [
      ...liveLeads.map((l) => ({ ...toRecipient(l), sub: `${LEAD_KIND_LABEL[l.kind]} · new lead · ${l.hq}` })),
      ...contacts,
    ],
    [liveLeads, contacts],
  )
  const suggest = (c?: ContactOption): EmailPurpose => (!c ? "truck_available" : c.kind === "lead" ? "intro" : c.segment ? SUGGESTED[c.segment] : "check_in")

  const [toId, setToId] = React.useState(init.to)
  const to = all.find((c) => c.id === toId)
  const [q, setQ] = React.useState("")
  const [purpose, setPurpose] = React.useState<EmailPurpose>(init.purpose ?? suggest(to))
  const [tone, setTone] = React.useState<OutreachTone>("friendly")
  const [custom, setCustom] = React.useState(!!init.brief)
  const [brief, setBrief] = React.useState(init.brief ?? "")
  const [understood, setUnderstood] = React.useState<string[]>([])
  const [subject, setSubject] = React.useState("")
  const [body, setBody] = React.useState("")
  const [writing, setWriting] = React.useState(false)
  const [followUp, setFollowUp] = React.useState(true)
  const [followUpDays, setFollowUpDays] = React.useState("3")
  const [when, setWhen] = React.useState<"now" | "tomorrow" | "scheduled">(init.afterDays ? "scheduled" : "now")
  const [scheduleAt, setScheduleAt] = React.useState(init.afterDays ? toLocalInput(atNine(init.afterDays)) : "")

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
        body: JSON.stringify({ campaign: purpose, tone, brief: custom ? brief : undefined, region: to.region, equipment: [], lanes: [] }),
      })
      const draft = (await res.json()) as OutreachDraft
      // one recipient, so the personal fields are filled in straight away and the text reads as final
      setSubject(renderTemplate(draft.subject, to))
      setUnderstood(draft.understood ?? [])
      await streamInto(renderTemplate(draft.body, to), setBody)
    } finally {
      setWriting(false)
    }
  }, [to, purpose, tone, custom, brief])

  // redraft when the recipient, purpose or tone changes; a written description waits for "Write it for me"
  const writeRef = React.useRef(write)
  React.useEffect(() => {
    writeRef.current = write
  }, [write])
  React.useEffect(() => {
    if (!open || !toId || custom) return
    writeRef.current()
  }, [open, toId, purpose, tone, custom])

  const results = React.useMemo(() => {
    const s = q.trim().toLowerCase()
    const hits = s ? all.filter((c) => `${c.name} ${c.contactName} ${c.email} ${c.lane ?? ""} ${c.sub}`.toLowerCase().includes(s)) : all.filter((c) => c.kind === "broker")
    return hits.slice(0, 8)
  }, [q, all])

  const sendAt = when === "now" ? undefined : when === "tomorrow" ? atNine(1).getTime() : scheduleAt ? new Date(scheduleAt).getTime() : undefined

  const send = () => {
    if (!to) return
    const at = sendAt ?? nowMs()
    const id = sendEmail({
      recipient: to, purpose, tone, brief: custom ? brief : undefined, subject, body, at,
      followUpDays: followUp ? Number(followUpDays) : undefined,
    })
    toast.success(when === "now" ? `Email sent to ${to.contactName}` : `Email scheduled for ${formatWhen(at)}`, {
      description: followUp ? `If ${to.contactName.split(" ")[0]} doesn't reply in ${followUpDays} days, a short follow-up goes out automatically.` : "You'll see here when it's opened and what they reply.",
    })
    onSent?.(id)
    onOpenChange(false)
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full gap-0 p-0 sm:max-w-2xl">
        <SheetHeader className="border-b border-border px-5 py-4">
          <SheetTitle className="font-display text-xl">New email</SheetTitle>
          <SheetDescription>A personal email to one company, outside of a campaign. The AI writes it, you check it and send.</SheetDescription>
        </SheetHeader>

        <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
          <section className="space-y-1.5">
            <Label>To</Label>
            {to ? (
              <div className="flex items-center gap-3 rounded-sm border border-border bg-background px-3 py-2">
                <RegionTag region={to.region} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-semibold">{to.name}</span>
                    {to.segment ? <SegmentBadge segment={to.segment} /> : null}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">{to.contactName} · {to.email} · {to.lane}</div>
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
                          setPurpose(init.purpose ?? suggest(c))
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
          </section>

          <section className="space-y-1.5">
            <Label>What is it about?</Label>
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
            {to && purpose === suggest(to) ? (
              <p className="text-xs text-muted-foreground">Suggested by the AI for {to.segment ? `a ${to.segment.toLowerCase().replace(/s$/, "")}` : "a new company"}.</p>
            ) : null}
          </section>

          <section className="space-y-3">
            <StylePicker value={style} onChange={setStyle} />
            {custom ? <BriefBox brief={brief} setBrief={setBrief} onWrite={write} writing={writing} understood={understood} examples={EXAMPLES} placeholder="e.g. Tell them we have a reefer free in Dallas tomorrow and ask if they have anything going to Chicago" /> : null}
          </section>

          <section className="space-y-3">
            <div className="flex items-center justify-between">
              <Label htmlFor="m-subject">Subject</Label>
              <Button variant="ghost" size="sm" onClick={write} disabled={writing || !to}>
                <Wand2 className={writing ? "animate-pulse" : undefined} /> {writing ? "Writing…" : "Rewrite"}
              </Button>
            </div>
            <Input id="m-subject" value={subject} onChange={(e) => setSubject(e.target.value)} placeholder={to ? "" : "Pick who to email first"} />
            <Textarea value={body} onChange={(e) => setBody(e.target.value)} rows={11} className="text-[0.9rem] leading-relaxed" aria-label="Email text" placeholder={to ? "" : "The AI writes the email once you pick a company."} />
          </section>

          <section className="grid gap-4 sm:grid-cols-2">
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
            <div className="space-y-1.5">
              <Label>If they don&apos;t reply</Label>
              <label className="flex h-8 items-center gap-2.5 text-sm">
                <Switch checked={followUp} onCheckedChange={setFollowUp} /> Follow up after
                <Select value={followUpDays} onValueChange={(v) => v && setFollowUpDays(v)} disabled={!followUp}>
                  <SelectTrigger size="sm" className="w-24"><SelectValue>{followUpDays} days</SelectValue></SelectTrigger>
                  <SelectContent>{["2", "3", "5", "7"].map((d) => <SelectItem key={d} value={d}>{d} days</SelectItem>)}</SelectContent>
                </Select>
              </label>
              <p className="text-xs text-muted-foreground">Stops automatically when they reply.</p>
            </div>
          </section>
        </div>

        <SheetFooter className="flex-row items-center gap-2 border-t border-border px-5 py-3">
          <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground" suppressHydrationWarning>
            {to ? `To ${to.contactName} <${to.email}>` : "No recipient yet"}
            {sendAt ? ` · ${formatWhen(sendAt)}` : ""}
          </span>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button className="font-semibold" disabled={!to || !subject || !body || writing || (when === "scheduled" && !scheduleAt)} onClick={send}>
            {when === "now" ? <Send /> : <CalendarClock />} {when === "now" ? "Send email" : "Schedule email"}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
