import { cityLabel, type Region } from "./geo"
import { fitWeightedCity, makeEquipment, makeLanes } from "./generate"
import { INDUSTRIES, makeCompany } from "./names"
import { chance, int, pick, sampleN, weighted, type Rng } from "./rng"
import type { Equipment, Lead, LeadKind, LeadSource } from "./types"

/** Where each kind of company is found. */
const SOURCES: Record<LeadKind, Record<Region, [LeadSource, number][]>> = {
  Broker: {
    US: [["FMCSA SAFER", 4], ["DAT Directory", 2], ["Truckstop", 2], ["LinkedIn", 1]],
    EU: [["TIMOCOM", 4], ["Trans.eu", 3], ["LinkedIn", 1]],
  },
  Shipper: {
    US: [["ThomasNet", 3], ["Google Maps", 3], ["Import records", 2], ["LinkedIn", 1.5]],
    EU: [["Kompass", 3], ["EU Business Register", 3], ["Google Maps", 2], ["LinkedIn", 1]],
  },
  Forwarder: {
    US: [["FMCSA SAFER", 2], ["LinkedIn", 2], ["Import records", 1], ["Google Maps", 1]],
    EU: [["TIMOCOM", 2], ["Kompass", 2], ["EU Business Register", 1], ["LinkedIn", 1]],
  },
}

export const SOURCE_LIST: Record<Region, LeadSource[]> = {
  US: ["FMCSA SAFER", "DAT Directory", "Truckstop", "ThomasNet", "Import records", "Google Maps", "LinkedIn"],
  EU: ["TIMOCOM", "Trans.eu", "Kompass", "EU Business Register", "Google Maps", "LinkedIn"],
}

/** Trailer types each industry actually ships in. */
const INDUSTRY_EQUIPMENT: Record<string, Record<Region, Equipment[]>> = {
  "Food & beverage": { US: ["Reefer", "Dry Van"], EU: ["Reefer", "Box Trailer"] },
  "Building materials": { US: ["Flatbed", "Step Deck"], EU: ["Tautliner", "Mega Trailer"] },
  "Steel & metals": { US: ["Flatbed", "Step Deck"], EU: ["Tautliner"] },
  "Automotive parts": { US: ["Dry Van"], EU: ["Mega Trailer", "Tautliner"] },
  "Retail & consumer goods": { US: ["Dry Van"], EU: ["Box Trailer", "Tautliner"] },
  "Paper & packaging": { US: ["Dry Van"], EU: ["Tautliner", "Mega Trailer"] },
  "Chemicals & plastics": { US: ["Dry Van", "Flatbed"], EU: ["Tautliner", "Box Trailer"] },
  Agriculture: { US: ["Reefer", "Flatbed"], EU: ["Reefer", "Tautliner"] },
}

/** A crawled company that needs transport, without its score (scored against the lookalike profile). */
export function makeLead(rng: Rng, n: number, used: Set<string>, discoveredAt: Date): Omit<Lead, "score"> {
  const kind = weighted<LeadKind>(rng, [["Broker", 45], ["Shipper", 40], ["Forwarder", 15]])
  const region: Region = "US"
  void chance
  const hq = fitWeightedCity(rng, region, 1.2)
  const industry = kind === "Shipper" ? pick(rng, INDUSTRIES) : undefined
  const { name, contact, registration } = makeCompany(rng, hq, used, kind, industry)
  const size = weighted(rng, [["Small", 5], ["Mid-size", 3.5], ["Enterprise", 1.2]] as const)
  // brokers move many loads; a shipper books its own truckloads
  const monthlyLoads =
    kind === "Shipper"
      ? size === "Small" ? int(rng, 8, 40) : size === "Mid-size" ? int(rng, 30, 180) : int(rng, 150, 900)
      : size === "Small" ? int(rng, 20, 120) : size === "Mid-size" ? int(rng, 100, 600) : int(rng, 500, 3000)
  const eqPool = industry ? INDUSTRY_EQUIPMENT[industry.name][region] : undefined
  return {
    id: `L${String(n).padStart(5, "0")}`,
    kind,
    industry: industry?.name,
    name,
    region,
    country: hq.code,
    hq: cityLabel(hq),
    zone: hq.zone,
    registration,
    contact,
    equipment: eqPool ? sampleN(rng, eqPool, int(rng, 1, eqPool.length)) : makeEquipment(rng, region),
    lanes: makeLanes(rng, hq, 1.2, int(rng, 2, 4)),
    size,
    monthlyLoads,
    source: weighted(rng, SOURCES[kind][region]),
    discoveredAt: discoveredAt.toISOString(),
    emailVerified: chance(rng, 0.84),
  }
}
