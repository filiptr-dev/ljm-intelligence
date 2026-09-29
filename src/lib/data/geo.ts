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
const eu = (name: string, code: string, lat: number, lon: number, zone: string): City => ({
  name, code, lat, lon, zone, region: "EU",
})

export const US_ZONES = ["Midwest", "South Central", "Southeast", "Northeast", "West"] as const
export const EU_ZONES = ["DACH", "Benelux", "Central Europe", "Italy", "France & Iberia", "Balkans"] as const
export const ZONES = [...US_ZONES, ...EU_ZONES] as const

export const CITIES: City[] = [
  us("Chicago", "IL", 41.88, -87.63, "Midwest"),
  us("Indianapolis", "IN", 39.77, -86.16, "Midwest"),
  us("Columbus", "OH", 39.96, -83.0, "Midwest"),
  us("Detroit", "MI", 42.33, -83.05, "Midwest"),
  us("Kansas City", "MO", 39.1, -94.58, "Midwest"),
  us("St. Louis", "MO", 38.63, -90.2, "Midwest"),
  us("Minneapolis", "MN", 44.98, -93.27, "Midwest"),
  us("Dallas", "TX", 32.78, -96.8, "South Central"),
  us("Houston", "TX", 29.76, -95.37, "South Central"),
  us("San Antonio", "TX", 29.42, -98.49, "South Central"),
  us("Laredo", "TX", 27.5, -99.5, "South Central"),
  us("Oklahoma City", "OK", 35.47, -97.52, "South Central"),
  us("Memphis", "TN", 35.15, -90.05, "Southeast"),
  us("Nashville", "TN", 36.16, -86.78, "Southeast"),
  us("Atlanta", "GA", 33.75, -84.39, "Southeast"),
  us("Charlotte", "NC", 35.23, -80.84, "Southeast"),
  us("Jacksonville", "FL", 30.33, -81.66, "Southeast"),
  us("Miami", "FL", 25.76, -80.19, "Southeast"),
  us("Savannah", "GA", 32.08, -81.09, "Southeast"),
  us("Newark", "NJ", 40.74, -74.17, "Northeast"),
  us("Allentown", "PA", 40.6, -75.49, "Northeast"),
  us("Harrisburg", "PA", 40.27, -76.88, "Northeast"),
  us("Boston", "MA", 42.36, -71.06, "Northeast"),
  us("Los Angeles", "CA", 34.05, -118.24, "West"),
  us("Ontario", "CA", 34.06, -117.65, "West"),
  us("Phoenix", "AZ", 33.45, -112.07, "West"),
  us("Denver", "CO", 39.74, -104.99, "West"),
  us("Salt Lake City", "UT", 40.76, -111.89, "West"),
  us("Seattle", "WA", 47.61, -122.33, "West"),
  eu("Munich", "DE", 48.14, 11.58, "DACH"),
  eu("Frankfurt", "DE", 50.11, 8.68, "DACH"),
  eu("Hamburg", "DE", 53.55, 9.99, "DACH"),
  eu("Stuttgart", "DE", 48.78, 9.18, "DACH"),
  eu("Vienna", "AT", 48.21, 16.37, "DACH"),
  eu("Zurich", "CH", 47.37, 8.54, "DACH"),
  eu("Rotterdam", "NL", 51.92, 4.48, "Benelux"),
  eu("Antwerp", "BE", 51.22, 4.4, "Benelux"),
  eu("Venlo", "NL", 51.37, 6.17, "Benelux"),
  eu("Warsaw", "PL", 52.23, 21.01, "Central Europe"),
  eu("Poznan", "PL", 52.41, 16.93, "Central Europe"),
  eu("Prague", "CZ", 50.08, 14.44, "Central Europe"),
  eu("Budapest", "HU", 47.5, 19.04, "Central Europe"),
  eu("Bratislava", "SK", 48.15, 17.11, "Central Europe"),
  eu("Milan", "IT", 45.46, 9.19, "Italy"),
  eu("Verona", "IT", 45.44, 10.99, "Italy"),
  eu("Bologna", "IT", 44.49, 11.34, "Italy"),
  eu("Lyon", "FR", 45.76, 4.84, "France & Iberia"),
  eu("Paris", "FR", 48.86, 2.35, "France & Iberia"),
  eu("Barcelona", "ES", 41.39, 2.17, "France & Iberia"),
  eu("Madrid", "ES", 40.42, -3.7, "France & Iberia"),
  eu("Skopje", "MK", 41.99, 21.43, "Balkans"),
  eu("Belgrade", "RS", 44.79, 20.45, "Balkans"),
  eu("Sofia", "BG", 42.7, 23.32, "Balkans"),
  eu("Thessaloniki", "GR", 40.64, 22.94, "Balkans"),
  eu("Zagreb", "HR", 45.81, 15.98, "Balkans"),
  eu("Ljubljana", "SI", 46.06, 14.51, "Balkans"),
  eu("Bucharest", "RO", 44.43, 26.1, "Balkans"),
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
  "South Central": 1,
  Southeast: 0.8,
  Northeast: 0.3,
  West: 0.15,
  DACH: 1,
  Balkans: 1,
  Italy: 0.8,
  "Central Europe": 0.7,
  Benelux: 0.35,
  "France & Iberia": 0.2,
}
