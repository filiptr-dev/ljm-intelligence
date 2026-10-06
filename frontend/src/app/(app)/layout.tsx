import { AppSidebar } from "@/components/app/app-sidebar"
import { EngineProvider } from "@/components/app/engine"
import { Topbar } from "@/components/app/topbar"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"

// Still per-request so time-relative labels (`timeAgo`) read fresh.
export const dynamic = "force-dynamic"

/**
 * App-group layout — wraps every authenticated page in the sidebar, topbar,
 * and campaign store. Nothing in the `(auth)` route group (e.g. /login) sees
 * this layout, which is the whole point: a signed-out visitor must land on
 * a chrome-free sign-in form.
 *
 * EngineProvider no longer takes seed/profile/knownNames: the seeded fake
 * feed is gone, replaced by a real `useLiveFeed()` backend read (plan
 * 2026-10-06).
 */
export default function AppLayout({ children }: LayoutProps<"/">) {
  return (
    <EngineProvider>
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
