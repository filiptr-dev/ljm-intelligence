/**
 * Follow-ups — /pipeline. SERVER COMPONENT shell.
 *
 * Fetches the board once server-side so the first paint carries real
 * cards. Everything interactive (refresh, note editor, deep-links) lives
 * in ./pipeline-client.tsx. Same pattern as /call-list.
 */

import { api } from "@/lib/api/server"
import type { Board } from "@/lib/api/followups"
import PipelineClient from "./pipeline-client"

export const dynamic = "force-dynamic"

export default async function PipelinePage() {
  let initial: Board | null = null
  try {
    const { data, response } = await api.GET("/followups", { params: { query: { limit: 50 } } })
    if (response.ok && data) initial = data as unknown as Board
  } catch {
    initial = null
  }
  return <PipelineClient initial={initial} />
}
