import type { Region } from "@/lib/data/geo"

export const currency = (r: Region) => (r === "US" ? "$" : "€")

export function money(v: number, r: Region, compact = false) {
  const c = currency(r)
  if (compact && Math.abs(v) >= 1000) {
    return `${c}${v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : (v / 1000).toFixed(v >= 1e5 ? 0 : 1) + "K"}`
  }
  return `${c}${Math.round(v).toLocaleString("en-US")}`
}

export const perUnit = (v: number, r: Region) => `${currency(r)}${v.toFixed(2)}/${r === "US" ? "mi" : "km"}`

export const pct = (v: number, digits = 0) => `${(v * 100).toFixed(digits)}%`

export const num = (v: number) => Math.round(v).toLocaleString("en-US")

export function timeAgo(iso: string, now = Date.now()) {
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000))
  if (s < 45) return "just now"
  const m = Math.round(s / 60)
  if (m < 60) return `${m} min ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.round(h / 24)
  if (d < 45) return `${d}d ago`
  return `${Math.round(d / 30)} mo ago`
}

export const dateShort = (iso: string) =>
  new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })

export const dateTime = (iso: string) =>
  new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" })

export const monthLabel = (key: string) =>
  new Date(`${key}-01T00:00:00Z`).toLocaleDateString("en-US", { month: "short", year: "2-digit", timeZone: "UTC" })
