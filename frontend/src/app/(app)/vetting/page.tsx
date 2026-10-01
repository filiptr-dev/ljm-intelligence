import { ToolPlaceholder } from "@/components/app/tool-placeholder"

export default function VettingPage() {
  return (
    <ToolPlaceholder
      eyebrow="Broker Check"
      title="Vet a broker before you haul"
      description="Authority age, status, MC/DOT lookup and fraud red flags — pulled straight from FMCSA public records, not a paywalled tool."
      whatItWillDo={[
        "Look up any broker by MC or DOT and show authority status in one screen.",
        "Highlight red flags: brand-new authority, no phone, out-of-service history.",
        "Cross-reference LJM's suppression + prior-contact history.",
        "One-click 'safe to haul' summary for dispatchers.",
      ]}
      data={[
        "FMCSA Company Census: authority status, add_date, operating status",
        "SAFER out-of-service flags",
        "Suppression + prior sent history from LJM's crawler store",
        "Simple red-flag heuristics (new authority + no phone + flag mismatch)",
      ]}
    />
  )
}
