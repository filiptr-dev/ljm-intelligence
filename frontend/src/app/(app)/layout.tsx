import { AppSidebar } from "@/components/app/app-sidebar"
import { EngineProvider } from "@/components/app/engine"
import { Topbar } from "@/components/app/topbar"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { getStore } from "@/lib/data/store"
import { DEMO_NOW, LEAD_KIND_LABEL } from "@/lib/data/types"

// render per request so "today" and "min ago" are always relative to the demo moment
export const dynamic = "force-dynamic"

/**
 * App-group layout — wraps every authenticated page in the sidebar, topbar,
 * and demo engine. Nothing in the `(auth)` route group (e.g. /login) sees
 * this layout, which is the whole point: a signed-out visitor must land on
 * a chrome-free sign-in form.
 */
export default async function AppLayout({ children }: LayoutProps<"/">) {
  const store = await getStore()
  const today = store.leads.filter((l) => DEMO_NOW.getTime() - new Date(l.discoveredAt).getTime() < 86_400_000).length
  const baseline = { scanned: 18_240 + today * 37, found: today, sent: 64, replies: 9 }
  const seed = store.leads.slice(0, 8).map((l) => ({
    at: new Date(l.discoveredAt).getTime(),
    text: l.name,
    detail: `${LEAD_KIND_LABEL[l.kind]}${l.industry ? ` · ${l.industry}` : ""} · ${l.hq} · via ${l.source}`,
    score: l.score,
    region: l.region,
  }))

  return (
    <EngineProvider profile={store.profile} knownNames={store.knownNames} baseline={baseline} seed={seed}>
      <SidebarProvider>
        <AppSidebar />
        <SidebarInset className="min-w-0 bg-background">
          <Topbar />
          <main className="mx-auto w-full max-w-[1440px] min-w-0 px-4 pt-5 pb-16 md:px-8">{children}</main>
        </SidebarInset>
      </SidebarProvider>
    </EngineProvider>
  )
}
