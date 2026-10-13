/**
 * The one-and-only send seam for the rich email builder.
 *
 * Three adapters, one shape — so `SingleEmailBuilder`, the thread-reply
 * surface, and (later) `OutreachBuilder` all talk to the backend through
 * the same injected function. The builder never imports a backend module
 * directly; it only sees an `EmailSendAdapter`.
 *
 * Keeping these out of the component file means a test can swap in a fake
 * adapter without mocking the fetch layer, and `/outreach` can keep its
 * demo-engine path simply by not injecting an adapter.
 */

import type { EmailDesign } from "@/components/app/engine"
import * as inbox from "./inbox"

export type SendResult = { ok: boolean; mode?: string; messageId?: string; threadId?: string; reason?: string }

/** Plain-words explanation for the `reason` codes the send endpoints return with `ok:false`. */
const SEND_FAILURE_TEXT: Record<string, string> = {
  no_footer: "Missing company postal address (OUTREACH_POSTAL_ADDRESS) — required for sending",
  invalid_email: "That recipient email address isn't valid",
  thread_not_found: "This conversation no longer exists",
}

export function describeSendFailure(reason?: string | null): string {
  if (!reason) return "Send failed (no reason given)"
  return SEND_FAILURE_TEXT[reason] ?? reason
}

export type SendSingleIn = {
  to: string
  subject: string
  body_text: string
  design?: EmailDesign
  tone?: string
  purpose?: string
}
export type SendReplyIn = {
  body_text: string
  design?: EmailDesign
  tone?: string
  purpose?: string
}

export type EmailSendAdapter =
  | { kind: "single"; send: (p: SendSingleIn) => Promise<SendResult> }
  | { kind: "reply"; send: (p: SendReplyIn) => Promise<SendResult> }

/** Map the builder's `EmailDesign` (frontend shape) onto the backend's `EmailDesignIn`. */
function toWire(design?: EmailDesign): inbox.EmailDesignWire | undefined {
  if (!design) return undefined
  return {
    accent_hex: design.accent,
    signature: design.signature,
    logo: design.showLogo,
    cta_label: design.ctaLabel ?? "",
    cta_url: design.ctaUrl ?? "",
    layout: design.layout,
    show_truck: design.showTruck,
  }
}

export const singleSendAdapter: EmailSendAdapter = {
  kind: "single",
  async send(p) {
    const res = await inbox.compose({
      to: p.to,
      subject: p.subject,
      body_text: p.body_text,
      design: toWire(p.design),
      tone: p.tone,
      purpose: p.purpose,
    })
    return { ok: res.ok, mode: res.mode ?? undefined, messageId: res.message_id ?? undefined, threadId: res.thread_id ?? undefined,
      reason: res.ok ? undefined : describeSendFailure(res.reason) }
  },
}

export function replySendAdapter(threadId: string): EmailSendAdapter {
  return {
    kind: "reply",
    async send(p) {
      const res = await inbox.sendReply(threadId, {
        body_text: p.body_text,
        design: toWire(p.design),
        tone: p.tone,
        purpose: p.purpose,
      })
      return {
        ok: res.ok,
        mode: res.mode ?? undefined,
        messageId: res.message_id ?? undefined,
        reason: res.ok ? undefined : describeSendFailure(res.reason),
      }
    },
  }
}
