/**
 * LoadStop (TMS) — typed wrappers over the openapi-fetch client.
 *
 * Settings panel + the Integrations status row talk to the backend
 * ``/loadboards/loadstop`` surface. The raw ``/api/loadboards/*`` fetches
 * these replaced had no matching Next route and 404'd on Save; routing
 * everything through the typed client (same house pattern as `inbox.ts`
 * and `connectors.ts`) sends the request via the shared ``/api/proxy``
 * BFF, which attaches the bearer and reaches FastAPI's real router.
 *
 * The password is write-only on the backend; GET never echoes it back.
 */

import { api } from "./client"
import type { components } from "./schema"

export type LoadstopDriver = "off" | "api"
export type LoadstopToggles = { fleet: boolean; customers: boolean; loads: boolean; rates: boolean }

/** UI-friendly shape — StateOut with the ``driver`` field narrowed and
 * optional ``email_masked`` / ``last_login_at`` flattened to nullable. */
export type LoadstopState = {
  driver: LoadstopDriver
  status: string
  email_set: boolean
  email_masked: string | null
  has_session: boolean
  last_login_at: string | null
  toggles: LoadstopToggles
  last_sync: Record<string, string | null>
  dat_connected: boolean
}

export type LoadstopPatch = components["schemas"]["PatchIn"]
export type LoadstopTestResult = components["schemas"]["TestOut"]

function narrow(raw: components["schemas"]["StateOut"]): LoadstopState {
  const t = raw.toggles
  return {
    driver: raw.driver === "api" ? "api" : "off",
    status: raw.status,
    email_set: raw.email_set,
    email_masked: raw.email_masked ?? null,
    has_session: raw.has_session,
    last_login_at: raw.last_login_at ?? null,
    toggles: {
      fleet: !!t.fleet,
      customers: !!t.customers,
      loads: !!t.loads,
      rates: !!t.rates,
    },
    last_sync: {
      fleet: raw.last_sync.fleet ?? null,
      customers: raw.last_sync.customers ?? null,
      loads: raw.last_sync.loads ?? null,
      rates: raw.last_sync.rates ?? null,
    },
    dat_connected: raw.dat_connected,
  }
}

export async function getLoadstopState(signal?: AbortSignal): Promise<LoadstopState | null> {
  const { data } = await api.GET("/loadboards/loadstop", { signal })
  return data ? narrow(data) : null
}

export async function patchLoadstopState(
  patch: LoadstopPatch,
  signal?: AbortSignal,
): Promise<LoadstopState | null> {
  const { data } = await api.PATCH("/loadboards/loadstop", { body: patch, signal })
  return data ? narrow(data) : null
}

export async function testLoadstopConnection(signal?: AbortSignal): Promise<LoadstopTestResult | null> {
  const { data } = await api.POST("/loadboards/loadstop/test", { signal })
  return data ?? null
}
