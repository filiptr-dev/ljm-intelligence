import Link from "next/link"
import { PageHeader, Panel } from "@/components/app/ui"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import * as inbox from "@/lib/api/inbox"
import { dateTime } from "@/lib/format"
import { cn } from "@/lib/utils"

/**
 * Messages → Status board — one row per OPEN conversation. Owner, stage
 * (waiting_on_us / waiting_on_them), next step, counterparty, last
 * direction. Backed by `/inbox/status-board`.
 */
export default async function MessagesPage() {
  const [rows, noReply] = await Promise.all([inbox.statusBoard(500), inbox.noReply(50)])
  const waitingOnUs = rows.filter((r) => r.stage === "waiting_on_us")
  const waitingOnThem = rows.filter((r) => r.stage === "waiting_on_them")

  return (
    <>
      <PageHeader
        eyebrow="Operate"
        title="Status board"
        description="Every open conversation, grouped by who owes a reply. Click a row to open the thread and reply inline."
      />

      <div className="mb-4 flex items-center gap-2">
        <Link href="/emails/compose" className="rounded-sm border border-border bg-card px-3 py-1.5 text-sm hover:bg-muted">+ New email</Link>
        <Link href="/emails" className="rounded-sm px-3 py-1.5 text-sm text-muted-foreground hover:underline">All emails</Link>
      </div>

      <Panel title={`Waiting on us (${waitingOnUs.length})`} description="They've written; we owe a reply.">
        <StatusTable rows={waitingOnUs} />
      </Panel>

      <div className="mt-5">
        <Panel title={`Waiting on them (${waitingOnThem.length})`} description="We've replied; broker owes us the next move.">
          <StatusTable rows={waitingOnThem} />
        </Panel>
      </div>

      {noReply.length > 0 ? (
        <div className="mt-5">
          <Panel title={`No reply yet (${noReply.length})`} description="Threads where we spoke last and silence persists.">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-4">Sent</TableHead>
                  <TableHead>Subject</TableHead>
                  <TableHead>To</TableHead>
                  <TableHead className="pr-4">Suggested</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {noReply.map((r) => (
                  <TableRow key={`${r.mailbox}:${r.thread_id}`}>
                    <TableCell className="pl-4 font-mono text-xs text-muted-foreground">{dateTime(r.we_sent_at as unknown as string)}</TableCell>
                    <TableCell className="max-w-[360px]">
                      <Link className="hover:underline" href={`/emails/${encodeURIComponent(r.thread_id)}`}>{r.subject}</Link>
                      <div className="text-xs text-muted-foreground">{r.days_waiting}d waiting</div>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{r.to_email}</TableCell>
                    <TableCell className="pr-4 text-sm text-muted-foreground">{r.suggested_nudge}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Panel>
        </div>
      ) : null}
    </>
  )
}

function StatusTable({ rows }: { rows: inbox.StatusBoardRow[] }) {
  if (rows.length === 0) {
    return <div className="p-3 text-sm text-muted-foreground">Nothing in this bucket.</div>
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="pl-4">Last</TableHead>
          <TableHead>Thread</TableHead>
          <TableHead>Counterparty</TableHead>
          <TableHead>Mailbox</TableHead>
          <TableHead className="pr-4">Next step</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={`${r.mailbox}:${r.thread_id}`}>
            <TableCell className="pl-4 font-mono text-xs text-muted-foreground">
              {r.last_sent_at ? dateTime(r.last_sent_at as unknown as string) : "—"}
            </TableCell>
            <TableCell className="max-w-[360px]">
              <Link className="block hover:underline" href={`/emails/${encodeURIComponent(r.thread_id)}`}>
                <div className="truncate text-sm">{r.subject || "(no subject)"}</div>
                <div className={cn("text-xs", r.last_direction === "in" ? "text-chart-1" : "text-muted-foreground")}>
                  Last: {r.last_direction === "in" ? "they wrote" : "we wrote"} · {r.message_count} message{r.message_count === 1 ? "" : "s"}
                </div>
              </Link>
            </TableCell>
            <TableCell className="font-mono text-xs">{r.counterparty}</TableCell>
            <TableCell className="font-mono text-xs">{r.mailbox}</TableCell>
            <TableCell className="pr-4 text-sm">{r.next_step}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
