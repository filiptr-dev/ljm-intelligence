/**
 * LoadStop (TMS) — typed wrappers over the openapi-fetch client.
 *
 * Credentials now come from env on the backend (``LOADSTOP_USERNAME`` /
 * ``LOADSTOP_PASSWORD``), so this surface no longer carries a PATCH or a
 * /test. We expose two reads/writes:
 *
 *  * ``getLoadstopState()`` — slim status row.
 *  * ``syncLoadstop(service)`` — operator-triggered one-shot refresh.
 */

import { api } from "./client"

export type LoadstopSyncService = "fleet" | "customers" | "loads" | "rates" | "all"

export type LoadstopState = {
  configured: boolean
  email_masked: string | null
  has_session: boolean
  last_login_at: string | null
  last_sync: {
    fleet: string | null
    customers: string | null
    loads: string | null
    rates: string | null
  }
  kill_switch_on: boolean
}

export type LoadstopSyncResult = { service: string; queued: boolean; task: string }

type RawState = {
  configured?: boolean
  email_masked?: string | null
  has_session?: boolean
  last_login_at?: string | null
  last_sync?: Partial<LoadstopState["last_sync"]> | null
  kill_switch_on?: boolean
}

function narrow(raw: RawState | undefined): LoadstopState | null {
  if (!raw) return null
  const ls = raw.last_sync ?? {}
  return {
    configured: !!raw.configured,
    email_masked: raw.email_masked ?? null,
    has_session: !!raw.has_session,
    last_login_at: raw.last_login_at ?? null,
    last_sync: {
      fleet: ls.fleet ?? null,
      customers: ls.customers ?? null,
      loads: ls.loads ?? null,
      rates: ls.rates ?? null,
    },
    kill_switch_on: !!raw.kill_switch_on,
  }
}

export async function getLoadstopState(signal?: AbortSignal): Promise<LoadstopState | null> {
  const { data } = await api.GET("/loadboards/loadstop", { signal })
  return narrow(data as RawState | undefined)
}

export async function syncLoadstop(
  service: LoadstopSyncService,
  signal?: AbortSignal,
): Promise<{ ok: boolean; status: number; result: LoadstopSyncResult | null; reason: string | null }> {
  const { data, response, error } = await api.POST("/loadboards/loadstop/sync/{service}", {
    params: { path: { service } },
    signal,
  })
  if (!response.ok) {
    const detail = (error as { detail?: string } | undefined)?.detail ?? null
    return { ok: false, status: response.status, result: null, reason: detail }
  }
  return { ok: true, status: response.status, result: (data as unknown as LoadstopSyncResult) ?? null, reason: null }
}
