import { ToolPlaceholder } from "@/components/app/tool-placeholder"

export default function CallListPage() {
  return (
    <ToolPlaceholder
      eyebrow="Call List"
      title="Your daily call queue"
      description="A prioritised call queue built from crawled leads — score, new authority, days since last touch — with click-to-call and outcome logging."
      whatItWillDo={[
        "Rank tomorrow's top 25 calls from the crawler's leads for you every morning.",
        "Show why each broker is on the list (new authority, hot score, stale relationship).",
        "Let you tap to call and log the outcome in one step (booked / not now / dead).",
        "Feed outcomes back into the AI so the next day's list gets sharper.",
      ]}
      data={[
        "Crawled leads (Neon `leads`) with phone number and current AI score",
        "FMCSA add_date to flag freshly-issued MC/DOT numbers",
        "Last-touch age from `sent_log` (or the campaigns store)",
        "Suppression list — anyone unsubscribed is filtered out",
      ]}
    />
  )
}
