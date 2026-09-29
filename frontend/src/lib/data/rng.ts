// Seeded PRNG so the demo data is identical on every run.
export type Rng = () => number

export function createRng(seed: number): Rng {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export const pick = <T,>(rng: Rng, items: readonly T[]): T =>
  items[Math.floor(rng() * items.length)]

export const between = (rng: Rng, min: number, max: number) =>
  min + rng() * (max - min)

export const int = (rng: Rng, min: number, max: number) =>
  Math.floor(between(rng, min, max + 1))

export const chance = (rng: Rng, p: number) => rng() < p

export function sampleN<T>(rng: Rng, items: readonly T[], n: number): T[] {
  const pool = [...items]
  const out: T[] = []
  while (out.length < n && pool.length) {
    out.push(pool.splice(Math.floor(rng() * pool.length), 1)[0])
  }
  return out
}

export function weighted<T>(rng: Rng, items: readonly [T, number][]): T {
  const total = items.reduce((s, [, w]) => s + w, 0)
  let r = rng() * total
  for (const [item, w] of items) {
    r -= w
    if (r <= 0) return item
  }
  return items[items.length - 1][0]
}
