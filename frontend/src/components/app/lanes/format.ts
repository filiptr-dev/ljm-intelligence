export const usd = (v: number) =>
  Math.abs(v) >= 1_000_000
    ? `$${(v / 1_000_000).toFixed(2)}M`
    : Math.abs(v) >= 10_000
      ? `$${Math.round(v / 1_000)}k`
      : `$${Math.round(v).toLocaleString("en-US")}`

export const usdFull = (v: number) => `$${Math.round(v).toLocaleString("en-US")}`
export const int = (v: number) => Math.round(v).toLocaleString("en-US")
export const perMile = (v: number | null | undefined) => (v == null ? "—" : `$${v.toFixed(2)}`)
export const pct = (v: number | null | undefined, digits = 1) => (v == null ? "—" : `${v.toFixed(digits)}%`)
export const signedPct = (v: number | null | undefined, digits = 1) =>
  v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`

/** Lane filter key `Chicago,IL>Atlanta,GA` -> `Chicago, IL → Atlanta, GA`. */
export function laneLabel(key: string): string {
  const [o, d] = key.split(">")
  if (!d) return key
  const fmt = (s: string) => s.replace(/,(?=[A-Z]{2}$)/, ", ")
  return `${fmt(o)} → ${fmt(d)}`
}

export const STATE_NAMES: Record<string, string> = {
  AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California", CO: "Colorado", CT: "Connecticut",
  DE: "Delaware", DC: "District of Columbia", FL: "Florida", GA: "Georgia", HI: "Hawaii", ID: "Idaho", IL: "Illinois",
  IN: "Indiana", IA: "Iowa", KS: "Kansas", KY: "Kentucky", LA: "Louisiana", ME: "Maine", MD: "Maryland",
  MA: "Massachusetts", MI: "Michigan", MN: "Minnesota", MS: "Mississippi", MO: "Missouri", MT: "Montana",
  NE: "Nebraska", NV: "Nevada", NH: "New Hampshire", NJ: "New Jersey", NM: "New Mexico", NY: "New York",
  NC: "North Carolina", ND: "North Dakota", OH: "Ohio", OK: "Oklahoma", OR: "Oregon", PA: "Pennsylvania",
  RI: "Rhode Island", SC: "South Carolina", SD: "South Dakota", TN: "Tennessee", TX: "Texas", UT: "Utah",
  VT: "Vermont", VA: "Virginia", WA: "Washington", WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming",
}
export const STATE_ABBR_BY_NAME: Record<string, string> = Object.fromEntries(
  Object.entries(STATE_NAMES).map(([abbr, name]) => [name, abbr]),
)
