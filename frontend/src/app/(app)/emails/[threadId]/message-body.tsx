"use client"

import * as React from "react"

/**
 * Readable message body: paragraphs preserved, URLs made clickable,
 * quoted history ("On ... wrote:" header or ">"-prefixed lines) folded
 * behind a toggle, signatures ("-- " separator) de-emphasised.
 *
 * Rendered client-side so the "Show quoted text" toggle doesn't need a
 * roundtrip. The input is always the sanitised ``body_text`` stored at
 * ingest — no raw HTML ever renders here.
 */
export function MessageBody({ text }: { text: string }) {
  const parts = React.useMemo(() => split(text), [text])
  const [showQuoted, setShowQuoted] = React.useState(false)
  return (
    <div className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-muted-foreground">
      {parts.main ? <Linkify text={parts.main} /> : null}
      {parts.signature ? (
        <div className="mt-3 border-t border-border/50 pt-2 text-xs opacity-70">
          <Linkify text={parts.signature} />
        </div>
      ) : null}
      {parts.quoted ? (
        <div className="mt-3">
          <button
            type="button"
            onClick={() => setShowQuoted((v) => !v)}
            className="text-xs italic text-muted-foreground underline underline-offset-2 hover:text-foreground"
          >
            {showQuoted ? "Hide quoted text" : "Show quoted text"}
          </button>
          {showQuoted ? (
            <div className="mt-2 border-l-2 border-border pl-3 text-xs opacity-80">
              <Linkify text={parts.quoted} />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

// ---- helpers ---------------------------------------------------------

const URL_RE = /\b(https?:\/\/[^\s<>()]+|www\.[^\s<>()]+)/g
const EMAIL_RE = /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi

/** Linkify URLs + email addresses in plain text, safely. */
function Linkify({ text }: { text: string }) {
  const nodes: React.ReactNode[] = []
  let i = 0
  // Walk two patterns in one pass: find the next match of either and
  // emit text + anchor; everything else is a text node (React escapes).
  const matches: Array<{ start: number; end: number; kind: "url" | "email"; value: string }> = []
  for (const m of text.matchAll(URL_RE)) {
    matches.push({ start: m.index!, end: m.index! + m[0].length, kind: "url", value: m[0] })
  }
  for (const m of text.matchAll(EMAIL_RE)) {
    matches.push({ start: m.index!, end: m.index! + m[0].length, kind: "email", value: m[0] })
  }
  matches.sort((a, b) => a.start - b.start)
  // Drop overlaps (email inside a URL etc.)
  const kept: typeof matches = []
  let lastEnd = -1
  for (const m of matches) {
    if (m.start >= lastEnd) {
      kept.push(m)
      lastEnd = m.end
    }
  }
  let key = 0
  for (const m of kept) {
    if (m.start > i) nodes.push(text.slice(i, m.start))
    const href = m.kind === "email" ? `mailto:${m.value}` : m.value.startsWith("http") ? m.value : `https://${m.value}`
    nodes.push(
      <a
        key={key++}
        href={href}
        target={m.kind === "url" ? "_blank" : undefined}
        rel="noreferrer noopener"
        className="text-primary underline underline-offset-2 hover:no-underline"
      >
        {m.value}
      </a>
    )
    i = m.end
  }
  if (i < text.length) nodes.push(text.slice(i))
  return <>{nodes}</>
}

interface Parts {
  main: string
  signature: string
  quoted: string
}

const ON_WROTE = /^on .+ wrote:\s*$/i
const SIG_SEP = /^-- ?$/

/**
 * Split a plain-text body into (main, signature, quoted).
 *
 * Rules:
 *   * The first "On ... wrote:" or run of lines starting with "> "
 *     anchors the quoted block — everything from that point to EOF is
 *     quoted history.
 *   * A line that is exactly ``-- `` (sig separator) marks the start of
 *     a signature; it's de-emphasised but still shown.
 *   * Trailing blank lines inside each bucket are trimmed; interior
 *     blanks preserved.
 */
function split(raw: string): Parts {
  const lines = (raw || "").replace(/\r\n/g, "\n").split("\n")
  let quotedStart = -1
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (ON_WROTE.test(line.trim())) {
      quotedStart = i
      break
    }
    if (line.startsWith(">")) {
      // Only treat as a quoted block if we have ≥1 further line in the
      // same block — one stray '>' isn't the start of history.
      let j = i
      while (j < lines.length && (lines[j].startsWith(">") || lines[j].trim() === "")) j++
      if (j - i >= 1) {
        quotedStart = i
        break
      }
    }
    i++
  }
  const beforeQuoted = quotedStart === -1 ? lines : lines.slice(0, quotedStart)
  const quoted = quotedStart === -1 ? "" : lines.slice(quotedStart).join("\n").trim()
  // signature inside the pre-quoted block
  let sigStart = -1
  for (let k = beforeQuoted.length - 1; k >= 0; k--) {
    if (SIG_SEP.test(beforeQuoted[k])) {
      sigStart = k
      break
    }
  }
  const main = sigStart === -1 ? beforeQuoted.join("\n").trim() : beforeQuoted.slice(0, sigStart).join("\n").trim()
  const signature = sigStart === -1 ? "" : beforeQuoted.slice(sigStart + 1).join("\n").trim()
  return { main, signature, quoted }
}

// Exported for unit tests.
export const __test__ = { split }
