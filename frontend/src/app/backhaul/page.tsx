import { ToolPlaceholder } from "@/components/app/tool-placeholder"

export default function BackhaulPage() {
  return (
    <ToolPlaceholder
      eyebrow="Backhaul Finder"
      title="Don't come back empty"
      description="Given a delivery city, surface brokers and shippers nearby who might have your return load."
      whatItWillDo={[
        "Type in your drop city and see ranked backhaul options within ~150 miles.",
        "Prioritise loads heading back toward your home region.",
        "Filter by equipment, pickup date and minimum rate.",
        "Draft an intro email in one click via the shared builder.",
      ]}
      data={[
        "Delivery city + drop date + equipment",
        "Crawled leads within ~150mi of the drop, by state + city (great-circle)",
        "Match score prioritising lanes that head back toward your home region",
        "Contact fitness — has phone / has verified email / not recently contacted",
      ]}
    />
  )
}
