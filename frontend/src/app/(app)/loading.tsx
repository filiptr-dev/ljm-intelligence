/**
 * NOTE (2026-10-05 review follow-up): a group-level loading.tsx forced
 * the Overview skeleton onto every (app) route (brokers, emails, …),
 * which caused a visible flash of unrelated chrome on each navigation.
 * The Overview page now owns its own `<Suspense fallback={<OverviewSkeleton/>}>`,
 * so the group-level fallback is intentionally a no-op. This file is
 * kept as `return null` instead of being deleted so Next.js doesn't
 * pick up a stale cached loading boundary.
 */

export default function AppLoading() {
  return null
}
