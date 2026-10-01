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

export async function sendReply(
  threadId: string,
  body: { body_text: string; body_html?: string },
  signal?: AbortSignal,
): Promise<ReplyOut> {
  const res = await api.POST("/inbox/threads/{thread_id}/reply", {
    params: { path: { thread_id: threadId } },
    body: { body_text: body.body_text, body_html: body.body_html ?? "" },
    signal,
  })
  return unwrap(res, `/inbox/threads/${threadId}/reply`)
}

export async function compose(
  body: { to: string; subject: string; body_text: string; body_html?: string },
  signal?: AbortSignal,
): Promise<ComposeOut> {
  const res = await api.POST("/inbox/compose", {
    body: { ...body, body_html: body.body_html ?? "" },
    signal,
  })
  return unwrap(res, "/inbox/compose")
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
