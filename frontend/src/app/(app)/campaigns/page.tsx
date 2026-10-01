import { CampaignsDashboard } from "@/components/app/campaigns-dashboard"
import { getStore } from "@/lib/data/store"

export default async function CampaignsPage() {
  const s = await getStore()
  return <CampaignsDashboard history={s.campaignHistory} />
}
