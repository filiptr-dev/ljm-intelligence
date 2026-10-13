/**
 * Gradual Gmail history-import — progress read + manual step kick.
 */
import { api, ApiRequestError } from "./client"
import type { components } from "./schema"

export type BackfillProgressRow = components["schemas"]["BackfillProgressRow"]

function unwrap<T>(res: { data?: T; error?: unknown; response: Response }, path: string): T {
  if (res.data !== undefined) return res.data
  throw new ApiRequestError(res.response.status, path, res.error)
}

export async function getBackfillProgress(signal?: AbortSignal): Promise<BackfillProgressRow[]> {
  const res = await api.GET("/inbox/backfill/progress", { signal })
  return unwrap(res, "/inbox/backfill/progress")
}

export async function kickBackfillStep(mailbox: string): Promise<void> {
  await api.POST("/inbox/backfill/step", { params: { query: { mailbox } } })
}
