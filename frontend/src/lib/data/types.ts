import type { Region } from "./geo"

export const US_EQUIPMENT = ["Dry Van", "Reefer", "Flatbed", "Step Deck"] as const
export const EU_EQUIPMENT = ["Tautliner", "Mega Trailer", "Reefer", "Box Trailer"] as const
export const EQUIPMENT = ["Dry Van", "Reefer", "Flatbed", "Step Deck", "Tautliner", "Mega Trailer", "Box Trailer"] as const
export type Equipment = (typeof EQUIPMENT)[number]

export type Persona = "loyal" | "price_shopper" | "dormant" | "growing" | "occasional" | "difficult"

export type Lane = { origin: string; destination: string }

export type Contact = { name: string; email: string; phone: string; title: string }

export type Broker = {
  id: string
  name: string
  region: Region
  country: string
  hq: string
  zone: string
  registration: string // MC number (US) or VAT ID (EU)
  contact: Contact
  equipment: Equipment[]
  lanes: Lane[]
  size: "Small" | "Mid-size" | "Enterprise"
  /** ground truth used only by the generator, never by the analysis */
  persona: Persona
}

export type Email = {
  id: string
  threadId: string
  brokerId: string
  direction: "in" | "out"
  from: string
  to: string
  subject: string
  body: string
  sentAt: string
}

export type LeadSource =
  | "FMCSA SAFER"
  | "DAT Directory"
  | "Truckstop"
  | "LinkedIn"
  | "Google Maps"
  | "TIMOCOM"
  | "Trans.eu"
  | "EU Business Register"
  | "ThomasNet"
  | "Kompass"
  | "Import records"

/** Everyone who needs a truck: brokers, companies shipping their own freight, forwarders / 3PLs. */
export const LEAD_KINDS = ["Broker", "Shipper", "Forwarder"] as const
export type LeadKind = (typeof LEAD_KINDS)[number]
export const LEAD_KIND_LABEL: Record<LeadKind, string> = {
  Broker: "Freight broker",
  Shipper: "Direct shipper",
  Forwarder: "Forwarder / 3PL",
}

export type Lead = {
  id: string
  kind: LeadKind
  industry?: string
  name: string
  region: Region
  country: string
  hq: string
  zone: string
  registration: string
  contact: Contact
  equipment: Equipment[]
  lanes: Lane[]
  size: Broker["size"]
  monthlyLoads: number
  source: LeadSource
  discoveredAt: string
  emailVerified: boolean
  score: number
}

/**
 * The demo client. Real company (LJM International, Lincoln Park NJ) — treat
 * the address/phone/email as public site data, not as something to contact
 * without consent. `dispatcher` is a plausible demo name, clearly labelled as
 * demo copy in the sidebar; NOT asserted as a real employee.
 * No fleet-truck count on purpose: stating "N trucks" as fact about a real
 * client is the kind of small lie that lands wrong in a first meeting.
 */
export const CLIENT = {
  company: "LJM International",
  legalName: "LJM International",
  dispatcher: "Nick Rivera",
  email: "safety@ljminternational.com",
  phone: "862-203-4274",
  address: "22 Troy Lane, Lincoln Park, NJ 07035",
  website: "ljminternational.com",
  founded: 2017,
  fleet: "long-haul dry van · eastern US",
}

/** "Now", truncated to the hour, so history always ends today; 18 months of data. */
export const DEMO_NOW = (() => {
  const d = new Date()
  d.setUTCMinutes(0, 0, 0)
  return d
})()
export const DATA_START = (() => {
  const d = new Date(DEMO_NOW)
  d.setUTCMonth(d.getUTCMonth() - 18, 1)
  d.setUTCHours(0, 0, 0, 0)
  return d
})()
