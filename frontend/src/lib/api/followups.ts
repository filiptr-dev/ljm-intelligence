/**
 * Follow-ups domain — board read + per-lead note upsert.
 *
 * Stage is derived server-side; no column-move endpoint exists on purpose
 * (see plan: "Pipeline stage is derived, not stored.").
 */

import { api, ApiRequestError } from "./client"
import type { components } from "./schema"

export type Board = components["schemas"]["BoardOut"]
export type BoardCard = components["schemas"]["BoardCardOut"]
export type NextActionPill = components["schemas"]["NextActionPill"]
export type SaveNoteIn = components["schemas"]["SaveNoteIn"]
export type SaveNoteOut = components["schemas"]["SaveNoteOut"]

function unwrap<T>(res: { data?: T; error?: unknown; response: Response }, path: string): T {
  if (res.data !== undefined) return res.data
  throw new ApiRequestError(res.response.status, path, res.error)
}

export async function getBoard(limit = 50): Promise<Board> {
  const res = await api.GET("/followups", { params: { query: { limit } } })
  return unwrap(res, "/followups")
}

export async function saveNote(
  leadId: string,
  body: SaveNoteIn,
): Promise<SaveNoteOut> {
  const res = await api.POST("/followups/{lead_id}/note", {
    params: { path: { lead_id: leadId } },
    body,
  })
  return unwrap(res, `/followups/${leadId}/note`)
}
