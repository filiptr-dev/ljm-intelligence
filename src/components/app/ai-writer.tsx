"use client"

import { Check, PenLine, Wand2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import type { OutreachTone } from "@/lib/ai/types"
import { Segmented } from "./segmented"

/** The AI writes in one of three tones, or from the user's own description of the email. */
export type WriteStyle = OutreachTone | "custom"

export const STYLE_OPTIONS: { value: WriteStyle; label: React.ReactNode }[] = [
  { value: "professional", label: "Professional" },
  { value: "friendly", label: "Friendly" },
  { value: "direct", label: "Direct" },
  { value: "custom", label: <span className="inline-flex items-center gap-1"><PenLine className="size-3.5" /> Describe it</span> },
]

const CAMPAIGN_EXAMPLES = [
  "Short email announcing 5 new reefer trucks on Chicago to Dallas, offer 10% off the first load and ask for a quick call",
  "Friendly note to food manufacturers: no broker margin, live GPS tracking, start with one trial load",
  "Direct email for Q4: we still have flatbed capacity out of Houston, ask them to send a lane to quote",
  "Formal intro for German forwarders: tautliner capacity Munich to Belgrade every week, customs paperwork handled",
]

export function StylePicker({ value, onChange }: { value: WriteStyle; onChange: (v: WriteStyle) => void }) {
  return (
    <div className="space-y-1.5">
      <Label>How the AI writes it</Label>
      <Segmented value={value} onChange={onChange} className="flex w-full [&>button]:flex-1 [&>button]:px-2" options={STYLE_OPTIONS} />
    </div>
  )
}

/** "Describe the email you want": a plain-English brief the AI turns into the email. */
export function BriefBox({
  brief, setBrief, onWrite, writing, understood, examples = CAMPAIGN_EXAMPLES, placeholder,
}: {
  brief: string
  setBrief: (v: string) => void
  onWrite: () => void
  writing: boolean
  understood?: string[]
  examples?: string[]
  placeholder?: string
}) {
  return (
    <div className="rounded-sm border border-asphalt/25 bg-background p-3">
      <Label htmlFor="brief" className="font-semibold">Describe the email you want</Label>
      <p className="mt-0.5 text-xs text-muted-foreground">
        Write it like you would tell a colleague: what to offer, which lane or trailer, any discount, the tone and what you want them to do.
      </p>
      <Textarea
        id="brief"
        value={brief}
        onChange={(e) => setBrief(e.target.value)}
        rows={3}
        placeholder={placeholder ?? "e.g. Short, friendly email about our new reefer trucks on Chicago to Dallas. Offer 10% off the first load and ask for a call."}
        className="mt-2 text-[0.9rem]"
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && brief.trim()) onWrite()
        }}
      />
      <div className="mt-2 flex flex-wrap gap-1.5">
        <span className="py-0.5 text-xs text-muted-foreground">Try:</span>
        {examples.map((ex) => (
          <button
            key={ex}
            type="button"
            onClick={() => setBrief(ex)}
            className="max-w-[260px] truncate rounded-sm border border-border bg-card px-2 py-0.5 text-xs hover:border-asphalt hover:bg-accent"
            title={ex}
          >
            {ex}
          </button>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={onWrite} disabled={writing || !brief.trim()}>
          <Wand2 className={writing ? "animate-pulse" : undefined} /> {writing ? "Writing…" : "Write it for me"}
        </Button>
        <span className="text-xs text-muted-foreground">Ctrl + Enter</span>
      </div>
      {understood?.length ? (
        <div className="mt-3 border-t border-border pt-2">
          <div className="eyebrow mb-1.5">The AI understood</div>
          <ul className="flex flex-wrap gap-1.5">
            {understood.map((u) => (
              <li key={u} className="inline-flex items-center gap-1 rounded-sm bg-good/10 px-2 py-0.5 text-xs font-medium text-foreground">
                <Check className="size-3 text-good" /> {u}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}

/** Stream a finished draft into a field word by word, like a model response. */
export function streamInto(text: string, set: (v: string) => void) {
  const words = text.split(/(\s+)/)
  let i = 0
  set("")
  return new Promise<void>((resolve) => {
    const id = setInterval(() => {
      i += 6
      set(words.slice(0, i).join(""))
      if (i >= words.length) {
        clearInterval(id)
        resolve()
      }
    }, 30)
  })
}
