import Link from "next/link"
import { notFound } from "next/navigation"
import { PageHeader, Panel } from "@/components/app/ui"
import { IntentBadge, SentimentDot } from "@/components/app/intent"
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table"
import type { Intent } from "@/lib/ai/types"
import * as inbox from "@/lib/api/inbox"
import { dateTime } from "@/lib/format"
import { ApiRequestError } from "@/lib/api/client"
import { ThreadReply } from "./thread-reply"

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

/**
 * Thread view — inline reply, AI draft pre-filled on open (plan Step 4).
 *
 * One `/inbox/threads/{id}` fetch + one `/inbox/threads/{id}/ai-draft` fetch
 * (both server-side, cookie-authed). The composer client island receives
 * the draft shape and calls `/inbox/threads/{id}/reply`.
 */
export default async function ThreadPage({ params }: { params: Promise<{ threadId: string }> }) {
  const { threadId: raw } = await params
  const threadId = decodeURIComponent(raw)
  let thread: inbox.ThreadOut
  try {
    thread = await inbox.getThread(threadId)
  } catch (e) {
    if (e instanceof ApiRequestError && e.status === 404) notFound()
    throw e
  }
  const draft = await inbox.getAiDraft(threadId).catch(() => null)
  const subjectForReply = draft?.subject ?? (thread.subject.toLowerCase().startsWith("re:") ? thread.subject : `Re: ${thread.subject}`)
  const bodyForReply = draft?.body_text ?? ""

  return (
    <>
      <PageHeader
        eyebrow="Email thread"
        title={thread.subject || "(no subject)"}
        description={`${thread.messages.length} message${thread.messages.length === 1 ? "" : "s"} in this conversation.`}
      />
      <Link href="/emails" className="mb-3 inline-block text-sm text-muted-foreground hover:underline">← Back to inbox</Link>

      <Panel title="Conversation" description="Newest at the bottom.">
        <Table>
          <TableBody>
            {thread.messages.map((m) => {
              const mapped = INTENT_MAP[m.intent] ?? "other"
              return (
                <TableRow key={`${m.mailbox}:${m.message_id}`} className={m.direction === "in" ? "bg-background" : "bg-muted/30"}>
                  <TableCell className="w-32 align-top font-mono text-xs text-muted-foreground">
                    {dateTime(m.sent_at as unknown as string)}
                  </TableCell>
                  <TableCell className="w-48 align-top">
                    <div className="font-semibold text-sm">{m.direction === "in" ? m.from_addr : "LJM → " + (m.to_addr || "")}</div>
                    <div className="mt-1 flex items-center gap-2">
                      <IntentBadge intent={mapped} />
                      <SentimentDot value={m.sentiment} />
                    </div>
                    {m.broker_name ? <div className="mt-1 text-xs text-muted-foreground">{m.broker_name}</div> : null}
                  </TableCell>
                  <TableCell className="align-top">
                    <div className="font-semibold text-sm">{m.subject}</div>
                    <pre className="mt-1 max-h-80 overflow-auto whitespace-pre-wrap break-words font-sans text-sm text-muted-foreground">
                      {m.body_text}
                    </pre>
                    {m.lane_from && m.lane_to ? (
                      <div className="mt-2 text-xs text-muted-foreground">
                        Lane: <span className="font-mono">{m.lane_from} → {m.lane_to}</span>
                        {m.rate_usd ? <> · Rate <span className="font-mono">${m.rate_usd.toLocaleString()}</span></> : null}
                      </div>
                    ) : null}
                    {m.evidence ? <div className="mt-2 text-xs italic text-muted-foreground">AI pulled: “{m.evidence}”</div> : null}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </Panel>

      <div className="mt-5">
        <ThreadReply threadId={threadId} initialSubject={subjectForReply} initialBody={bodyForReply} />
      </div>
    </>
  )
}
