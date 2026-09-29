import type { EmailInsight, RejectionReason } from "@/lib/ai/types"
import { marketRate } from "@/lib/data/generate"
import type { Region } from "@/lib/data/geo"
import { DATA_START, DEMO_NOW, type Broker, type Email, type Equipment } from "@/lib/data/types"
import { kmeans, standardize } from "./kmeans"
import { buildProfile, type LookalikeProfile } from "./similarity"

const DAY = 86_400_000
const now = DEMO_NOW.getTime()

export type Outcome = "booked" | "rejected" | "declined" | "no_reply" | "pending"

export type Thread = {
  id: string
  brokerId: string
  region: Region
  start: string
  pitchAt?: string
  pitchReplied: boolean
  offerAt?: string
  offerPerUnit?: number
  quoteRate?: number
  quotePerUnit?: number
  lane?: string
  equipment?: Equipment
  responseMin?: number
  outcome: Outcome
  reason?: RejectionReason
}

export const SEGMENTS = ["Core partners", "Growing", "Price shoppers", "Dormant", "Occasional"] as const
export type Segment = (typeof SEGMENTS)[number]

export const REASON_LABELS: Record<RejectionReason, string> = {
  rate_too_high: "Rate too high",
  already_covered: "Already covered",
  other_carrier: "Went with another carrier",
  timing: "Pickup / delivery timing",
  equipment: "Equipment mismatch",
  compliance: "Insurance / paperwork",
}

export type BrokerStats = {
  id: string
  emails: number
  threads: number
  booked: number
  rejected: number
  declined: number
  winRate: number
  revenue: number
  avgPerUnit: number
  rateIndex: number
  avgResponseMin: number
  firstContact: string
  lastContact: string
  daysSinceLast: number
  recentShare: number
  sentiment: number
  complaints: number
  paymentIssues: number
  praise: number
  topLane?: string
  topReason?: RejectionReason
  reasons: Partial<Record<RejectionReason, number>>
  monthly: number[] // threads per month, last 12 months
  health: number
  healthDelta: number
  segment: Segment
}

const monthKey = (iso: string) => iso.slice(0, 7)
const avg = (xs: number[]) => (xs.length ? xs.reduce((s, v) => s + v, 0) / xs.length : 0)
const median = (xs: number[]) => {
  if (!xs.length) return 0
  const s = [...xs].sort((a, b) => a - b)
  return s[Math.floor(s.length / 2)]
}
const mode = <T,>(xs: T[]) => {
  const c = new Map<T, number>()
  xs.forEach((x) => c.set(x, (c.get(x) ?? 0) + 1))
  return [...c.entries()].sort((a, b) => b[1] - a[1])[0]?.[0]
}

function buildThreads(emails: Email[], byId: Map<string, EmailInsight>, brokers: Map<string, Broker>) {
  const groups = new Map<string, Email[]>()
  for (const e of emails) {
    const g = groups.get(e.threadId)
    if (g) g.push(e)
    else groups.set(e.threadId, [e])
  }
  const threads: Thread[] = []
  for (const [id, list] of groups) {
    const ins = list.map((e) => byId.get(e.id)!)
    const find = (i: EmailInsight["intent"]) => list.findIndex((_, k) => ins[k].intent === i)
    const pitch = find("capacity_offer")
    const offer = find("load_offer")
    const quote = find("quote")
    const booked = find("booked")
    const rejected = find("rejected")
    const declined = find("declined")
    const lane = ins[offer >= 0 ? offer : 0].lane
    const outcome: Outcome =
      booked >= 0 ? "booked" : rejected >= 0 ? "rejected" : declined >= 0 ? "declined" : offer < 0 ? "no_reply" : "pending"
    threads.push({
      id,
      brokerId: list[0].brokerId,
      region: brokers.get(list[0].brokerId)!.region,
      start: list[0].sentAt,
      pitchAt: pitch >= 0 ? list[pitch].sentAt : undefined,
      pitchReplied: pitch >= 0 && offer >= 0,
      offerAt: offer >= 0 ? list[offer].sentAt : undefined,
      offerPerUnit: offer >= 0 ? ins[offer].perUnit : undefined,
      quoteRate: quote >= 0 ? ins[quote].rate : undefined,
      quotePerUnit: quote >= 0 ? ins[quote].perUnit : undefined,
      lane: lane ? `${lane.origin} → ${lane.destination}` : undefined,
      equipment: ins[offer >= 0 ? offer : 0].equipment,
      responseMin:
        offer >= 0 && (quote >= 0 || declined >= 0)
          ? (new Date(list[quote >= 0 ? quote : declined].sentAt).getTime() - new Date(list[offer].sentAt).getTime()) / 60000
          : undefined,
      outcome,
      reason: rejected >= 0 ? ins[rejected].rejectionReason : undefined,
    })
  }
  return threads
}

function healthScore(days: number, winRate: number, sentiment: number, threads: number, payment: number) {
  const recency = Math.exp(-days / 60)
  const volume = Math.min(1, threads / 12)
  const raw = 0.3 * recency + 0.25 * winRate + 0.15 * ((sentiment + 1) / 2) + 0.3 * volume
  return Math.max(0, Math.min(100, Math.round(raw * 100 - Math.min(15, payment * 5))))
}

export function buildAnalytics(brokers: Broker[], emails: Email[], insights: EmailInsight[]) {
  const byId = new Map(insights.map((i) => [i.emailId, i]))
  const brokerMap = new Map(brokers.map((b) => [b.id, b]))
  const threads = buildThreads(emails, byId, brokerMap)

  // ---------- per broker ----------
  const months12 = Array.from({ length: 12 }, (_, i) => {
    const d = new Date(DEMO_NOW)
    d.setUTCDate(1)
    d.setUTCMonth(d.getUTCMonth() - 11 + i)
    return d.toISOString().slice(0, 7)
  })

  const statsRaw = brokers.map((b) => {
    const ts = threads.filter((t) => t.brokerId === b.id)
    const es = emails.filter((e) => e.brokerId === b.id)
    const inbound = es.filter((e) => e.direction === "in").map((e) => byId.get(e.id)!)
    const all = es.map((e) => byId.get(e.id)!)
    const booked = ts.filter((t) => t.outcome === "booked")
    const rejected = ts.filter((t) => t.outcome === "rejected")
    const lastContact = es.length ? es[es.length - 1].sentAt : DEMO_NOW.toISOString()
    const daysSinceLast = Math.round((now - new Date(lastContact).getTime()) / DAY)
    const winRate = booked.length + rejected.length ? booked.length / (booked.length + rejected.length) : 0
    const sentiment = avg(inbound.map((i) => i.sentiment))
    const offers = ts.filter((t) => t.offerPerUnit && t.equipment)
    const rateIndex = avg(offers.map((t) => t.offerPerUnit! / marketRate(t.equipment!, b.region))) || 1
    const recent = ts.filter((t) => now - new Date(t.start).getTime() < 90 * DAY).length
    const paymentIssues = all.filter((i) => i.intent === "payment_issue").length / 2 // chase + reply
    const reasons: Partial<Record<RejectionReason, number>> = {}
    rejected.forEach((t) => t.reason && (reasons[t.reason] = (reasons[t.reason] ?? 0) + 1))
    const health = healthScore(daysSinceLast, winRate, sentiment, ts.length, paymentIssues)
    // health 90 days ago, for the trend arrow
    const cut = now - 90 * DAY
    const tsPrev = ts.filter((t) => new Date(t.start).getTime() < cut)
    const bPrev = tsPrev.filter((t) => t.outcome === "booked").length
    const rPrev = tsPrev.filter((t) => t.outcome === "rejected").length
    const lastPrev = tsPrev.length ? new Date(tsPrev[tsPrev.length - 1].start).getTime() : cut - 365 * DAY
    const prevHealth = healthScore(Math.round((cut - lastPrev) / DAY), bPrev + rPrev ? bPrev / (bPrev + rPrev) : 0, sentiment, tsPrev.length, 0)

    return {
      id: b.id,
      emails: es.length,
      threads: ts.length,
      booked: booked.length,
      rejected: rejected.length,
      declined: ts.filter((t) => t.outcome === "declined").length,
      winRate,
      revenue: booked.reduce((s, t) => s + (t.quoteRate ?? 0), 0),
      avgPerUnit: avg(booked.map((t) => t.quotePerUnit ?? 0).filter(Boolean)),
      rateIndex,
      avgResponseMin: avg(ts.map((t) => t.responseMin).filter((x): x is number => x !== undefined)),
      firstContact: es[0]?.sentAt ?? lastContact,
      lastContact,
      daysSinceLast,
      recentShare: ts.length ? recent / ts.length : 0,
      sentiment,
      complaints: all.filter((i) => i.intent === "complaint").length,
      paymentIssues,
      praise: all.filter((i) => i.intent === "praise").length,
      topLane: mode(booked.map((t) => t.lane).filter(Boolean) as string[]) ?? mode(ts.map((t) => t.lane).filter(Boolean) as string[]),
      topReason: mode(rejected.map((t) => t.reason).filter(Boolean) as RejectionReason[]),
      reasons,
      monthly: months12.map((m) => ts.filter((t) => monthKey(t.start) === m).length),
      health,
      healthDelta: tsPrev.length >= 3 ? health - prevHealth : 0,
      segment: "Occasional" as Segment,
    }
  })

  // ---------- segmentation: k-means on behaviour ----------
  // brokers with too little history can't be profiled; they are "Occasional" by rule
  const MIN_THREADS = 4
  const eligible = statsRaw.map((s, i) => ({ s, i })).filter(({ s }) => s.threads >= MIN_THREADS)
  const features = eligible.map(({ s }) => [
    Math.log1p(s.threads), s.winRate, s.rateIndex, Math.min(s.daysSinceLast, 300) / 100, s.recentShare, s.sentiment,
  ])
  const K = 4
  const { labels } = kmeans(standardize(features), K)
  const clusters = Array.from({ length: K }, (_, c) => {
    const members = eligible.filter((_, j) => labels[j] === c).map(({ s }) => s)
    return {
      c,
      days: avg(members.map((m) => m.daysSinceLast)),
      win: avg(members.map((m) => m.winRate)),
      recent: avg(members.map((m) => m.recentShare)),
    }
  })
  const assign = new Map<number, Segment>()
  const take = (seg: Segment, by: (x: (typeof clusters)[number]) => number) => {
    const rest = clusters.filter((x) => !assign.has(x.c))
    const best = rest.sort((a, b) => by(b) - by(a))[0]
    assign.set(best.c, seg)
  }
  take("Dormant", (x) => x.days)
  take("Price shoppers", (x) => -x.win)
  take("Growing", (x) => x.recent)
  take("Core partners", () => 0)
  const segOf = new Map<number, Segment>(eligible.map(({ i }, j) => [i, assign.get(labels[j])!]))
  const stats: BrokerStats[] = statsRaw.map((s, i) => ({ ...s, segment: segOf.get(i) ?? "Occasional" }))
  const statsById = new Map(stats.map((s) => [s.id, s]))

  // ---------- fleet-wide ----------
  const decided = threads.filter((t) => t.outcome === "booked" || t.outcome === "rejected")
  const winRateOf = (ts: Thread[]) => {
    const d = ts.filter((t) => t.outcome === "booked" || t.outcome === "rejected")
    return d.length ? d.filter((t) => t.outcome === "booked").length / d.length : 0
  }
  const within = (t: Thread, from: number, to: number) => {
    const x = now - new Date(t.start).getTime()
    return x >= from * DAY && x < to * DAY
  }
  const last90 = threads.filter((t) => within(t, 0, 90))
  const prev90 = threads.filter((t) => within(t, 90, 180))
  const revenue = (ts: Thread[], r: Region) =>
    ts.filter((t) => t.outcome === "booked" && t.region === r).reduce((s, t) => s + (t.quoteRate ?? 0), 0)
  const responseTimes = threads.map((t) => t.responseMin).filter((x): x is number => x !== undefined)

  const kpis = {
    emails: emails.length,
    brokers: brokers.length,
    activeBrokers: stats.filter((s) => s.daysSinceLast <= 60).length,
    threads: threads.length,
    booked: decided.filter((t) => t.outcome === "booked").length,
    rejected: decided.filter((t) => t.outcome === "rejected").length,
    winRate: winRateOf(threads),
    winRate90: winRateOf(last90),
    winRatePrev90: winRateOf(prev90),
    booked90: last90.filter((t) => t.outcome === "booked").length,
    bookedPrev90: prev90.filter((t) => t.outcome === "booked").length,
    revenueUSD: revenue(threads, "US"),
    revenueEUR: revenue(threads, "EU"),
    revenueUSD90: revenue(last90, "US"),
    revenueEUR90: revenue(last90, "EU"),
    medianResponseMin: median(responseTimes),
  }

  const monthsAll: string[] = []
  for (let d = new Date(DATA_START); d <= DEMO_NOW; d.setUTCMonth(d.getUTCMonth() + 1)) {
    monthsAll.push(d.toISOString().slice(0, 7))
  }
  const monthly = monthsAll.map((m) => {
    const ts = threads.filter((t) => monthKey(t.start) === m)
    const bookedUS = ts.filter((t) => t.outcome === "booked" && t.region === "US" && t.quotePerUnit)
    const bookedEU = ts.filter((t) => t.outcome === "booked" && t.region === "EU" && t.quotePerUnit)
    return {
      month: m,
      booked: ts.filter((t) => t.outcome === "booked").length,
      rejected: ts.filter((t) => t.outcome === "rejected").length,
      decided: ts.filter((t) => t.outcome === "booked" || t.outcome === "rejected").length,
      winRate: winRateOf(ts),
      usdPerMile: Number(avg(bookedUS.map((t) => t.quotePerUnit!)).toFixed(2)) || null,
      eurPerKm: Number(avg(bookedEU.map((t) => t.quotePerUnit!)).toFixed(2)) || null,
    }
  })

  const rejectedThreads = threads.filter((t) => t.outcome === "rejected" && t.reason)
  const reasons = (Object.keys(REASON_LABELS) as RejectionReason[])
    .map((r) => {
      const count = rejectedThreads.filter((t) => t.reason === r).length
      return { reason: r, label: REASON_LABELS[r], count, share: count / (rejectedThreads.length || 1) }
    })
    .sort((a, b) => b.count - a.count)

  const withName = (s: BrokerStats) => ({ ...s, name: brokerMap.get(s.id)!.name, region: brokerMap.get(s.id)!.region })
  const topPartners = [...stats].sort((a, b) => b.booked - a.booked).slice(0, 10).map(withName)
  const topRejectors = [...stats].filter((s) => s.rejected >= 3).sort((a, b) => b.rejected - a.rejected).slice(0, 10).map(withName)

  const buckets: [string, number, number][] = [["Under 30 min", 0, 30], ["30–60 min", 30, 60], ["1–2 hours", 60, 120], ["Over 2 hours", 120, Infinity]]
  const responseBuckets = buckets.map(([label, lo, hi]) => {
    const ts = decided.filter((t) => t.responseMin !== undefined && t.responseMin >= lo && t.responseMin < hi)
    return { label, quotes: ts.length, winRate: winRateOf(ts) }
  })

  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
  const HOURS: [string, number, number][] = [["6–9", 6, 9], ["9–12", 9, 12], ["12–15", 12, 15], ["15–19", 15, 19]]
  const pitches = threads.filter((t) => t.pitchAt)
  const heatmap = DAYS.map((day, di) => ({
    day,
    cells: HOURS.map(([label, lo, hi]) => {
      const ps = pitches.filter((t) => {
        const d = new Date(t.pitchAt!)
        return d.getUTCDay() === di + 1 && d.getUTCHours() >= lo && d.getUTCHours() < hi
      })
      return { hour: label, sent: ps.length, replyRate: ps.length ? ps.filter((t) => t.pitchReplied).length / ps.length : 0 }
    }),
  }))

  const laneMap = new Map<string, Thread[]>()
  threads.forEach((t) => t.lane && laneMap.set(t.lane, [...(laneMap.get(t.lane) ?? []), t]))
  const lanes = [...laneMap.entries()]
    .map(([lane, ts]) => ({
      lane,
      region: ts[0].region,
      loads: ts.filter((t) => t.outcome === "booked").length,
      offers: ts.length,
      winRate: winRateOf(ts),
      avgPerUnit: avg(ts.filter((t) => t.outcome === "booked" && t.quotePerUnit).map((t) => t.quotePerUnit!)),
    }))
    .sort((a, b) => b.loads - a.loads)
    .slice(0, 12)

  const segments = SEGMENTS.map((seg) => {
    const ms = stats.filter((s) => s.segment === seg)
    return {
      segment: seg,
      count: ms.length,
      winRate: avg(ms.map((m) => m.winRate)),
      booked: ms.reduce((s, m) => s + m.booked, 0),
      avgDaysSince: avg(ms.map((m) => m.daysSinceLast)),
    }
  })

  // lookalike profile: the brokers you'd want more of
  const best = stats.filter((s) => s.segment === "Core partners" || s.segment === "Growing")
  const profile: LookalikeProfile = buildProfile(
    best.map((s) => ({ entity: brokerMap.get(s.id)!, weight: 0.5 + s.health / 100 })),
  )

  // ---------- insight cards ----------
  const dormantValuable = stats
    .filter((s) => s.segment === "Dormant" && s.booked >= 3)
    .sort((a, b) => b.revenue - a.revenue)
  const fast = responseBuckets[0]
  const slow = responseBuckets[3]
  const cells = heatmap.flatMap((r) => r.cells.map((c) => ({ ...c, day: r.day }))).filter((c) => c.sent >= 8)
  const bestCell = [...cells].sort((a, b) => b.replyRate - a.replyRate)[0]
  const worstCell = [...cells].sort((a, b) => a.replyRate - b.replyRate)[0]
  const priceShoppers = stats.filter((s) => s.segment === "Price shoppers")
  const psRateShare = avg(priceShoppers.map((s) => (s.reasons.rate_too_high ?? 0) / (s.rejected || 1)))
  const payers = stats.filter((s) => s.paymentIssues >= 1).sort((a, b) => b.paymentIssues - a.paymentIssues)
  const dormantLastReasons = threads.filter((t) => statsById.get(t.brokerId)?.segment === "Dormant" && t.outcome === "rejected")
  const lostToCarrier = dormantLastReasons.filter((t) => t.reason === "other_carrier").length / (dormantLastReasons.length || 1)

  const highlights = [
    {
      id: "dormant",
      tone: "action" as const,
      title: `${dormantValuable.length} former regulars have gone quiet`,
      body: `They booked ${dormantValuable.reduce((s, x) => s + x.booked, 0)} loads with you before they stopped. ${Math.round(lostToCarrier * 100)}% of their last rejections were "went with another carrier", so they are buying capacity somewhere else.`,
      cta: { label: "Start re-engagement", href: "/outreach?audience=existing&segment=Dormant&campaign=reengage" },
      brokers: dormantValuable.slice(0, 3).map((s) => brokerMap.get(s.id)!.name),
    },
    {
      id: "speed",
      tone: "insight" as const,
      title: `Fast quotes win ${(fast.winRate / (slow.winRate || 1)).toFixed(1)}× more loads`,
      body: `Quotes sent within 30 minutes win ${Math.round(fast.winRate * 100)}% of loads. After 2 hours that drops to ${Math.round(slow.winRate * 100)}%. The median reply time today is ${Math.round(kpis.medianResponseMin)} minutes.`,
      cta: { label: "See response analysis", href: "/intelligence#speed" },
      brokers: [],
    },
    {
      id: "timing",
      tone: "insight" as const,
      title: `${bestCell.day} ${bestCell.hour}h is your best time to pitch`,
      body: `Capacity emails sent ${bestCell.day} between ${bestCell.hour}h get a reply ${Math.round(bestCell.replyRate * 100)}% of the time, compared with ${Math.round(worstCell.replyRate * 100)}% on ${worstCell.day} ${worstCell.hour}h. Auto-outreach is already scheduled for these peak slots.`,
      cta: { label: "View heatmap", href: "/intelligence#timing" },
      brokers: [],
    },
    {
      id: "price",
      tone: "warning" as const,
      title: `${priceShoppers.length} brokers mostly shop on price`,
      body: `${Math.round(psRateShare * 100)}% of their rejections are "rate too high". Offer them only backhauls, where an empty truck costs more than a lower rate.`,
      cta: { label: "Review price shoppers", href: "/brokers?segment=Price+shoppers" },
      brokers: priceShoppers.slice(0, 3).map((s) => brokerMap.get(s.id)!.name),
    },
    {
      id: "payment",
      tone: "warning" as const,
      title: `${payers.length} brokers have overdue invoices`,
      body: `Together they have ${payers.reduce((s, p) => s + p.paymentIssues, 0)} invoices more than 38 days past due. Think about quick-pay terms or asking for a deposit before the next load.`,
      cta: { label: "See brokers", href: "/brokers?sort=payment" },
      brokers: payers.slice(0, 3).map((s) => brokerMap.get(s.id)!.name),
    },
  ]

  return {
    threads, stats, statsById, kpis, monthly, reasons, topPartners, topRejectors,
    responseBuckets, heatmap, lanes, segments, profile, highlights,
  }
}

export type Analytics = ReturnType<typeof buildAnalytics>
