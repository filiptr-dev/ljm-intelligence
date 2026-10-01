/**
 * Auth-group layout — intentionally blank. Pages in this group (currently
 * only /login) render without the sidebar, topbar, or engine ticker, so a
 * signed-out visitor sees only the sign-in form.
 *
 * `dynamic = "force-dynamic"` matches the old root-layout behaviour: the
 * login page uses `useSearchParams()` to read the `?next=` redirect target,
 * and prerendering that in client components without a Suspense boundary
 * trips Next's CSR-bailout error. Dynamic rendering avoids the prerender
 * pass entirely and keeps the page simple.
 */
export const dynamic = "force-dynamic"

export default function AuthLayout({ children }: LayoutProps<"/">) {
  return <>{children}</>
}
