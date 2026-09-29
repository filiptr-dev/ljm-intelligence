"use client"

/**
 * The building blocks of the email builder, shared by the campaign builder
 * (`OutreachBuilder`, many recipients) and the one-off builder
 * (`SingleEmailBuilder`, exactly one recipient). Same editor, personal fields,
 * design controls and preview everywhere, so there is one email UX in the app.
 */

import * as React from "react"
import { ChevronLeft, ChevronRight, Monitor, Smartphone, Sparkles } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"
import { BriefBox, StylePicker, type WriteStyle } from "./ai-writer"
import { EmailPreview, renderTemplate } from "./email-preview"
import type { EmailDesign, Recipient } from "./engine"
import { Segmented } from "./segmented"

/** Personal fields, in plain words: each one is swapped for the recipient's own details on send. */
export const FIELDS = [
  { tag: "{{first_name}}", label: "First name", hint: "The contact person's first name" },
  { tag: "{{company}}", label: "Company name", hint: "The name of the company you are emailing" },
  { tag: "{{lane}}", label: "Their lane", hint: "The route they ship most, e.g. Chicago → Dallas" },
  { tag: "{{equipment}}", label: "Trailer type", hint: "The trailer they need, e.g. Reefer or Flatbed" },
  { tag: "{{sender}}", label: "Your name", hint: "Your dispatcher's name, used in the sign-off" },
]
export const ACCENTS = [
  { name: "LJM red", hex: "#BC2444" },
  { name: "Fleet blue", hex: "#2f63a8" },
  { name: "Highway green", hex: "#3d8f5a" },
  { name: "Steel", hex: "#8b9098" },
  { name: "Charcoal", hex: "#2B2B2B" },
]

export function Step({ n, title, children, action }: { n: number; title: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <section className="rounded-sm border border-border bg-card">
      <header className="flex items-center gap-3 border-b border-border px-4 py-3">
        <span className="flex size-7 items-center justify-center rounded-sm bg-asphalt font-display text-sm font-bold text-safety">{n}</span>
        <h2 className="flex-1 font-display text-lg font-semibold">{title}</h2>
        {action}
      </header>
      <div className="p-4">{children}</div>
    </section>
  )
}

type Setter = React.Dispatch<React.SetStateAction<string>>

/** Message step body: type/angle picker slot, tone, brief, subject, personal fields, body. */
export function MessageFields({
  typeSlot, style, setStyle, custom, brief, setBrief, onWrite, writing, understood,
  subject, setSubject, body, setBody, preview,
  fieldsHint = "each company's own details, so every company gets a message written just for them",
  bodyHint = "Use the arrows in the preview on the right to see the finished email for each company.",
}: {
  typeSlot: React.ReactNode
  style: WriteStyle
  setStyle: (v: WriteStyle) => void
  custom: boolean
  brief: string
  setBrief: (v: string) => void
  onWrite: () => void
  writing: boolean
  understood: string[]
  subject: string
  setSubject: Setter
  body: string
  setBody: Setter
  preview?: Recipient
  fieldsHint?: string
  bodyHint?: string
}) {
  const bodyRef = React.useRef<HTMLTextAreaElement>(null)
  const subjectRef = React.useRef<HTMLInputElement>(null)
  // personal-field buttons insert into whichever field was used last
  const [target, setTargetState] = React.useState<"subject" | "body">("body")
  const targetRef = React.useRef<"subject" | "body">("body")
  const setTarget = (t: "subject" | "body") => {
    targetRef.current = t
    setTargetState(t)
  }
  const insertTag = (tag: string) => {
    const el = targetRef.current === "subject" ? subjectRef.current : bodyRef.current
    const set = targetRef.current === "subject" ? setSubject : setBody
    if (!el) return set((b) => b + tag)
    const a = el.selectionStart ?? el.value.length
    const z = el.selectionEnd ?? a
    set((b) => b.slice(0, a) + tag + b.slice(z))
    requestAnimationFrame(() => {
      el.focus()
      el.setSelectionRange(a + tag.length, a + tag.length)
    })
  }

  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2">
        {typeSlot}
        <StylePicker value={style} onChange={setStyle} />
      </div>
      {custom ? (
        <div className="mt-3">
          <BriefBox brief={brief} setBrief={setBrief} onWrite={onWrite} writing={writing} understood={understood} />
        </div>
      ) : null}
      <div className="mt-3 space-y-1.5">
        <Label htmlFor="subject">Subject</Label>
        <Input id="subject" ref={subjectRef} value={subject} onFocus={() => setTarget("subject")} onChange={(e) => setSubject(e.target.value)} />
      </div>

      <div className="mt-3 rounded-sm border border-border bg-background p-3">
        <div className="flex items-start gap-2">
          <Sparkles className="mt-0.5 size-4 shrink-0 text-chart-2" />
          <div className="text-sm">
            <div className="font-semibold">Personal fields</div>
            <p className="text-muted-foreground">
              Click a button to add it to the {target === "subject" ? "subject" : "email"} where your cursor is. When the email is sent, it is replaced with {fieldsHint}. In the text it looks like <code className="rounded-[2px] bg-muted px-1 font-mono text-[0.75rem]">{"{{company}}"}</code>.
            </p>
          </div>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
          {FIELDS.map((f) => {
            const example = renderTemplate(f.tag, preview)
            return (
              <button
                key={f.tag}
                type="button"
                title={f.hint}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => insertTag(f.tag)}
                className="flex min-w-0 flex-col items-start rounded-sm border border-border bg-card px-2.5 py-1.5 text-left transition-colors hover:border-asphalt hover:bg-accent"
              >
                <span className="text-sm font-semibold">+ {f.label}</span>
                <span className="w-full truncate text-[0.7rem] text-muted-foreground">e.g. {example}</span>
              </button>
            )
          })}
        </div>
      </div>

      <div className="mt-3 space-y-1.5">
        <Label htmlFor="body">Email body</Label>
        <Textarea id="body" ref={bodyRef} value={body} onFocus={() => setTarget("body")} onChange={(e) => setBody(e.target.value)} rows={12} className="text-[0.9rem] leading-relaxed" />
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">{bodyHint}</p>
      </div>
    </>
  )
}

/** Design step body: layout, accent, header/banner/signature toggles, button. */
export function DesignFields({ design, setDesign }: { design: EmailDesign; setDesign: React.Dispatch<React.SetStateAction<EmailDesign>> }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="space-y-1.5">
        <Label>Layout</Label>
        <Segmented
          value={design.layout}
          onChange={(layout) => setDesign((d) => ({ ...d, layout }))}
          className="flex w-full [&>button]:flex-1"
          options={[{ value: "plain", label: "Plain text" }, { value: "branded", label: "Branded" }, { value: "card", label: "Card" }]}
        />
      </div>
      <div className="space-y-1.5">
        <Label>Accent colour</Label>
        <div className="flex gap-2">
          {ACCENTS.map((a) => (
            <button
              key={a.hex}
              type="button"
              title={a.name}
              aria-label={a.name}
              onClick={() => setDesign((d) => ({ ...d, accent: a.hex }))}
              className={cn("size-8 rounded-sm border-2 transition-transform", design.accent === a.hex ? "scale-110 border-asphalt" : "border-transparent ring-1 ring-border")}
              style={{ background: a.hex }}
            />
          ))}
        </div>
      </div>
      <div className="space-y-2.5">
        {([
          ["showLogo", "Company logo header"],
          ["showTruck", "Truck banner (matches recipient's region)"],
          ["signature", "Dispatcher signature"],
        ] as const).map(([k, label]) => (
          <label key={k} className="flex items-center gap-2.5 text-sm">
            <Switch checked={design[k]} onCheckedChange={(v) => setDesign((d) => ({ ...d, [k]: v }))} disabled={design.layout === "plain" && k !== "signature"} />
            {label}
          </label>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1.5">
          <Label htmlFor="cta">Button text</Label>
          <Input id="cta" value={design.ctaLabel} onChange={(e) => setDesign((d) => ({ ...d, ctaLabel: e.target.value }))} placeholder="No button" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="cta-url">Button link</Label>
          <Input id="cta-url" value={design.ctaUrl} onChange={(e) => setDesign((d) => ({ ...d, ctaUrl: e.target.value }))} />
        </div>
      </div>
    </div>
  )
}

/** Preview header (recipient arrows, desktop/mobile) plus the rendered email. */
export function PreviewPanel({
  subject, body, design, recipients, previewIdx, setPreviewIdx, mobile, setMobile,
}: {
  subject: string
  body: string
  design: EmailDesign
  recipients: Recipient[]
  previewIdx: number
  setPreviewIdx: React.Dispatch<React.SetStateAction<number>>
  mobile: boolean
  setMobile: (v: boolean) => void
}) {
  const preview = recipients[Math.min(previewIdx, Math.max(0, recipients.length - 1))]
  return (
    <>
      <div className="flex items-center gap-2">
        <h2 className="font-display text-lg font-semibold">Preview</h2>
        <div className="ml-auto flex items-center gap-1 text-sm">
          <Button variant="ghost" size="icon-sm" disabled={previewIdx <= 0} onClick={() => setPreviewIdx((i) => Math.max(0, i - 1))} aria-label="Previous recipient"><ChevronLeft /></Button>
          <span className="num min-w-16 text-center font-mono text-xs">{recipients.length ? `${Math.min(previewIdx, recipients.length - 1) + 1} / ${recipients.length}` : "0 / 0"}</span>
          <Button variant="ghost" size="icon-sm" disabled={previewIdx >= recipients.length - 1} onClick={() => setPreviewIdx((i) => i + 1)} aria-label="Next recipient"><ChevronRight /></Button>
        </div>
        <Segmented value={mobile ? "m" : "d"} onChange={(v) => setMobile(v === "m")} options={[{ value: "d", label: <Monitor className="size-3.5" /> }, { value: "m", label: <Smartphone className="size-3.5" /> }]} />
      </div>
      <div className="max-h-[calc(100vh-15rem)] overflow-y-auto">
        <EmailPreview subject={subject} body={body} design={design} recipient={preview} mobile={mobile} />
      </div>
    </>
  )
}
