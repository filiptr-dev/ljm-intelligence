"use client"

import { useState, useTransition } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import * as inbox from "@/lib/api/inbox"

/**
 * Inline reply composer — Gmail-style, no redirect. Shares the backend's
 * `inbox.ai_draft_reply()` shape: on mount the parent SC has already pre-
 * filled `initialSubject` + `initialBody` from the shared email builder.
 * Clicking "Send" calls `/inbox/threads/{id}/reply` and routes back to the
 * emails list on success.
 */
export function ThreadReply({
  threadId,
  initialSubject,
  initialBody,
}: {
  threadId: string
  initialSubject: string
  initialBody: string
}) {
  const router = useRouter()
  const [subject, setSubject] = useState(initialSubject)
  const [body, setBody] = useState(initialBody)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<{ mode: string } | null>(null)
  const [pending, startTransition] = useTransition()

  function onSend() {
    setError(null)
    setDone(null)
    startTransition(async () => {
      try {
        const res = await inbox.sendReply(threadId, { body_text: body })
        if (!res.ok) {
          setError(res.reason ?? "Send failed")
          return
        }
        setDone({ mode: res.mode ?? "simulated" })
        router.refresh()
      } catch (e) {
        setError(e instanceof Error ? e.message : "Send failed")
      }
    })
  }

  return (
    <div className="rounded-sm border border-border bg-card p-3">
      <div className="mb-2 text-sm font-semibold">Reply inline</div>
      <Input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Subject" className="mb-2" />
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={8}
        className="w-full rounded-sm border border-border bg-background p-2 font-mono text-sm"
      />
      <div className="mt-2 flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">
          Draft pre-filled by AI. Owner switch controls whether this leaves the building.
        </span>
        <Button onClick={onSend} disabled={pending || body.trim().length === 0}>
          {pending ? "Sending…" : "Send reply"}
        </Button>
      </div>
      {error ? <div className="mt-2 text-sm text-bad">{error}</div> : null}
      {done ? (
        <div className="mt-2 text-sm text-good">
          Sent via <span className="font-mono">{done.mode}</span> adapter.
        </div>
      ) : null}
    </div>
  )
}
