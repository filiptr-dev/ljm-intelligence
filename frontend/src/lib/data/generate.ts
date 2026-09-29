import { CITIES, CLIENT_ZONES, cityLabel, citiesIn, roadDistance, type City, type Region } from "./geo"
import { makeCompany } from "./names"
import { between, chance, createRng, int, pick, sampleN, weighted, type Rng } from "./rng"
import {
  CLIENT, DATA_START, DEMO_NOW, EU_EQUIPMENT, US_EQUIPMENT,
  type Broker, type Email, type Equipment, type Lane, type Persona,
} from "./types"

const DAY = 86_400_000
const MIN = 60_000

/** Market rate per mile (US, USD) or per km (EU, EUR). */
export const MARKET_RATE: Record<Equipment, number> = {
  "Dry Van": 2.18, Reefer: 2.56, Flatbed: 2.74, "Step Deck": 2.92,
  Tautliner: 1.46, "Mega Trailer": 1.52, "Box Trailer": 1.41,
}
const EU_REEFER = 1.74
export const marketRate = (eq: Equipment, region: Region) =>
  region === "EU" && eq === "Reefer" ? EU_REEFER : MARKET_RATE[eq]

const PERSONA_WEIGHTS: [Persona, number][] = [
  ["loyal", 12], ["price_shopper", 17], ["dormant", 15],
  ["growing", 10], ["occasional", 38], ["difficult", 8],
]

export const fitWeightedCity = (rng: Rng, region: Region, bias: number) =>
  weighted(
    rng,
    citiesIn(region).map((c) => [c, 0.15 + (CLIENT_ZONES[c.zone] ?? 0) * bias] as [City, number]),
  )

export function makeLanes(rng: Rng, hq: City, bias: number, count: number): Lane[] {
  const lanes: Lane[] = []
  for (let i = 0; i < count; i++) {
    const origin = chance(rng, 0.45) ? hq : fitWeightedCity(rng, hq.region, bias)
    let dest = fitWeightedCity(rng, hq.region, bias)
    while (dest === origin) dest = pick(rng, citiesIn(hq.region))
    lanes.push({ origin: cityLabel(origin), destination: cityLabel(dest) })
  }
  // drop repeats after drawing, so the seeded sequence (and all demo numbers) stays the same
  return lanes.filter((l, i) => lanes.findIndex((x) => x.origin === l.origin && x.destination === l.destination) === i)
}

export function makeEquipment(rng: Rng, region: Region): Equipment[] {
  const list = region === "US" ? US_EQUIPMENT : EU_EQUIPMENT
  return sampleN(rng, list, weighted(rng, [[1, 4], [2, 4], [3, 2]]))
}

function makeBrokers(rng: Rng): Broker[] {
  const used = new Set<string>()
  const brokers: Broker[] = []
  for (let i = 0; i < 150; i++) {
    const region: Region = i < 90 ? "US" : "EU"
    const persona = weighted(rng, PERSONA_WEIGHTS)
    // brokers that work well with the client tend to run the client's lanes
    const bias = persona === "loyal" || persona === "growing" ? 4 : persona === "occasional" ? 0.6 : 1.5
    const hq = fitWeightedCity(rng, region, bias)
    const { name, contact, registration } = makeCompany(rng, hq, used)
    brokers.push({
      id: `b${String(i + 1).padStart(3, "0")}`,
      name,
      region,
      country: hq.code,
      hq: cityLabel(hq),
      zone: hq.zone,
      registration,
      contact,
      equipment: makeEquipment(rng, region),
      lanes: makeLanes(rng, hq, bias, int(rng, 2, 4)),
      size: weighted(rng, [["Small", 5], ["Mid-size", 4], ["Enterprise", 1.5]]),
      persona,
    })
  }
  return brokers
}

// ---------- email text ----------

type Ctx = {
  rng: Rng
  broker: Broker
  first: string
  lane: Lane
  eq: Equipment
  distance: number
  pickup: Date
}

const money = (v: number, region: Region) =>
  region === "US" ? `$${Math.round(v).toLocaleString("en-US")}` : `€${Math.round(v).toLocaleString("en-US")}`
const perUnit = (v: number, region: Region) =>
  region === "US" ? `$${v.toFixed(2)}/mi` : `€${v.toFixed(2)}/km`
const unit = (region: Region) => (region === "US" ? "mi" : "km")
const shortDate = (d: Date) =>
  d.toLocaleDateString("en-US", { month: "short", day: "2-digit", timeZone: "UTC" })
const sign = (ctx: Ctx) => `${ctx.first}\n${ctx.broker.name}\n${ctx.broker.contact.phone}`
const us = `${CLIENT.dispatcher}\nDispatch · ${CLIENT.company}\n${CLIENT.phone}`

function offerEmail(ctx: Ctx, rate: number) {
  const { rng, broker, lane, eq, distance, pickup } = ctx
  const r = broker.region
  const weight = r === "US" ? `${int(rng, 18, 44)},${int(rng, 100, 999)} lbs` : `${int(rng, 8, 24)} t`
  const openers = [
    `We have a ${eq} load ready`,
    `Got a ${eq} load that needs a truck`,
    `Looking for capacity on a ${eq} load`,
    `New ${eq} load available`,
  ]
  const asks = ["Can you cover?", "Do you have a truck for this?", "Let me know if you can take it.", "Please confirm availability."]
  return {
    subject: `Load offer: ${lane.origin} → ${lane.destination} · ${eq} · PU ${shortDate(pickup)}`,
    body: `Hi ${CLIENT.dispatcher.split(" ")[0]},\n\n${pick(rng, openers)}: ${lane.origin} → ${lane.destination}, pickup ${shortDate(pickup)}, ${weight}, ${distance} ${unit(r)}.\nWe can offer ${money(rate, r)} all-in (${perUnit(rate / distance, r)}). ${pick(rng, asks)}\n\nThanks,\n${sign(ctx)}`,
  }
}

function quoteEmail(ctx: Ctx, quote: number, offer: number) {
  const { rng, broker, lane, distance } = ctx
  const r = broker.region
  if (Math.abs(quote - offer) < 1) {
    return {
      subject: `Re: Load offer: ${lane.origin} → ${lane.destination}`,
      body: `Hi ${ctx.first},\n\nWe can cover it at ${money(quote, r)} (${perUnit(quote / distance, r)}). Truck is ready near ${lane.origin}, please send the rate confirmation.\n\n${us}`,
    }
  }
  const lines = [
    `Thanks for the offer. We have a truck near ${lane.origin} and can cover it for ${money(quote, r)} (${perUnit(quote / distance, r)}).`,
    `Our driver can load on time. Best we can do is ${money(quote, r)} (${perUnit(quote / distance, r)}) for this one.`,
    `We can take it at ${money(quote, r)} (${perUnit(quote / distance, r)}), fuel prices are up on this lane.`,
  ]
  return {
    subject: `Re: Load offer: ${lane.origin} → ${lane.destination}`,
    body: `Hi ${ctx.first},\n\n${pick(rng, lines)} Let me know.\n\n${us}`,
  }
}

export type RejectionReason =
  | "rate_too_high" | "already_covered" | "other_carrier" | "timing" | "equipment" | "compliance"

const REJECTION_TEXT: Record<RejectionReason, string[]> = {
  rate_too_high: [
    "That rate is too high for us, our customer won't pay that much.",
    "Sorry, we can't go that high on this lane.",
    "Your rate is above our budget for this load, we have to pass.",
  ],
  already_covered: [
    "This one is already covered, thanks anyway.",
    "We got it covered a few minutes ago, sorry.",
    "Load is already covered. Will reach out on the next one.",
  ],
  other_carrier: [
    "We went with another carrier on this one.",
    "Booked it with a different carrier who was already in the area.",
    "Another carrier took it, sorry.",
  ],
  timing: [
    "Pickup time doesn't work, we need the truck earlier.",
    "Delivery window is too tight with your ETA, we have to pass.",
    "We can't wait for your truck, shipper needs it loaded this morning.",
  ],
  equipment: [
    "We need a trailer with food-grade certification, yours doesn't match.",
    "Customer requires tarps and straps on board, trailer doesn't match the requirements.",
    "Need a team driver for this one, trailer and setup doesn't match.",
  ],
  compliance: [
    "Your insurance certificate doesn't meet our requirements, please update the carrier packet.",
    "Carrier packet is incomplete on our side, we can't book until the insurance is updated.",
    "Safety rating and insurance documents need to be updated before we can book.",
  ],
}

function outcomeEmail(ctx: Ctx, booked: boolean, reason: RejectionReason, ref: string, warm: boolean) {
  const { rng, broker, lane } = ctx
  const subject = `Re: Load offer: ${lane.origin} → ${lane.destination}`
  if (booked) {
    const lines =
      broker.region === "US"
        ? [`Booked. Rate confirmation attached for load #${ref}. Please send driver name and truck number.`, `Confirmed, load #${ref} is yours. Sending the rate con now.`]
        : [`Confirmed, transport order ${ref} attached. Please send truck plates and driver name.`, `Booked. Transport order ${ref} is attached, please confirm the loading slot.`]
    const extra = warm ? pick(rng, ["\nThanks, appreciate the quick response!", "\nGreat, thank you for the fast reply!", ""]) : ""
    return { subject, body: `Hi ${CLIENT.dispatcher.split(" ")[0]},\n\n${pick(rng, lines)}${extra}\n\n${sign(ctx)}` }
  }
  return { subject, body: `Hi ${CLIENT.dispatcher.split(" ")[0]},\n\n${pick(rng, REJECTION_TEXT[reason])}\n\n${sign(ctx)}` }
}

// ---------- behaviour model ----------

const WIN_RATE: Record<Persona, number> = {
  loyal: 0.74, price_shopper: 0.24, dormant: 0.52, growing: 0.58, occasional: 0.4, difficult: 0.5,
}
const OFFER_FACTOR: Record<Persona, number> = {
  loyal: 1.02, price_shopper: 0.86, dormant: 0.97, growing: 1.0, occasional: 0.95, difficult: 0.96,
}
const THREADS: Record<Persona, [number, number]> = {
  loyal: [10, 16], price_shopper: [6, 11], dormant: [6, 11], growing: [5, 10], occasional: [1, 4], difficult: [5, 9],
}

/** Reply likelihood of a cold capacity email by weekday / hour (UTC-ish business time). */
function replyPropensity(d: Date) {
  const day = d.getUTCDay()
  const h = d.getUTCHours()
  const dayF = [0.2, 0.75, 1, 1, 0.95, 0.55, 0.2][day]
  const hourF = h >= 7 && h <= 10 ? 1 : h >= 11 && h <= 14 ? 0.7 : h >= 15 && h <= 17 ? 0.45 : 0.2
  return dayF * hourF
}

function businessTime(rng: Rng, t: number) {
  const d = new Date(t)
  const day = d.getUTCDay()
  if (day === 0 || day === 6) {
    if (chance(rng, 0.85)) d.setUTCDate(d.getUTCDate() + (day === 6 ? 2 : 1))
  }
  d.setUTCHours(int(rng, 6, 18), int(rng, 0, 59), 0, 0)
  return d
}

/** Dispatcher response time in minutes: log-normal, median ≈ 45 min. */
const responseMinutes = (rng: Rng) => Math.max(4, Math.round(Math.exp(Math.log(45) + 0.95 * gauss(rng))))
function gauss(rng: Rng) {
  const u = Math.max(rng(), 1e-9)
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rng())
}
const speedFactor = (m: number) => (m < 30 ? 1.2 : m < 60 ? 1 : m < 120 ? 0.8 : 0.6)

export type Dataset = { brokers: Broker[]; emails: Email[] }

export function generateDataset(seed = 20260929): Dataset {
  const rng = createRng(seed)
  const brokers = makeBrokers(rng)
  const emails: Email[] = []
  let threadN = 0
  let refN = 48210
  let invN = 7100

  const push = (e: Omit<Email, "id">) => emails.push({ ...e, id: "" })

  for (const broker of brokers) {
    const p = broker.persona
    const first = broker.contact.name.split(" ")[0]
    let start = DATA_START.getTime() + between(rng, 0, 300) * DAY
    let end = DEMO_NOW.getTime() - between(rng, 0, 20) * DAY
    if (p === "dormant") {
      start = DATA_START.getTime() + between(rng, 0, 90) * DAY
      end = DEMO_NOW.getTime() - between(rng, 95, 240) * DAY
    }
    if (p === "growing") start = DEMO_NOW.getTime() - between(rng, 120, 190) * DAY
    const n = int(rng, ...THREADS[p])

    for (let k = 0; k < n; k++) {
      const u = p === "growing" ? Math.sqrt(rng()) : rng()
      const t0 = businessTime(rng, start + u * (end - start))
      if (t0.getTime() > DEMO_NOW.getTime() - 2 * 3600_000) continue
      const threadId = `t${String(++threadN).padStart(4, "0")}`
      const lane = pick(rng, broker.lanes)
      const eq = pick(rng, broker.equipment)
      const o = CITIES.find((c) => cityLabel(c) === lane.origin)!
      const d = CITIES.find((c) => cityLabel(c) === lane.destination)!
      const distance = roadDistance(o, d)
      const pickup = new Date(t0.getTime() + int(rng, 1, 3) * DAY)
      const ctx: Ctx = { rng, broker, first, lane, eq, distance, pickup }
      const r = broker.region
      const brokerAddr = broker.contact.email
      const base = { threadId, brokerId: broker.id }
      let t = t0.getTime()

      // 30% of threads start with the client pitching an empty truck
      if (chance(rng, 0.42)) {
        const city = o
        push({
          ...base, direction: "out", from: CLIENT.email, to: brokerAddr,
          subject: `Truck available: ${eq} in ${cityLabel(city)} · ${shortDate(pickup)}`,
          body: `Hi ${first},\n\nWe have a ${eq} empty in ${cityLabel(city)} on ${shortDate(pickup)}, looking for a load heading towards the ${d.zone} area. Anything you need covered?\n\n${us}`,
          sentAt: t0.toISOString(),
        })
        const pReply = replyPropensity(t0) * (p === "loyal" ? 0.95 : p === "price_shopper" ? 0.55 : 0.7)
        if (!chance(rng, pReply)) continue
        t += int(rng, 20, 300) * MIN
      }

      const offer = marketRate(eq, r) * OFFER_FACTOR[p] * between(rng, 0.92, 1.08) * distance
      push({ ...base, direction: "in", from: brokerAddr, to: CLIENT.email, ...offerEmail(ctx, offer), sentAt: new Date(t).toISOString() })

      const rt = responseMinutes(rng)
      t += rt * MIN
      if (chance(rng, 0.1)) {
        push({
          ...base, direction: "out", from: CLIENT.email, to: brokerAddr,
          subject: `Re: Load offer: ${lane.origin} → ${lane.destination}`,
          body: `Hi ${first},\n\nSorry, we don't have a truck available for that pickup. Keep us in mind for the next one.\n\n${us}`,
          sentAt: new Date(t).toISOString(),
        })
        continue
      }
      const premium = chance(rng, 0.25) ? 1 : between(rng, 1.03, 1.16)
      const quote = offer * premium
      push({ ...base, direction: "out", from: CLIENT.email, to: brokerAddr, ...quoteEmail(ctx, quote, offer), sentAt: new Date(t).toISOString() })

      // late-period dormant brokers drift to other carriers
      const drifting = p === "dormant" && t > end - 60 * DAY
      const winP = WIN_RATE[p] * speedFactor(rt) * (1 - (premium - 1) * 2.2) * (drifting ? 0.45 : 1)
      const booked = chance(rng, Math.min(0.95, winP))
      let reason: RejectionReason = "already_covered"
      if (!booked) {
        reason =
          p === "price_shopper" || premium > 1.1
            ? weighted(rng, [["rate_too_high", 7], ["already_covered", 1.5], ["other_carrier", 1.5]])
            : drifting
              ? weighted(rng, [["other_carrier", 6], ["rate_too_high", 2], ["timing", 1]])
              : weighted(rng, [["rate_too_high", 2.5], ["already_covered", 2.5], ["other_carrier", 2], ["timing", 2], ["equipment", 0.7], ["compliance", 0.5]])
      }
      const ref = String(++refN)
      t += int(rng, 15, 240) * MIN
      push({
        ...base, direction: "in", from: brokerAddr, to: CLIENT.email,
        ...outcomeEmail(ctx, booked, reason, ref, p === "loyal" || p === "growing"),
        sentAt: new Date(t).toISOString(),
      })
      if (!booked) continue

      // after delivery: invoice, payment chasing, feedback
      const delivered = pickup.getTime() + Math.ceil(distance / (r === "US" ? 550 : 700)) * DAY
      const inv = `INV-${++invN}`
      if (delivered < DEMO_NOW.getTime() && chance(rng, 0.55)) {
        push({
          ...base, direction: "out", from: CLIENT.email, to: brokerAddr,
          subject: `Invoice ${inv} · Load #${ref}`,
          body: `Hi ${first},\n\nPlease find attached invoice ${inv} for load #${ref} (${lane.origin} → ${lane.destination}), amount ${money(quote, r)}. POD attached. Payment terms 30 days.\n\n${us}`,
          sentAt: businessTime(rng, delivered + DAY).toISOString(),
        })
        const late = p === "difficult" ? 0.6 : 0.07
        const chaseAt = delivered + int(rng, 38, 55) * DAY
        if (chaseAt < DEMO_NOW.getTime() && chance(rng, late)) {
          const days = Math.round((chaseAt - delivered) / DAY)
          push({
            ...base, direction: "out", from: CLIENT.email, to: brokerAddr,
            subject: `Overdue: Invoice ${inv}`,
            body: `Hi ${first},\n\nFollowing up on invoice ${inv} for load #${ref}, it is now ${days} days past due. Please advise on the payment status.\n\n${us}`,
            sentAt: businessTime(rng, chaseAt).toISOString(),
          })
          push({
            ...base, direction: "in", from: brokerAddr, to: CLIENT.email,
            subject: `Re: Overdue: Invoice ${inv}`,
            body: `Hi ${CLIENT.dispatcher.split(" ")[0]},\n\nSorry for the delay, payment for invoice ${inv} is delayed on our side and scheduled for next week.\n\n${sign(ctx)}`,
            sentAt: businessTime(rng, chaseAt + int(rng, 1, 4) * DAY).toISOString(),
          })
        }
      }
      const fb = businessTime(rng, delivered + int(rng, 0, 2) * DAY)
      if (fb.getTime() < DEMO_NOW.getTime()) {
        if (chance(rng, p === "difficult" ? 0.3 : 0.05)) {
          push({
            ...base, direction: "in", from: brokerAddr, to: CLIENT.email,
            subject: `Issue on load #${ref}`,
            body: `Hi ${CLIENT.dispatcher.split(" ")[0]},\n\nDriver arrived ${int(rng, 2, 6)} hours late at delivery and our customer is not happy. Please make sure this doesn't happen again.\n\n${sign(ctx)}`,
            sentAt: fb.toISOString(),
          })
        } else if (chance(rng, p === "loyal" ? 0.22 : 0.05)) {
          push({
            ...base, direction: "in", from: brokerAddr, to: CLIENT.email,
            subject: `Load #${ref} delivered`,
            body: `Hi ${CLIENT.dispatcher.split(" ")[0]},\n\nGreat job on load #${ref}, driver was on time and very professional. We'll send more loads your way.\n\n${sign(ctx)}`,
            sentAt: fb.toISOString(),
          })
        }
      }
    }
  }

  const now = DEMO_NOW.toISOString()
  const kept = emails.filter((e) => e.sentAt <= now).sort((a, b) => a.sentAt.localeCompare(b.sentAt))
  kept.forEach((e, i) => (e.id = `e${String(i + 1).padStart(5, "0")}`))
  return { brokers, emails: kept }
}
