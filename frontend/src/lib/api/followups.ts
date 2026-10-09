/**
 * Follow-ups domain — board read, per-lead note upsert, manual stage move.
 *
 * Stage is still derived server-side, but a manual override now wins over
 * the derived bucket until a real event (reply, booked call) with a newer
 * timestamp arrives. The override is persisted via PATCH; drag-and-drop
 * on /pipeline calls `setStage` and optimistically moves the card.
 */

import { api, ApiRequestError } from "./client"
import type { components } from "./schema"

export type Board = components["schemas"]["BoardOut"]
export type BoardCard = components["schemas"]["BoardCardOut"]
export type NextActionPill = components["schemas"]["NextActionPill"]
export type SaveNoteIn = components["schemas"]["SaveNoteIn"]
export type SaveNoteOut = components["schemas"]["SaveNoteOut"]
export type SetStageIn = components["schemas"]["SetStageIn"]
export type SetStageOut = components["schemas"]["SetStageOut"]
export type Stage = SetStageIn["stage"]

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

export async function setStage(
  leadId: string,
  stage: Stage,
): Promise<SetStageOut> {
  const res = await api.PATCH("/followups/{lead_id}/stage", {
    params: { path: { lead_id: leadId } },
    body: { stage },
  })
  return unwrap(res, `/followups/${leadId}/stage`)
}
