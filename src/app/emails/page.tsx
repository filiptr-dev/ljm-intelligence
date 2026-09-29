import Link from "next/link"
import { Search, Sparkles } from "lucide-react"
import { INTENT_META, IntentBadge, SentimentDot } from "@/components/app/intent"
import { BarList, PageHeader, Panel, RegionTag, StackBar } from "@/components/app/ui"
import { buttonVariants } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import type { EmailInsight, Intent, RejectionReason } from "@/lib/ai/types"
import { REASON_LABELS } from "@/lib/analytics"
import { getStore } from "@/lib/data/store"
import { dateTime, money, pct } from "@/lib/format"
import { cn } from "@/lib/utils"

const PAGE = 40

/** Turns a plain-English question into structured filters (Gemini does this in production). */
function interpret(q: string): { intent?: Intent; reason?: RejectionReason; text?: string; note: string } {
  const t = q.toLowerCase()
  if (/late|complain|unhappy|not happy|issue/.test(t)) return { intent: "complaint", note: "service complaints" }
  if (/pay|invoice|overdue|money owed/.test(t)) return { intent: "payment_issue", note: "overdue payment conversations" }
  if (/price|expensive|too high|rate/.test(t) && /reject|lost|no|why/.test(t)) return { intent: "rejected", reason: "rate_too_high", note: "rejections because of price" }
  if (/other carrier|competit|another carrier/.test(t)) return { intent: "rejected", reason: "other_carrier", note: "loads lost to other carriers" }
  if (/reject|lost|decline|said no/.test(t)) return { intent: "rejected", note: "all broker rejections" }
  if (/praise|thank|happy|good job|great/.test(t)) return { intent: "praise", note: "positive feedback" }
  if (/book|won|confirmed/.test(t)) return { intent: "booked", note: "booked loads" }
  if (/offer|load available|need a truck/.test(t)) return { intent: "load_offer", note: "incoming load offers" }
  return { text: q, note: `emails mentioning “${q}”` }
}

export default async function EmailsPage({ searchParams }: PageProps<"/emails">) {
  const sp = await searchParams
  const s = await getStore()
  const q = typeof sp.q === "string" ? sp.q.trim() : ""
  const intentParam = typeof sp.intent === "string" ? (sp.intent as Intent) : undefined
  const page = Math.max(1, Number(sp.page) || 1)

  const ai = q ? interpret(q) : undefined
  const intent = ai?.intent ?? intentParam
  const matches = s.emails
    .map((e) => ({ e, i: s.insightMap.get(e.id)! as EmailInsight }))
    .filter(({ i }) => !intent || i.intent === intent)
    .filter(({ i }) => !ai?.reason || i.rejectionReason === ai.reason)
    .filter(({ e }) => !ai?.text || `${e.subject} ${e.body}`.toLowerCase().includes(ai.text.toLowerCase()))
    .reverse()
  const rows = matches.slice((page - 1) * PAGE, page * PAGE)
  const pages = Math.ceil(matches.length / PAGE)

  const counts = new Map<Intent, number>()
  s.insights.forEach((i) => counts.set(i.intent, (counts.get(i.intent) ?? 0) + 1))
  const inboundIds = new Set(s.emails.filter((e) => e.direction === "in").map((e) => e.id))
  const inbound = s.insights.filter((i) => inboundIds.has(i.emailId))
  const pos = inbound.filter((i) => i.sentiment > 0.2).length
  const neg = inbound.filter((i) => i.sentiment < -0.1).length

  const href = (p: Record<string, string | number | undefined>) => {
    const u = new URLSearchParams()
    Object.entries({ intent: intentParam, q: q || undefined, ...p }).forEach(([k, v]) => v !== undefined && v !== "" && u.set(k, String(v)))
    return `/emails?${u}`
  }

  return (
    <>
      <PageHeader
        eyebrow="Email analysis"
        title="Your inbox, structured"
        description={`${s.kpis.emails.toLocaleString("en-US")} emails read by the AI. Each one gets an intent, a sentiment score and extracted rates, lanes and reasons.`}
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
        <div className="flex flex-wrap gap-1.5">
          {["Why do we lose loads on price?", "Overdue invoices", "Late delivery complaints"].map((ex) => (
            <Link key={ex} href={`/emails?q=${encodeURIComponent(ex)}`} className="rounded-sm bg-muted px-2 py-1 text-xs hover:bg-secondary">{ex}</Link>
          ))}
        </div>
        <button className={cn(buttonVariants(), "font-semibold")}>Ask</button>
      </form>

      <div className="mb-5 grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Panel title="Emails by intent" description="What each email is about">
          <BarList
            rows={[...counts.entries()].sort((a, b) => b[1] - a[1]).map(([k, v]) => ({
              label: <Link href={href({ intent: k, q: undefined, page: undefined })} className="hover:underline">{INTENT_META[k].label}</Link>,
              value: v,
            }))}
          />
        </Panel>
        <Panel title="Tone of broker emails" description={`${inbound.length.toLocaleString("en-US")} incoming emails, sentiment -1 to +1`}>
          <StackBar
            parts={[
              { label: "Positive", value: pos, color: "var(--good)" },
              { label: "Neutral", value: inbound.length - pos - neg, color: "var(--steel)" },
              { label: "Negative", value: neg, color: "var(--bad)" },
            ]}
          />
          <p className="mt-4 border-t border-border pt-3 text-sm text-muted-foreground">
            <span className="font-semibold text-foreground">AI read: </span>
            negative emails are mostly price rejections and overdue invoices. Complaints about service are rare ({counts.get("complaint") ?? 0} in 18 months), so drivers are not the problem. Price and speed are.
          </p>
        </Panel>
      </div>

      <div className="rounded-sm border border-border bg-card">
        <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
          {ai ? (
            <div className="flex items-center gap-2 text-sm">
              <Sparkles className="size-4 text-chart-2" />
              AI interpreted your question as <b>{ai.note}</b>
              <span className="text-muted-foreground">· {matches.length} emails</span>
              <Link href="/emails" className="text-chart-1 hover:underline">Clear</Link>
            </div>
          ) : (
            <div className="flex flex-wrap gap-1">
              <Link href="/emails" className={cn("rounded-sm px-2.5 py-1 text-sm", !intent ? "bg-asphalt text-white" : "hover:bg-muted")}>All</Link>
              {(Object.keys(INTENT_META) as Intent[]).filter((k) => counts.get(k)).map((k) => (
                <Link key={k} href={href({ intent: k, page: undefined })} className={cn("rounded-sm px-2.5 py-1 text-sm", intent === k ? "bg-asphalt text-white" : "hover:bg-muted")}>
                  {INTENT_META[k].label}
                </Link>
              ))}
            </div>
          )}
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
            {rows.map(({ e, i }) => {
              const b = s.brokerMap.get(e.brokerId)!
              return (
                <TableRow key={e.id}>
                  <TableCell className="pl-4 font-mono text-xs text-muted-foreground">{dateTime(e.sentAt)}</TableCell>
                  <TableCell className="max-w-[220px]">
                    <Link href={`/brokers/${b.id}`} className="flex items-center gap-2 hover:underline">
                      <RegionTag region={b.region} />
                      <span className="truncate">{b.name}</span>
                    </Link>
                  </TableCell>
                  <TableCell className="max-w-[360px]">
                    <div className="truncate text-sm">
                      <span className={cn("mr-2 font-mono text-[0.65rem] font-semibold", e.direction === "in" ? "text-chart-1" : "text-muted-foreground")}>{e.direction === "in" ? "IN" : "OUT"}</span>
                      {e.subject}
                    </div>
                    {i.evidence ? <div className="truncate text-xs text-muted-foreground">“{i.evidence}”</div> : null}
                  </TableCell>
                  <TableCell><IntentBadge intent={i.intent} /></TableCell>
                  <TableCell className="text-xs">
                    {i.rejectionReason ? REASON_LABELS[i.rejectionReason] : i.rate ? <span className="font-mono">{money(i.rate, b.region)}{i.distance ? ` · ${i.distance} ${b.region === "US" ? "mi" : "km"}` : ""}</span> : <span className="text-muted-foreground">–</span>}
                  </TableCell>
                  <TableCell className="pr-4"><SentimentDot value={i.sentiment} /></TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
        <div className="flex items-center justify-between border-t border-border p-3 text-sm">
          <span className="text-muted-foreground">
            {matches.length ? `${(page - 1) * PAGE + 1}–${Math.min(page * PAGE, matches.length)} of ${matches.length}` : "No emails"} · avg confidence {pct(rows.reduce((a, r) => a + r.i.confidence, 0) / (rows.length || 1))}
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
