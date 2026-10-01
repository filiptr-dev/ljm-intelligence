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
