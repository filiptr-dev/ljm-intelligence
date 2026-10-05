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
