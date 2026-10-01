"use client"

/**
 * Broker detail — /brokers/[id]  (real-data v1)
 *
 * Full contact block + server-computed next-action chip + merged activity
 * timeline, all from the typed `lib/api/brokers` client. The seeded
 * email-analytics card is deliberately kept as a dashed "Sample data" strip
 * at the bottom until the inbox connector lands real reply-rate data.
 *
 * Honest-by-design: a missing field renders as "—" with no source badge.
 * Never fake anything.
 */

import * as React from "react"
import Link from "next/link"
import { useParams } from "next/navigation"
import { toast } from "sonner"
import {
  ArrowLeft,
  Mail,
  Phone,
  MapPin,
  ExternalLink,
} from "lucide-react"
import { Panel } from "@/components/app/ui"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { InboxCards } from "./inbox-cards"
import {
  getBroker,
  getBrokerActivity,
  type BrokerDetail,
  type ActivityEvent,
  type ContactField,
  type NamedContact,
  type NextActionKind,
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

export default function BrokerDetailPage() {
  const params = useParams<{ id: string }>()
  const id = params.id

  const [data, setData] = React.useState<BrokerDetail | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [notFoundFlag, setNotFoundFlag] = React.useState(false)
  const [moreCursor, setMoreCursor] = React.useState<string | null>(null)
  const [moreLoading, setMoreLoading] = React.useState(false)
  const [extraActivity, setExtraActivity] = React.useState<ActivityEvent[]>([])

  React.useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      try {
        const d = await getBroker(id)
        if (!cancelled) {
          setData(d)
          // Prime the "load more" cursor using the oldest timestamp we already have.
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

  return (
    <>
      <Link
        href="/brokers"
        className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" /> All brokers
      </Link>
      <InboxCards email={b.primary_email?.value ?? null} />

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
        <div className="space-y-5">
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

        <div className="space-y-4">
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
        </div>
      </div>
    </>
  )
}
