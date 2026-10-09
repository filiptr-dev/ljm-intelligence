"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { toast } from "sonner"
import { CalendarClock, Send, Wand2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import type { OutreachTone } from "@/lib/ai/types"
import { streamInto, type WriteStyle } from "@/components/app/ai-writer"
import {
  DesignFields,
  MessageFields,
  PreviewPanel,
  Step,
} from "@/components/app/email-builder-parts"
import { DEFAULT_DESIGN, type EmailDesign } from "@/components/app/engine"
import type { Recipient } from "@/lib/campaigns/types"
import { Segmented } from "@/components/app/segmented"
import { rewrite as aiRewrite } from "@/lib/api/inbox"
import { replySendAdapter } from "@/lib/api/email-send"

/**
 * Rich inline thread reply — Steps 3 (Message) + 4 (Design) + 5 (Sending)
 * from the builder, prefilled from the server-side AI draft. No `To`/`What`
 * steps (those are implicit from the thread). The look is pre-`0333732`
 * 1:1 (`MessageFields` + `DesignFields` + `PreviewPanel` reused verbatim)
 * — only the data source + send path are new.
 *
 * Rewrite streams into the body via the shared `streamInto` helper, same
 * UX as `/outreach`.
 */
export function ThreadReply({
  threadId,
  initialSubject,
  initialBody,
  recipientEmail,
}: {
  threadId: string
  initialSubject: string
  initialBody: string
  recipientEmail: string
}) {
  const router = useRouter()
  const [subject, setSubject] = React.useState(initialSubject)
  const [body, setBody] = React.useState(initialBody)
  const [tone, setTone] = React.useState<OutreachTone>("professional")
  const [custom, setCustom] = React.useState(false)
  const [brief, setBrief] = React.useState("")
  const [understood] = React.useState<string[]>([])
  const [writing, setWriting] = React.useState(false)
  const [design, setDesign] = React.useState<EmailDesign>(DEFAULT_DESIGN)
  const [previewIdx, setPreviewIdx] = React.useState(0)
  const [mobile, setMobile] = React.useState(false)
  const [when, setWhen] = React.useState<"now" | "tomorrow" | "scheduled">("now")
  const [scheduleAt, setScheduleAt] = React.useState("")
  const [pending, setPending] = React.useState(false)

  const style: WriteStyle = custom ? "custom" : tone
  const setStyle = (v: WriteStyle) => {
    setCustom(v === "custom")
    if (v !== "custom") setTone(v)
  }

  const recipient: Recipient = React.useMemo(
    () => ({
      id: `addr:${recipientEmail}`,
      name: recipientEmail.split("@", 2)[1] ?? recipientEmail,
      contactName: recipientEmail.split("@", 1)[0] ?? recipientEmail,
      email: recipientEmail,
      region: "US",
      kind: "broker",
    }),
    [recipientEmail],
  )

  const write = React.useCallback(async () => {
    setWriting(true)
    try {
      const r = await aiRewrite({ body_text: body || "Hi,", tone, brief: custom ? brief : undefined })
      await streamInto(r.body_text, setBody)
    } finally {
      setWriting(false)
    }
  }, [body, tone, custom, brief])

  const canSend = !!body.trim() && !pending && !(when === "scheduled" && !scheduleAt)

  const send = async () => {
    if (!canSend) return
    setPending(true)
    try {
      const adapter = replySendAdapter(threadId)
      if (adapter.kind !== "reply") return
      const res = await adapter.send({ body_text: body, design, tone })
      if (!res.ok) {
        toast.error(res.reason ?? "Send failed")
        return
      }
      toast.success("Reply sent", { description: `Sent via ${res.mode ?? "simulated"} adapter.` })
      router.refresh()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Send failed")
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,520px)]">
      <div className="min-w-0 space-y-5">
        <Step
          n={3}
          title="Message"
          action={
            <Button variant="outline" size="sm" onClick={write} disabled={writing}>
              <Wand2 className={writing ? "animate-pulse" : undefined} /> {writing ? "Writing…" : "Rewrite with AI"}
            </Button>
          }
        >
          <div className="mb-3 space-y-1.5">
            <Label>Subject</Label>
            <Input value={subject} onChange={(e) => setSubject(e.target.value)} />
          </div>
          <MessageFields
            typeSlot={
              <div className="space-y-1.5">
                <Label>Thread</Label>
                <div className="flex h-9 items-center rounded-sm border border-border bg-background px-3 text-sm text-muted-foreground">
                  Reply to {recipientEmail}
                </div>
              </div>
            }
            style={style}
            setStyle={setStyle}
            custom={custom}
            brief={brief}
            setBrief={setBrief}
            onWrite={write}
            writing={writing}
            understood={understood}
            subject={subject}
            setSubject={setSubject}
            body={body}
            setBody={setBody}
            preview={recipient}
            fieldsHint={`${recipient.name}'s own details, so it reads as a personal email`}
            bodyHint="Personal fields are filled in when the email is sent."
          />
        </Step>

        <Step n={4} title="Design">
          <DesignFields design={design} setDesign={setDesign} />
        </Step>

        <Step n={5} title="Sending">
          <div className="space-y-1.5">
            <Label>When</Label>
            <Segmented
              value={when}
              onChange={(v) => setWhen(v)}
              className="flex w-full [&>button]:flex-1"
              options={[{ value: "now", label: "Now" }, { value: "tomorrow", label: "Tomorrow 9:00" }, { value: "scheduled", label: "Pick date" }]}
            />
            {when === "scheduled" ? (
              <Input type="datetime-local" value={scheduleAt} onChange={(e) => setScheduleAt(e.target.value)} />
            ) : null}
            {when !== "now" ? (
              <p className="text-xs text-muted-foreground">
                Scheduled sends run through the simulated adapter until the queue backend lands.
              </p>
            ) : null}
          </div>
        </Step>
      </div>

      <div className="min-w-0 space-y-4 xl:sticky xl:top-20 xl:self-start">
        <div className="rounded-sm border-2 border-asphalt bg-card p-4">
          <div className="flex flex-wrap items-center gap-3">
            <div className="min-w-0 flex-1 text-sm">
              <div className="truncate font-semibold">To {recipient.contactName}</div>
              <div className="text-xs text-muted-foreground">{recipientEmail} · {when === "now" ? "sends now" : "scheduled"}</div>
            </div>
            <Button size="lg" className="font-semibold" disabled={!canSend} onClick={send}>
              {when === "now" ? <Send /> : <CalendarClock />} {pending ? "Sending…" : when === "now" ? "Send reply" : "Schedule reply"}
            </Button>
          </div>
        </div>
        <PreviewPanel
          subject={subject}
          body={body}
          design={design}
          recipients={[recipient]}
          previewIdx={previewIdx}
          setPreviewIdx={setPreviewIdx}
          mobile={mobile}
          setMobile={setMobile}
        />
      </div>
    </div>
  )
}
