/**
 * Shared bits of the tile-less US map: the lower-48 state polygons, view bounds, and the
 * theme palette reader. Used by the lanes map and the fleet's last-known-location mini map,
 * so both look the same and neither needs a tile server or an API key.
 */

import { feature } from "topojson-client"
import type { FeatureCollection, Geometry } from "geojson"
import type { GeometryCollection, Topology } from "topojson-specification"
import usAtlas from "us-atlas/states-10m.json"
import { STATE_ABBR_BY_NAME, STATE_NAMES } from "@/components/app/lanes/format"

export type RGBA = [number, number, number, number]
export type StateProps = { abbr: string; name: string }

export const US_BOUNDS: [[number, number], [number, number]] = [[-125.5, 24], [-66, 49.8]]
export const MAX_BOUNDS: [[number, number], [number, number]] = [[-140, 18], [-52, 56]]

// Lower 48 + DC. Alaska's Aleutians cross the antimeridian and smear across a
// mercator view; the carrier's lanes are continental.
export const STATES: FeatureCollection<Geometry, StateProps> = (() => {
  const topo = usAtlas as unknown as Topology
  const fc = feature(topo, topo.objects.states as GeometryCollection) as unknown as FeatureCollection<Geometry, { name: string }>
  return {
    type: "FeatureCollection",
    features: fc.features
      .map((f) => ({ ...f, properties: { name: f.properties.name, abbr: STATE_ABBR_BY_NAME[f.properties.name] ?? "" } }))
      .filter((f) => f.properties.abbr && !["AK", "HI"].includes(f.properties.abbr)),
  }
})()

type Ring = number[][]
type StateGeom = { abbr: string; name: string; lng: number; lat: number; spanLng: number; spanLat: number; area: number }

/** Centroid + bbox of each state's biggest polygon (islands and exclaves don't pull the label). */
const STATE_GEOMS: StateGeom[] = STATES.features.map((f) => {
  const g = f.geometry
  const polys: Ring[][] = g.type === "Polygon" ? [g.coordinates as Ring[]] : g.type === "MultiPolygon" ? (g.coordinates as Ring[][]) : []
  let best: StateGeom | null = null
  for (const poly of polys) {
    const ring = poly[0]
    let a = 0, cx = 0, cy = 0
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
    for (let i = 0; i < ring.length - 1; i++) {
      const [x0, y0] = ring[i], [x1, y1] = ring[i + 1]
      const cr = x0 * y1 - x1 * y0
      a += cr; cx += (x0 + x1) * cr; cy += (y0 + y1) * cr
      minX = Math.min(minX, x0); maxX = Math.max(maxX, x0); minY = Math.min(minY, y0); maxY = Math.max(maxY, y0)
    }
    const area = Math.abs(a / 2)
    if (!area || (best && area <= best.area)) continue
    best = { abbr: f.properties.abbr, name: f.properties.name, lng: cx / (3 * a), lat: cy / (3 * a), spanLng: maxX - minX, spanLat: maxY - minY, area }
  }
  return best ?? { abbr: f.properties.abbr, name: f.properties.name, lng: 0, lat: 0, spanLng: 0, spanLat: 0, area: 0 }
})
// A few states read better with a nudged anchor (coastline / peninsula centroids sit off the visual middle).
const LABEL_NUDGE: Record<string, [number, number]> = { FL: [0.6, 0.3], MI: [0.3, -0.6], LA: [-0.3, 0.3], MD: [0.2, 0.1], ID: [0, -1], VA: [0.3, 0], KY: [0.3, 0] }

export type StateLabel = { abbr: string; text: string; position: [number, number] }

const mercY = (lat: number) => 0.5 - Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360)) / (2 * Math.PI)
const labelCache = new Map<string, StateLabel[]>()

/**
 * Collision-safe state labels for a zoom level. Greedy, biggest state first: the full name
 * if the state is wide enough to hold it, else the abbreviation; a label that would overlap
 * an already-placed one (or doesn't fit the state at all) is dropped, so DC/RI/DE only
 * appear once zoomed in. Pure maths on mercator pixels — no map instance needed.
 */
export function placeStateLabels(zoom: number, opts: { abbrOnly?: boolean; fontPx?: number } = {}): StateLabel[] {
  const fontPx = opts.fontPx ?? 11
  const z = Math.round(zoom * 4) / 4
  const key = `${z}|${opts.abbrOnly ? 1 : 0}|${fontPx}`
  const hit = labelCache.get(key)
  if (hit) return hit
  const scale = 512 * 2 ** z
  const charW = fontPx * 0.62
  const placed: { x0: number; x1: number; y0: number; y1: number }[] = []
  const out: StateLabel[] = []
  for (const g of [...STATE_GEOMS].sort((a, b) => b.area - a.area)) {
    if (!g.abbr) continue
    const [dx, dy] = LABEL_NUDGE[g.abbr] ?? [0, 0]
    const lng = g.lng + dx, lat = g.lat + dy
    const cx = ((lng + 180) / 360) * scale, cy = mercY(lat) * scale
    const stateW = (g.spanLng / 360) * scale
    for (const text of opts.abbrOnly ? [g.abbr] : [STATE_NAMES[g.abbr] ?? g.name, g.abbr]) {
      const w = text.length * charW + 6
      const h = fontPx + 4
      if (w > stateW * (text === g.abbr ? 1.1 : 0.95)) continue
      const box = { x0: cx - w / 2, x1: cx + w / 2, y0: cy - h / 2, y1: cy + h / 2 }
      if (placed.some((b) => box.x0 < b.x1 && box.x1 > b.x0 && box.y0 < b.y1 && box.y1 > b.y0)) continue
      placed.push(box)
      out.push({ abbr: g.abbr, text, position: [lng, lat] })
      break
    }
  }
  labelCache.set(key, out)
  return out
}

/** Resolve a CSS colour token (hex / oklch / anything) to RGB via a 1px canvas. */
function readColor(ctx: CanvasRenderingContext2D, cssVar: string, fallback: [number, number, number]): [number, number, number] {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(cssVar).trim()
  if (!raw) return fallback
  ctx.clearRect(0, 0, 1, 1)
  ctx.fillStyle = "#000"
  ctx.fillStyle = raw
  ctx.fillRect(0, 0, 1, 1)
  const d = ctx.getImageData(0, 0, 1, 1).data
  return [d[0], d[1], d[2]]
}

export type Palette = Record<"bg" | "card" | "border" | "muted" | "brand" | "ink" | "good" | "bad" | "steel", [number, number, number]>

export function readPalette(): Palette {
  const c = document.createElement("canvas")
  c.width = c.height = 1
  const ctx = c.getContext("2d", { willReadFrequently: true })!
  return {
    bg: readColor(ctx, "--background", [239, 237, 232]),
    card: readColor(ctx, "--card", [255, 255, 255]),
    border: readColor(ctx, "--border", [216, 212, 203]),
    muted: readColor(ctx, "--muted", [233, 230, 224]),
    brand: readColor(ctx, "--chart-1", [188, 36, 68]),
    ink: readColor(ctx, "--foreground", [43, 43, 43]),
    good: readColor(ctx, "--good", [46, 125, 50]),
    bad: readColor(ctx, "--bad", [198, 40, 40]),
    steel: readColor(ctx, "--steel", [139, 144, 152]),
  }
}

export const mix = (a: [number, number, number], b: [number, number, number], t: number): [number, number, number] => [
  a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t,
]
export const rgb = (c: [number, number, number], a = 255): RGBA => [c[0], c[1], c[2], a]
export const hex = (c: [number, number, number]) => `rgb(${Math.round(c[0])},${Math.round(c[1])},${Math.round(c[2])})`
