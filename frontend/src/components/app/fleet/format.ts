/** Fleet vocab -> words the dispatcher uses, plus tiny date helpers. */

export const STATUS_LABEL: Record<string, string> = {
  available: "Available",
  on_load: "On a load",
  in_shop: "In the shop",
  out_of_service: "Out of service",
}
export const STATUS_ORDER = ["available", "on_load", "in_shop", "out_of_service"] as const

export const EQUIPMENT_LABEL: Record<string, string> = { van: "Van", reefer: "Reefer", flatbed: "Flatbed", stepdeck: "Stepdeck" }
export const EQUIPMENT_ORDER = ["van", "reefer", "flatbed", "stepdeck"] as const

export const DOC_LABEL: Record<string, string> = {
  registration: "Registration",
  insurance: "Insurance",
  inspection_cert: "Inspection certificate",
  adr: "ADR certificate",
  tacho_calibration: "Tacho calibration",
}
export const docLabel = (k: string) => DOC_LABEL[k] ?? k.replace(/_/g, " ")

export const SEVERITY_ORDER = ["critical", "major", "minor"] as const

/** Red inside 14 days, amber inside 30, otherwise fine. */
export type Tone = "bad" | "warn" | "ok"
export const expiryTone = (days: number): Tone => (days <= 14 ? "bad" : days <= 30 ? "warn" : "ok")

export function when(days: number): string {
  if (days < 0) return `${-days} day${days === -1 ? "" : "s"} overdue`
  if (days === 0) return "today"
  if (days === 1) return "tomorrow"
  return `in ${days} days`
}

const DATE = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })
/** Accepts `2026-10-09` or a full ISO timestamp; always rendered in UTC so the day never shifts. */
export const fmtDate = (iso: string | null | undefined) => (iso ? DATE.format(new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso)) : "—")

export const miles = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v).toLocaleString("en-US")} mi`)
