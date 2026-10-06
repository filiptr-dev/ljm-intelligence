/**
 * Analysis domain — predictions + further-analyses typed client.
 */

import { api, ApiRequestError } from "./client"
import type { components } from "./schema"

export type BrokerPrediction = components["schemas"]["BrokerPredictionOut"]
export type LanePrediction = components["schemas"]["LanePredictionOut"]
export type Lookalike = components["schemas"]["LookalikeOut"]
export type Objection = components["schemas"]["ObjectionOut"]
export type WorkloadCell = components["schemas"]["WorkloadCellOut"]
export type ThreadAgeBucket = components["schemas"]["ThreadAgeOut"]
export type LossReason = components["schemas"]["LossReasonOut"]
export type FirstTouch = components["schemas"]["FirstTouchOut"]
export type PredictionsPage = components["schemas"]["PredictionsPageOut"]

// Shared KPI foundation shapes (one chart system).
export type MetricPoint = components["schemas"]["MetricPointOut"]
export type KpiBlock = components["schemas"]["KpiBlockOut"]
export type BreakdownRow = components["schemas"]["BreakdownRowOut"]
export type Breakdown = components["schemas"]["BreakdownOut"]
export type FunnelStep = components["schemas"]["FunnelStepOut"]
export type Funnel = components["schemas"]["FunnelOut"]
export type OverviewKpi = components["schemas"]["OverviewKpiOut"]
export type CrawlerKpi = components["schemas"]["CrawlerKpiOut"]
export type CallOutcomeKpi = components["schemas"]["CallOutcomeKpiOut"]
export type DayPulse = components["schemas"]["DayPulseOut"]
export type TopbarCounters = components["schemas"]["TopbarCountersOut"]
export type LaneKpi = components["schemas"]["LaneKpiOut"]
export type CapacityKpi = components["schemas"]["CapacityKpiOut"]
export type BrokerKpi = components["schemas"]["BrokerKpiOut"]

export type PeriodLabel = "today" | "7d" | "30d" | "90d" | "custom"

type PeriodQuery = { period?: PeriodLabel; from?: string; to?: string }

export async function getOverviewKpi(opts: PeriodQuery = {}, signal?: AbortSignal): Promise<OverviewKpi> {
  const res = await api.GET("/analysis/overview", { params: { query: opts }, signal })
  return unwrap(res, "/analysis/overview")
}

export async function getCrawlerKpi(opts: PeriodQuery = {}, signal?: AbortSignal): Promise<CrawlerKpi> {
  const res = await api.GET("/analysis/crawler", { params: { query: opts }, signal })
  return unwrap(res, "/analysis/crawler")
}

export async function getCallOutcomeKpi(opts: PeriodQuery = {}, signal?: AbortSignal): Promise<CallOutcomeKpi> {
  const res = await api.GET("/analysis/call-outcomes", { params: { query: opts }, signal })
  return unwrap(res, "/analysis/call-outcomes")
}

export async function getDayPulse(signal?: AbortSignal): Promise<DayPulse> {
  const res = await api.GET("/analysis/day-pulse", { signal })
  return unwrap(res, "/analysis/day-pulse")
}

export async function getLaneKpi(opts: PeriodQuery & { region?: string } = {}, signal?: AbortSignal): Promise<LaneKpi> {
  const res = await api.GET("/analysis/lanes", { params: { query: opts }, signal })
  return unwrap(res, "/analysis/lanes")
}

export async function getCapacityKpi(opts: PeriodQuery = {}, signal?: AbortSignal): Promise<CapacityKpi> {
  const res = await api.GET("/analysis/capacity", { params: { query: opts }, signal })
  return unwrap(res, "/analysis/capacity")
}

export async function getBrokerKpi(brokerId: string, opts: PeriodQuery = {}, signal?: AbortSignal): Promise<BrokerKpi> {
  const res = await api.GET("/analysis/broker-kpis/{broker_id}", {
    params: { path: { broker_id: brokerId }, query: opts },
    signal,
  })
  return unwrap(res, `/analysis/broker-kpis/${brokerId}`)
}

/** GET /analysis/topbar-counters — today's (ET) scanned / found / sent / replies
 *  for the tenant, plus the top-bar pill's `last_crawl_at` / `last_crawl_status`
 *  piggybacked so the pill and the counters stay in lockstep off one poll. */
export async function getTopbarCounters(signal?: AbortSignal): Promise<TopbarCounters> {
  const res = await api.GET("/analysis/topbar-counters", { signal })
  return unwrap(res, "/analysis/topbar-counters")
}

export type LiveFeedItem = components["schemas"]["LiveFeedItemOut"]
export type LiveFeedOut = components["schemas"]["LiveFeedOut"]

/** GET /analysis/live-feed — rolling 12h, real `found` / `outreach` / `reply`
 *  events only, tenant-scoped. Replaces the client-side createRng fiction. */
export async function getLiveFeed(limit = 25, signal?: AbortSignal): Promise<LiveFeedOut> {
  const res = await api.GET("/analysis/live-feed", {
    params: { query: { limit } },
    signal,
  })
  return unwrap(res, "/analysis/live-feed")
}

export type CampaignStatusItem = components["schemas"]["CampaignStatusItemOut"]
export type CampaignStatusOut = components["schemas"]["CampaignStatusOut"]

/** POST /analysis/campaign-status — real per-recipient `sent_at` /
 *  `replied_at` for a client-side campaign (campaigns live in localStorage
 *  in v1; this read is how the dashboard stops lying about delivery).
 *  Opens and wins are never returned — no table backs them. */
export async function getCampaignStatus(
  createdAt: string,
  emails: string[],
  signal?: AbortSignal,
): Promise<CampaignStatusOut> {
  const res = await api.POST("/analysis/campaign-status", {
    body: { created_at: createdAt, emails },
    signal,
  })
  return unwrap(res, "/analysis/campaign-status")
}

function unwrap<T>(res: { data?: T; error?: unknown; response: Response }, path: string): T {
  if (res.data !== undefined) return res.data
  throw new ApiRequestError(res.response.status, path, res.error)
}

export async function getPredictions(signal?: AbortSignal): Promise<PredictionsPage> {
  const res = await api.GET("/analysis/predictions", { signal })
  return unwrap(res, "/analysis/predictions")
}

export async function getBrokerPredictions(
  brokerDomain?: string,
  signal?: AbortSignal,
): Promise<BrokerPrediction[]> {
  const res = await api.GET("/analysis/broker-predictions", {
    params: { query: { broker_domain: brokerDomain } },
    signal,
  })
  return unwrap(res, "/analysis/broker-predictions")
}

export async function getLookalikes(
  brokerDomain: string,
  topN = 5,
  signal?: AbortSignal,
): Promise<Lookalike[]> {
  const res = await api.GET("/analysis/lookalikes/{broker_domain}", {
    params: { path: { broker_domain: brokerDomain }, query: { top_n: topN } },
    signal,
  })
  return unwrap(res, `/analysis/lookalikes/${brokerDomain}`)
}

export async function getObjections(
  brokerDomain?: string,
  signal?: AbortSignal,
): Promise<Objection[]> {
  const res = await api.GET("/analysis/objections", {
    params: { query: { broker_domain: brokerDomain } },
    signal,
  })
  return unwrap(res, "/analysis/objections")
}
