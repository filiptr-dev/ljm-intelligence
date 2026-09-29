import { HardHat } from "lucide-react"
import { PageHeader, Panel } from "./ui"

/**
 * Shared "In development" tool page shell.
 *
 * On-brand LJM look: safety-red accent stripe, charcoal panels, hard-hat mark.
 * Every tool that hasn't shipped yet renders one of these so nothing 404s and
 * the client sees the intent clearly.
 */
export function ToolPlaceholder({
  eyebrow,
  title,
  description,
  whatItWillDo,
  data,
}: {
  eyebrow: string
  title: string
  description: string
  whatItWillDo: string[]
  data: string[]
}) {
  return (
    <>
      <PageHeader
        eyebrow={eyebrow}
        title={title}
        description={description}
        actions={
          <span className="inline-flex items-center gap-1.5 rounded-sm border-2 border-asphalt bg-safety px-2.5 py-1 text-[0.68rem] font-bold tracking-wider text-asphalt uppercase">
            <HardHat className="size-3.5" /> In development — building now
          </span>
        }
      />

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel
          title={<span className="inline-flex items-center gap-2"><span className="inline-block h-4 w-1 bg-safety" aria-hidden /> What it will do</span>}
          description="The shape of the finished tool, so you know what to expect."
        >
          <ul className="space-y-2 text-sm">
            {whatItWillDo.map((line) => (
              <li key={line} className="flex gap-2">
                <span className="mt-1 size-1.5 shrink-0 rounded-full bg-safety" aria-hidden />
                <span>{line}</span>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel
          title={<span className="inline-flex items-center gap-2"><span className="inline-block h-4 w-1 bg-chart-2" aria-hidden /> Data it uses</span>}
          description="Pulled from the crawler's persisted Neon store — no re-crawl on open."
        >
          <ul className="space-y-2 text-sm text-muted-foreground">
            {data.map((d) => (
              <li key={d} className="flex gap-2">
                <span className="mt-1 size-1.5 shrink-0 rounded-full bg-muted-foreground" aria-hidden />
                <span>{d}</span>
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      <div className="mt-6 rounded-sm border-l-4 border-safety bg-asphalt px-4 py-3 text-sm text-white">
        <span className="font-semibold">Being built right now.</span> Placeholders on the sidebar are wired to real routes so the menu never breaks the demo; each will replace this page as it ships.
      </div>
    </>
  )
}
