"use client"

import { EUTruck, USTruck } from "@/components/brand/trucks"
import { TireGlyph } from "@/components/brand/tire"
import { CLIENT } from "@/lib/data/types"
import { cn } from "@/lib/utils"
import type { EmailDesign, Recipient } from "./engine"

export function renderTemplate(tpl: string, r?: Recipient) {
  const first = r?.contactName.split(" ")[0] ?? "there"
  return tpl
    .replaceAll("{{first_name}}", first)
    .replaceAll("{{company}}", r?.name ?? "your company")
    .replaceAll("{{lane}}", r?.lane ?? "your lanes")
    .replaceAll("{{equipment}}", r?.equipment ?? "dry van")
    .replaceAll("{{sender}}", CLIENT.dispatcher)
}

const READABLE: Record<string, string> = {
  "{{first_name}}": "[First name]",
  "{{company}}": "[Company name]",
  "{{lane}}": "[Their lane]",
  "{{equipment}}": "[Trailer type]",
  "{{sender}}": "[Your name]",
}
/** Personal fields shown in plain words, for places that list templates rather than a finished email. */
export const readableTags = (s: string) => s.replace(/\{\{\w+\}\}/g, (t) => READABLE[t] ?? t)

function ClientLogo({ light }: { light: boolean }) {
  return (
    <div className="flex items-center gap-2">
      <svg viewBox="0 0 100 100" className="size-7" aria-hidden>
        <TireGlyph />
      </svg>
      <div className={cn("leading-none", light ? "text-white" : "text-[#16171a]")}>
        <div className="font-display text-lg font-bold tracking-wide">Ironline</div>
        <div className="text-[0.55rem] font-semibold tracking-[0.24em] uppercase opacity-80">Transport</div>
      </div>
    </div>
  )
}

const isLight = (hex: string) => {
  const n = parseInt(hex.slice(1), 16)
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255]
  return 0.299 * r + 0.587 * g + 0.114 * b > 150
}

export function EmailPreview({
  subject, body, design, recipient, mobile = false,
}: { subject: string; body: string; design: EmailDesign; recipient?: Recipient; mobile?: boolean }) {
  const text = renderTemplate(body, recipient)
  const sub = renderTemplate(subject, recipient)
  const onAccentLight = !isLight(design.accent)
  const paragraphs = text.split(/\n{2,}/)

  const cta = design.ctaLabel ? (
    <a
      href={design.ctaUrl}
      onClick={(e) => e.preventDefault()}
      className="mt-2 inline-block rounded-[3px] px-5 py-2.5 text-sm font-bold"
      style={{ background: design.accent, color: onAccentLight ? "#fff" : "#16171a" }}
    >
      {design.ctaLabel}
    </a>
  ) : null

  const signature = design.signature ? (
    <div className="mt-5 flex items-center gap-3 border-t border-[#e4e1da] pt-4 text-xs text-[#5c5f66]">
      <div className="flex size-9 items-center justify-center rounded-full bg-[#16171a] font-display text-sm font-bold text-white">MD</div>
      <div>
        <div className="font-semibold text-[#16171a]">{CLIENT.dispatcher} · Dispatch</div>
        <div>{CLIENT.company} · {CLIENT.fleet}</div>
        <div>{CLIENT.phone} · {CLIENT.email}</div>
      </div>
    </div>
  ) : null

  const content = (
    <div className="space-y-3 text-[0.9rem] leading-relaxed text-[#16171a]">
      {paragraphs.map((p, i) => (
        <p key={i} className="whitespace-pre-line">{p}</p>
      ))}
      {cta}
      {signature}
    </div>
  )

  return (
    <div className="overflow-hidden rounded-sm border border-border bg-white">
      <div className="space-y-1 border-b border-border bg-[#f6f5f2] px-4 py-3 text-xs">
        <div><span className="inline-block w-14 text-muted-foreground">From</span> {CLIENT.dispatcher} &lt;{CLIENT.email}&gt;</div>
        <div className="truncate"><span className="inline-block w-14 text-muted-foreground">To</span> {recipient ? `${recipient.contactName} <${recipient.email}>` : "Select recipients"}</div>
        <div className="truncate text-sm font-semibold"><span className="inline-block w-14 text-xs font-normal text-muted-foreground">Subject</span> {sub || "(no subject)"}</div>
      </div>
      <div className={cn("mx-auto transition-all", mobile ? "max-w-[360px]" : "max-w-none")}>
        {design.layout === "plain" ? (
          <div className="p-6">{content}</div>
        ) : design.layout === "branded" ? (
          <div>
            {design.showLogo ? (
              <div className="flex items-center justify-between px-6 py-4" style={{ background: design.accent }}>
                <ClientLogo light={onAccentLight} />
                <span className="text-[0.65rem] font-semibold tracking-[0.18em] uppercase" style={{ color: onAccentLight ? "#fff" : "#16171a" }}>
                  Capacity update
                </span>
              </div>
            ) : (
              <div className="h-2" style={{ background: design.accent }} />
            )}
            {design.showTruck ? (
              <div className="border-b border-[#e4e1da] bg-[#f4f2ee] px-8 pt-5">
                {recipient?.region === "EU" ? <EUTruck cab={design.accent} /> : <USTruck cab={design.accent} />}
                <div className="-mx-8 mt-1 h-2 bg-[#16171a]" />
              </div>
            ) : null}
            <div className="p-6">{content}</div>
            <div className="bg-[#16171a] px-6 py-3 text-[0.65rem] text-[#8b9098]">
              {CLIENT.company} · Asset-based carrier · US DOT & EU licensed · Unsubscribe
            </div>
          </div>
        ) : (
          <div className="bg-[#efede8] p-5">
            <div className="overflow-hidden rounded-sm border-t-4 bg-white" style={{ borderColor: design.accent }}>
              {design.showLogo ? <div className="border-b border-[#e4e1da] px-6 py-4"><ClientLogo light={false} /></div> : null}
              {design.showTruck ? (
                <div className="px-10 pt-4">{recipient?.region === "EU" ? <EUTruck cab={design.accent} /> : <USTruck cab={design.accent} />}</div>
              ) : null}
              <div className="p-6">{content}</div>
            </div>
            <div className="mt-3 text-center text-[0.65rem] text-[#8b9098]">{CLIENT.company} · Unsubscribe</div>
          </div>
        )}
      </div>
    </div>
  )
}
