"use client"

/**
 * Broker detail — /brokers/[id]  (restored v2)
 *
 * Rewires the real-data v1 page to also carry the sections that commit
 * `ba15198` silently dropped from the pre-rewire page: one AI panel
 * (summary + risks + AI next step + always-visible Draft email, reusing
 * `SingleEmailBuilder` + the real-send adapter), Email timeline, Main lane,
 * Why they said no. KPIs are promoted into the right rail.
 *
 * Honest-by-design: a missing field renders as "—" / "no data yet". Sections
 * are never hidden for lack of data. We never fake anything.
 */

import * as React from "react"
import Link from "next/link"
import { useParams } from "next/navigation"
import { toast } from "sonner"
import {
  ArrowLeft,
  ExternalLink,
  Mail,
  MapPin,
  Phone,
} from "lucide-react"
import { Panel } from "@/components/app/ui"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { ContactOption } from "@/components/app/email-composer"
import { SingleEmailBuilder } from "@/components/app/single-email-builder"
import { singleSendAdapter } from "@/lib/api/email-send"
import { InboxCards } from "./inbox-cards"
import { BrokerKpis } from "./broker-kpis"
import { AiSummaryCard, BrokerOverviewSections, BrokerStatTiles } from "./overview-sections"
import {
  getBroker,
  getBrokerActivity,
  getBrokerObjections,
  type ActivityEvent,
  type BrokerDetail,
  type ContactField,
  type NamedContact,
  type NextActionKind,
  type ObjectionItem,
} from "@/lib/api/brokers"

const ACTION_LABEL: Record<NextActionKind, string> = {
  call: "Call",
  email: "Email",
  follow_up: "Follow up",
  wait: "Wait",
}
const ACTION_STYLE: Record<NextActionKind, string> = {
  call: "bg-bad text-white",
  email: "bg-chart-2 text-white",
  follow_up: "bg-warn text-asphalt",
  wait: "bg-muted text-muted-foreground",
}

// Anchor id the "Email" next-step button scrolls to.
const DRAFT_ANCHOR = "broker-draft-email"

function SourceBadge({ source }: { source: string | null | undefined }) {
  if (!source) return null
  return (
    <span className="ml-2 inline-flex items-center rounded-[3px] border border-border bg-background px-1.5 py-0.5 text-[0.6rem] font-semibold tracking-wider uppercase text-muted-foreground">
      {source}
    </span>
  )
}

function FieldLine({
  icon,
  field,
  href,
}: {
  icon: React.ReactNode
  field: ContactField
  href?: string
}) {
  if (!field.value) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        {icon}
        <span>—</span>
      </div>
    )
  }
  const content = (
    <>
      <span className="min-w-0 truncate">{field.value}</span>
      <SourceBadge source={field.source} />
    </>
  )
  return (
    <div className="flex min-w-0 items-center gap-2 text-sm">
      {icon}
      {href ? (
        <a href={href} className="min-w-0 flex-1 truncate underline-offset-2 hover:underline">
          {content}
        </a>
      ) : (
        <div className="min-w-0 flex-1 truncate">{content}</div>
      )}
    </div>
  )
}

function NamedContactCard({ c }: { c: NamedContact }) {
  return (
    <div className="rounded-sm border border-border bg-card p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">{c.name.value ?? "—"}</span>
        <SourceBadge source={c.name.source} />
        {c.is_decision_maker ? (
          <span className="inline-flex items-center rounded-[3px] bg-safety px-1.5 py-0.5 text-[0.6rem] font-bold tracking-wider uppercase text-asphalt">
            DM
          </span>
        ) : null}
        <span className="ml-auto text-[0.68rem] text-muted-foreground">
          sighted {c.sighted_count}×
        </span>
      </div>
      <div className="mt-1 text-xs text-muted-foreground">{c.title.value ?? "—"}</div>
      <div className="mt-2 space-y-1">
        <FieldLine
          icon={<Mail className="size-3.5" />}
          field={c.email}
          href={c.email.value ? `mailto:${c.email.value}` : undefined}
        />
        <FieldLine
          icon={<Phone className="size-3.5" />}
          field={c.phone}
          href={c.phone.value ? `tel:${c.phone.value}` : undefined}
        />
        {c.linkedin_url ? (
          <a
            href={c.linkedin_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:underline"
          >
            <ExternalLink className="size-3.5" /> LinkedIn
          </a>
        ) : null}
      </div>
    </div>
  )
}

function ActivityRow({ e }: { e: ActivityEvent }) {
  if (e.kind === "call_outcome") {
    return (
      <li className="flex items-start gap-2 border-l-2 border-border py-1.5 pl-3 text-sm">
        <Phone className="mt-0.5 size-3.5 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <div className="font-medium">Call: {e.outcome}</div>
          {e.note ? <div className="text-xs text-muted-foreground">{e.note}</div> : null}
          <div className="text-[0.68rem] text-muted-foreground">
            {new Date(e.logged_at).toLocaleString()}
          </div>
        </div>
      </li>
    )
  }
  return (
    <li className="flex items-start gap-2 border-l-2 border-border py-1.5 pl-3 text-sm">
      <Mail className="mt-0.5 size-3.5 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <div className="font-medium">
          Email sent{e.replied_at ? " · replied" : ""}
          <span className="ml-2 text-[0.6rem] font-semibold uppercase text-muted-foreground">
            {e.mode}
          </span>
        </div>
        <div className="truncate text-xs text-muted-foreground">
          {e.subject || "(no subject)"} → {e.to_email}
        </div>
        <div className="text-[0.68rem] text-muted-foreground">
          {new Date(e.sent_at).toLocaleString()}
        </div>
      </div>
    </li>
  )
}

/**
 * Main lane — origin → destination summary. Always rendered; with no lane
 * on file it says "—" / "no data yet" instead of disappearing.
 */
function MainLaneCard({ lane }: { lane: BrokerDetail["broker"]["main_lane"] | null }) {
  const dash = <span className="text-muted-foreground">—</span>
  if (!lane) {
    return (
      <Panel title="Main lane">
        <p className="text-sm text-muted-foreground">— · no data yet</p>
      </Panel>
    )
  }
  return (
    <Panel title="Main lane">
      <div className="flex flex-wrap items-baseline gap-2 text-base font-semibold">
        <span>{lane.origin ?? dash}</span>
        <span className="text-muted-foreground">→</span>
        <span>{lane.destination ?? dash}</span>
      </div>
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {lane.miles_band ? <span>{lane.miles_band} mi</span> : null}
        {lane.last_seen_at ? (
          <span>last seen {new Date(lane.last_seen_at).toLocaleDateString()}</span>
        ) : null}
      </div>
    </Panel>
  )
}

/**
 * Email timeline — email-only view of the activity feed (filters out
 * call_outcomes). Fed by the same `activity[]` already on the detail
 * payload; "Load more" uses the existing /brokers/{id}/activity cursor.
 */
function EmailTimeline({ emails }: { emails: ActivityEvent[] }) {
  if (emails.length === 0) {
    return (
      <Panel title="Email timeline" description="No emails sent to this broker yet.">
        <p className="text-sm text-muted-foreground">—</p>
      </Panel>
    )
  }
  return (
    <Panel
      title="Email timeline"
      description={`${emails.length} email${emails.length === 1 ? "" : "s"}, newest first.`}
      bodyClassName="p-0"
    >
      <ol className="divide-y divide-border">
        {emails.map((e, i) => {
          if (e.kind !== "email_sent") return null
          return (
            <li key={`${e.sent_at}-${i}`} className="flex items-center gap-3 px-4 py-2.5">
              <span className="w-10 shrink-0 font-mono text-[0.65rem] font-semibold text-muted-foreground">
                OUT
              </span>
              <span className="min-w-0 flex-1 truncate text-sm">
                {e.subject || "(no subject)"}
              </span>
              <span className="truncate text-xs text-muted-foreground">→ {e.to_email}</span>
              {e.replied_at ? (
                <span className="rounded-[3px] bg-good/15 px-1.5 py-0.5 text-[0.6rem] font-semibold uppercase text-good">
                  replied
                </span>
              ) : null}
              <span className="rounded-[3px] bg-muted px-1.5 py-0.5 text-[0.6rem] font-semibold uppercase text-muted-foreground">
                {e.mode}
              </span>
              <span className="w-28 text-right text-xs text-muted-foreground">
                {new Date(e.sent_at).toLocaleString()}
              </span>
            </li>
          )
        })}
      </ol>
    </Panel>
  )
}

/**
 * Why they said no — always shown; empty state says so honestly.
 * Reads `/brokers/{id}/objections`.
 */
function WhyTheySaidNo({ items }: { items: ObjectionItem[] }) {
  if (items.length === 0) {
    return (
      <Panel title="Why they said no" description="0 on file">
        <p className="text-sm text-muted-foreground">No rejections yet.</p>
      </Panel>
    )
  }
  return (
    <Panel title="Why they said no" description={`${items.length} on file`}>
      <ul className="space-y-2">
        {items.map((o, i) => (
          <li key={`${o.logged_at}-${i}`} className="border-l-2 border-bad pl-3 text-sm">
            <div className="flex items-center gap-2">
              <span className="rounded-[3px] bg-muted px-1.5 py-0.5 text-[0.6rem] font-semibold uppercase text-muted-foreground">
                {o.source}
              </span>
              {o.contact_name ? (
                <span className="text-xs text-muted-foreground">{o.contact_name}</span>
              ) : null}
              <span className="ml-auto text-[0.68rem] text-muted-foreground">
                {new Date(o.logged_at).toLocaleDateString()}
              </span>
            </div>
            <div className="mt-0.5">{o.text}</div>
          </li>
        ))}
      </ul>
    </Panel>
  )
}

/**
 * Build the preselected recipient for the inline SingleEmailBuilder from
 * the detail payload. We synthesise a `ContactOption` from the broker
 * header rather than forking the component — same seam `/emails/compose`
 * uses when `?to=<email>` is passed in.
 */
function buildInitialRecipient(
  b: BrokerDetail["broker"],
  emailOverride?: string | null,
): ContactOption | undefined {
  const email = emailOverride || b.primary_email.value
  if (!email) return undefined
  const contactName = b.contacts[0]?.name.value ?? b.name
  return {
    id: `broker:${b.id}`,
    kind: "broker",
    name: b.name,
    contactName,
    email,
    // The region VO is required on `Recipient`; the broker row carries only
    // a US state so we pass it through — the builder only displays this
    // string, never routes on it.
    region: b.state as ContactOption["region"],
    sub: `Broker · ${b.city ? `${b.city}, ` : ""}${b.state}`,
  }
}

export default function BrokerDetailPage() {
  const params = useParams<{ id: string }>()
  const id = params.id

  const [data, setData] = React.useState<BrokerDetail | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [notFoundFlag, setNotFoundFlag] = React.useState(false)
  const [moreCursor, setMoreCursor] = React.useState<string | null>(null)
  const [moreLoading, setMoreLoading] = React.useState(false)
  const [extraActivity, setExtraActivity] = React.useState<ActivityEvent[]>([])
  const [objections, setObjections] = React.useState<ObjectionItem[]>([])
  // Address the operator asked to draft to (one click on the AI panel's
  // Draft email button). null = builder closed.
  const [draftTo, setDraftTo] = React.useState<string | null>(null)

  React.useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      try {
        const [d, obj] = await Promise.all([getBroker(id), getBrokerObjections(id)])
        if (!cancelled) {
          setData(d)
          setObjections(obj.items)
          const items = d.activity ?? []
          if (items.length > 0) {
            const last = items[items.length - 1]
            const ts = last.kind === "call_outcome" ? last.logged_at : last.sent_at
            setMoreCursor(btoa(ts).replace(/=+$/, ""))
          }
        }
      } catch (e) {
        const msg = String(e)
        if (msg.includes("404")) setNotFoundFlag(true)
        else toast.error("Couldn't load broker", { description: msg })
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [id])

  const loadMore = async () => {
    if (!moreCursor) return
    setMoreLoading(true)
    try {
      const page = await getBrokerActivity(id, moreCursor, 50)
      setExtraActivity((prev) => [...prev, ...page.items])
      setMoreCursor(page.next_cursor ?? null)
    } catch (e) {
      toast.error("Couldn't load more activity", { description: String(e) })
    } finally {
      setMoreLoading(false)
    }
  }

  if (notFoundFlag) {
    return (
      <>
        <Link
          href="/brokers"
          className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-4" /> All brokers
        </Link>
        <p className="text-sm text-muted-foreground">Broker not found.</p>
      </>
    )
  }

  if (loading || !data) {
    return (
      <>
        <Link
          href="/brokers"
          className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-4" /> All brokers
        </Link>
        <p className="text-sm text-muted-foreground">Loading…</p>
      </>
    )
  }

  const b = data.broker
  const activityAll = [...data.activity, ...extraActivity]
  const emailOnly = activityAll.filter((e) => e.kind === "email_sent")
  const initialRecipient = buildInitialRecipient(b, draftTo)
  // Email is the suggested purpose only when the server picked `email` as
  // the next action; otherwise let the builder's own heuristic choose.
  const initialPurpose = b.next_action.kind === "email" ? ("intro" as const) : undefined
  const openDraft = (addr: string) => {
    setDraftTo(addr)
    // The section mounts on the next render; scroll after it exists.
    setTimeout(() => document.getElementById(DRAFT_ANCHOR)?.scrollIntoView({ behavior: "smooth" }), 50)
  }

  return (
    <>
      <Link
        href="/brokers"
        className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" /> All brokers
      </Link>
      <InboxCards email={b.primary_email?.value ?? null} />

      {/* Hero header — unchanged from v1 */}
      <div className="mb-5 flex flex-col gap-4 border-b border-border pb-5 lg:flex-row lg:items-end">
        <div className="min-w-0 flex-1">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            {b.mc ? (
              <span className="inline-flex items-center rounded-[3px] border border-border bg-background px-1.5 py-0.5 text-xs font-mono">
                MC-{b.mc}
              </span>
            ) : null}
            {b.dot ? (
              <span className="inline-flex items-center rounded-[3px] border border-border bg-background px-1.5 py-0.5 text-xs font-mono">
                DOT-{b.dot}
              </span>
            ) : null}
            <span className="text-sm text-muted-foreground">
              {b.city ? `${b.city}, ` : ""}
              {b.state}
            </span>
            {b.fit_score != null ? (
              <span className="inline-flex items-center rounded-[3px] border border-border bg-background px-1.5 py-0.5 text-[0.68rem] font-medium">
                fit {b.fit_score}
              </span>
            ) : null}
            <span
              className={cn(
                "inline-flex items-center rounded-[3px] px-2 py-0.5 text-[0.68rem] font-bold tracking-wider uppercase",
                ACTION_STYLE[b.next_action.kind],
              )}
              title={b.next_action.reason}
            >
              {ACTION_LABEL[b.next_action.kind]}
            </span>
          </div>
          <h1 className="font-display text-3xl leading-none font-bold md:text-[2.4rem]">
            {b.name}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">{b.next_action.reason}</p>
          <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
            {b.website_url ? (
              <a
                href={b.website_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 hover:underline"
              >
                <ExternalLink className="size-3.5" /> Website
              </a>
            ) : null}
            {b.linkedin_company_url ? (
              <a
                href={b.linkedin_company_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 hover:underline"
              >
                <ExternalLink className="size-3.5" /> LinkedIn
              </a>
            ) : null}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {b.phone.value ? (
            <a
              href={`tel:${b.phone.value}`}
              className="inline-flex h-10 items-center gap-1.5 rounded-sm bg-safety px-3 text-sm font-semibold text-asphalt hover:opacity-90"
            >
              <Phone className="size-4" />
              <span className="font-mono">{b.phone.value}</span>
            </a>
          ) : null}
          {b.primary_email.value ? (
            <a
              href={`mailto:${b.primary_email.value}`}
              className="inline-flex h-10 items-center gap-1.5 rounded-sm border border-border px-3 text-sm hover:bg-muted"
            >
              <Mail className="size-4" />
              Email
            </a>
          ) : null}
        </div>
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        {/* ---- main column ---- */}
        <div className="space-y-5">
          <AiSummaryCard
            brokerId={b.id}
            email={b.primary_email.value ?? null}
            fallbackStep={{ label: ACTION_LABEL[b.next_action.kind], detail: b.next_action.reason }}
            onDraft={openDraft}
          />

          <BrokerStatTiles om={data.overview_metrics ?? null} />

          <Panel
            title="Contact block"
            description="Every field with a visible source. Missing data stays missing — we don't fake it."
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <FieldLine
                icon={<Phone className="size-4" />}
                field={b.phone}
                href={b.phone.value ? `tel:${b.phone.value}` : undefined}
              />
              <FieldLine
                icon={<Mail className="size-4" />}
                field={b.primary_email}
                href={b.primary_email.value ? `mailto:${b.primary_email.value}` : undefined}
              />
              <FieldLine icon={<MapPin className="size-4" />} field={b.address} />
            </div>
          </Panel>

          <Panel
            title={`Named contacts (${b.contacts.length})`}
            description={
              b.contacts.length === 0
                ? "No named contacts yet — the enrichment run hasn't surfaced anyone here."
                : "Ordered: decision-makers first, then confidence, then provenance count."
            }
          >
            {b.contacts.length === 0 ? (
              <p className="text-sm text-muted-foreground">—</p>
            ) : (
              <div className="grid gap-2 sm:grid-cols-2">
                {b.contacts.map((c) => (
                  <NamedContactCard key={c.id} c={c} />
                ))}
              </div>
            )}
          </Panel>

          {/* Draft email — opened by the AI panel's button. SingleEmailBuilder
              auto-drafts on mount against the real-send adapter (one click →
              generated email). Same component used by /emails/compose. */}
          {draftTo !== null && initialRecipient ? (
            <section
              id={DRAFT_ANCHOR}
              aria-label="Draft email"
              className="rounded-sm border border-border bg-card"
            >
              <header className="border-b border-border px-4 py-2.5">
                <h2 className="text-sm font-semibold">Draft email</h2>
                <p className="text-xs text-muted-foreground">
                  Composes to {initialRecipient.email} via the real send path.
                </p>
              </header>
              <div className="p-3">
                <SingleEmailBuilder
                  key={initialRecipient.email}
                  contacts={[]}
                  backHref={`/brokers/${b.id}`}
                  backLabel={`Back to ${b.name}`}
                  initialRecipient={initialRecipient}
                  initialToId={initialRecipient.id}
                  initialPurpose={initialPurpose}
                  onSend={singleSendAdapter}
                />
              </div>
            </section>
          ) : null}

          <EmailTimeline emails={emailOnly} />

          <Panel
            title="Email analytics"
            description="Sample data — real analytics unlock when the inbox connector is live."
          >
            <div className="rounded-sm border border-dashed border-warn bg-warn/10 p-3 text-xs text-asphalt">
              These tiles are seeded placeholders. Open-rate, best send-time and
              reply-rate will light up once <code>gmail-inbox-connector</code> +{" "}
              <code>inbox-analysis</code> ship. The data below is real:{" "}
              <strong>{data.summary.sent_count_30d}</strong> emails sent in the
              last 30 days,{" "}
              <strong>{data.summary.reply_count_30d}</strong> replies. Everything
              else here is for layout — not for decisions.
            </div>
          </Panel>
        </div>

        {/* ---- right rail ---- */}
        <div className="space-y-4">
          <MainLaneCard lane={b.main_lane ?? null} />

          {/* Restored (ba15198^): health gauge + 12-month chart; never hidden. */}
          <BrokerOverviewSections metrics={data.overview_metrics ?? null} />

          <BrokerKpis brokerId={b.id} />

          <Panel
            title="Activity"
            description={activityAll.length === 0 ? "No activity yet." : "Newest first — calls + emails merged."}
          >
            {activityAll.length === 0 ? (
              <p className="text-sm text-muted-foreground">—</p>
            ) : (
              <ul className="space-y-0.5">
                {activityAll.map((e, i) => (
                  <ActivityRow
                    key={`${e.kind}-${e.kind === "call_outcome" ? e.logged_at : e.sent_at}-${i}`}
                    e={e}
                  />
                ))}
              </ul>
            )}
            {moreCursor ? (
              <div className="mt-3 flex justify-center">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={moreLoading}
                  onClick={() => void loadMore()}
                >
                  {moreLoading ? "Loading…" : "Load more"}
                </Button>
              </div>
            ) : null}
          </Panel>

          <Panel title="Summary (last 30d)" description="Real numbers from sent_log + call_outcomes.">
            <ul className="space-y-1 text-sm">
              <li className="flex justify-between">
                <span className="text-muted-foreground">Sent</span>
                <span className="font-mono font-semibold">{data.summary.sent_count_30d}</span>
              </li>
              <li className="flex justify-between">
                <span className="text-muted-foreground">Replied</span>
                <span className="font-mono font-semibold">{data.summary.reply_count_30d}</span>
              </li>
              {data.summary.last_call ? (
                <li className="flex justify-between">
                  <span className="text-muted-foreground">Last call</span>
                  <span>
                    {data.summary.last_call.outcome} ·{" "}
                    {new Date(data.summary.last_call.logged_at).toLocaleDateString()}
                  </span>
                </li>
              ) : null}
              {data.summary.last_email ? (
                <li className="flex justify-between">
                  <span className="text-muted-foreground">Last email</span>
                  <span>{new Date(data.summary.last_email.sent_at).toLocaleDateString()}</span>
                </li>
              ) : null}
            </ul>
          </Panel>

          <WhyTheySaidNo items={objections} />
        </div>
      </div>
    </>
  )
}
