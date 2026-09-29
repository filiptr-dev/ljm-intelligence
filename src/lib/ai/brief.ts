import { CITIES, cityLabel } from "@/lib/data/geo"
import type { OutreachDraft, OutreachInput, OutreachTone } from "./types"

/**
 * Mock of "describe the email you want": pulls the facts out of a plain-English
 * brief (trailer, lanes, offer, season, call to action, length, tone) and writes
 * the email from them. With Gemini the same brief goes straight into the prompt.
 */

const EQUIPMENT_WORDS: [RegExp, string][] = [
  [/reefer|refrigerated|frigo|temperature/, "Reefer"],
  [/flatbed/, "Flatbed"],
  [/step ?deck/, "Step Deck"],
  [/dry ?van/, "Dry Van"],
  [/tautliner|curtain/, "Tautliner"],
  [/mega/, "Mega Trailer"],
  [/box trailer/, "Box Trailer"],
]
const FEATURES: [RegExp, string][] = [
  [/hazmat|adr|dangerous/, "hazmat-certified drivers"],
  [/team driver/, "team drivers for urgent loads"],
  [/tracking|gps|live location/, "live GPS tracking on every load"],
  [/24\/7|weekend|around the clock/, "24/7 dispatch, weekends included"],
  [/customs|border|cmr|t1/, "border and customs paperwork handled for you"],
  [/on[- ]time|reliab/, "98% on-time delivery"],
]
const SEASONS: [RegExp, string][] = [
  [/produce|harvest/, "produce season"],
  [/christmas|holiday/, "the holiday rush"],
  [/black friday/, "Black Friday"],
  [/q4|fourth quarter/, "Q4"],
  [/summer/, "summer"],
  [/peak/, "peak season"],
]

export function draftFromBrief(input: OutreachInput): OutreachDraft {
  const raw = input.brief ?? ""
  const b = raw.toLowerCase()
  const understood: string[] = []

  let tone: OutreachTone = input.tone
  if (/friendly|casual|warm|relaxed/.test(b)) tone = "friendly"
  else if (/formal|professional|polite/.test(b)) tone = "professional"
  else if (/direct|straight|to the point|urgent|pushy/.test(b)) tone = "direct"
  understood.push(`Tone: ${tone[0].toUpperCase()}${tone.slice(1)}`)

  const eqs = [...new Set(EQUIPMENT_WORDS.filter(([re]) => re.test(b)).map(([, e]) => e))]
  const eq = eqs.length ? eqs.join(" and ") : "{{equipment}}"
  if (eqs.length) understood.push(`Trailer: ${eqs.join(", ")}`)

  const cities = CITIES.filter((c) => new RegExp(`\\b${c.name.toLowerCase()}\\b`).test(b))
    .sort((x, y) => b.indexOf(x.name.toLowerCase()) - b.indexOf(y.name.toLowerCase()))
  const lane = cities.length >= 2 ? `${cityLabel(cities[0])} → ${cityLabel(cities[1])}` : cities.length === 1 ? `lanes out of ${cityLabel(cities[0])}` : "{{lane}}"
  if (cities.length) understood.push(`Lane: ${lane}`)

  const trucks = b.match(/(\d+)\s+(?:new\s+|extra\s+|more\s+)?(?:[a-z-]+\s+)?(?:trucks|units|tractors|reefers|flatbeds|trailers)\b/)
  if (trucks) understood.push(`News: ${trucks[1]} new trucks`)
  const discount = b.match(/(\d{1,2})\s?%/)
  const trial = /trial|test load|first load|try us/.test(b)
  if (discount) understood.push(`Offer: ${discount[1]}% off the first load`)
  else if (trial) understood.push("Offer: trial load")
  const season = SEASONS.find(([re]) => re.test(b))?.[1]
  if (season) understood.push(`Season: ${season}`)
  const features = FEATURES.filter(([re]) => re.test(b)).map(([, f]) => f)
  features.forEach((f) => understood.push(`Selling point: ${f}`))
  const cta = /call|meeting|meet|talk|phone/.test(b) ? "call" : /quote|rate|price|pricing/.test(b) ? "quote" : "reply"
  understood.push(`Ask: ${cta === "call" ? "book a short call" : cta === "quote" ? "send a lane to quote" : "reply to the email"}`)
  const short = /short|brief|quick|concise|few lines|two lines|2 lines/.test(b)
  if (short) understood.push("Length: short")
  const shipper = input.campaign === "shipper_direct" || /shipper|manufactur|factory|direct customer|no broker/.test(b)
  if (shipper) understood.push("Audience: direct shippers")

  const opener = { professional: "Hi {{first_name}},", friendly: "Hey {{first_name}}, hope your week is going well!", direct: "{{first_name}}," }[tone]
  const where = lane === "{{lane}}" ? "on {{lane}}" : lane.startsWith("lanes") ? `on ${lane}` : `on ${lane}`

  const subject = trucks
    ? `${trucks[1]} new ${eqs[0] ?? ""} trucks ${lane === "{{lane}}" ? "for {{company}}" : where}`.replace(/\s+/g, " ")
    : discount
      ? `${discount[1]}% off your first load with LJM International`
      : season
        ? `${season[0].toUpperCase()}${season.slice(1)} capacity for {{company}}`
        : trial
          ? "Try LJM International with one trial load"
          : `${eq} capacity ${lane === "{{lane}}" ? "for {{company}}" : where}`

  const lines: string[] = []
  if (!short) lines.push("I'm {{sender}} from LJM International, a dry-van carrier based in Lincoln Park, NJ, running the eastern US.")
  lines.push(
    trucks
      ? `We just added ${trucks[1]} ${eqs[0] ?? ""} trucks and they run ${where} every week.`.replace(/\s+/g, " ")
      : `We run ${eq} trucks ${where} every week and have capacity available now.`,
  )
  if (season) lines.push(`With ${season} coming up, we've kept trucks free for new partners so you're not stuck without capacity.`)
  if (discount) lines.push(`As a new partner you get ${discount[1]}% off your first load.`)
  else if (trial) lines.push("We're happy to start with a single trial load so you can see our service first.")
  if (shipper && !short) lines.push("Working with us directly means our own drivers, one point of contact and no broker margin on top of the rate.")
  if (features.length) lines.push(`You also get ${features.join(", ")}.`)
  lines.push(
    cta === "call"
      ? tone === "direct" ? "Can we talk for 15 minutes this week?" : "Would you have 15 minutes for a quick call this week?"
      : cta === "quote"
        ? "Send me a lane and I'll quote within 15 minutes."
        : tone === "friendly" ? "Just hit reply if there's anything we can help with!" : "Reply to this email and we'll take it from there.",
  )

  const body = [opener, short ? lines.join(" ") : lines.join("\n\n"), "Best regards,\n{{sender}}"].join("\n\n")
  return { subject, body, understood }
}
