import type { Metadata } from "next"
import { Barlow, Barlow_Condensed, IBM_Plex_Mono } from "next/font/google"
import { AppSidebar } from "@/components/app/app-sidebar"
import { EngineProvider } from "@/components/app/engine"
import { Topbar } from "@/components/app/topbar"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { getStore } from "@/lib/data/store"
import { DEMO_NOW, LEAD_KIND_LABEL } from "@/lib/data/types"
import "./globals.css"

const sans = Barlow({ variable: "--font-barlow", subsets: ["latin", "latin-ext"], weight: ["400", "500", "600", "700"] })
const heading = Barlow_Condensed({ variable: "--font-barlow-condensed", subsets: ["latin", "latin-ext"], weight: ["500", "600", "700"] })
const mono = IBM_Plex_Mono({ variable: "--font-plex-mono", subsets: ["latin"], weight: ["400", "500", "600"] })

// render per request so "today" and "min ago" are always relative to the demo moment
export const dynamic = "force-dynamic"

export const metadata: Metadata = {
  title: "FreightRadar · Broker Intelligence",
  description: "Find, analyse and win freight brokers for your trucks.",
}

export default async function RootLayout({ children }: LayoutProps<"/">) {
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
    <html lang="en" className={`${sans.variable} ${heading.variable} ${mono.variable} h-full antialiased`}>
      <body className="min-h-full bg-background">
        <TooltipProvider>
          <EngineProvider profile={store.profile} knownNames={store.knownNames} baseline={baseline} seed={seed}>
            <SidebarProvider>
              <AppSidebar />
              <SidebarInset className="min-w-0 bg-background">
                <Topbar />
                <main className="mx-auto w-full max-w-[1440px] min-w-0 px-4 pt-5 pb-16 md:px-8">{children}</main>
              </SidebarInset>
            </SidebarProvider>
          </EngineProvider>
          <Toaster position="bottom-right" theme="light" />
        </TooltipProvider>
      </body>
    </html>
  )
}
