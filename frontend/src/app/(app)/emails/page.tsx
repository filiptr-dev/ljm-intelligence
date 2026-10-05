import Link from "next/link"
import { Search, Sparkles } from "lucide-react"
import { INTENT_META, IntentBadge, SentimentDot } from "@/components/app/intent"
import { BarList, PageHeader, Panel, StackBar } from "@/components/app/ui"
import { buttonVariants } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import type { Intent } from "@/lib/ai/types"
import * as inbox from "@/lib/api/inbox"
import { dateTime, money, pct } from "@/lib/format"
import { cn } from "@/lib/utils"

/**
 * Emails — real inbox view, backed by `/inbox/emails`.
 *
 * The demo UI is preserved as-is (per plan-gate decision): "Tone of broker
 * emails" StackBar, intent BarList, per-row SentimentDot, pagination, AI
 * query interpreter — only the data source swapped from `getStore()` to the
 * typed `inbox.listEmails()` call.
 */

const PAGE = 40

// Backend uses finer-grained intent labels; the demo UI's INTENT_META keeps
// the shorter palette. One map, called once per row.
const INTENT_MAP: Record<string, Intent> = {
  load_offer: "load_offer",
  rate_request: "quote",
  booked: "booked",
  complaint: "complaint",
  payment: "payment_issue",
  praise: "praise",
  urgent_truck: "other",
  detention: "other",
  routine: "other",
}

function mapIntent(x: string | null | undefined): Intent {
  return INTENT_MAP[(x ?? "").toLowerCase()] ?? "other"
}

export default async function EmailsPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams
  const q = typeof sp.q === "string" ? sp.q.trim() : ""
  const intent = typeof sp.intent === "string" ? sp.intent : undefined
  const page = Math.max(1, Number(sp.page) || 1)

  // When the operator submits a question, hand it to the AI first so we
  // can map natural language ("brokers who complained about late delivery")
  // onto the structured filters the list query already understands.
  // Identity fallback: if the AI is unavailable the ask() call returns
  // all-nulls and the original substring behaviour is preserved.
  let aiSummary = ""
  let effectiveIntent = intent
  let effectiveQ: string | undefined = q || undefined
  if (q && !intent) {
    const ask = await inbox.ask(q)
    aiSummary = ask.summary
    if (ask.intent) effectiveIntent = ask.intent
    if (ask.keywords.length > 0) effectiveQ = ask.keywords[0]
  }

  const data = await inbox.listEmails({ intent: effectiveIntent, q: effectiveQ, page, page_size: PAGE })
  const rows = data.items
  const total = data.total
  const pages = Math.ceil(total / PAGE)
  const counts = new Map(data.intent_counts.map((c) => [c.intent, c.count]))

  const sentiment = data.sentiment
  const pos = sentiment.positive
  const neg = sentiment.negative
  const neu = Math.max(0, sentiment.inbound_total - pos - neg)

  const href = (p: Record<string, string | number | undefined>) => {
    const u = new URLSearchParams()
    Object.entries({ intent, q: q || undefined, ...p }).forEach(([k, v]) => v !== undefined && v !== "" && u.set(k, String(v)))
    return `/emails?${u}`
  }

  // Build the BarList rows against the ACTUAL backend intents (so the sum
  // matches the ground truth we just rendered under the sentiment panel).
  const intentRows = [...counts.entries()]
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => {
      const mapped = mapIntent(k)
      return {
        label: <Link href={href({ intent: k, page: undefined })} className="hover:underline">{INTENT_META[mapped].label}</Link>,
        value: v,
      }
    })

  return (
    <>
      <PageHeader
        eyebrow="Email analysis"
        title="Your inbox, structured"
        description={`${total.toLocaleString("en-US")} emails read by the AI. Each one gets an intent, a sentiment score and extracted rates, lanes and reasons.`}
      />

      <form action="/emails" className="mb-5 flex flex-col gap-2 rounded-sm border-2 border-asphalt bg-card p-3 sm:flex-row sm:items-center">
        <Sparkles className="hidden size-5 shrink-0 text-chart-2 sm:block" />
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground sm:hidden" />
          <Input
            key={q}
            name="q"
            defaultValue={q}
            placeholder="Ask your inbox… e.g. “which brokers complained about late delivery?”"
            className="h-10 border-0 bg-transparent pl-8 text-base shadow-none focus-visible:ring-0 sm:pl-0"
          />
        </div>
        <button className={cn(buttonVariants(), "font-semibold")}>Ask</button>
      </form>

      {aiSummary ? (
        <p className="mb-5 -mt-3 flex items-start gap-2 px-1 text-sm text-muted-foreground">
          <Sparkles className="mt-0.5 size-3.5 shrink-0 text-chart-2" />
          <span><span className="font-medium text-foreground">AI understood:</span> {aiSummary}</span>
        </p>
      ) : null}

      <div className="mb-5 grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Panel title="Emails by intent" description="What each email is about">
          <BarList rows={intentRows} />
        </Panel>
        <Panel title="Tone of broker emails" description={`${sentiment.inbound_total.toLocaleString("en-US")} incoming emails, sentiment -1 to +1`}>
          <StackBar
            parts={[
              { label: "Positive", value: pos, color: "var(--good)" },
              { label: "Neutral", value: neu, color: "var(--steel)" },
              { label: "Negative", value: neg, color: "var(--bad)" },
            ]}
          />
        </Panel>
      </div>

      <div className="rounded-sm border border-border bg-card">
        <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
          <div className="flex flex-wrap gap-1">
            <Link href="/emails" className={cn("rounded-sm px-2.5 py-1 text-sm", !intent ? "bg-asphalt text-white" : "hover:bg-muted")}>All</Link>
            {[...counts.entries()].filter(([, v]) => v > 0).map(([k]) => (
              <Link key={k} href={href({ intent: k, page: undefined })} className={cn("rounded-sm px-2.5 py-1 text-sm", intent === k ? "bg-asphalt text-white" : "hover:bg-muted")}>
                {INTENT_META[mapIntent(k)].label}
              </Link>
            ))}
          </div>
        </div>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="pl-4">Date</TableHead>
              <TableHead>Broker</TableHead>
              <TableHead>Subject</TableHead>
              <TableHead>Intent</TableHead>
              <TableHead>Extracted</TableHead>
              <TableHead className="pr-4">Tone</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => {
              const mapped = mapIntent(r.intent)
              const brokerLabel = r.broker_name ?? ((r.from_addr || "").split("@")[0] || "Unknown")
              return (
                <TableRow key={`${r.mailbox}:${r.message_id}`}>
                  <TableCell className="pl-4 font-mono text-xs text-muted-foreground">{dateTime(r.sent_at as unknown as string)}</TableCell>
                  <TableCell className="max-w-[220px]">
                    <span className="truncate" title={r.from_addr}>{brokerLabel}</span>
                  </TableCell>
                  <TableCell className="max-w-[360px]">
                    <Link href={`/emails/${encodeURIComponent(r.thread_id)}`} className="block hover:underline">
                      <div className="truncate text-sm">
                        <span className={cn("mr-2 font-mono text-[0.65rem] font-semibold", r.direction === "in" ? "text-chart-1" : "text-muted-foreground")}>{r.direction === "in" ? "IN" : "OUT"}</span>
                        {r.subject}
                      </div>
                      {r.evidence ? <div className="truncate text-xs text-muted-foreground">“{r.evidence}”</div> : null}
                    </Link>
                  </TableCell>
                  <TableCell><IntentBadge intent={mapped} /></TableCell>
                  <TableCell className="text-xs">
                    {r.rate_usd ? <span className="font-mono">{money(r.rate_usd, "US")}</span> : <span className="text-muted-foreground">–</span>}
                    {r.lane_from && r.lane_to ? <div className="text-muted-foreground">{r.lane_from} → {r.lane_to}</div> : null}
                  </TableCell>
                  <TableCell className="pr-4"><SentimentDot value={r.sentiment} /></TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
        <div className="flex items-center justify-between border-t border-border p-3 text-sm">
          <span className="text-muted-foreground">
            {total ? `${(page - 1) * PAGE + 1}–${Math.min(page * PAGE, total)} of ${total}` : "No emails"} · avg confidence {pct(rows.reduce((a, r) => a + (r.confidence || 0), 0) / (rows.length || 1))}
          </span>
          <div className="flex gap-2">
            {page > 1 ? <Link href={href({ page: page - 1 })} className={buttonVariants({ variant: "outline", size: "sm" })}>Previous</Link> : null}
            {page < pages ? <Link href={href({ page: page + 1 })} className={buttonVariants({ variant: "outline", size: "sm" })}>Next</Link> : null}
          </div>
        </div>
      </div>
    </>
  )
}
