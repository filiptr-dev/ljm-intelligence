"use client"

/**
 * Interactive US lane map — deck.gl layers over a tile-less maplibre host.
 *
 * No tile server, no API key, no network at view time: the basemap is the
 * bundled `us-atlas` state polygons drawn by a GeoJsonLayer on an empty
 * maplibre style (maplibre only supplies the pan / wheel-zoom / double-click
 * gestures). Loaded with next/dynamic({ ssr: false }) so this whole bundle
 * stays on /intelligence/lanes.
 *
 * Hover opens a rich card for the state / city / lane under the cursor
 * (amendment A2) using data that is already on the page — no request, and no
 * AI call, on hover. Click a state or an arc to filter the page.
 */

import "maplibre-gl/dist/maplibre-gl.css"
import * as React from "react"
import { Map as MapLibreMap, NavigationControl, type IControl } from "maplibre-gl"
import { ArcLayer, GeoJsonLayer, ScatterplotLayer } from "@deck.gl/layers"
import { HeatmapLayer } from "@deck.gl/aggregation-layers"
import { MapboxOverlay } from "@deck.gl/mapbox"
import type { PickingInfo } from "@deck.gl/core"
import { RotateCcw } from "lucide-react"
import { EntityCard } from "@/components/app/lanes/entity-card"
import type { CityEntity, HeatArc, HeatmapData, LaneEntity } from "@/lib/api/lanes"
import {
  hex, mix, readPalette, rgb, MAX_BOUNDS, STATES, US_BOUNDS,
  type Palette, type RGBA, type StateProps,
} from "./us-map-base"


export type MapMode = "origin" | "dest" | "flows"
type Hover = { entity: LaneEntity | CityEntity; x: number; y: number } | null

export default function UsLaneMap({
  data, mode, selectedState, selectedLane, highlightLane, insights, aiUnavailable, onSelectState, onSelectLane,
}: {
  data: HeatmapData
  mode: MapMode
  selectedState?: string | null
  selectedLane?: string | null
  highlightLane?: string | null
  insights: Record<string, string[]>
  aiUnavailable: boolean
  onSelectState: (abbr: string) => void
  onSelectLane: (key: string) => void
}) {
  const hostRef = React.useRef<HTMLDivElement>(null)
  const mapRef = React.useRef<MapLibreMap | null>(null)
  const overlayRef = React.useRef<MapboxOverlay | null>(null)
  // This module is client-only (next/dynamic ssr:false), so reading the DOM during init is safe.
  const [palette, setPalette] = React.useState<Palette>(() => readPalette())
  const [hover, setHover] = React.useState<Hover>(null)
  const [size, setSize] = React.useState({ w: 0, h: 0 })

  // Latest props for the long-lived deck callbacks.
  const live = React.useRef({ onSelectState, onSelectLane })
  React.useEffect(() => { live.current = { onSelectState, onSelectLane } })

  const stateByAbbr = React.useMemo(() => new Map(data.states.map((s) => [s.label, s])), [data.states])
  const cityByKey = React.useMemo(() => new Map(data.cities.map((c) => [c.key, c])), [data.cities])
  const maxState = React.useMemo(() => {
    const val = (s: LaneEntity) => (mode === "origin" ? s.runs_as_origin : mode === "dest" ? s.runs_as_dest : s.metrics.runs) ?? 0
    return Math.max(1, ...data.states.map(val))
  }, [data.states, mode])
  const maxArc = React.useMemo(() => Math.max(1, ...data.arcs.map((a) => a.runs)), [data.arcs])

  // What the cursor is over -> an entity for the card (read by the long-lived deck callbacks).
  const lookup = React.useRef({ cityByKey, stateByAbbr })
  React.useEffect(() => { lookup.current = { cityByKey, stateByAbbr } })

  // palette (re-read when the theme class flips)
  React.useEffect(() => {
    const mo = new MutationObserver(() => setPalette(readPalette()))
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "style", "data-theme"] })
    return () => mo.disconnect()
  }, [])

  // map + overlay, once
  React.useEffect(() => {
    if (!hostRef.current || mapRef.current) return
    const map = new MapLibreMap({
      container: hostRef.current,
      style: { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": hex(palette.bg) } }] },
      bounds: US_BOUNDS,
      fitBoundsOptions: { padding: 16 },
      maxBounds: MAX_BOUNDS,
      minZoom: 2.2,
      maxZoom: 9,
      attributionControl: false,
      dragRotate: false,
      pitchWithRotate: false,
      touchPitch: false,
    })
    map.touchZoomRotate.disableRotation()
    map.addControl(new NavigationControl({ showCompass: false }), "top-right")
    const overlay = new MapboxOverlay({
      interleaved: false,
      layers: [],
      onHover: (info: PickingInfo) => {
        const canvas = map.getCanvas()
        const hit = resolveEntity(info, lookup.current)
        canvas.style.cursor = hit ? "pointer" : ""
        setHover(hit && info.x != null && info.y != null ? { entity: hit, x: info.x, y: info.y } : null)
      },
      onClick: (info: PickingInfo) => {
        const id = info.layer?.id
        if (id === "arcs" && info.object) live.current.onSelectLane((info.object as HeatArc).key)
        else if (id === "states" && info.object) live.current.onSelectState((info.object as { properties: StateProps }).properties.abbr)
      },
    })
    map.addControl(overlay as unknown as IControl)
    mapRef.current = map
    overlayRef.current = overlay
    const ro = new ResizeObserver(() => hostRef.current && setSize({ w: hostRef.current.clientWidth, h: hostRef.current.clientHeight }))
    ro.observe(hostRef.current)
    return () => {
      ro.disconnect()
      overlay.finalize()
      map.remove()
      mapRef.current = null
      overlayRef.current = null
    }
    // Created once; later theme changes flow through the style / layers effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // background colour follows the theme
  React.useEffect(() => {
    const map = mapRef.current
    if (map && map.getLayer("bg")) map.setPaintProperty("bg", "background-color", hex(palette.bg))
  }, [palette])

  // layers
  React.useEffect(() => {
    const overlay = overlayRef.current
    if (!overlay) return
    const p = palette
    const sel = selectedState?.toUpperCase()
    const lanePick = selectedLane ?? highlightLane ?? null
    const stateRuns = (abbr: string) => {
      const s = stateByAbbr.get(abbr)
      if (!s) return 0
      return (mode === "origin" ? s.runs_as_origin : mode === "dest" ? s.runs_as_dest : s.metrics.runs) ?? 0
    }
    const cityPoints = data.cities.filter((c) => (mode === "origin" ? (c.runs_as_origin ?? 0) > 0 : mode === "dest" ? (c.runs_as_dest ?? 0) > 0 : true))
    const heatSource = mode === "origin" ? data.origins : mode === "dest" ? data.dests : []

    const layers = [
      new GeoJsonLayer<StateProps>({
        id: "states",
        data: STATES,
        pickable: true,
        stroked: true,
        filled: true,
        getFillColor: (f) => {
          const n = stateRuns(f.properties.abbr)
          if (!n) return rgb(mix(p.bg, p.muted, 0.7), 255)
          return rgb(mix(p.card, p.brand, 0.08 + 0.34 * Math.sqrt(n / maxState)), 255)
        },
        getLineColor: (f) => (f.properties.abbr === sel ? rgb(p.brand, 255) : rgb(p.border, 255)),
        getLineWidth: (f) => (f.properties.abbr === sel ? 2.5 : 0.8),
        lineWidthUnits: "pixels",
        autoHighlight: true,
        highlightColor: [...p.brand, 60] as RGBA,
        updateTriggers: { getFillColor: [mode, maxState, data, p], getLineColor: [sel, p], getLineWidth: [sel] },
      }),
      new HeatmapLayer({
        id: "heat",
        data: heatSource,
        visible: mode !== "flows",
        pickable: false,
        getPosition: (d: { lng: number; lat: number }) => [d.lng, d.lat],
        getWeight: (d: { weight: number }) => d.weight,
        radiusPixels: 46,
        intensity: 1.3,
        threshold: 0.04,
        colorRange: [0.1, 0.3, 0.5, 0.7, 0.9, 1].map((t) => rgb(mix(p.card, p.brand, t), Math.round(40 + 215 * t))),
        updateTriggers: { getPosition: [mode, p] },
      }),
      new ArcLayer<HeatArc>({
        id: "arcs",
        data: mode === "flows" ? data.arcs : [],
        pickable: true,
        greatCircle: true,
        getSourcePosition: (a) => [a.o_lng, a.o_lat],
        getTargetPosition: (a) => [a.d_lng, a.d_lat],
        getWidth: (a) => 1.5 + 5.5 * (a.runs / maxArc),
        widthUnits: "pixels",
        widthMinPixels: 1.5,
        getHeight: (a) => 0.15 + 0.5 * (a.runs / maxArc),
        getSourceColor: (a) => arcColor(a, p, lanePick),
        getTargetColor: (a) => arcColor(a, p, lanePick),
        autoHighlight: true,
        highlightColor: rgb(p.ink, 255),
        updateTriggers: { getSourceColor: [lanePick, p], getTargetColor: [lanePick, p], getWidth: [maxArc] },
      }),
      new ScatterplotLayer<CityEntity>({
        id: "cities",
        data: cityPoints,
        pickable: true,
        getPosition: (c) => [c.lng, c.lat],
        getRadius: (c) => 3.5 + 7 * Math.sqrt(c.metrics.runs / Math.max(1, data.cities[0]?.metrics.runs ?? 1)),
        radiusUnits: "pixels",
        getFillColor: rgb(p.ink, 230),
        getLineColor: rgb(p.card, 255),
        getLineWidth: 1.2,
        lineWidthUnits: "pixels",
        stroked: true,
        autoHighlight: true,
        highlightColor: rgb(p.brand, 255),
        updateTriggers: { getPosition: [mode], getFillColor: [p], getLineColor: [p] },
      }),
    ]
    overlay.setProps({ layers })
  }, [data, mode, palette, selectedState, selectedLane, highlightLane, stateByAbbr, maxState, maxArc])

  const reset = () => mapRef.current?.fitBounds(US_BOUNDS, { padding: 16, duration: 600 })

  // keep the card inside the frame
  const CARD_W = 336
  const left = hover ? (hover.x + 18 + CARD_W > size.w ? Math.max(8, hover.x - 18 - CARD_W) : hover.x + 18) : 0
  const top = hover ? Math.max(8, Math.min(hover.y - 24, Math.max(8, size.h - 560))) : 0

  const legend =
    mode === "origin" ? "Shading and glow: where runs start" : mode === "dest" ? "Shading and glow: where runs end" : "Shading: runs through each state"

  return (
    <div className="relative h-[40rem] w-full overflow-hidden rounded-sm border border-border bg-background" data-testid="us-lane-map">
      <div className="absolute inset-0"><div ref={hostRef} className="h-full w-full" /></div>
      <button
        type="button"
        onClick={reset}
        className="absolute top-3 left-3 z-10 inline-flex h-8 items-center gap-1.5 rounded-sm border border-border bg-card px-2.5 text-xs font-medium shadow-sm hover:bg-muted"
      >
        <RotateCcw className="size-3.5" aria-hidden /> Reset view
      </button>
      <div className="pointer-events-none absolute bottom-3 left-3 z-10 max-w-[19rem] rounded-sm border border-border bg-card/95 px-3 py-2 text-[0.7rem] shadow-sm">
        <div className="font-medium">{legend}</div>
        <div className="mt-1 flex items-center gap-2 text-muted-foreground">
          fewer
          <span className="h-2 flex-1 rounded-[2px]" style={{ background: "linear-gradient(90deg, var(--card), var(--chart-1))" }} aria-hidden />
          more runs
        </div>
        {mode === "flows" ? (
          <div className="mt-1 flex items-center gap-1.5 text-muted-foreground">
            <span className="size-2 rounded-full bg-good" aria-hidden /> rate/mile up
            <span className="size-2 rounded-full bg-bad" aria-hidden /> down
            <span className="size-2 rounded-full bg-steel" aria-hidden /> steady · thicker = more runs
          </div>
        ) : null}
        <div className="mt-1 text-muted-foreground">Hover for details · click a state{mode === "flows" ? " or arc" : ""} to filter</div>
      </div>
      {hover ? (
        <div className="pointer-events-none absolute z-20" style={{ left, top }}>
          <EntityCard entity={hover.entity} suggestions={insights[hover.entity.key]} aiUnavailable={aiUnavailable} />
        </div>
      ) : null}
    </div>
  )
}

function resolveEntity(
  info: PickingInfo,
  { cityByKey, stateByAbbr }: { cityByKey: Map<string, CityEntity>; stateByAbbr: Map<string, LaneEntity> },
): LaneEntity | CityEntity | null {
  if (!info.object) return null
  const id = info.layer?.id
  if (id === "arcs") return (info.object as HeatArc).entity
  if (id === "cities") return cityByKey.get((info.object as CityEntity).key) ?? null
  if (id === "states") return stateByAbbr.get((info.object as { properties: StateProps }).properties.abbr) ?? null
  return null
}

function arcColor(a: HeatArc, p: Palette, pick: string | null): RGBA {
  const t = a.trend_pct
  const base = t == null || Math.abs(t) < 3 ? p.steel : t > 0 ? p.good : p.bad
  if (pick) return a.key === pick ? rgb(base, 255) : rgb(base, 55)
  return rgb(base, 210)
}
