import { CampaignDetail } from "@/components/app/campaign-detail"
import { getStore } from "@/lib/data/store"

export default async function CampaignPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  const s = await getStore()
  // past campaigns come from the server; campaigns sent in this browser live in the engine
  const history = s.campaignHistory.find((c) => c.id === id) ?? null
  return <CampaignDetail id={id} history={history} />
}
