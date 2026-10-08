/**
 * Contacts domain module — freight-manager contacts plan.
 *
 * Mirrors the `enrichment.ts` shape: every call goes through the typed
 * openapi-fetch client, no hand-typed shapes, no `any`. Regenerate the
 * schema via `pnpm gen:api` whenever the backend contract changes.
 */

import { api, ApiRequestError } from "./client"
import type { components } from "./schema"

export type Contact = components["schemas"]["ContactOut"]
export type ContactList = components["schemas"]["ContactListOut"]
export type Refresh = components["schemas"]["RefreshOut"]
export type CampaignRecipient = components["schemas"]["CampaignRecipient"]
export type Campaign = components["schemas"]["CampaignOut"]

function unwrap<T>(res: { data?: T; error?: unknown; response: Response }, path: string): T {
  if (res.data !== undefined) return res.data
  throw new ApiRequestError(res.response.status, path, res.error)
}

export async function listContactsForLead(leadId: string): Promise<ContactList> {
  const res = await api.GET("/leads/{lead_id}/contacts", {
    params: { path: { lead_id: leadId } },
  })
  return unwrap(res, `/leads/${leadId}/contacts`)
}

export async function refreshContactsForLead(
  leadId: string,
  sources?: Array<"fmcsa" | "gemini" | "site" | "inbox">,
): Promise<Refresh> {
  const res = await api.POST("/leads/{lead_id}/contacts/refresh", {
    params: { path: { lead_id: leadId } },
    body: { sources: sources ?? null },
  })
  return unwrap(res, `/leads/${leadId}/contacts/refresh`)
}

export async function listFreightManagers(cursor?: string, limit = 50): Promise<ContactList> {
  const res = await api.GET("/contacts", {
    params: { query: { role: "freight_manager", cursor: cursor ?? null, limit } },
  })
  return unwrap(res, "/contacts?role=freight_manager")
}

export async function runCampaign(input: {
  tone?: "professional" | "friendly" | "direct" | "persuasive"
  limit?: number
  dry_run: boolean
}): Promise<Campaign> {
  const res = await api.POST("/email/campaigns", {
    body: {
      segment: "freight_manager",
      tone: input.tone ?? "professional",
      limit: input.limit ?? 50,
      dry_run: input.dry_run,
    },
  })
  return unwrap(res, "/email/campaigns")
}
