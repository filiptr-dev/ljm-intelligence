/**
 * Typed helpers for the mail + loads connectors.
 *
 * Thin wrappers around the generated `openapi-fetch` client so the Settings
 * panel never spells raw URLs. New endpoint ⇒ regenerate `schema.d.ts` with
 * `pnpm gen:api` and this file follows automatically.
 */

import { api } from "./client"
import type { components } from "./schema"

export type MailStatus = components["schemas"]["MailStatusOut"]
export type TestSendResult = components["schemas"]["TestSendOut"]
export type LoadSourceRow = components["schemas"]["SourceOut"]
export type ConnectionTestResult = components["schemas"]["ConnectionTestOut"]
export type RefreshStats = components["schemas"]["RefreshStatsOut"]
export type LoadRow = components["schemas"]["LoadOut"]

export async function getMailStatus(): Promise<MailStatus | null> {
  const { data } = await api.GET("/mail/status", {})
  return data ?? null
}

export async function postMailTestSend(to: string): Promise<TestSendResult | null> {
  const { data } = await api.POST("/mail/test-send", { body: { to } })
  return data ?? null
}

export async function postMailDisconnect(): Promise<boolean> {
  const { data } = await api.POST("/mail/disconnect", {})
  return !!data?.ok
}

export async function postMailReconnect(): Promise<boolean> {
  const { data } = await api.POST("/mail/reconnect", {})
  return !!data?.ok
}

export type TestReadResult = components["schemas"]["TestReadOut"]

export async function postMailTestRead(): Promise<TestReadResult | null> {
  const { data } = await api.POST("/mail/test-read", {})
  return data ?? null
}

export async function postMailBackfill(mailbox: string, months = 12): Promise<unknown> {
  const { data } = await api.POST("/mail/backfill", { body: { mailbox, months } })
  return data ?? null
}

export async function listLoadSources(): Promise<LoadSourceRow[]> {
  const { data } = await api.GET("/loads/sources", {})
  return data?.items ?? []
}

export async function testLoadSource(kind: string): Promise<ConnectionTestResult | null> {
  const { data } = await api.POST("/loads/sources/{kind}/test", {
    params: { path: { kind } },
  })
  return data ?? null
}

export async function refreshLoadSource(kind: string): Promise<RefreshStats | null> {
  const { data } = await api.POST("/loads/sources/{kind}/refresh", {
    params: { path: { kind } },
  })
  return data ?? null
}

export async function listLoads(limit = 50): Promise<LoadRow[]> {
  const { data } = await api.GET("/loads", { params: { query: { limit } } })
  return data?.items ?? []
}
