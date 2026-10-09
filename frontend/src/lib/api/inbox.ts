/**
 * Inbox domain — typed wrappers over the openapi-fetch client.
 *
 * One module per domain (bb-unikoop pattern). Shapes come from the
 * generated `schema.d.ts` — regenerate via `pnpm gen:api` whenever the
 * backend changes and let `tsc` catch the callers.
 */

import { api, ApiRequestError } from "./client"
import type { components } from "./schema"

export type EmailListItem = components["schemas"]["EmailListItem"]
export type EmailsPage = components["schemas"]["EmailsPageOut"]
export type TriageRow = components["schemas"]["TriageOut"]
export type NoReplyRow = components["schemas"]["NoReplyOut"]
export type ResponseTimeStats = components["schemas"]["ResponseTimeOut"]
export type StaffRow = components["schemas"]["StaffOut"]
export type OverviewKPIs = components["schemas"]["KPIsOut"]
export type RelationshipHealth = components["schemas"]["RelationshipOut"]
export type ThreadOut = components["schemas"]["ThreadOut"]
export type StatusBoardRow = components["schemas"]["StatusBoardRowOut"]
export type AiDraft = components["schemas"]["AiDraftOutModel"]
export type ReplyOut = components["schemas"]["ReplyOut"]
export type ComposeOut = components["schemas"]["ComposeOut"]
export type ForgetContactOut = components["schemas"]["ForgetContactOut"]

function unwrap<T>(res: { data?: T; error?: unknown; response: Response }, path: string): T {
  if (res.data !== undefined) return res.data
  throw new ApiRequestError(res.response.status, path, res.error)
}

export async function listEmails(
  opts: { intent?: string; q?: string; page?: number; page_size?: number } = {},
  signal?: AbortSignal,
): Promise<EmailsPage> {
  const res = await api.GET("/inbox/emails", { params: { query: opts }, signal })
  return unwrap(res, "/inbox/emails")
}

export async function listTriage(
  opts: { urgent_only?: boolean; limit?: number } = {},
  signal?: AbortSignal,
): Promise<TriageRow[]> {
  const res = await api.GET("/inbox/triage", { params: { query: opts }, signal })
  return unwrap(res, "/inbox/triage")
}

export async function getThread(threadId: string, signal?: AbortSignal): Promise<ThreadOut> {
  const res = await api.GET("/inbox/threads/{thread_id}", {
    params: { path: { thread_id: threadId } },
    signal,
  })
  return unwrap(res, `/inbox/threads/${threadId}`)
}

export async function getAiDraft(threadId: string, signal?: AbortSignal): Promise<AiDraft | null> {
  const res = await api.GET("/inbox/threads/{thread_id}/ai-draft", {
    params: { path: { thread_id: threadId } },
    signal,
  })
  if (res.data === null || res.data === undefined) return null
  return res.data as AiDraft
}

/** Design block the rich builder sends alongside the body. */
export type EmailDesignWire = {
  accent_hex?: string | null
  signature?: boolean
  logo?: boolean
  cta_label?: string
  cta_url?: string
  layout?: string
  show_truck?: boolean
}

export async function sendReply(
  threadId: string,
  body: {
    body_text: string
    body_html?: string
    design?: EmailDesignWire
    tone?: string
    purpose?: string
  },
  signal?: AbortSignal,
): Promise<ReplyOut> {
  const res = await api.POST("/inbox/threads/{thread_id}/reply", {
    params: { path: { thread_id: threadId } },
    // openapi-fetch's generated type for POST bodies evolves after `pnpm
    // gen:api`; we send the extended shape and let the backend ignore
    // unknown fields until the schema is regenerated.
    body: {
      body_text: body.body_text,
      body_html: body.body_html ?? "",
      ...(body.design ? { design: body.design } : {}),
      ...(body.tone ? { tone: body.tone } : {}),
      ...(body.purpose ? { purpose: body.purpose } : {}),
    } as never,
    signal,
  })
  return unwrap(res, `/inbox/threads/${threadId}/reply`)
}

export async function compose(
  body: {
    to: string
    subject: string
    body_text: string
    body_html?: string
    design?: EmailDesignWire
    tone?: string
    purpose?: string
  },
  signal?: AbortSignal,
): Promise<ComposeOut> {
  const res = await api.POST("/inbox/compose", {
    body: {
      to: body.to,
      subject: body.subject,
      body_text: body.body_text,
      body_html: body.body_html ?? "",
      ...(body.design ? { design: body.design } : {}),
      ...(body.tone ? { tone: body.tone } : {}),
      ...(body.purpose ? { purpose: body.purpose } : {}),
    } as never,
    signal,
  })
  return unwrap(res, "/inbox/compose")
}

/** Compose-time AI draft — hits the server-side `/inbox/ai-draft`. */
export async function aiDraftCompose(
  body: {
    to: string
    purpose: string
    tone: string
    brief?: string
    recipient_name?: string
    lane?: string
    equipment?: string
  },
  signal?: AbortSignal,
): Promise<AiDraft> {
  const res = await api.POST("/inbox/ai-draft" as never, {
    body: {
      to: body.to,
      purpose: body.purpose,
      tone: body.tone,
      brief: body.brief ?? "",
      recipient_name: body.recipient_name ?? "",
      lane: body.lane ?? "",
      equipment: body.equipment ?? "",
    } as never,
    signal,
  } as never)
  return unwrap(res as never, "/inbox/ai-draft")
}

/** Rewrite the body in the given tone — hits the server-side `/inbox/rewrite`. */
export async function rewrite(
  body: { body_text: string; tone: string; brief?: string },
  signal?: AbortSignal,
): Promise<AiDraft> {
  const res = await api.POST("/inbox/rewrite" as never, {
    body: { body_text: body.body_text, tone: body.tone, brief: body.brief ?? "" } as never,
    signal,
  } as never)
  return unwrap(res as never, "/inbox/rewrite")
}

/** One cited email the AI answer leans on. */
export type AskCitation = {
  thread_id: string
  subject: string
  snippet: string
  sent_at: string
  from_addr: string
  intent: string | null
  sentiment: number | null
}

/** Shape returned by `POST /inbox/ask` — real AI answer + filter hint. */
export type AskOut = {
  intent: string | null
  keywords: string[]
  sentiment: "positive" | "negative" | null
  summary: string
  answer: string
  citations: AskCitation[]
  ok: boolean
  error:
    | "no_question"
    | "no_data"
    | "ai_unavailable"
    | "ai_timeout"
    | "ai_error"
    | "ai_parse"
    | null
}

/**
 * Ask a natural-language question against the real inbox. The server
 * runs Gemini over a bounded slice of recent mail and returns a plain
 * answer with citations plus the structured filter hint the /emails
 * list uses to narrow down. Never 500s — ``ok=false`` + ``error``
 * tells the UI exactly why we don't have a real answer.
 */
export async function ask(question: string, signal?: AbortSignal): Promise<AskOut> {
  const res = await api.POST("/inbox/ask" as never, {
    body: { question } as never,
    signal,
  } as never)
  return unwrap(res as never, "/inbox/ask") as AskOut
}

export async function statusBoard(limit = 200, signal?: AbortSignal): Promise<StatusBoardRow[]> {
  const res = await api.GET("/inbox/status-board", { params: { query: { limit } }, signal })
  return unwrap(res, "/inbox/status-board")
}

export async function noReply(limit = 50, signal?: AbortSignal): Promise<NoReplyRow[]> {
  const res = await api.GET("/inbox/no-reply", { params: { query: { limit } }, signal })
  return unwrap(res, "/inbox/no-reply")
}

export async function responseTime(
  brokerDomain?: string,
  signal?: AbortSignal,
): Promise<ResponseTimeStats> {
  const res = await api.GET("/inbox/response-time", {
    params: { query: { broker_domain: brokerDomain } },
    signal,
  })
  return unwrap(res, "/inbox/response-time")
}

export async function overviewKpis(signal?: AbortSignal): Promise<OverviewKPIs> {
  const res = await api.GET("/inbox/overview-kpis", { signal })
  return unwrap(res, "/inbox/overview-kpis")
}

export async function relationship(
  brokerDomain: string,
  signal?: AbortSignal,
): Promise<RelationshipHealth> {
  const res = await api.GET("/inbox/relationship/{broker_domain}", {
    params: { path: { broker_domain: brokerDomain } },
    signal,
  })
  return unwrap(res, `/inbox/relationship/${brokerDomain}`)
}

export async function staff(signal?: AbortSignal): Promise<StaffRow[]> {
  const res = await api.GET("/inbox/staff", { signal })
  return unwrap(res, "/inbox/staff")
}

export async function forgetContact(
  email: string,
  signal?: AbortSignal,
): Promise<ForgetContactOut> {
  const res = await api.POST("/inbox/forget-contact", { body: { email }, signal })
  return unwrap(res, "/inbox/forget-contact")
}
