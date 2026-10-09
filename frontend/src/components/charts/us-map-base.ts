/**
 * Shared bits of the tile-less US map: the lower-48 state polygons, view bounds, and the
 * theme palette reader. Used by the lanes map and the fleet's last-known-location mini map,
 * so both look the same and neither needs a tile server or an API key.
 */

import { feature } from "topojson-client"
import type { FeatureCollection, Geometry } from "geojson"
import type { GeometryCollection, Topology } from "topojson-specification"
import usAtlas from "us-atlas/states-10m.json"
import { STATE_ABBR_BY_NAME } from "@/components/app/lanes/format"

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
