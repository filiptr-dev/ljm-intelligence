"use client"

import { useState, useTransition } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import * as inbox from "@/lib/api/inbox"

/**
 * New-email composer — the demo surface. Uses the real backend
 * `/inbox/compose` endpoint; the owner switch controls whether the message
 * leaves the building (default OFF → simulated-sender records a receipt).
 */
export function ComposeForm({ initialTo }: { initialTo: string }) {
  const router = useRouter()
  const [to, setTo] = useState(initialTo)
  const [subject, setSubject] = useState("")
  const [body, setBody] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<{ threadId: string; mode: string } | null>(null)
  const [pending, startTransition] = useTransition()

  function onSend() {
    setError(null)
    setDone(null)
    startTransition(async () => {
      try {
        const res = await inbox.compose({ to, subject, body_text: body })
        if (!res.ok) {
          setError("Send failed")
          return
        }
        setDone({ threadId: res.thread_id ?? "", mode: res.mode ?? "simulated" })
        router.refresh()
      } catch (e) {
        setError(e instanceof Error ? e.message : "Send failed")
      }
    })
  }

  return (
    <div className="rounded-sm border border-border bg-card p-3">
      <Input placeholder="To (email)" value={to} onChange={(e) => setTo(e.target.value)} className="mb-2" />
      <Input placeholder="Subject" value={subject} onChange={(e) => setSubject(e.target.value)} className="mb-2" />
      <textarea
        placeholder="Write your email…"
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={10}
        className="w-full rounded-sm border border-border bg-background p-2 font-mono text-sm"
      />
      <div className="mt-2 flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">
          Owner switch controls whether this leaves the building — default is simulated.
        </span>
        <Button onClick={onSend} disabled={pending || to.trim().length < 3 || subject.trim().length === 0 || body.trim().length === 0}>
          {pending ? "Sending…" : "Send email"}
        </Button>
      </div>
      {error ? <div className="mt-2 text-sm text-bad">{error}</div> : null}
      {done ? (
        <div className="mt-2 text-sm text-good">
          Sent via <span className="font-mono">{done.mode}</span> adapter. Thread id <span className="font-mono">{done.threadId}</span>.
        </div>
      ) : null}
    </div>
  )
}
