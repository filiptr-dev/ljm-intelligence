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
export type LoadRow = components["schemas"]["LoadGroupOut"]
export type LoadGroupRow = components["schemas"]["LoadGroupOut"]
export type LoadsListOut = components["schemas"]["LoadsListOut"]
export type PasteIn = components["schemas"]["PasteIn"]
export type StatusIn = components["schemas"]["StatusIn"]
export type InquiryOut = components["schemas"]["InquiryOut"]
export type LoadboardCredRow = components["schemas"]["LoadboardCredOut"]
export type LoadboardCredsList = components["schemas"]["LoadboardCredsListOut"]
export type LoadboardDriverOut = components["schemas"]["LoadboardDriverOut"]
export type LoadboardDriverIn = components["schemas"]["LoadboardDriverIn"]

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

export async function listLoads(limit = 100): Promise<LoadGroupRow[]> {
  // The backend `/loads` endpoint returns deduped group rows (plan 2026-10-08);
  // the old flat shape lives at `/loads/all` for debugging.
  const { data } = await api.GET("/loads", { params: { query: { limit } } })
  return data?.items ?? []
}

export async function postLoadsPaste(text: string): Promise<LoadGroupRow[]> {
  const { data } = await api.POST("/loads/paste", { body: { text } })
  return data?.items ?? []
}

export async function postLoadGroupStatus(
  groupHash: string,
  status: StatusIn["status"],
): Promise<{ group_hash: string; status: string; rows_updated: number } | null> {
  const { data } = await api.POST("/loads/{group_hash}/status", {
    params: { path: { group_hash: groupHash } },
    body: { status },
  })
  return data ?? null
}

export async function postLoadGroupInquiry(
  groupHash: string,
): Promise<InquiryOut | null> {
  const { data } = await api.POST("/loads/{group_hash}/inquiry", {
    params: { path: { group_hash: groupHash } },
  })
  return data ?? null
}

export async function deleteDemoLoads(): Promise<{ deleted: number } | null> {
  const { data } = await api.DELETE("/loads/demo", {})
  return (data as { deleted: number } | undefined) ?? null
}

// ---- Load-board credentials (per source) + driver + broker-page URLs ------
export async function listLoadboardCreds(): Promise<LoadboardCredsList | null> {
  const { data } = await api.GET("/settings/connectors/loadboard", {})
  return data ?? null
}

export async function setLoadboardCred(
  src: string,
  username: string,
  password: string,
): Promise<LoadboardCredRow | null> {
  const { data } = await api.POST("/settings/connectors/loadboard/{src}", {
    params: { path: { src } },
    body: { username, password },
  })
  return data ?? null
}

export async function testLoadboardCred(src: string): Promise<LoadboardCredRow | null> {
  const { data } = await api.POST("/settings/connectors/loadboard/{src}/test", {
    params: { path: { src } },
  })
  return data ?? null
}

export async function clearLoadboardCred(src: string): Promise<LoadboardCredRow | null> {
  const { data } = await api.DELETE("/settings/connectors/loadboard/{src}", {
    params: { path: { src } },
  })
  return data ?? null
}

export async function putBrokerPageUrls(
  urls: Array<{ label: string; url: string; enabled: boolean }>,
): Promise<LoadboardCredsList | null> {
  const { data } = await api.PUT("/settings/connectors/loadboard/broker-page-urls", {
    body: { urls },
  })
  return data ?? null
}

export async function getLoadboardDrivers(): Promise<LoadboardDriverOut | null> {
  const { data } = await api.GET("/settings/connectors/loadboard/drivers", {})
  return data ?? null
}

export async function putLoadboardDrivers(
  patch: Partial<LoadboardDriverIn>,
): Promise<LoadboardDriverOut | null> {
  const { data } = await api.PUT("/settings/connectors/loadboard/drivers", {
    body: patch,
  })
  return data ?? null
}

// ---- Agent-browser sidecar (migration 0041) --------------------------------
export type AgentBrowserState = components["schemas"]["AgentBrowserOut"]
export type AgentBrowserPatch = components["schemas"]["AgentBrowserIn"]
export type AgentBrowserTestResult = components["schemas"]["AgentBrowserTestOut"]

export async function getAgentBrowser(): Promise<AgentBrowserState | null> {
  const { data } = await api.GET("/settings/connectors/agent-browser", {})
  return data ?? null
}

export async function putAgentBrowser(
  patch: AgentBrowserPatch,
): Promise<AgentBrowserState | null> {
  const { data } = await api.PUT("/settings/connectors/agent-browser", { body: patch })
  return data ?? null
}

export async function clearAgentBrowser(): Promise<AgentBrowserState | null> {
  const { data } = await api.DELETE("/settings/connectors/agent-browser", {})
  return data ?? null
}

export async function testAgentBrowser(): Promise<AgentBrowserTestResult | null> {
  const { data } = await api.POST("/settings/connectors/agent-browser/test", {})
  return data ?? null
}
