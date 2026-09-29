"use client"

import * as React from "react"
import { toast } from "sonner"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { CalendarClock, ChevronLeft, ChevronRight, Copy, Monitor, Plus, Rocket, Search, Smartphone, Sparkles, Wand2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import type { CampaignType, OutreachDraft, OutreachTone } from "@/lib/ai/types"
import { SEGMENTS, type Segment } from "@/lib/analytics"
import { GOAL_LABEL, type Campaign, type FollowUp, type GoalType } from "@/lib/campaigns/types"
import type { Region } from "@/lib/data/geo"
import type { Lead, LeadKind } from "@/lib/data/types"
import { cn } from "@/lib/utils"
import { BriefBox, streamInto, StylePicker, type WriteStyle } from "./ai-writer"
import { EmailPreview, renderTemplate } from "./email-preview"
import { DEFAULT_DESIGN, toRecipient, useEngine, type EmailDesign, type Recipient } from "./engine"
import { KindBadge, leadHaystack } from "./lead-finder"
import { ScoreChip } from "./live-feed"
import { Segmented } from "./segmented"
import { RegionTag, SEGMENT_COLOR, SegmentBadge } from "./ui"

export type ExistingRow = {
  id: string
  name: string
  region: Region
  segment: Segment
  contactName: string
  email: string
  lane: string
  equipment: string
  health: number
  daysSinceLast: number
  booked: number
}

type Audience = "new" | "existing" | "both"

const CAMPAIGNS: { value: CampaignType; label: string; hint: string }[] = [
  { value: "new_leads", label: "Capacity intro", hint: "First contact with a new broker or forwarder" },
  { value: "shipper_direct", label: "Direct shipper intro", hint: "Companies that ship their own freight" },
  { value: "reengage", label: "Re-engagement", hint: "Brokers who went quiet" },
  { value: "winback", label: "Win-back on price", hint: "Brokers who rejected you on rate" },
  { value: "dedicated_lane", label: "Dedicated lane offer", hint: "Weekly capacity for steady partners" },
]
const GOAL_HINT: Record<GoalType, string> = {
  replies: "Any answer counts, including “not now”.",
  positive: "Replies where the company is interested or asks for a rate.",
  accounts: "Companies that book their first load with you.",
  loads: "Total loads booked by companies from this campaign.",
}

const DEFAULT_FOLLOW_UPS: FollowUp[] = [
  { afterDays: 3, subject: "Re: {{company}} capacity", body: "Hi {{first_name}},\n\nJust following up on my email below. We still have {{equipment}} trucks on {{lane}} this week. Would a quick rate help?\n\nBest regards,\n{{sender}}" },
  { afterDays: 7, subject: "Last note from Ironline Transport", body: "Hi {{first_name}},\n\nI don't want to fill your inbox, so this is my last note. If {{lane}} ever needs a reliable truck, just reply to this email.\n\nBest regards,\n{{sender}}" },
  { afterDays: 14, subject: "Still need trucks on {{lane}}?", body: "Hi {{first_name}},\n\nChecking in one more time. We have new capacity on {{lane}} next month.\n\nBest regards,\n{{sender}}" },
]

function dominantKind(c: Campaign): LeadKind | "existing" | undefined {
  const counts = new Map<string, number>()
  c.recipients.forEach((r) => {
    const k = r.kind === "broker" ? "existing" : r.companyType
    if (k) counts.set(k, (counts.get(k) ?? 0) + 1)
  })
  const [top, n] = [...counts.entries()].sort((a, b) => b[1] - a[1])[0] ?? []
  return top && n! / c.recipients.length > 0.6 ? (top as LeadKind | "existing") : undefined
}

/** Next Tue–Thu 09:00 local time: when brokers reply most. */
function nextPeak() {
  const d = new Date()
  d.setHours(9, 0, 0, 0)
  if (d.getTime() <= Date.now()) d.setDate(d.getDate() + 1)
  while (![2, 3, 4].includes(d.getDay())) d.setDate(d.getDate() + 1)
  return d.getTime()
}
function tomorrowAtNine() {
  const d = new Date()
  d.setDate(d.getDate() + 1)
  d.setHours(9, 0, 0, 0)
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T09:00`
}
const formatWhen = (ms: number) =>
  new Date(ms).toLocaleString("en-US", { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })

/** Personal fields, in plain words: each one is swapped for the recipient's own details on send. */
const FIELDS = [
  { tag: "{{first_name}}", label: "First name", hint: "The contact person's first name" },
  { tag: "{{company}}", label: "Company name", hint: "The name of the company you are emailing" },
  { tag: "{{lane}}", label: "Their lane", hint: "The route they ship most, e.g. Chicago → Dallas" },
  { tag: "{{equipment}}", label: "Trailer type", hint: "The trailer they need, e.g. Reefer or Flatbed" },
  { tag: "{{sender}}", label: "Your name", hint: "Your dispatcher's name, used in the sign-off" },
]
const ACCENTS = [
  { name: "Safety yellow", hex: "#f5b800" },
  { name: "Fleet blue", hex: "#2f63a8" },
  { name: "Diesel red", hex: "#c4432b" },
  { name: "Highway green", hex: "#3d8f5a" },
  { name: "Asphalt", hex: "#16171a" },
]

const existingToRecipient = (b: ExistingRow): Recipient => ({
  id: b.id, kind: "broker", name: b.name, contactName: b.contactName, email: b.email,
  region: b.region, lane: b.lane, equipment: b.equipment, verified: true,
})

function Step({ n, title, children, action }: { n: number; title: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <section className="rounded-sm border border-border bg-card">
      <header className="flex items-center gap-3 border-b border-border px-4 py-3">
        <span className="flex size-7 items-center justify-center rounded-sm bg-asphalt font-display text-sm font-bold text-safety">{n}</span>
        <h2 className="flex-1 font-display text-lg font-semibold">{title}</h2>
        {action}
      </header>
      <div className="p-4">{children}</div>
    </section>
  )
}

export function OutreachBuilder({
  leads, existing, initial,
}: {
  leads: Lead[]
  existing: ExistingRow[]
  initial: { audience: Audience; ids: string[]; campaign?: string; segment?: string; template?: Campaign | null; templateId?: string }
}) {
  const router = useRouter()
  const { liveLeads, contacted, sendCampaign, campaigns: liveCampaigns } = useEngine()
  const tpl = initial.template ?? undefined
  const allLeads = React.useMemo(() => [...liveLeads, ...leads], [liveLeads, leads])

  // a reused campaign keeps its kind of audience: existing brokers, or the same type of new company
  const tplKind = initial.template ? dominantKind(initial.template) : undefined
  const [audience, setAudience] = React.useState<Audience>(tplKind === "existing" ? "existing" : tplKind ? "new" : initial.audience)
  const [selected, setSelected] = React.useState<Set<string>>(() => {
    if (initial.ids.length) return new Set(initial.ids)
    if (tplKind === "existing") return new Set()
    if (tplKind) return new Set(leads.filter((l) => l.kind === tplKind && l.score >= 60 && l.emailVerified).slice(0, 25).map((l) => l.id))
    if (initial.segment) return new Set(existing.filter((b) => b.segment === initial.segment).map((b) => b.id))
    if (initial.audience === "new") return new Set(leads.filter((l) => l.score >= 80 && l.emailVerified).slice(0, 25).map((l) => l.id))
    return new Set()
  })
  const [q, setQ] = React.useState("")
  const [region, setRegion] = React.useState<"all" | Region>("all")
  const [segment, setSegment] = React.useState<string>(initial.segment ?? "all")
  const [minScore, setMinScore] = React.useState("0")
  const [kindFilter, setKindFilter] = React.useState<"all" | LeadKind>(tplKind && tplKind !== "existing" ? tplKind : "all")
  const [hideContacted, setHideContacted] = React.useState(false)
  const [leadLimit, setLeadLimit] = React.useState(60)

  const [campaign, setCampaign] = React.useState<CampaignType>(
    (tpl?.type as CampaignType) ?? (CAMPAIGNS.find((c) => c.value === initial.campaign)?.value) ?? (initial.audience === "new" ? "new_leads" : "reengage"),
  )
  const [tone, setTone] = React.useState<OutreachTone>(tpl?.tone ?? "professional")
  const [custom, setCustom] = React.useState(!!tpl?.brief)
  const [brief, setBrief] = React.useState(tpl?.brief ?? "")
  const [understood, setUnderstood] = React.useState<string[]>([])
  const style: WriteStyle = custom ? "custom" : tone
  const setStyle = (v: WriteStyle) => {
    setCustom(v === "custom")
    if (v !== "custom") setTone(v)
  }
  const [subject, setSubject] = React.useState(tpl?.subject ?? "")
  const [body, setBody] = React.useState(tpl?.body ?? "")
  const [writing, setWriting] = React.useState(false)
  const [design, setDesign] = React.useState<EmailDesign>(tpl?.design ?? DEFAULT_DESIGN)
  const [previewIdx, setPreviewIdx] = React.useState(0)
  const [mobile, setMobile] = React.useState(false)

  // campaign settings
  const [name, setName] = React.useState(tpl ? `${tpl.name} · again` : "")
  const [goalType, setGoalType] = React.useState<GoalType | "none">(tpl?.goal?.type ?? "positive")
  const [goalTarget, setGoalTarget] = React.useState(String(tpl?.goal?.target ?? 10))
  const [when, setWhen] = React.useState<"now" | "peak" | "scheduled">("now")
  const [scheduleAt, setScheduleAt] = React.useState("")
  const [followUps, setFollowUps] = React.useState<FollowUp[]>(tpl?.followUps?.length ? tpl.followUps : [DEFAULT_FOLLOW_UPS[0]])
  const [reused, setReused] = React.useState<string | undefined>(tpl?.name)
  /** a reused campaign brings its own text, so skip the automatic AI draft for it */
  const skipDraft = React.useRef<{ campaign: string; tone: string } | null>(tpl ? { campaign: tpl.type, tone: tpl.tone ?? "professional" } : null)
  const bodyRef = React.useRef<HTMLTextAreaElement>(null)
  const subjectRef = React.useRef<HTMLInputElement>(null)
  // personal-field buttons insert into whichever field was used last
  const [target, setTargetState] = React.useState<"subject" | "body">("body")
  const targetRef = React.useRef<"subject" | "body">("body")
  const setTarget = (t: "subject" | "body") => {
    targetRef.current = t
    setTargetState(t)
  }

  const showLeads = audience !== "existing"
  const showExisting = audience !== "new"

  const leadRows = allLeads.filter(
    (l) =>
      showLeads &&
      (region === "all" || l.region === region) &&
      l.score >= Number(minScore) &&
      (kindFilter === "all" || l.kind === kindFilter) &&
      (!hideContacted || !contacted.has(l.id)) &&
      (!q || leadHaystack(l).includes(q.toLowerCase())),
  )
  const existingRows = existing.filter(
    (b) =>
      showExisting &&
      (region === "all" || b.region === region) &&
      (segment === "all" || b.segment === segment) &&
      (!q || `${b.name} ${b.contactName} ${b.email} ${b.lane} ${b.equipment} ${b.segment}`.toLowerCase().includes(q.toLowerCase())),
  )
  const matchIds = [...leadRows.map((l) => l.id), ...existingRows.map((b) => b.id)]

  const recipients: Recipient[] = React.useMemo(() => {
    const out: Recipient[] = []
    if (showLeads) allLeads.forEach((l) => selected.has(l.id) && out.push(toRecipient(l)))
    if (showExisting) existing.forEach((b) => selected.has(b.id) && out.push(existingToRecipient(b)))
    return out
  }, [selected, allLeads, existing, showLeads, showExisting])
  const preview = recipients[Math.min(previewIdx, Math.max(0, recipients.length - 1))]

  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })
  const selectMany = (ids: string[], on: boolean) =>
    setSelected((s) => {
      const n = new Set(s)
      ids.forEach((id) => (on ? n.add(id) : n.delete(id)))
      return n
    })

  const write = React.useCallback(async () => {
    setWriting(true)
    const regions = new Set(recipients.map((r) => r.region))
    try {
      const res = await fetch("/api/ai/draft", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          campaign, tone,
          brief: custom ? brief : undefined,
          region: regions.size === 1 ? [...regions][0] : "mixed",
          // leave equipment and lanes as merge tags so every broker gets their own
          equipment: [],
          lanes: [],
        }),
      })
      const draft = (await res.json()) as OutreachDraft
      setSubject(draft.subject)
      setUnderstood(draft.understood ?? [])
      await streamInto(draft.body, setBody)
    } finally {
      setWriting(false)
    }
  }, [campaign, tone, recipients, custom, brief])

  // first draft on load and whenever the campaign type or tone changes
  const writeRef = React.useRef(write)
  React.useEffect(() => {
    writeRef.current = write
  }, [write])
  React.useEffect(() => {
    // keep the reused text until the user actually changes campaign type or tone
    const skip = skipDraft.current
    if (skip && skip.campaign === campaign && skip.tone === tone) return
    skipDraft.current = null
    writeRef.current()
  }, [campaign, tone])

  // reusing a campaign that was sent from this browser (it lives in the engine, not on the server)
  const appliedLive = React.useRef(false)
  React.useEffect(() => {
    if (tpl || !initial.templateId || appliedLive.current) return
    const c = liveCampaigns.find((x) => x.id === initial.templateId)
    if (!c) return
    appliedLive.current = true
    skipDraft.current = { campaign: c.type, tone: c.tone ?? "professional" }
    /* eslint-disable react-hooks/set-state-in-effect -- one-time copy of a stored campaign into the form */
    setCampaign(c.type as CampaignType)
    setTone(c.tone ?? "professional")
    setCustom(!!c.brief)
    setBrief(c.brief ?? "")
    setSubject(c.subject)
    setBody(c.body)
    setDesign(c.design)
    setName(`${c.name} · again`)
    if (c.goal) {
      setGoalType(c.goal.type)
      setGoalTarget(String(c.goal.target))
    }
    if (c.followUps?.length) setFollowUps(c.followUps)
    setReused(c.name)
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [liveCampaigns, initial.templateId, tpl])

  const insertTag = (tag: string) => {
    const el = targetRef.current === "subject" ? subjectRef.current : bodyRef.current
    const set = targetRef.current === "subject" ? setSubject : setBody
    if (!el) return set((b) => b + tag)
    const a = el.selectionStart ?? el.value.length
    const z = el.selectionEnd ?? a
    set((b) => b.slice(0, a) + tag + b.slice(z))
    requestAnimationFrame(() => {
      el.focus()
      el.setSelectionRange(a + tag.length, a + tag.length)
    })
  }

  /** start time for peak / picked schedules; "now" is resolved at launch */
  const plannedStart = () => (when === "peak" ? nextPeak() : when === "scheduled" && scheduleAt ? new Date(scheduleAt).getTime() : undefined)
  const defaultName = `${CAMPAIGNS.find((c) => c.value === campaign)!.label} · ${new Date().toLocaleDateString("en-US", { month: "short", day: "numeric" })}`

  const send = () => {
    const at = plannedStart() ?? Date.now()
    const id = sendCampaign({
      name: name.trim() || defaultName,
      type: campaign,
      tone,
      brief: custom ? brief : undefined,
      subject,
      body,
      design,
      recipients,
      goal: goalType === "none" ? undefined : { type: goalType, target: Math.max(1, Number(goalTarget) || 1) },
      schedule: { mode: when, at },
      followUps,
    })
    toast.success(when === "now" ? `Campaign launched · ${recipients.length} emails going out` : `Campaign scheduled for ${formatWhen(at)}`, {
      description: followUps.length ? `${followUps.length} automatic follow-up${followUps.length > 1 ? "s" : ""} to people who don't reply.` : "Each email is personalised for its recipient.",
    })
    router.push(`/campaigns/${id}`)
  }

  const leadIdsShown = leadRows.slice(0, leadLimit).map((l) => l.id)
  const existingIdsShown = existingRows.map((b) => b.id)

  return (
    <>
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,520px)]">
        <div className="min-w-0 space-y-5">
          <Step n={1} title="Campaign">
            {reused ? (
              <div className="mb-3 flex items-center gap-2 rounded-sm border-l-4 border-good bg-good/10 px-3 py-2 text-sm">
                <Copy className="size-4 text-good" /> Reusing <b>{reused}</b>: message, design, goal and follow-ups are copied. Pick who to send it to below.
              </div>
            ) : null}
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="cname">Campaign name</Label>
                <Input id="cname" value={name} onChange={(e) => setName(e.target.value)} placeholder={defaultName} />
              </div>
              <div className="space-y-1.5">
                <Label>Goal</Label>
                <div className="flex gap-2">
                  <Select value={goalType} onValueChange={(v) => v && setGoalType(v as GoalType | "none")}>
                    <SelectTrigger className="flex-1"><SelectValue>{goalType === "none" ? "No goal" : GOAL_LABEL[goalType]}</SelectValue></SelectTrigger>
                    <SelectContent>
                      {(Object.keys(GOAL_LABEL) as GoalType[]).map((g) => <SelectItem key={g} value={g}>{GOAL_LABEL[g]}</SelectItem>)}
                      <SelectItem value="none">No goal</SelectItem>
                    </SelectContent>
                  </Select>
                  {goalType !== "none" ? (
                    <Input type="number" min={1} value={goalTarget} onChange={(e) => setGoalTarget(e.target.value)} className="w-20" aria-label="Goal target" />
                  ) : null}
                </div>
                <p className="text-xs text-muted-foreground">{goalType === "none" ? "You can still track results without a goal." : GOAL_HINT[goalType]}</p>
              </div>
              <div className="space-y-1.5">
                <Label>When to send</Label>
                <Segmented
                  value={when}
                  onChange={(v) => {
                    setWhen(v)
                    if (v === "scheduled" && !scheduleAt) setScheduleAt(tomorrowAtNine())
                  }}
                  className="flex w-full [&>button]:flex-1"
                  options={[{ value: "now", label: "Now" }, { value: "peak", label: "Best time" }, { value: "scheduled", label: "Pick date" }]}
                />
                {when === "scheduled" ? (
                  <Input type="datetime-local" value={scheduleAt} onChange={(e) => setScheduleAt(e.target.value)} />
                ) : (
                  <p className="text-xs text-muted-foreground" suppressHydrationWarning>
                    {when === "peak" ? `Your brokers reply most Tue–Thu mornings. Next slot: ${formatWhen(nextPeak())}.` : "Emails start going out as soon as you launch."}
                  </p>
                )}
              </div>
            </div>
          </Step>

          <Step
            n={2}
            title="Audience"
            action={<span className="text-sm text-muted-foreground"><b className="num font-mono text-foreground">{recipients.length}</b> selected</span>}
          >
            <div className="flex flex-wrap items-center gap-3">
              <Segmented
                value={audience}
                onChange={(v) => {
                  setAudience(v)
                  if (v === "existing" && (campaign === "new_leads" || campaign === "shipper_direct")) setCampaign("reengage")
                  if (v === "new" && campaign !== "new_leads" && campaign !== "shipper_direct") setCampaign("new_leads")
                }}
                options={[
                  { value: "new", label: "New leads" },
                  { value: "existing", label: "Existing brokers" },
                  { value: "both", label: "Both" },
                ]}
              />
              <Segmented value={region} onChange={setRegion} options={[{ value: "all", label: "US & EU" }, { value: "US", label: "US" }, { value: "EU", label: "EU" }]} />
            </div>

            <div className="relative mt-3">
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search by company, contact, email, city, lane, equipment or industry…"
                className="h-10 pl-9 text-[0.95rem]"
              />
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-3">
              {showLeads ? (
                <>
                  <Segmented
                    value={kindFilter}
                    onChange={(v) => {
                      setKindFilter(v)
                      if (v === "Shipper" && campaign === "new_leads") setCampaign("shipper_direct")
                      if (v !== "Shipper" && campaign === "shipper_direct") setCampaign("new_leads")
                    }}
                    options={[{ value: "all", label: "Everyone" }, { value: "Broker", label: "Brokers" }, { value: "Shipper", label: "Shippers" }, { value: "Forwarder", label: "Forwarders" }]}
                  />
                  <Select value={minScore} onValueChange={(v) => setMinScore(v ?? "0")}>
                    <SelectTrigger className="w-40"><SelectValue>{minScore === "0" ? "Any match score" : `Match ≥ ${minScore}`}</SelectValue></SelectTrigger>
                    <SelectContent>
                      {["0", "50", "70", "80", "90"].map((s) => <SelectItem key={s} value={s}>{s === "0" ? "Any match score" : `Match ≥ ${s}`}</SelectItem>)}
                    </SelectContent>
                  </Select>
                  <label className="flex items-center gap-2 text-sm">
                    <Switch checked={hideContacted} onCheckedChange={setHideContacted} /> Hide contacted
                  </label>
                </>
              ) : null}
              {showExisting ? (
                <Select value={segment} onValueChange={(v) => setSegment(v ?? "all")}>
                  <SelectTrigger className="w-44"><SelectValue>{segment === "all" ? "All segments" : segment}</SelectValue></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All segments</SelectItem>
                    {SEGMENTS.map((s) => (
                      <SelectItem key={s} value={s}>
                        <span className="size-2 rounded-[2px]" style={{ background: SEGMENT_COLOR[s] }} /> {s}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : null}
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
              <span><b className="num font-mono">{matchIds.length}</b> {q ? "match your search" : "available"}</span>
              <Button variant="outline" size="sm" className="ml-auto" disabled={!matchIds.length} onClick={() => selectMany(matchIds, true)}>
                Select all {matchIds.length}
              </Button>
              <Button variant="ghost" size="sm" disabled={!selected.size} onClick={() => setSelected(new Set())}>Clear selection</Button>
            </div>

            <div className="mt-2 max-h-[420px] overflow-y-auto rounded-sm border border-border">
              {showLeads ? (
                <>
                  <div className="sticky top-0 z-10 flex items-center gap-3 border-b border-border bg-muted px-3 py-2 text-xs font-semibold">
                    <Checkbox
                      checked={leadIdsShown.length > 0 && leadIdsShown.every((id) => selected.has(id))}
                      onCheckedChange={(v) => selectMany(leadIdsShown, !!v)}
                      aria-label="Select visible new leads"
                    />
                    New leads · {leadRows.length}
                  </div>
                  {leadRows.slice(0, leadLimit).map((l) => (
                    <label key={l.id} className="flex cursor-pointer items-center gap-3 border-b border-border px-3 py-2 text-sm last:border-0 hover:bg-muted/50">
                      <Checkbox checked={selected.has(l.id)} onCheckedChange={() => toggle(l.id)} />
                      <RegionTag region={l.region} />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-2">
                          <span className="truncate font-medium">{l.name}</span>
                          <KindBadge kind={l.kind} />
                        </span>
                        <span className="block truncate text-xs text-muted-foreground">
                          {l.contact.name} · {l.hq} · {l.lanes[0].origin} → {l.lanes[0].destination} · {l.equipment.join(", ")}{l.industry ? ` · ${l.industry}` : ""}
                        </span>
                      </span>
                      {contacted.has(l.id) ? <span className="text-[0.7rem] font-semibold text-good">Contacted</span> : null}
                      {!l.emailVerified ? <span className="text-[0.7rem] text-warn">unverified</span> : null}
                      <ScoreChip score={l.score} />
                    </label>
                  ))}
                  {leadRows.length > leadLimit ? (
                    <button type="button" onClick={() => setLeadLimit((n) => n + 60)} className="w-full border-b border-border py-2 text-sm font-medium text-chart-1 hover:bg-muted/50">
                      Show more ({leadRows.length - leadLimit} left)
                    </button>
                  ) : null}
                </>
              ) : null}
              {showExisting ? (
                <>
                  <div className="sticky top-0 z-10 flex items-center gap-3 border-b border-border bg-muted px-3 py-2 text-xs font-semibold">
                    <Checkbox
                      checked={existingIdsShown.length > 0 && existingIdsShown.every((id) => selected.has(id))}
                      onCheckedChange={(v) => selectMany(existingIdsShown, !!v)}
                      aria-label="Select all existing brokers"
                    />
                    Existing brokers · {existingRows.length}
                  </div>
                  {existingRows.map((b) => (
                    <label key={b.id} className="flex cursor-pointer items-center gap-3 border-b border-border px-3 py-2 text-sm last:border-0 hover:bg-muted/50">
                      <Checkbox checked={selected.has(b.id)} onCheckedChange={() => toggle(b.id)} />
                      <RegionTag region={b.region} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium">{b.name}</span>
                        <span className="block truncate text-xs text-muted-foreground">{b.contactName} · {b.lane} · {b.equipment} · last contact {b.daysSinceLast}d ago</span>
                      </span>
                      <SegmentBadge segment={b.segment} />
                    </label>
                  ))}
                </>
              ) : null}
              {!matchIds.length ? <p className="p-6 text-center text-sm text-muted-foreground">Nothing matches “{q}”. Try a city, lane or company name.</p> : null}
            </div>

            {recipients.length ? (
              <div className="mt-3">
                <div className="eyebrow mb-1.5">Will receive the email · {recipients.length}</div>
                <div className="flex max-h-28 flex-wrap gap-1.5 overflow-y-auto">
                  {recipients.slice(0, 40).map((r) => (
                    <span key={r.id} className="inline-flex items-center gap-1 rounded-sm border border-border bg-background py-0.5 pr-1 pl-2 text-xs">
                      {r.name}
                      <button type="button" onClick={() => toggle(r.id)} className="rounded-[2px] px-1 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={`Remove ${r.name}`}>×</button>
                    </span>
                  ))}
                  {recipients.length > 40 ? <span className="px-1 text-xs text-muted-foreground">+{recipients.length - 40} more</span> : null}
                </div>
              </div>
            ) : null}
          </Step>

          <Step
            n={3}
            title="Message"
            action={
              <Button variant="outline" size="sm" onClick={write} disabled={writing}>
                <Wand2 className={writing ? "animate-pulse" : undefined} /> {writing ? "Writing…" : "Rewrite with AI"}
              </Button>
            }
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Campaign type</Label>
                <Select value={campaign} onValueChange={(v) => v && setCampaign(v as CampaignType)}>
                  <SelectTrigger className="w-full"><SelectValue>{CAMPAIGNS.find((c) => c.value === campaign)?.label}</SelectValue></SelectTrigger>
                  <SelectContent>
                    {CAMPAIGNS.map((c) => (
                      <SelectItem key={c.value} value={c.value}>
                        <div>
                          <div>{c.label}</div>
                          <div className="text-xs text-muted-foreground">{c.hint}</div>
                        </div>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <StylePicker value={style} onChange={setStyle} />
            </div>
            {custom ? (
              <div className="mt-3">
                <BriefBox brief={brief} setBrief={setBrief} onWrite={write} writing={writing} understood={understood} />
              </div>
            ) : null}
            <div className="mt-3 space-y-1.5">
              <Label htmlFor="subject">Subject</Label>
              <Input id="subject" ref={subjectRef} value={subject} onFocus={() => setTarget("subject")} onChange={(e) => setSubject(e.target.value)} />
            </div>

            <div className="mt-3 rounded-sm border border-border bg-background p-3">
              <div className="flex items-start gap-2">
                <Sparkles className="mt-0.5 size-4 shrink-0 text-chart-2" />
                <div className="text-sm">
                  <div className="font-semibold">Personal fields</div>
                  <p className="text-muted-foreground">
                    Click a button to add it to the {target === "subject" ? "subject" : "email"} where your cursor is. When the email is sent, it is replaced with each company&apos;s own details, so every company gets a message written just for them. In the text it looks like <code className="rounded-[2px] bg-muted px-1 font-mono text-[0.75rem]">{"{{company}}"}</code>.
                  </p>
                </div>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
                {FIELDS.map((f) => {
                  const example = renderTemplate(f.tag, preview)
                  return (
                    <button
                      key={f.tag}
                      type="button"
                      title={f.hint}
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => insertTag(f.tag)}
                      className="flex min-w-0 flex-col items-start rounded-sm border border-border bg-card px-2.5 py-1.5 text-left transition-colors hover:border-asphalt hover:bg-accent"
                    >
                      <span className="text-sm font-semibold">+ {f.label}</span>
                      <span className="w-full truncate text-[0.7rem] text-muted-foreground">e.g. {example}</span>
                    </button>
                  )
                })}
              </div>
            </div>

            <div className="mt-3 space-y-1.5">
              <Label htmlFor="body">Email body</Label>
              <Textarea id="body" ref={bodyRef} value={body} onFocus={() => setTarget("body")} onChange={(e) => setBody(e.target.value)} rows={12} className="text-[0.9rem] leading-relaxed" />
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                Use the arrows in the preview on the right to see the finished email for each company.
              </p>
            </div>
          </Step>

          <Step
            n={4}
            title="Follow-ups"
            action={
              followUps.length < 3 ? (
                <Button variant="outline" size="sm" onClick={() => setFollowUps((f) => [...f, DEFAULT_FOLLOW_UPS[Math.min(f.length, DEFAULT_FOLLOW_UPS.length - 1)]])}>
                  <Plus /> Add follow-up
                </Button>
              ) : null
            }
          >
            <p className="mb-3 text-sm text-muted-foreground">
              Follow-ups go automatically to people who haven&apos;t replied. Anyone who replies, or asks to be removed, is taken out of the sequence. Past campaigns got about 40% more replies thanks to follow-ups.
            </p>
            {followUps.length === 0 ? <p className="rounded-sm border border-dashed border-border p-4 text-center text-sm text-muted-foreground">No follow-ups. Only the first email will be sent.</p> : null}
            <ol className="space-y-3">
              {followUps.map((f, i) => (
                <li key={i} className="rounded-sm border border-border p-3">
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <span className="flex size-6 items-center justify-center rounded-sm bg-asphalt font-display text-xs font-bold text-safety">{i + 2}</span>
                    <span className="text-sm font-semibold">Follow-up {i + 1}</span>
                    <span className="text-sm text-muted-foreground">if no reply after</span>
                    <Select value={String(f.afterDays)} onValueChange={(v) => v && setFollowUps((xs) => xs.map((x, j) => (j === i ? { ...x, afterDays: Number(v) } : x)))}>
                      <SelectTrigger size="sm" className="w-24"><SelectValue>{f.afterDays} days</SelectValue></SelectTrigger>
                      <SelectContent>{[2, 3, 4, 5, 7, 10, 14].map((d) => <SelectItem key={d} value={String(d)}>{d} days</SelectItem>)}</SelectContent>
                    </Select>
                    <Button variant="ghost" size="sm" className="ml-auto" onClick={() => setFollowUps((xs) => xs.filter((_, j) => j !== i))}>Remove</Button>
                  </div>
                  <Input value={f.subject} onChange={(e) => setFollowUps((xs) => xs.map((x, j) => (j === i ? { ...x, subject: e.target.value } : x)))} className="mb-2" aria-label={`Follow-up ${i + 1} subject`} />
                  <Textarea value={f.body} onChange={(e) => setFollowUps((xs) => xs.map((x, j) => (j === i ? { ...x, body: e.target.value } : x)))} rows={5} className="text-[0.9rem]" aria-label={`Follow-up ${i + 1} text`} />
                </li>
              ))}
            </ol>
          </Step>

          <Step n={5} title="Design">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Layout</Label>
                <Segmented
                  value={design.layout}
                  onChange={(layout) => setDesign((d) => ({ ...d, layout }))}
                  className="flex w-full [&>button]:flex-1"
                  options={[{ value: "plain", label: "Plain text" }, { value: "branded", label: "Branded" }, { value: "card", label: "Card" }]}
                />
              </div>
              <div className="space-y-1.5">
                <Label>Accent colour</Label>
                <div className="flex gap-2">
                  {ACCENTS.map((a) => (
                    <button
                      key={a.hex}
                      type="button"
                      title={a.name}
                      aria-label={a.name}
                      onClick={() => setDesign((d) => ({ ...d, accent: a.hex }))}
                      className={cn("size-8 rounded-sm border-2 transition-transform", design.accent === a.hex ? "scale-110 border-asphalt" : "border-transparent ring-1 ring-border")}
                      style={{ background: a.hex }}
                    />
                  ))}
                </div>
              </div>
              <div className="space-y-2.5">
                {([
                  ["showLogo", "Company logo header"],
                  ["showTruck", "Truck banner (matches recipient's region)"],
                  ["signature", "Dispatcher signature"],
                ] as const).map(([k, label]) => (
                  <label key={k} className="flex items-center gap-2.5 text-sm">
                    <Switch checked={design[k]} onCheckedChange={(v) => setDesign((d) => ({ ...d, [k]: v }))} disabled={design.layout === "plain" && k !== "signature"} />
                    {label}
                  </label>
                ))}
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1.5">
                  <Label htmlFor="cta">Button text</Label>
                  <Input id="cta" value={design.ctaLabel} onChange={(e) => setDesign((d) => ({ ...d, ctaLabel: e.target.value }))} placeholder="No button" />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="cta-url">Button link</Label>
                  <Input id="cta-url" value={design.ctaUrl} onChange={(e) => setDesign((d) => ({ ...d, ctaUrl: e.target.value }))} />
                </div>
              </div>
            </div>
          </Step>
        </div>

        <div className="min-w-0 space-y-4 xl:sticky xl:top-20 xl:self-start">
          <div className="rounded-sm border-2 border-asphalt bg-card p-4">
            <div className="flex flex-wrap items-center gap-3">
              <div className="min-w-0 flex-1 text-sm">
                <div className="truncate font-semibold">{name.trim() || defaultName}</div>
                <div className="text-xs text-muted-foreground" suppressHydrationWarning>
                  {recipients.length} recipients · {when === "now" ? "sends now" : `starts ${plannedStart() ? formatWhen(plannedStart()!) : "when you pick a date"}`}
                  {goalType !== "none" ? ` · goal ${goalTarget} ${GOAL_LABEL[goalType].toLowerCase()}` : ""}
                  {followUps.length ? ` · ${followUps.length} follow-up${followUps.length > 1 ? "s" : ""}` : ""}
                </div>
              </div>
              <Button size="lg" className="font-semibold" disabled={!recipients.length || !subject || !body || writing || (when === "scheduled" && !scheduleAt)} onClick={send}>
                {when === "now" ? <Rocket /> : <CalendarClock />} {when === "now" ? "Launch campaign" : "Schedule campaign"}
              </Button>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <h2 className="font-display text-lg font-semibold">Preview</h2>
            <div className="ml-auto flex items-center gap-1 text-sm">
              <Button variant="ghost" size="icon-sm" disabled={previewIdx <= 0} onClick={() => setPreviewIdx((i) => Math.max(0, i - 1))} aria-label="Previous recipient"><ChevronLeft /></Button>
              <span className="num min-w-16 text-center font-mono text-xs">{recipients.length ? `${Math.min(previewIdx, recipients.length - 1) + 1} / ${recipients.length}` : "0 / 0"}</span>
              <Button variant="ghost" size="icon-sm" disabled={previewIdx >= recipients.length - 1} onClick={() => setPreviewIdx((i) => i + 1)} aria-label="Next recipient"><ChevronRight /></Button>
            </div>
            <Segmented value={mobile ? "m" : "d"} onChange={(v) => setMobile(v === "m")} options={[{ value: "d", label: <Monitor className="size-3.5" /> }, { value: "m", label: <Smartphone className="size-3.5" /> }]} />
          </div>
          <div className="max-h-[calc(100vh-15rem)] overflow-y-auto">
            <EmailPreview subject={subject} body={body} design={design} recipient={preview} mobile={mobile} />
          </div>
        </div>
      </div>

      <p className="mt-6 text-center text-sm text-muted-foreground">
        After launch you&apos;ll see the campaign live: deliveries, opens, replies read by the AI, and progress towards your goal. All campaigns are in{" "}
        <Link href="/campaigns" className="font-semibold text-chart-1 hover:underline">Campaigns</Link>.
      </p>
    </>
  )
}
