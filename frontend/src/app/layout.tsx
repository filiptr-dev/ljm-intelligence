import type { Metadata } from "next"
import { Barlow, Barlow_Condensed, IBM_Plex_Mono } from "next/font/google"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { SessionProvider } from "@/lib/auth/session"
import "./globals.css"

const sans = Barlow({ variable: "--font-barlow", subsets: ["latin", "latin-ext"], weight: ["400", "500", "600", "700"] })
const heading = Barlow_Condensed({ variable: "--font-barlow-condensed", subsets: ["latin", "latin-ext"], weight: ["500", "600", "700"] })
const mono = IBM_Plex_Mono({ variable: "--font-plex-mono", subsets: ["latin"], weight: ["400", "500", "600"] })

export const metadata: Metadata = {
  title: "LJM Intelligence · Broker Intelligence",
  description: "Find, analyse and win freight brokers for LJM International's dry-van fleet across the eastern US.",
}

/**
 * Root layout — deliberately minimal. The app chrome (sidebar, topbar,
 * engine ticker, store-backed data) lives in the `(app)` route group's
 * layout so unauthenticated pages (the `(auth)` group — currently just
 * /login) render blank. See `src/app/(app)/layout.tsx`.
 */
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${sans.variable} ${heading.variable} ${mono.variable} h-full antialiased`}>
      <body className="min-h-full bg-background">
        <SessionProvider>
          <TooltipProvider>
            {children}
            <Toaster position="bottom-right" theme="light" />
          </TooltipProvider>
        </SessionProvider>
      </body>
    </html>
  )
}
