import { Suspense } from "react"
import Form from "next/form"
import Link from "next/link"
import { AlertCircle, Search, Sparkles } from "lucide-react"
import { INTENT_META, IntentBadge, SentimentDot } from "@/components/app/intent"
import { MailboxSelector } from "@/components/app/mailbox-selector"
import { BarList, PageHeader, Panel, StackBar } from "@/components/app/ui"
import { buttonVariants } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { AskButton } from "./ask-button"
import type { Intent } from "@/lib/ai/types"
import * as inbox from "@/lib/api/inbox"
import type { AskOut, ChipFilter } from "@/lib/api/inbox"
import { dateTime, money, pct } from "@/lib/format"
import { cn } from "@/lib/utils"

// Suggested-question chips are no longer hardcoded — the backend mines
// 0-6 concrete prompts (named brokers / lanes / dates / counts) from the
// real mailbox and we render whatever it returns. Empty inbox → no strip.

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
  const mailbox: string[] | undefined = ((): string[] | undefined => {
    const raw = sp.mailbox
    const arr = raw === undefined ? [] : Array.isArray(raw) ? raw : [raw]
    const flat: string[] = []
    for (const v of arr) {
      for (const part of String(v).split(",")) {
        const t = part.trim()
        if (t && !flat.includes(t)) flat.push(t)
      }
    }
    return flat.length ? flat : undefined
  })()
  const page = Math.max(1, Number(sp.page) || 1)
  const mailboxes = await inbox.listMailboxes().catch(() => [])
  const suggestions = await inbox.suggestQuestions({ mailbox }).catch(() => [])
  // A chip click carries its structured filter in the URL as `chip=<json>`
  // (base64url-encoded). We replay it on `/inbox/ask` so the AI answers over
  // the SAME rows the chip counted — otherwise the AI contradicts us
  // ("the emails don't contain 6 complaints") when its keyword retrieval
  // misses the chip's SQL recipe.
  let chipFromUrl: ChipFilter | null = null
  if (typeof sp.chip === "string" && sp.chip) {
    try {
      const json = Buffer.from(sp.chip, "base64url").toString("utf8")
      const parsed = JSON.parse(json) as unknown
      if (parsed && typeof parsed === "object") chipFromUrl = parsed as ChipFilter
    } catch {
      // Malformed chip = ignore; the question text still flows through as a
      // free-text ask.
    }
  }

  // The AI answer used to be awaited inline — 12-25s with no feedback.
  // We now stream it via a Suspense boundary so the page shell + the row
  // list render immediately. For row filtering we use whatever the chip
  // encoded (intent / keyword) so the list still narrows the moment the
  // chip is clicked; the Gemini-interpreted intent used to overlay this
  // and now lands later, in-place of "Answering…".
  const effectiveIntent =
    intent || (typeof chipFromUrl?.intent === "string" ? chipFromUrl.intent : undefined)
  const effectiveQ: string | undefined = q || undefined

  const data = await inbox.listEmails({ intent: effectiveIntent, q: effectiveQ, page, page_size: PAGE, mailbox })
  const needsReplyRows = await inbox.needsReply({ mailbox, limit: 10 }).catch(() => [])
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
    if (mailbox) for (const m of mailbox) u.append("mailbox", m)
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

      <MailboxSelector options={mailboxes} />

      <Form action="/emails" className="mb-3 flex flex-col gap-2 rounded-sm border-2 border-asphalt bg-card p-3 sm:flex-row sm:items-center">
        {mailbox ? mailbox.map((m) => <input key={m} type="hidden" name="mailbox" value={m} />) : null}
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
        {suggestions.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {suggestions.map((s) => {
              // Carry the chip's structured filter through the URL so the
              // Ask layer retrieves the SAME rows the chip counted. Encoded
              // as base64url JSON — short, URL-safe, no quoting pain.
              const chipParam = s.chip_filter
                ? `&chip=${encodeURIComponent(
                    Buffer.from(JSON.stringify(s.chip_filter), "utf8").toString("base64url"),
                  )}`
                : ""
              return (
                <Link
                  key={s.text}
                  href={`/emails?q=${encodeURIComponent(s.text)}${mailbox ? mailbox.map((m) => `&mailbox=${encodeURIComponent(m)}`).join("") : ""}${chipParam}`}
                  className="rounded-sm bg-muted px-2 py-1 text-xs hover:bg-secondary"
                  title={s.kind}
                >
                  {s.text}
                </Link>
              )
            })}
          </div>
        ) : null}
        <AskButton />
      </Form>

      <Panel
        title="Needs reply"
        description="Threads where a broker is waiting on you right now. Routine-low + auto-replies are hidden."
      >
        {needsReplyRows.length === 0 ? (
          <p className="text-sm text-muted-foreground">No threads waiting on a reply.</p>
        ) : (
          <ul className="divide-y divide-border text-sm">
            {needsReplyRows.map((r) => {
              const mapped = mapIntent(r.intent)
              return (
                <li key={`${r.mailbox}:${r.thread_id}`} className="py-2">
                  <Link
                    href={`/emails/${encodeURIComponent(r.thread_id)}`}
                    className="block hover:underline"
                  >
                    <div className="flex items-center gap-2">
                      <span className="font-semibold">{r.broker_name || r.from_addr}</span>
                      <IntentBadge intent={mapped} />
                      <span className="ml-auto text-xs text-muted-foreground">{dateTime(r.sent_at as unknown as string)}</span>
                    </div>
                    <div className="truncate text-xs text-muted-foreground">{r.subject || "(no subject)"}</div>
                  </Link>
                </li>
              )
            })}
          </ul>
        )}
      </Panel>

      {q ? (
        <Suspense
          key={`${q}:${(mailbox ?? []).join(",")}:${sp.chip ?? ""}`}
          fallback={
            <div className="mb-5 flex items-center gap-2 rounded-sm border border-chart-2/40 bg-chart-2/5 p-4 text-sm">
              <Sparkles className="size-4 animate-pulse text-chart-2" />
              <span className="font-semibold text-chart-2">Answering…</span>
              <span className="text-muted-foreground">
                reading the inbox for &ldquo;{q}&rdquo; — this takes 10-25 seconds.
              </span>
            </div>
          }
        >
          <AskAnswer q={q} mailbox={mailbox} chip={chipFromUrl} />
        </Suspense>
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
            <Link href={href({ intent: undefined, page: undefined })} className={cn("rounded-sm px-2.5 py-1 text-sm", !intent ? "bg-asphalt text-white" : "hover:bg-muted")}>All</Link>
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
                    <span
                      title={r.mailbox}
                      className="ml-2 inline-block rounded-sm bg-muted px-1.5 py-0.5 font-mono text-[0.6rem] uppercase tracking-wide text-muted-foreground"
                    >
                      {(r.mailbox || "").split("@")[0] || r.mailbox}
                    </span>
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

/**
 * AI answer block — streamed in via a Suspense boundary so the row list
 * and the page shell render immediately. Earlier version awaited this
 * at the top of the page, which stalled the whole route for 10-25s on a
 * cold Gemini call. The chip's own `intent` filter already narrows the
 * list below, so dropping the gate doesn't lose the chip semantics.
 */
async function AskAnswer({
  q, mailbox, chip,
}: {
  q: string
  mailbox: string[] | undefined
  chip: ChipFilter | null
}) {
  let ask: AskOut | null = null
  try {
    ask = await inbox.ask(q, { mailbox, chip })
  } catch {
    ask = {
      intent: null, keywords: [], sentiment: null, summary: "",
      answer: "The server couldn't reach the AI service. Please try again.",
      citations: [], ok: false, error: "ai_error",
    }
  }
  if (!ask) return null
  return (
    <div
      className={cn(
        "mb-5 rounded-sm border p-4",
        ask.ok
          ? "border-chart-2/40 bg-chart-2/5"
          : "border-destructive/40 bg-destructive/5",
      )}
    >
      <div className="mb-1 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide">
        {ask.ok ? (
          <>
            <Sparkles className="size-3.5 text-chart-2" />
            <span className="text-chart-2">AI answer</span>
          </>
        ) : (
          <>
            <AlertCircle className="size-3.5 text-destructive" />
            <span className="text-destructive">
              No AI answer{ask.error ? ` · ${ask.error.replace(/_/g, " ")}` : ""}
            </span>
          </>
        )}
      </div>
      <p className="text-sm leading-relaxed text-foreground">{ask.answer}</p>
      {ask.ok && ask.summary ? (
        <p className="mt-2 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">Interpreted as:</span> {ask.summary}
        </p>
      ) : null}
      {ask.ok && ask.citations.length > 0 ? (
        <ul className="mt-3 space-y-1.5 border-t border-border pt-3">
          {ask.citations.map((c) => (
            <li key={c.thread_id} className="text-xs">
              <Link
                href={`/emails/${encodeURIComponent(c.thread_id)}`}
                className="font-medium hover:underline"
              >
                {c.subject || "(no subject)"}
              </Link>
              <span className="ml-1 text-muted-foreground">
                — {c.from_addr}
                {c.sent_at ? ` · ${c.sent_at.slice(0, 10)}` : ""}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
