import { cn } from "@/lib/utils"
import { docLabel, expiryTone, STATUS_LABEL, when, type Tone } from "./format"

const TONE: Record<Tone, string> = {
  bad: "border-bad/30 bg-bad/10 text-bad",
  warn: "border-warn/30 bg-warn/10 text-warn",
  ok: "border-good/30 bg-good/10 text-good",
}

// On a load is the "working" state, so it takes the charcoal brand tone; the red accent is kept for danger.
const STATUS_CLASS: Record<string, string> = {
  available: TONE.ok,
  on_load: "border-asphalt/25 bg-asphalt text-white",
  in_shop: TONE.warn,
  out_of_service: TONE.bad,
}

const chip = "inline-flex h-5 items-center gap-1 rounded-sm border px-1.5 text-[0.7rem] leading-none font-semibold whitespace-nowrap"

export function StatusChip({ status, className }: { status: string; className?: string }) {
  return <span className={cn(chip, STATUS_CLASS[status] ?? TONE.warn, className)}>{STATUS_LABEL[status] ?? status}</span>
}

/** Document expiry: red <= 14 days, amber <= 30, green after that. */
export function ExpiryBadge({ days, kind, className }: { days: number; kind?: string; className?: string }) {
  return (
    <span className={cn(chip, TONE[expiryTone(days)], className)}>
      {kind ? `${docLabel(kind)} · ` : ""}
      {when(days)}
    </span>
  )
}

export function ToneChip({ tone, children, className }: { tone: Tone; children: React.ReactNode; className?: string }) {
  return <span className={cn(chip, TONE[tone], className)}>{children}</span>
}
