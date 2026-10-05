/**
 * One chart system — the shared primitives every analytics view consumes.
 *
 * The rule, enforced by this barrel + the plan: analytics pages import from
 * `@/components/charts/primitives` only. Any new chart type lands here as a
 * primitive. No recharts imports elsewhere.
 */

export { KpiTile } from "./KpiTile"
export { TrendLine } from "./TrendLine"
export { Breakdown } from "./Breakdown"
export { FunnelChart } from "./FunnelChart"
export { ThinSample } from "./ThinSample"
export { EmptyChart } from "./EmptyChart"
export { ChartSkeleton } from "./ChartSkeleton"
export { Sparkline } from "./Sparkline"
