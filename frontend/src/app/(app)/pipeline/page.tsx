import { ToolPlaceholder } from "@/components/app/tool-placeholder"

export default function PipelinePage() {
  return (
    <ToolPlaceholder
      eyebrow="Follow-ups"
      title="Move leads through your pipeline"
      description="A kanban of leads across New → Contacted → Replied → Booked, with reminder-based follow-ups so nothing falls through the cracks."
      whatItWillDo={[
        "Drag leads through four columns as they progress.",
        "Auto-schedule the next follow-up based on reply state.",
        "Nudge you when a hot lead has gone quiet for too long.",
        "Close the loop with the campaigns store so counts stay consistent.",
      ]}
      data={[
        "Crawled leads (Neon `leads`) as the New column",
        "`sent_log` mode='simulated'|'real' + replied_at drive stage",
        "Campaign follow-up schedule from the existing engine",
        "Owner-owned notes + next-touch date per card",
      ]}
    />
  )
}
