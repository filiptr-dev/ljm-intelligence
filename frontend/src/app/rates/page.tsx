import { ToolPlaceholder } from "@/components/app/tool-placeholder"

export default function RatesPage() {
  return (
    <ToolPlaceholder
      eyebrow="Lane Rate Calculator"
      title="What should this lane pay?"
      description="Estimate $/mile for an origin → destination lane — distance, fuel, deadhead, equipment — and get a quote range you can trust."
      whatItWillDo={[
        "Give a low-mid-high $/mile estimate for any origin → destination pair.",
        "Account for current diesel by PADD region and equipment class.",
        "Compare against LJM's own past booked rates on the same lane.",
        "Copy the quote straight into the email builder as merge fields.",
      ]}
      data={[
        "Origin + destination (US city / state pair)",
        "Great-circle distance + a highway detour factor",
        "Live EIA weekly diesel price by PADD region",
        "Historical booked rates from `sent_log` where the lane matches",
      ]}
    />
  )
}
