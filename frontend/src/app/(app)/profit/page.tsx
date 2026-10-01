import { ToolPlaceholder } from "@/components/app/tool-placeholder"

export default function ProfitPage() {
  return (
    <ToolPlaceholder
      eyebrow="Load Profit Calculator"
      title="Should you take this load?"
      description="Rate minus fuel, driver pay, tolls and deadhead — the margin, and a clear go/no-go verdict."
      whatItWillDo={[
        "Punch in a rate offer and get the net margin in seconds.",
        "Live diesel + your truck's MPG feed the fuel line automatically.",
        "Warn on tight margins and factor in deadhead + tolls per corridor.",
        "Save wins to sharpen the baseline for future loads.",
      ]}
      data={[
        "Rate offered · miles · equipment",
        "Live diesel by PADD · truck MPG default (6.5, override)",
        "Driver pay per loaded mile + deadhead miles",
        "Tolls estimate for the corridor (I-95, I-80, PA Turnpike)",
      ]}
    />
  )
}
