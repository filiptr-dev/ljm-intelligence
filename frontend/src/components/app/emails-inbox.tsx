"use client"

import * as React from "react"
import Link from "next/link"
import { toast } from "sonner"
import { Check, Clock, Forward, Plus, Reply, Sparkles, Trophy } from "lucide-react"
import { Button, buttonVariants } from "@/components/ui/button"
import type { EmailPurpose } from "@/lib/ai/types"
import { POSITIVE, PURPOSE_LABEL, TONE_LABEL, type Campaign, type CampaignRecipient, type ReplyCategory } from "@/lib/campaigns/types"
import { LEAD_KIND_LABEL } from "@/lib/data/types"
import { num, timeAgo } from "@/lib/format"
import { useNow } from "@/lib/use-now"
import { cn } from "@/lib/utils"
import { pctText, ReplyChip } from "./campaign-bits"
import { type ComposerInit, type ContactOption } from "./email-composer"
import { renderTemplate } from "./email-preview"
import { useEngine } from "./engine"
import { SentimentDot } from "./intent"
import { Segmented } from "./segmented"
import { PageHeader, RegionTag, StatTile } from "./ui"

type Filter = "all" | "waiting" | "replied" | "scheduled"
const DAY = 86_400_000
const HOUR = 3_600_000

const ago = (ms: number) => timeAgo(new Date(ms).toISOString())
const when = (ms: number) =>
  new Date(ms).toLocaleString("en-US", { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })

function stateOf(c: Campaign, now: number) {
  const r = c.recipients[0]
  if (r.status === "queued") return (c.schedule?.at ?? 0) > now ? "Scheduled" : "Sending"
  if (r.status === "bounced") return "Bounced"
  if (r.reply) return "Replied"
  return r.status === "opened" ? "Opened" : "Delivered"
}

/** sent recently and no answer yet; older emails without a reply just count as "no reply" */
const isWaiting = (c: Campaign, st: string, now: number) => st === "Sending" || ((st === "Delivered" || st === "Opened") && now - (c.schedule?.at ?? c.createdAt) < 7 * DAY)

/** What the AI suggests doing with a reply, and the email that does it. */
function nextStep(r: CampaignRecipient, speedLift: number): { text: string; init?: ComposerInit; label?: string } {
  const first = r.contactName.split(" ")[0]
  switch (r.reply?.category as ReplyCategory | undefined) {
    case "rates":
      return { text: `${first} wants a price. Answer within the hour: in your emails, the fastest quotes win ${speedLift.toFixed(1)}× as often as slow ones.`, label: "Send the rate", init: { to: r.id, purpose: "send_rate" } }
    case "interested":
      return { text: `${first} is interested. Send your rate and carrier packet today while it's warm.`, label: "Reply with a rate", init: { to: r.id, purpose: "send_rate" } }
    case "not_now":
      return { text: `Not a no. Check in again in about 30 days, the AI can schedule it now.`, label: "Schedule a check-in", init: { to: r.id, purpose: "check_in", afterDays: 30 } }
    case "out_of_office":
      return { text: `${first} is away. Send it again when they're back next week.`, label: "Send again next week", init: { to: r.id, purpose: "truck_available", afterDays: 7 } }
    case "not_interested":
      return { text: "They said no. The AI keeps them off one-off emails for 90 days, but they stay in quarterly campaigns." }
    case "unsubscribe":
      return { text: "Removed from every list automatically. No further emails will be sent." }
    default:
      return { text: "" }
  }
}

export function EmailsInbox({ history, campaignReplyRate, speedLift }: { history: Campaign[]; contacts?: ContactOption[]; campaignReplyRate: number; speedLift: number; initial?: ComposerInit }) {
  const { campaigns, sendFollowUp } = useEngine()
  const emails = React.useMemo(
    () => [...campaigns.filter((c) => c.single), ...history].sort((a, b) => (b.schedule?.at ?? b.createdAt) - (a.schedule?.at ?? a.createdAt)),
    [campaigns, history],
  )
  const [filter, setFilter] = React.useState<Filter>("all")
  // open on the latest reply: that's where the AI has something to say
  const [selectedId, setSelectedId] = React.useState<string | undefined>(history.find((c) => c.recipients[0].reply)?.id)
  // The composer is now a full page — build a link with the same init fields.
  const composeHref = (init: ComposerInit = {}) => {
    const p = new URLSearchParams()
    if (init.to) p.set("broker", init.to)
    if (init.purpose) p.set("purpose", init.purpose)
    const qs = p.toString()
    return qs ? `/emails/compose?${qs}` : "/emails/compose"
  }

  const now = useNow()
  const shown = emails.filter((c) => {
    const st = stateOf(c, now)
    return filter === "all" || (filter === "replied" ? st === "Replied" : filter === "scheduled" ? st === "Scheduled" : isWaiting(c, st, now))
  })
  const selected = emails.find((c) => c.id === selectedId) ?? shown[0]

  // stats over the last 30 days
  const recent = emails.filter((c) => now - c.createdAt < 30 * DAY && c.recipients[0].status !== "queued")
  const delivered = recent.filter((c) => c.recipients[0].status !== "bounced")
  const opened = delivered.filter((c) => ["opened", "replied"].includes(c.recipients[0].status)).length
  const replied = delivered.filter((c) => c.recipients[0].reply)
  const positive = replied.filter((c) => POSITIVE.includes(c.recipients[0].reply!.category)).length
  const replyHours = replied.map((c) => (c.recipients[0].reply!.at - (c.schedule?.at ?? c.createdAt)) / HOUR).filter((h) => h > 0).sort((a, b) => a - b)
  const medianHours = replyHours.length ? replyHours[Math.floor(replyHours.length / 2)] : 0
  const waiting = emails.filter((c) => isWaiting(c, stateOf(c, now), now)).length
  const replyRate = replied.length / (delivered.length || 1)

  const counts: Record<Filter, number> = {
    all: emails.length,
    waiting: emails.filter((c) => isWaiting(c, stateOf(c, now), now)).length,
    replied: emails.filter((c) => stateOf(c, now) === "Replied").length,
    scheduled: emails.filter((c) => stateOf(c, now) === "Scheduled").length,
  }

  return (
    <>
      <PageHeader
        eyebrow="Grow"
        title="Emails"
        description="Quick personal emails to one company, outside of campaigns: a truck that's free tomorrow, a quote to chase, a thank-you. The AI writes them, tracks opens and reads every reply."
        actions={
          <Link href={composeHref()} className={cn(buttonVariants({ size: "lg" }), "font-semibold")}>
            <Plus /> New email
          </Link>
        }
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        <StatTile label="Sent · last 30 days" value={num(recent.length)} sub={`${pctText(opened / (delivered.length || 1))} opened`} />
        <StatTile label="Reply rate" value={pctText(replyRate)} sub={`vs ${pctText(campaignReplyRate)} for campaign emails`} />
        <StatTile label="Interested replies" value={num(positive)} sub={`${pctText(positive / (replied.length || 1))} of replies`} />
        <StatTile label="Typical reply time" value={medianHours < 1 ? `${Math.round(medianHours * 60)} min` : `${medianHours.toFixed(1)} h`} sub="Median, from send to reply" />
        <StatTile label="Waiting for a reply" value={num(waiting)} sub="Sent in the last 7 days" />
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
        <section className="flex min-w-0 flex-col rounded-sm border border-border bg-card lg:max-h-[calc(100vh-9rem)]">
          <div className="border-b border-border p-2">
            <Segmented
              value={filter}
              onChange={setFilter}
              className="flex w-full [&>button]:flex-1 [&>button]:px-1.5"
              options={(["all", "waiting", "replied", "scheduled"] as Filter[]).map((f) => ({
                value: f,
                label: <span>{{ all: "All", waiting: "Waiting", replied: "Replied", scheduled: "Scheduled" }[f]} <span className="num font-mono text-xs opacity-70">{counts[f]}</span></span>,
              }))}
            />
          </div>
          <ul className="min-h-0 flex-1 divide-y divide-border overflow-y-auto">
            {shown.map((c) => {
              const r = c.recipients[0]
              const st = stateOf(c, now)
              const active = selected?.id === c.id
              return (
                <li key={c.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(c.id)}
                    className={cn("block w-full px-3 py-2.5 text-left transition-colors", active ? "bg-accent shadow-[inset_3px_0_0_var(--safety)]" : "hover:bg-muted/50")}
                  >
                    <div className="flex items-center gap-2">
                      <RegionTag region={r.region} />
                      <span className="min-w-0 flex-1 truncate text-sm font-semibold">{r.name}</span>
                      <span className="shrink-0 text-[0.7rem] text-muted-foreground" suppressHydrationWarning>
                        {st === "Scheduled" ? when(c.schedule!.at) : ago(r.reply?.at ?? c.schedule?.at ?? c.createdAt)}
                      </span>
                    </div>
                    <div className="mt-0.5 truncate text-sm">{renderTemplate(c.subject, r)}</div>
                    <div className="mt-1 flex items-center gap-2">
                      {r.reply ? <ReplyChip category={r.reply.category} /> : <StateTag state={st} />}
                      <span className="truncate text-xs text-muted-foreground">{PURPOSE_LABEL[c.type as EmailPurpose] ?? c.type} · {r.contactName}</span>
                      {r.won ? <Trophy className="ml-auto size-3.5 shrink-0 text-good" aria-label="Won as customer" /> : null}
                    </div>
                  </button>
                </li>
              )
            })}
            {!shown.length ? <li className="p-8 text-center text-sm text-muted-foreground">Nothing here yet.</li> : null}
          </ul>
        </section>

        {selected ? (
          <EmailThread
            key={selected.id}
            c={selected}
            now={now}
            speedLift={speedLift}
            composeHref={composeHref}
            onFollowUp={() => {
              sendFollowUp(selected.id)
              toast.success(`Follow-up sent to ${selected.recipients[0].contactName}`)
            }}
          />
        ) : (
          <div className="rounded-sm border border-dashed border-border p-10 text-center text-sm text-muted-foreground">Select an email to see it and the reply.</div>
        )}
      </div>
    </>
  )
}

function StateTag({ state }: { state: string }) {
  const cls: Record<string, string> = {
    Scheduled: "border-chart-1/40 bg-chart-1/10 text-chart-1",
    Sending: "border-asphalt bg-asphalt text-white",
    Delivered: "border-border bg-background text-muted-foreground",
    Opened: "border-safety bg-safety/20 text-foreground",
    Bounced: "border-bad/40 bg-bad/10 text-bad",
  }
  return <span className={cn("inline-flex h-5 items-center rounded-[3px] border px-1.5 text-[0.68rem] font-semibold whitespace-nowrap", cls[state])}>{state}</span>
}

function EmailThread({ c, now, speedLift, composeHref, onFollowUp }: { c: Campaign; now: number; speedLift: number; composeHref: (init?: ComposerInit) => string; onFollowUp: () => void }) {
  const r = c.recipients[0]
  const st = stateOf(c, now)
  const sentAt = c.schedule?.at ?? c.createdAt
  const fu = c.followUps?.[0]
  const fuDue = fu ? sentAt + fu.afterDays * DAY : undefined
  const next = nextStep(r, speedLift)
  const steps = [
    { label: st === "Scheduled" ? `Scheduled · ${when(sentAt)}` : `Sent · ${ago(sentAt)}`, done: st !== "Scheduled" && st !== "Sending" },
    { label: "Opened", done: r.status === "opened" || r.status === "replied" },
    { label: r.reply ? `Replied · ${ago(r.reply.at)}` : "Replied", done: !!r.reply },
  ]

  return (
    <section className="min-w-0 space-y-4">
      <div className="rounded-sm border border-border bg-card p-4">
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <RegionTag region={r.region} />
              <span className="text-xs text-muted-foreground">{r.kind === "broker" ? "Existing broker" : r.companyType ? LEAD_KIND_LABEL[r.companyType] : "New lead"}</span>
              {r.won ? <span className="inline-flex h-5 items-center gap-1 rounded-[3px] bg-good px-1.5 text-[0.68rem] font-semibold text-white"><Trophy className="size-3" /> Won · {r.loads} load{r.loads === 1 ? "" : "s"} booked</span> : null}
            </div>
            <h2 className="truncate font-display text-2xl leading-tight font-bold">{r.name}</h2>
            <div className="text-sm text-muted-foreground">{r.contactName} · {r.email}{r.lane ? ` · ${r.lane}` : ""}</div>
          </div>
          <div className="flex shrink-0 gap-2">
            {r.kind === "broker" ? <Link href={`/brokers/${r.id}`} className="inline-flex h-8 items-center rounded-lg border border-border px-3 text-sm hover:bg-muted">Broker profile</Link> : null}
            <Link href={composeHref({ to: r.id })} className={buttonVariants({ variant: "outline", size: "sm" })}><Forward /> Email again</Link>
          </div>
        </div>
        <ol className="mt-4 grid grid-cols-3 gap-1">
          {steps.map((s) => (
            <li key={s.label} className="min-w-0">
              <div className={cn("h-1.5", s.done ? "bg-good" : "bg-muted")} />
              <div className={cn("mt-1 flex items-center gap-1 truncate text-xs", s.done ? "font-medium text-foreground" : "text-muted-foreground")} suppressHydrationWarning>
                {s.done ? <Check className="size-3 shrink-0 text-good" /> : <Clock className="size-3 shrink-0" />} {s.label}
              </div>
            </li>
          ))}
        </ol>
      </div>

      <article className="rounded-sm border border-border bg-card">
        <header className="border-b border-border px-4 py-3">
          <div className="text-xs text-muted-foreground" suppressHydrationWarning>
            From {renderTemplate("{{sender}}")} · {PURPOSE_LABEL[c.type as EmailPurpose] ?? c.type}{c.tone ? ` · ${TONE_LABEL[c.tone]}` : ""}{c.brief ? " · written from your description" : ""} · {when(sentAt)}
          </div>
          <h3 className="mt-0.5 font-semibold">{renderTemplate(c.subject, r)}</h3>
        </header>
        <div className="px-4 py-3 text-[0.92rem] leading-relaxed whitespace-pre-line">{renderTemplate(c.body, r)}</div>
        {fu && !r.reply && st !== "Bounced" ? (
          <footer className="flex flex-wrap items-center gap-2 border-t border-border bg-muted/40 px-4 py-2 text-sm">
            <Clock className="size-4 text-muted-foreground" />
            <span className="flex-1" suppressHydrationWarning>
              {(c.followUpsSent ?? 0) > 0 ? "Follow-up sent." : `Automatic follow-up ${fuDue && fuDue > now ? `on ${when(fuDue)}` : "is due"} if there's no reply.`}
            </span>
            {!c.historical && !(c.followUpsSent ?? 0) && st !== "Scheduled" && st !== "Sending" ? <Button variant="outline" size="sm" onClick={onFollowUp}>Send follow-up now</Button> : null}
          </footer>
        ) : null}
      </article>

      {r.reply ? (
        <article className="rounded-sm border-2 border-asphalt bg-card">
          <header className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
            <Reply className="size-4 -scale-x-100 text-muted-foreground" />
            <span className="font-semibold">{r.contactName}</span>
            <span className="text-xs text-muted-foreground" suppressHydrationWarning>replied {ago(r.reply.at)}</span>
            <span className="ml-auto flex items-center gap-2">
              <ReplyChip category={r.reply.category} />
              <SentimentDot value={r.reply.sentiment} />
            </span>
          </header>
          <p className="px-4 py-3 text-[0.95rem] italic">“{r.reply.text}”</p>
          {next.text ? (
            <footer className="flex flex-wrap items-center gap-3 border-t border-border bg-muted/40 px-4 py-3">
              <Sparkles className="size-4 shrink-0 text-chart-2" />
              <p className="min-w-0 flex-1 text-sm"><span className="font-semibold">AI suggests: </span>{next.text}</p>
              {next.init ? <Link href={composeHref(next.init)} className={cn(buttonVariants({ size: "sm" }), "font-semibold")}>{next.label}</Link> : null}
            </footer>
          ) : null}
          <div className="px-4 pb-2 text-[0.7rem] text-muted-foreground">Read by the AI · {Math.round(r.reply.confidence * 100)}% confident</div>
        </article>
      ) : st === "Opened" || st === "Delivered" ? (
        <p className="rounded-sm border border-dashed border-border p-4 text-center text-sm text-muted-foreground">
          No reply yet. Most replies to personal emails arrive within a few hours; the AI will read it and suggest what to do the moment it comes in.
        </p>
      ) : null}
    </section>
  )
}
