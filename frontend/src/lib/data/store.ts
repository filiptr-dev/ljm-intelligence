import { getAI } from "@/lib/ai"
import { buildAnalytics } from "@/lib/analytics"
import { buildCampaignHistory } from "@/lib/campaigns/history"
import { buildEmailHistory } from "@/lib/campaigns/email-history"
import { lookalikeScore } from "@/lib/analytics/similarity"
import { generateDataset } from "./generate"
import { makeLead } from "./leads"
import { createRng } from "./rng"
import { DEMO_NOW, type Lead } from "./types"

const LEAD_POOL = 340

async function build() {
  const ai = getAI()
  const { brokers, emails } = generateDataset()
  const insights = await ai.analyzeEmails(emails)
  const analytics = buildAnalytics(brokers, emails, insights)

  // crawler results from the last 14 days, newest first
  const rng = createRng(4242)
  const used = new Set(brokers.map((b) => b.name))
  const leads: Lead[] = Array.from({ length: LEAD_POOL }, (_, i) => {
    const ago = Math.pow(rng(), 1.6) * 14 * 86_400_000
    const lead = makeLead(rng, i + 1, used, new Date(DEMO_NOW.getTime() - ago))
    return { ...lead, score: lookalikeScore(lead, analytics.profile) }
  }).sort((a, b) => b.discoveredAt.localeCompare(a.discoveredAt))

  return {
    ai: { name: ai.name, model: ai.model },
    brokers,
    brokerMap: new Map(brokers.map((b) => [b.id, b])),
    emails,
    insights,
    insightMap: new Map(insights.map((i) => [i.emailId, i])),
    leads,
    knownNames: [...used],
    campaignHistory: buildCampaignHistory(brokers, analytics.statsById),
    emailHistory: buildEmailHistory(brokers, analytics.statsById, leads),
    ...analytics,
  }
}

export type Store = Awaited<ReturnType<typeof build>>

let cache: Promise<Store> | undefined
export const getStore = () => (cache ??= build())
