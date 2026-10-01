/**
 * Owner settings — /settings. SERVER COMPONENT shell.
 *
 * The owner settings panel used to be a 500-line `"use client"` page that
 * fetched `/settings` on mount. With cookie-only auth the typed client reads
 * the HttpOnly session cookie via `next/headers` server-side, so the first
 * payload lands in SSR and the client island opens pre-populated — no
 * mount-time flash, no spinner on the way in.
 *
 * The interactive bits (toggles, sliders, saves) still live in the client
 * island (`./settings-client.tsx`). The two in-panel islands
 * (`./ai-providers-panel`, `./connectors-panel`) remain self-contained and
 * fetch their own data; this shell doesn't try to pre-aggregate everything.
 */

import { api } from "@/lib/api/server"
import SettingsClient, { type SettingsOut } from "./settings-client"
import { OwnerSwitches } from "./owner-switches"

export const dynamic = "force-dynamic"

export default async function SettingsPage() {
  let initial: SettingsOut | null = null
  try {
    const { data, response } = await api.GET("/settings", {})
    if (response.ok && data) initial = data as unknown as SettingsOut
  } catch {
    initial = null
  }
  return (
    <>
      <SettingsClient initial={initial} />
      <div className="mt-6">
        <OwnerSwitches />
      </div>
    </>
  )
}
