export type Region = "US" | "EU"

export type City = {
  name: string
  code: string // state (US) or country (EU)
  lat: number
  lon: number
  zone: string
  region: Region
}

const us = (name: string, code: string, lat: number, lon: number, zone: string): City => ({
  name, code, lat, lon, zone, region: "US",
})
// EU factory kept for type parity with historical callers; no EU cities remain in the dataset.
const eu = (name: string, code: string, lat: number, lon: number, zone: string): City => ({
  name, code, lat, lon, zone, region: "EU",
})
void eu

// LJM operates in the US only: HQ NJ, and the eastern 32 states (see backend/app/region.py).
// Zones are trimmed to the three actually covered.
export const US_ZONES = ["Midwest", "Southeast", "Northeast"] as const
export const EU_ZONES = [] as const
export const ZONES = [...US_ZONES, ...EU_ZONES] as const

export const CITIES: City[] = [
  // Midwest (eastern portion + MO/IA/MN reach)
  us("Chicago", "IL", 41.88, -87.63, "Midwest"),
  us("Indianapolis", "IN", 39.77, -86.16, "Midwest"),
  us("Columbus", "OH", 39.96, -83.0, "Midwest"),
  us("Cleveland", "OH", 41.5, -81.69, "Midwest"),
  us("Cincinnati", "OH", 39.1, -84.51, "Midwest"),
  us("Detroit", "MI", 42.33, -83.05, "Midwest"),
  us("Milwaukee", "WI", 43.04, -87.91, "Midwest"),
  us("Kansas City", "MO", 39.1, -94.58, "Midwest"),
  us("St. Louis", "MO", 38.63, -90.2, "Midwest"),
  us("Des Moines", "IA", 41.59, -93.62, "Midwest"),
  us("Minneapolis", "MN", 44.98, -93.27, "Midwest"),
  // Southeast (+ AR/LA reach)
  us("Louisville", "KY", 38.25, -85.76, "Southeast"),
  us("Memphis", "TN", 35.15, -90.05, "Southeast"),
  us("Nashville", "TN", 36.16, -86.78, "Southeast"),
  us("Birmingham", "AL", 33.52, -86.8, "Southeast"),
  us("Atlanta", "GA", 33.75, -84.39, "Southeast"),
  us("Charlotte", "NC", 35.23, -80.84, "Southeast"),
  us("Jacksonville", "FL", 30.33, -81.66, "Southeast"),
  us("Miami", "FL", 25.76, -80.19, "Southeast"),
  us("Savannah", "GA", 32.08, -81.09, "Southeast"),
  us("New Orleans", "LA", 29.95, -90.07, "Southeast"),
  us("Little Rock", "AR", 34.75, -92.29, "Southeast"),
  // Northeast + Mid-Atlantic
  us("Newark", "NJ", 40.74, -74.17, "Northeast"),
  us("New York", "NY", 40.71, -74.01, "Northeast"),
  us("Philadelphia", "PA", 39.95, -75.17, "Northeast"),
  us("Allentown", "PA", 40.6, -75.49, "Northeast"),
  us("Harrisburg", "PA", 40.27, -76.88, "Northeast"),
  us("Pittsburgh", "PA", 40.44, -79.99, "Northeast"),
  us("Boston", "MA", 42.36, -71.06, "Northeast"),
  us("Baltimore", "MD", 39.29, -76.61, "Northeast"),
  us("Washington", "DC", 38.9, -77.04, "Northeast"),
  us("Richmond", "VA", 37.54, -77.44, "Northeast"),
]

export const cityLabel = (c: City) => `${c.name}, ${c.code}`

export const citiesIn = (region: Region) => CITIES.filter((c) => c.region === region)

export const findCity = (label: string) => CITIES.find((c) => cityLabel(c) === label)

/** Road distance estimate: great-circle × 1.18 road factor. Miles for US, km for EU. */
export function roadDistance(a: City, b: City): number {
  const R = 6371
  const toRad = (d: number) => (d * Math.PI) / 180
  const dLat = toRad(b.lat - a.lat)
  const dLon = toRad(b.lon - a.lon)
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLon / 2) ** 2
  const km = 2 * R * Math.asin(Math.sqrt(h)) * 1.18
  return Math.round(a.region === "US" ? km * 0.621 : km)
}

/** Where the client's own trucks run — drives how well a broker "fits". */
export const CLIENT_ZONES: Record<string, number> = {
  Midwest: 1,
  Southeast: 0.9,
  Northeast: 0.8,
}
