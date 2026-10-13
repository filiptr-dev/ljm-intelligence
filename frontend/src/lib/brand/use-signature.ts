"use client"

/**
 * The owner-configured email signature (Settings -> Email signature) plus the
 * sender mailbox the CTA replies to. One cached `GET /settings`; every consumer
 * (EmailPreview, `renderTemplate`'s {{sender}}) reads the same values the
 * backend uses when it sends, so preview and sent mail cannot drift.
 *
 * Empty string means "not configured": consumers omit the line, never default.
 */

import * as React from "react"
import { api } from "@/lib/api/client"

export type Signature = {
  name: string
  company: string
  phone: string
  email: string
  senderEmail: string
}

const EMPTY: Signature = { name: "", company: "", phone: "", email: "", senderEmail: "" }

let cache: Signature = EMPTY
let inflight: Promise<void> | null = null
let loaded = false
const listeners = new Set<() => void>()

function publish(next: Signature) {
  cache = next
  loaded = true
  listeners.forEach((l) => l())
}

/** Fire-and-forget load; safe to call repeatedly. */
export function ensureSignature(): void {
  if (loaded || inflight) return
  inflight = api
    .GET("/settings")
    .then(({ data }) => {
      if (data) setSignatureCache(data)
    })
    .catch(() => {})
    .finally(() => {
      inflight = null
    })
}

type SettingsBrand = {
  brand_dispatcher: string
  brand_company: string
  brand_phone: string
  brand_email: string
  sender_email: string
}

/** Push fresh values in (Settings page calls this after a successful save). */
export function setSignatureCache(s: SettingsBrand): void {
  publish({
    name: s.brand_dispatcher ?? "",
    company: s.brand_company ?? "",
    phone: s.brand_phone ?? "",
    email: s.brand_email ?? "",
    senderEmail: s.sender_email ?? "",
  })
}

/** Synchronous read for non-hook callers (e.g. `renderTemplate`). */
export function getSignature(): Signature {
  ensureSignature()
  return cache
}

function subscribe(l: () => void) {
  listeners.add(l)
  return () => listeners.delete(l)
}

export function useSignature(): Signature {
  React.useEffect(() => {
    ensureSignature()
  }, [])
  return React.useSyncExternalStore(subscribe, () => cache, () => EMPTY)
}

const LEGACY_QUOTE_URL = "https://ljminternational.com/quote"

/** TS mirror of backend `resolve_cta_url` (inbox/email_render.py). Keep in sync. */
export function resolveCtaUrl(label: string, url: string, senderEmail: string): string {
  const l = (label ?? "").trim()
  const u = (url ?? "").trim()
  if (l && senderEmail && (!u || u === LEGACY_QUOTE_URL)) {
    const subject = l.toLowerCase().includes("trial") ? "Trial%20load%20request" : "Quote%20request"
    return `mailto:${senderEmail}?subject=${subject}`
  }
  return u
}
