import type { City } from "./geo"
import type { Contact } from "./types"
import { int, pick, type Rng } from "./rng"

const US_ROOTS = [
  "Summit", "Blue Ridge", "Lone Star", "Great Lakes", "Keystone", "Patriot", "Heartland", "Redline",
  "Ironhorse", "Northstar", "Pinnacle", "Crossroads", "Eagle", "Liberty", "Frontier", "Canyon",
  "Prairie", "Atlas", "Titan", "Horizon", "Cardinal", "Magnolia", "Gateway", "Coastal", "Big Sky",
  "Bluegrass", "Buckeye", "Hoosier", "Delta", "Evergreen", "Granite", "Arrow", "Anchor", "Beacon",
  "Compass", "Falcon", "Harbor", "Junction", "Legacy", "Meridian", "Mustang", "Pioneer", "Rapid",
  "Sentinel", "Sterling", "Trident", "Vanguard", "Wildcat", "Apex", "Cornerstone", "Firebird",
  "Golden Spike", "Longhorn", "Red River", "Sagebrush", "Tri-State", "Union", "Westbound",
  "Nationwide", "Priority", "Allied", "Premier", "Precision", "Silverline", "Blackrock", "Copper State",
  "Riverbend", "Stateline", "Timberline", "Highpoint", "Crescent", "Bison", "Ozark", "Cypress",
]
const US_SUFFIX = [
  "Logistics", "Freight", "Transportation", "Brokerage", "Freight Solutions", "Supply Chain",
  "Logistics Group", "Transport Services", "Cargo", "Freight Partners",
]
const US_LEGAL = ["LLC", "Inc.", "LLC", "Corp.", "LLC"]

const EU_ROOTS = [
  "Alpen", "Rhein", "Donau", "Nordwest", "Baltic", "Adria", "Balkan", "Eurocargo", "Contra", "Transalp",
  "Vistula", "Hansa", "Iberia", "Panonia", "Carpat", "Mosel", "Elbe", "Via", "Continental", "Silesia",
  "Tatra", "Vardar", "Morava", "Sava", "Danubia", "Helvetia", "Lombarda", "Veneto", "Rhone",
  "Atlantica", "Duna", "Orbis", "Strada", "Magistrala", "Euroline", "Intertrans", "Corridor", "Nova",
  "Alpina", "Mercator", "Polaris", "Kontinent", "Westrans", "Adriatik", "Sirius", "Primus",
]
const EU_FORMS: Record<string, string[]> = {
  DE: ["Spedition GmbH", "Logistik GmbH", "Transport GmbH & Co. KG"],
  AT: ["Logistik GmbH", "Spedition GmbH"],
  CH: ["Logistik AG", "Transport AG"],
  NL: ["Logistics B.V.", "Transport B.V."],
  BE: ["Logistics NV", "Transport BV"],
  PL: ["Logistics Sp. z o.o.", "Trans Sp. z o.o."],
  CZ: ["Logistic s.r.o.", "Trans s.r.o."],
  SK: ["Logistic s.r.o."],
  HU: ["Logisztika Kft.", "Trans Kft."],
  IT: ["Trasporti S.r.l.", "Logistica S.p.A."],
  FR: ["Transports SAS", "Logistique SARL"],
  ES: ["Logística S.L.", "Transportes S.A."],
  MK: ["Logistics DOOEL", "Trans DOO"],
  RS: ["Logistika DOO", "Trans DOO"],
  BG: ["Logistics EOOD", "Trans OOD"],
  GR: ["Logistics S.A."],
  HR: ["Logistika d.o.o."],
  SI: ["Logistika d.o.o."],
  RO: ["Logistic S.R.L.", "Trans S.R.L."],
}
const TLD: Record<string, string> = {
  DE: "de", AT: "at", CH: "ch", NL: "nl", BE: "be", PL: "pl", CZ: "cz", SK: "sk", HU: "hu",
  IT: "it", FR: "fr", ES: "es", MK: "mk", RS: "rs", BG: "bg", GR: "gr", HR: "hr", SI: "si", RO: "ro",
}

const DIAL: Record<string, number> = {
  DE: 49, AT: 43, CH: 41, NL: 31, BE: 32, PL: 48, CZ: 420, SK: 421, HU: 36, IT: 39, FR: 33,
  ES: 34, MK: 389, RS: 381, BG: 359, GR: 30, HR: 385, SI: 386, RO: 40,
}

const US_FIRST = ["Mike", "Sarah", "Jason", "Ashley", "Kevin", "Megan", "Brian", "Nicole", "Tyler", "Rachel", "Derek", "Amanda", "Chris", "Lauren", "Josh", "Kayla", "Travis", "Brittany", "Cody", "Jessica", "Ryan", "Danielle", "Marcus", "Tanya", "Luis", "Maria", "Andre", "Keisha"]
const US_LAST = ["Johnson", "Miller", "Garcia", "Wilson", "Anderson", "Thompson", "Martinez", "Robinson", "Clark", "Lewis", "Walker", "Hall", "Young", "King", "Wright", "Scott", "Green", "Baker", "Nelson", "Carter", "Mitchell", "Perez", "Roberts", "Turner", "Phillips", "Campbell", "Parker", "Evans"]
const EU_NAMES: Record<string, [string[], string[]]> = {
  DE: [["Lukas", "Anna", "Jonas", "Lena", "Felix", "Sophie", "Tobias", "Julia"], ["Müller", "Schmidt", "Fischer", "Weber", "Wagner", "Becker", "Hoffmann", "Koch"]],
  PL: [["Piotr", "Anna", "Tomasz", "Katarzyna", "Marek", "Agnieszka"], ["Kowalski", "Nowak", "Wiśniewski", "Wójcik", "Kamiński", "Lewandowski"]],
  IT: [["Marco", "Giulia", "Luca", "Francesca", "Andrea", "Chiara"], ["Rossi", "Bianchi", "Romano", "Colombo", "Ricci", "Ferrari"]],
  BALKAN: [["Stefan", "Ana", "Nikola", "Elena", "Marko", "Ivana", "Aleksandar", "Marija"], ["Petrovski", "Jovanović", "Nikolov", "Stojanovski", "Kovačević", "Dimitrov", "Popović", "Ivanov"]],
  OTHER: [["Thomas", "Emma", "Daniel", "Laura", "Martin", "Eva", "Pieter", "Clara"], ["Janssen", "Dubois", "Novák", "Horváth", "García", "Peeters", "Svoboda", "Moreau"]],
}
const TITLES = ["Carrier Sales Manager", "Freight Broker", "Capacity Manager", "Logistics Coordinator", "Carrier Relations", "Transport Planner", "Head of Operations"]

const slug = (s: string) =>
  s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]+/g, "")

function nameFor(rng: Rng, country: string, region: "US" | "EU") {
  if (region === "US") return [pick(rng, US_FIRST), pick(rng, US_LAST)]
  const key = ["DE", "AT", "CH"].includes(country)
    ? "DE"
    : country === "PL"
      ? "PL"
      : country === "IT"
        ? "IT"
        : ["MK", "RS", "BG", "HR", "SI", "GR", "RO"].includes(country)
          ? "BALKAN"
          : "OTHER"
  const [first, last] = EU_NAMES[key]
  return [pick(rng, first), pick(rng, last)]
}

const US_FORWARDER = ["Global Forwarding", "Worldwide Logistics", "International Shipping", "Freight Forwarding", "Supply Chain Solutions", "3PL Services"]
const EU_FORWARDER = ["Forwarding", "International Logistics", "Cargo Solutions", "3PL"]

export type Industry = { name: string; us: string[]; eu: string[] }
export const INDUSTRIES: Industry[] = [
  { name: "Food & beverage", us: ["Foods Inc.", "Beverage Co.", "Fresh Produce LLC", "Dairy Farms"], eu: ["Lebensmittel GmbH", "Food S.r.l.", "Foods Sp. z o.o.", "Prehrana d.o.o."] },
  { name: "Building materials", us: ["Building Supply", "Lumber Co.", "Concrete Products LLC"], eu: ["Baustoffe GmbH", "Holz GmbH", "Građevina d.o.o."] },
  { name: "Steel & metals", us: ["Steel Corp.", "Metal Works Inc.", "Fabrication LLC"], eu: ["Stahl AG", "Metal d.o.o.", "Metalli S.p.A."] },
  { name: "Automotive parts", us: ["Auto Parts Inc.", "Components LLC"], eu: ["Automotive GmbH", "Autokomponenty s.r.o."] },
  { name: "Retail & consumer goods", us: ["Distribution Center", "Home Goods Inc.", "Wholesale LLC"], eu: ["Handel GmbH", "Distribuzione S.r.l.", "Retail Sp. z o.o."] },
  { name: "Paper & packaging", us: ["Packaging Corp.", "Paper Mills Inc."], eu: ["Verpackung GmbH", "Packaging Sp. z o.o."] },
  { name: "Chemicals & plastics", us: ["Plastics LLC", "Chemical Co."], eu: ["Chemie GmbH", "Plast d.o.o."] },
  { name: "Agriculture", us: ["Farms LLC", "Grain Co-op", "Agri Supply"], eu: ["Agrar GmbH", "Agro d.o.o."] },
]
const SHIPPER_TITLES = ["Logistics Manager", "Shipping Manager", "Supply Chain Director", "Procurement Manager", "Transport Coordinator"]

export function makeCompany(rng: Rng, city: City, used: Set<string>, kind: "Broker" | "Shipper" | "Forwarder" = "Broker", industry?: Industry) {
  let name = ""
  for (let i = 0; i < 50; i++) {
    const us = city.region === "US"
    if (kind === "Shipper" && industry) {
      name = us ? `${pick(rng, US_ROOTS)} ${pick(rng, industry.us)}` : `${pick(rng, EU_ROOTS)} ${pick(rng, industry.eu)}`
    } else if (kind === "Forwarder") {
      name = us ? `${pick(rng, US_ROOTS)} ${pick(rng, US_FORWARDER)} ${pick(rng, US_LEGAL)}` : `${pick(rng, EU_ROOTS)} ${pick(rng, EU_FORWARDER)} ${(EU_FORMS[city.code] ?? ["GmbH"])[0].split(" ").pop()}`
    } else {
      name = us
        ? `${pick(rng, US_ROOTS)} ${pick(rng, US_SUFFIX)} ${pick(rng, US_LEGAL)}`
        : `${pick(rng, EU_ROOTS)} ${pick(rng, EU_FORMS[city.code] ?? ["Logistics"])}`
    }
    if (!used.has(name)) break
  }
  used.add(name)

  const brand = name.split(" ").slice(0, city.region === "US" ? 2 : 1).join("")
  const domain = `${slug(brand)}.${city.region === "US" ? "com" : TLD[city.code] ?? "eu"}`
  const [first, last] = nameFor(rng, city.code, city.region)
  const contact: Contact = {
    name: `${first} ${last}`,
    email: `${slug(first)}.${slug(last)}@${domain}`,
    phone:
      city.region === "US"
        ? `+1 (${int(rng, 201, 989)}) 555-${String(int(rng, 100, 9999)).padStart(4, "0")}`
        : `+${DIAL[city.code] ?? 49} ${int(rng, 10, 99)} ${int(rng, 100, 999)} ${int(rng, 1000, 9999)}`,
    title: pick(rng, kind === "Shipper" ? SHIPPER_TITLES : TITLES),
  }
  const registration =
    city.region === "US"
      ? kind === "Shipper"
        ? `DUNS-${int(rng, 10, 99)}${int(rng, 1000000, 9999999)}`
        : `${kind === "Forwarder" ? "FF" : "MC"}-${int(rng, 100000, 1499999)}`
      : `${city.code}${int(rng, 100000000, 999999999)}`
  return { name, contact, registration }
}
