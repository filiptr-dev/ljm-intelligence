import { findCity, ZONES, type Region } from "@/lib/data/geo"
import { EQUIPMENT, type Equipment, type Lane } from "@/lib/data/types"

/** Client-safe: used on the server for the lead pool and in the browser for live finds. */

export type Profileable = {
  region: Region
  zone: string
  lanes: Lane[]
  equipment: Equipment[]
  size: "Small" | "Mid-size" | "Enterprise"
}

export type LookalikeProfile = Record<Region, number[]>

const SIZES = ["Small", "Mid-size", "Enterprise"] as const

const unit = (v: number[]) => {
  const n = Math.hypot(...v) || 1
  return v.map((x) => x / n)
}

/** Operating footprint (zones), equipment mix and size, each block unit-normalized then weighted. */
export function featureVector(e: Profileable): number[] {
  const zones = ZONES.map(() => 0)
  const zi = (z: string) => ZONES.indexOf(z as (typeof ZONES)[number])
  zones[zi(e.zone)] += 1
  for (const l of e.lanes) {
    const o = findCity(l.origin)
    const d = findCity(l.destination)
    if (o) zones[zi(o.zone)] += 0.6
    if (d) zones[zi(d.zone)] += 0.6
  }
  const eq = EQUIPMENT.map((x) => (e.equipment.includes(x) ? 1 : 0))
  const size = SIZES.map((s) => (s === e.size ? 1 : 0))
  return [
    ...unit(zones).map((v) => v * 1.0),
    ...unit(eq).map((v) => v * 0.75),
    ...size.map((v) => v * 0.35),
  ]
}

export function cosine(a: number[], b: number[]) {
  let dot = 0, na = 0, nb = 0
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i]
    na += a[i] * a[i]
    nb += b[i] * b[i]
  }
  return dot / (Math.sqrt(na * nb) || 1)
}

/** Weighted centroid of the best existing brokers, one per region. */
export function buildProfile(best: { entity: Profileable; weight: number }[]): LookalikeProfile {
  const out = {} as LookalikeProfile
  for (const region of ["US", "EU"] as Region[]) {
    const rows = best.filter((b) => b.entity.region === region)
    const vecs = rows.map((r) => featureVector(r.entity).map((v) => v * r.weight))
    if (!vecs.length) {
      // Region has no brokers (e.g. LJM operates US-only): give it a zero vector of the right shape.
      const len = featureVector({ region, zone: ZONES[0] ?? "", lanes: [], equipment: [], size: "Small" }).length
      out[region] = Array(len).fill(0)
      continue
    }
    out[region] = vecs[0].map((_, j) => vecs.reduce((s, v) => s + v[j], 0))
  }
  return out
}

export const similarity = (e: Profileable, profile: LookalikeProfile) =>
  cosine(featureVector(e), profile[e.region])

/** 0–100 lookalike score: how closely a company resembles your best brokers. */
export function lookalikeScore(e: Profileable, profile: LookalikeProfile) {
  const sim = similarity(e, profile)
  return Math.round(6 + Math.max(0, Math.min(1, (sim - 0.45) / 0.5)) * 93)
}

/** Per-block match (0–100) so the UI can explain a score: footprint, equipment, company size. */
export function explainScore(e: Profileable, profile: LookalikeProfile) {
  const v = featureVector(e)
  const p = profile[e.region]
  const z = ZONES.length
  const q = EQUIPMENT.length
  const block = (from: number, to: number) => Math.round(Math.max(0, cosine(v.slice(from, to), p.slice(from, to))) * 100)
  return { footprint: block(0, z), equipment: block(z, z + q), size: block(z + q, v.length) }
}
