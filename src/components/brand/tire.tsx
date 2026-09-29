import { cn } from "@/lib/utils"

/** Truck tire + aluminium wheel, drawn as a group so trucks can reuse it. */
export function TireGlyph({ cx = 50, cy = 50, r = 48, spinning = false }: { cx?: number; cy?: number; r?: number; spinning?: boolean }) {
  const s = r / 48
  const treads = Array.from({ length: 28 }, (_, i) => (i * 360) / 28)
  const lugs = Array.from({ length: 10 }, (_, i) => (i * 360) / 10)
  return (
    <g transform={`translate(${cx - 50 * s} ${cy - 50 * s}) scale(${s})`}>
      <g className={spinning ? "animate-tire" : undefined}>
        <circle cx="50" cy="50" r="48" fill="#1b1c1f" />
        {treads.map((a) => (
          <rect key={a} x="47.5" y="1.5" width="5" height="7" rx="1" fill="#34363b" transform={`rotate(${a} 50 50)`} />
        ))}
        <circle cx="50" cy="50" r="37" fill="none" stroke="#2e3035" strokeWidth="2" />
        <circle cx="50" cy="50" r="30" fill="#c9ccd1" />
        <circle cx="50" cy="50" r="26" fill="#aeb3ba" />
        {lugs.map((a) => (
          <ellipse key={a} cx="50" cy="29" rx="3.2" ry="5" fill="#6f747c" transform={`rotate(${a} 50 50)`} />
        ))}
        <circle cx="50" cy="50" r="13" fill="#dfe2e6" />
        {lugs.map((a) => (
          <circle key={`n${a}`} cx="50" cy="41" r="1.8" fill="#4a4e55" transform={`rotate(${a} 50 50)`} />
        ))}
        <circle cx="50" cy="50" r="5" fill="#2b2d31" />
      </g>
    </g>
  )
}

export function Tire({ className, spinning = false, title }: { className?: string; spinning?: boolean; title?: string }) {
  return (
    <svg viewBox="0 0 100 100" className={cn("size-8", className)} role={title ? "img" : undefined} aria-hidden={title ? undefined : true}>
      {title ? <title>{title}</title> : null}
      <TireGlyph spinning={spinning} />
    </svg>
  )
}
