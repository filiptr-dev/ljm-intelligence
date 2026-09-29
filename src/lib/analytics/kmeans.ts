import { createRng } from "@/lib/data/rng"

/** z-score each column so no feature dominates the distance. */
export function standardize(rows: number[][]): number[][] {
  const d = rows[0].length
  const mean = Array.from({ length: d }, (_, j) => rows.reduce((s, r) => s + r[j], 0) / rows.length)
  const std = Array.from({ length: d }, (_, j) =>
    Math.sqrt(rows.reduce((s, r) => s + (r[j] - mean[j]) ** 2, 0) / rows.length) || 1,
  )
  return rows.map((r) => r.map((v, j) => (v - mean[j]) / std[j]))
}

const dist2 = (a: number[], b: number[]) => a.reduce((s, v, i) => s + (v - b[i]) ** 2, 0)

/** k-means with k-means++ seeding and several restarts; returns the lowest-inertia run. */
export function kmeans(points: number[][], k: number, restarts = 12, seed = 7) {
  const rng = createRng(seed)
  let best = { labels: [] as number[], centroids: [] as number[][], inertia: Infinity }

  for (let run = 0; run < restarts; run++) {
    const centroids: number[][] = [points[Math.floor(rng() * points.length)]]
    while (centroids.length < k) {
      const d = points.map((p) => Math.min(...centroids.map((c) => dist2(p, c))))
      const total = d.reduce((s, v) => s + v, 0)
      let r = rng() * total
      let idx = 0
      while (r > d[idx] && idx < d.length - 1) r -= d[idx++]
      centroids.push(points[idx])
    }
    let labels = new Array(points.length).fill(0)
    for (let iter = 0; iter < 100; iter++) {
      const next = points.map((p) => {
        let bi = 0
        let bd = Infinity
        centroids.forEach((c, i) => {
          const dd = dist2(p, c)
          if (dd < bd) { bd = dd; bi = i }
        })
        return bi
      })
      const changed = next.some((l, i) => l !== labels[i])
      labels = next
      for (let c = 0; c < k; c++) {
        const members = points.filter((_, i) => labels[i] === c)
        if (members.length) centroids[c] = members[0].map((_, j) => members.reduce((s, m) => s + m[j], 0) / members.length)
      }
      if (!changed && iter > 0) break
    }
    const inertia = points.reduce((s, p, i) => s + dist2(p, centroids[labels[i]]), 0)
    if (inertia < best.inertia) best = { labels, centroids: centroids.map((c) => [...c]), inertia }
  }
  return best
}
