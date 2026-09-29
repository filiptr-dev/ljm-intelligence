import type { Intent } from "@/lib/ai/types"
import { cn } from "@/lib/utils"

export const INTENT_META: Record<Intent, { label: string; cls: string }> = {
  load_offer: { label: "Load offer", cls: "border-chart-1/40 bg-chart-1/10 text-chart-1" },
  capacity_offer: { label: "Capacity pitch", cls: "border-asphalt/30 bg-asphalt/5 text-foreground" },
  quote: { label: "Quote", cls: "border-border bg-muted text-foreground" },
  booked: { label: "Booked", cls: "border-good/40 bg-good/10 text-good" },
  rejected: { label: "Rejected", cls: "border-bad/40 bg-bad/10 text-bad" },
  declined: { label: "We declined", cls: "border-border bg-muted text-muted-foreground" },
  invoice: { label: "Invoice", cls: "border-border bg-muted text-foreground" },
  payment_issue: { label: "Payment issue", cls: "border-warn/50 bg-warn/10 text-warn" },
  complaint: { label: "Complaint", cls: "border-bad/40 bg-bad/10 text-bad" },
  praise: { label: "Praise", cls: "border-good/40 bg-good/10 text-good" },
  other: { label: "Other", cls: "border-border bg-muted text-muted-foreground" },
}

export function IntentBadge({ intent, className }: { intent: Intent; className?: string }) {
  const m = INTENT_META[intent]
  return (
    <span className={cn("inline-flex h-5 items-center rounded-[3px] border px-1.5 text-[0.7rem] font-semibold whitespace-nowrap", m.cls, className)}>
      {m.label}
    </span>
  )
}

export function SentimentDot({ value }: { value: number }) {
  const label = value > 0.2 ? "Positive" : value < -0.1 ? "Negative" : "Neutral"
  const cls = value > 0.2 ? "bg-good" : value < -0.1 ? "bg-bad" : "bg-steel"
  return (
    <span className="inline-flex items-center gap-1 text-[0.7rem] text-muted-foreground" title={`Sentiment ${value.toFixed(2)}`}>
      <span className={cn("size-2 rounded-full", cls)} aria-hidden />
      {label}
    </span>
  )
}
