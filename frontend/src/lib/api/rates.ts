/**
 * Typed wrappers over the generated `schema.d.ts` for the three rate tools.
 *
 * One module per bounded context (same pattern as `connectors.ts`,
 * `brokers.ts`, …). The pages NEVER spell URLs — a new endpoint = regenerate
 * schema + add a wrapper here.
 */

import { api } from "./client"
import type { components } from "./schema"

export type CityStateIn = components["schemas"]["CityStateIn"]
export type RateQuoteIn = components["schemas"]["RateQuoteIn"]
export type RateQuoteOut = components["schemas"]["RateQuoteOut"]
export type ProfitIn = components["schemas"]["ProfitIn"]
export type ProfitOut = components["schemas"]["ProfitOut"]
export type BackhaulIn = components["schemas"]["BackhaulIn"]
export type BackhaulOut = components["schemas"]["BackhaulOut"]
export type BackhaulCandidateOut = components["schemas"]["BackhaulCandidateOut"]
export type EiaKeyIn = components["schemas"]["EiaKeyIn"]
export type EiaKeyOut = components["schemas"]["EiaKeyOut"]

export async function quoteLane(body: RateQuoteIn): Promise<RateQuoteOut | null> {
  const { data } = await api.POST("/rates/quote", { body })
  return data ?? null
}

export async function scoreProfit(body: ProfitIn): Promise<ProfitOut | null> {
  const { data } = await api.POST("/rates/profit", { body })
  return data ?? null
}

export async function findBackhauls(body: BackhaulIn): Promise<BackhaulOut | null> {
  const { data } = await api.POST("/rates/backhaul", { body })
  return data ?? null
}

export async function getEiaKey(): Promise<EiaKeyOut | null> {
  const { data } = await api.GET("/settings/connectors/eia", {})
  return data ?? null
}

export async function setEiaKey(body: EiaKeyIn): Promise<EiaKeyOut | null> {
  const { data } = await api.POST("/settings/connectors/eia", { body })
  return data ?? null
}

export async function clearEiaKey(): Promise<EiaKeyOut | null> {
  const { data } = await api.DELETE("/settings/connectors/eia", {})
  return data ?? null
}
