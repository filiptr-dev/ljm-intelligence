"use client"

/**
 * Last-known-location mini map — the lanes map's recipe, shrunk: bundled `us-atlas`
 * state polygons drawn by deck.gl on a tile-less maplibre host (no tile server, no key),
 * one ScatterplotLayer dot for the unit. Loaded with next/dynamic({ ssr: false }).
 */

import "maplibre-gl/dist/maplibre-gl.css"
import * as React from "react"
import { Map as MapLibreMap, NavigationControl, type IControl } from "maplibre-gl"
import { GeoJsonLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers"
import { MapboxOverlay } from "@deck.gl/mapbox"
import { hex, mix, placeStateLabels, readPalette, rgb, MAX_BOUNDS, STATES, US_BOUNDS, type Palette, type StateLabel, type StateProps } from "./us-map-base"

export default function TruckLocationMap({ lat, lng, label }: { lat: number; lng: number; label: string }) {
  const hostRef = React.useRef<HTMLDivElement>(null)
  const mapRef = React.useRef<MapLibreMap | null>(null)
  const overlayRef = React.useRef<MapboxOverlay | null>(null)
  const [palette] = React.useState<Palette>(() => readPalette())
  const [zoom, setZoom] = React.useState(3.4)

  React.useEffect(() => {
    if (!hostRef.current || mapRef.current) return
    const map = new MapLibreMap({
      container: hostRef.current,
      style: { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": hex(palette.bg) } }] },
      bounds: US_BOUNDS,
      fitBoundsOptions: { padding: 8 },
      maxBounds: MAX_BOUNDS,
      minZoom: 2.2,
      maxZoom: 9,
      attributionControl: false,
      dragRotate: false,
      pitchWithRotate: false,
      touchPitch: false,
      cooperativeGestures: true, // the drawer scrolls; don't hijack the wheel
    })
    map.touchZoomRotate.disableRotation()
    map.addControl(new NavigationControl({ showCompass: false }), "top-right")
    const overlay = new MapboxOverlay({ interleaved: false, layers: [] })
    map.addControl(overlay as unknown as IControl)
    map.on("zoom", () => setZoom(Math.round(map.getZoom() * 4) / 4))
    mapRef.current = map
    overlayRef.current = overlay
    // The host may be laid out while a drawer is still sliding in; re-measure as it settles.
    const ro = new ResizeObserver(() => map.resize())
    ro.observe(hostRef.current)
    return () => {
      ro.disconnect()
      overlay.finalize()
      map.remove()
      mapRef.current = null
      overlayRef.current = null
    }
    // Created once; the layers effect below follows the props.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  React.useEffect(() => {
    const p = palette
    overlayRef.current?.setProps({
      layers: [
        new GeoJsonLayer<StateProps>({
          id: "states", data: STATES, pickable: false, stroked: true, filled: true,
          getFillColor: rgb(mix(p.card, p.muted, 0.55)), getLineColor: rgb(p.border), getLineWidth: 0.8, lineWidthUnits: "pixels",
        }),
        // light: abbreviations only, small, no outline weight to speak of
        new TextLayer<StateLabel>({
          id: "state-labels", data: placeStateLabels(zoom, { abbrOnly: true, fontPx: 10 }), pickable: false,
          getPosition: (l) => l.position, getText: (l) => l.text, getSize: 10, sizeUnits: "pixels",
          getColor: rgb(p.ink, 110), fontWeight: 600, getTextAnchor: "middle", getAlignmentBaseline: "center",
        }),
        new ScatterplotLayer<{ p: [number, number] }>({
          id: "halo", data: [{ p: [lng, lat] }], getPosition: (d) => d.p, getRadius: 16, radiusUnits: "pixels",
          getFillColor: rgb(p.brand, 50), stroked: false,
        }),
        new ScatterplotLayer<{ p: [number, number] }>({
          id: "unit", data: [{ p: [lng, lat] }], getPosition: (d) => d.p, getRadius: 6.5, radiusUnits: "pixels",
          getFillColor: rgb(p.brand), getLineColor: rgb(p.card), getLineWidth: 2, lineWidthUnits: "pixels", stroked: true,
        }),
      ],
    })
  }, [lat, lng, palette, zoom])

  // Zoom in on the unit a little, but keep the whole country readable around it. Only when the unit
  // moves: the layers effect above also re-runs on user zoom and must not snap the view back.
  React.useEffect(() => {
    mapRef.current?.easeTo({ center: [lng, lat], zoom: 3.4, duration: 0 })
  }, [lat, lng])

  return (
    <div className="relative h-56 w-full overflow-hidden rounded-sm border border-border bg-background" data-testid="truck-location-map" role="img" aria-label={label}>
      {/* maplibre forces `position: relative` on its host, so the sized wrapper is a separate element */}
      <div className="absolute inset-0"><div ref={hostRef} className="h-full w-full" /></div>
    </div>
  )
}
