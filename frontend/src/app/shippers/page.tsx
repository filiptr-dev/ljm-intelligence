import { ToolPlaceholder } from "@/components/app/tool-placeholder"

export default function ShippersPage() {
  return (
    <ToolPlaceholder
      eyebrow="Shipper Finder"
      title="Direct shippers in your footprint"
      description="Companies that ship their own freight — FMCSA carship='S' plus OpenStreetMap Overpass candidates in LJM's 32-state region."
      whatItWillDo={[
        "Show every direct shipper in LJM's 32 in-region states, ranked by lane fit.",
        "Blend FMCSA-registered shippers with OSM industrial + warehouse candidates.",
        "Let you filter by state, equipment need and match score.",
        "One-click hand-off to the email builder for a direct-carrier pitch.",
      ]}
      data={[
        "Crawled shippers (Neon `leads` where kind = 'Shipper')",
        "OpenStreetMap Overpass — industrial + warehouse tags per state",
        "Region enforced server-side to LJM's 32 in-region states",
        "Match score against LJM's lane footprint",
      ]}
    />
  )
}
