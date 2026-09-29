import { CAMPAIGN_TYPE_LABEL, POSITIVE, REPLY_LABEL, TONE_LABEL, type Campaign, type ReplyCategory } from "./types"

const DAY = 86_400_000
const CATEGORIES = Object.keys(REPLY_LABEL) as ReplyCategory[]

export type CampaignStatus = "Scheduled" | "Sending" | "Active" | "Completed"

export function summarize(c: Campaign, now = Date.now()) {
  const rs = c.recipients
  const delivered = rs.filter((r) => r.status !== "queued" && r.status !== "bounced").length
  const queued = rs.filter((r) => r.status === "queued").length
  const opened = rs.filter((r) => r.status === "opened" || r.status === "replied").length
  const replies = rs.filter((r) => r.reply)
  const positive = replies.filter((r) => POSITIVE.includes(r.reply!.category)).length
  const won = rs.filter((r) => r.won).length
  const loads = rs.reduce((s, r) => s + (r.loads ?? 0), 0)
  const byCategory = Object.fromEntries(CATEGORIES.map((k) => [k, replies.filter((r) => r.reply!.category === k).length])) as Record<ReplyCategory, number>
  const avgSentiment = replies.length ? replies.reduce((s, r) => s + r.reply!.sentiment, 0) / replies.length : 0

  const current = !c.goal ? 0 : c.goal.type === "replies" ? replies.length : c.goal.type === "positive" ? positive : c.goal.type === "accounts" ? won : loads
  const goal = c.goal ? { ...c.goal, current, pct: Math.min(1, current / (c.goal.target || 1)) } : undefined

  const scheduledAhead = c.schedule && c.schedule.at > now
  const status: CampaignStatus =
    scheduledAhead && queued === rs.length ? "Scheduled" : queued > 0 ? "Sending" : now - c.createdAt > 21 * DAY ? "Completed" : "Active"

  const maxStep = 1 + (c.followUps?.length ?? 0)
  const steps = Array.from({ length: maxStep }, (_, i) => {
    const step = i + 1
    const reached = rs.filter((r) => (r.step ?? 1) >= step && r.status !== "queued" && r.status !== "bounced").length
    const rep = replies.filter((r) => r.reply!.step === step).length
    return { step, delivered: reached, replies: rep, replyRate: reached ? rep / reached : 0 }
  })
  const nonResponders = rs.filter((r) => (r.status === "sent" || r.status === "opened") && !r.reply).length

  return {
    total: rs.length,
    delivered,
    queued,
    bounced: rs.filter((r) => r.status === "bounced").length,
    opened,
    replied: replies.length,
    positive,
    won,
    loads,
    openRate: delivered ? opened / delivered : 0,
    replyRate: delivered ? replies.length / delivered : 0,
    positiveRate: delivered ? positive / delivered : 0,
    byCategory,
    avgSentiment,
    goal,
    status,
    steps,
    nonResponders,
  }
}

export type CampaignSummary = ReturnType<typeof summarize>

type Group = { key: string; label: string; delivered: number; replies: number; positive: number; campaigns: number }

function groupBy(list: { c: Campaign; s: CampaignSummary }[], key: (c: Campaign) => string | undefined, label: (k: string) => string) {
  const m = new Map<string, Group>()
  for (const { c, s } of list) {
    const k = key(c)
    if (!k) continue
    const g = m.get(k) ?? { key: k, label: label(k), delivered: 0, replies: 0, positive: 0, campaigns: 0 }
    g.delivered += s.delivered
    g.replies += s.replied
    g.positive += s.positive
    g.campaigns++
    m.set(k, g)
  }
  return [...m.values()]
    .map((g) => ({ ...g, replyRate: g.delivered ? g.replies / g.delivered : 0, positiveRate: g.delivered ? g.positive / g.delivered : 0 }))
    .sort((a, b) => b.positiveRate - a.positiveRate)
}

/** What works across all campaigns: the numbers behind "reuse the successful ones". */
export function analyzeCampaigns(campaigns: Campaign[], now = Date.now()) {
  const list = campaigns.filter((c) => !c.auto || c.recipients.length).map((c) => ({ c, s: summarize(c, now) }))
  const withData = list.filter(({ s }) => s.delivered >= 10)

  const byType = groupBy(withData, (c) => c.type, (k) => CAMPAIGN_TYPE_LABEL[k] ?? k)
  const byTone = groupBy(withData, (c) => c.tone, (k) => TONE_LABEL[k as keyof typeof TONE_LABEL] ?? k)
  const byAudience = groupBy(
    withData.flatMap(({ c }) =>
      ["Broker", "Shipper", "Forwarder", "existing"].map((k) => {
        const rs = c.recipients.filter((r) => (k === "existing" ? r.kind === "broker" : r.kind === "lead" && r.companyType === k))
        return { c: { ...c, id: `${c.id}:${k}`, recipients: rs, type: k }, s: summarize({ ...c, recipients: rs }, now) }
      }),
    ),
    (c) => (c.recipients.length ? c.type : undefined),
    (k) => ({ Broker: "New brokers", Shipper: "Direct shippers", Forwarder: "Forwarders / 3PL", existing: "Existing brokers" })[k] ?? k,
  )
  const byDay = groupBy(
    withData,
    (c) => ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][new Date(c.schedule?.at ?? c.createdAt).getUTCDay()],
    (k) => k,
  )

  const subjects = [...withData]
    .sort((a, b) => b.s.positiveRate - a.s.positiveRate)
    .map(({ c, s }) => ({ id: c.id, subject: c.subject, name: c.name, type: c.type, tone: c.tone, positiveRate: s.positiveRate, replyRate: s.replyRate, delivered: s.delivered }))

  const later = withData.reduce((a, { s }) => a + s.steps.slice(1).reduce((x, st) => x + st.replies, 0), 0)
  const withFollowUp = withData.filter(({ c }) => (c.followUps?.length ?? 0) > 0)

  const allReplies = list.flatMap(({ c }) => c.recipients.filter((r) => r.reply).map((r) => ({ ...r.reply!, name: r.name, contactName: r.contactName, campaign: c.name, campaignId: c.id })))
  const byCategory = Object.fromEntries(CATEGORIES.map((k) => [k, allReplies.filter((r) => r.category === k).length])) as Record<ReplyCategory, number>

  const totals = list.reduce(
    (a, { s }) => ({
      delivered: a.delivered + s.delivered,
      opened: a.opened + s.opened,
      replied: a.replied + s.replied,
      positive: a.positive + s.positive,
      won: a.won + s.won,
      loads: a.loads + s.loads,
    }),
    { delivered: 0, opened: 0, replied: 0, positive: 0, won: 0, loads: 0 },
  )

  // written insights, computed from the numbers above
  const insights: { title: string; body: string; reuseId?: string }[] = []
  // compare like with like: cold campaigns to new companies only
  const coldTypes = byType.filter((g) => g.key === "new_leads" || g.key === "shipper_direct")
  if (coldTypes.length >= 2) {
    const [best, worst] = [coldTypes[0], coldTypes[coldTypes.length - 1]]
    insights.push({
      title: `For new customers, “${best.label}” works ${(best.positiveRate / (worst.positiveRate || 1)).toFixed(1)}× better`,
      body: `${Math.round(best.positiveRate * 100)}% of delivered emails get an interested reply, compared with ${Math.round(worst.positiveRate * 100)}% for “${worst.label}”. Companies that ship their own freight respond best to “no broker margin”.`,
      reuseId: subjects.find((x) => x.type === best.key)?.id,
    })
  }
  const dedicated = byType.find((g) => g.key === "dedicated_lane")
  if (dedicated) {
    insights.push({
      title: "Existing partners say yes to dedicated lanes",
      body: `${Math.round(dedicated.positiveRate * 100)}% of core partners replied with interest to a weekly dedicated-truck offer. Repeat it every quarter.`,
      reuseId: subjects.find((x) => x.type === "dedicated_lane")?.id,
    })
  }
  const fuStep1 = withFollowUp.reduce((a, { s }) => a + (s.steps[0]?.replies ?? 0), 0)
  if (withFollowUp.length && fuStep1) {
    insights.push({
      title: `Follow-ups bring ${Math.round((later / fuStep1) * 100)}% more replies`,
      body: `In campaigns with automatic follow-ups, ${later} of ${fuStep1 + later} replies came after a follow-up email, from people who ignored the first one. Keep at least one follow-up in every campaign.`,
    })
  }
  if (byTone.length >= 2) {
    insights.push({
      title: `${byTone[0].label} tone performs best`,
      body: `${Math.round(byTone[0].positiveRate * 100)}% interested replies with a ${byTone[0].label.toLowerCase()} tone, compared with ${Math.round(byTone[byTone.length - 1].positiveRate * 100)}% with a ${byTone[byTone.length - 1].label.toLowerCase()} tone.`,
    })
  }
  if (byDay.length >= 2) {
    insights.push({
      title: `Launch on ${byDay[0].label}, avoid ${byDay[byDay.length - 1].label}`,
      body: `Campaigns started on ${byDay[0].label} reach ${Math.round(byDay[0].replyRate * 100)}% replies, while ${byDay[byDay.length - 1].label} launches reach ${Math.round(byDay[byDay.length - 1].replyRate * 100)}%.`,
    })
  }

  return { list, byType, byTone, byAudience, byDay, subjects, allReplies, byCategory, totals, insights }
}
