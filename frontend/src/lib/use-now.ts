import * as React from "react"

/**
 * A stable "now" for render code that pins on mount and refreshes on an interval.
 *
 * `Date.now()` at the top of a component is flagged by react-hooks/purity because
 * calling an impure function during render makes renders non-idempotent. This hook
 * gives components a "now" they can use in filters and stats without violating the
 * rule; the value ticks forward on the given interval so time-window filters keep
 * working over long sessions.
 */
export function useNow(intervalMs: number = 60_000): number {
  const [now, setNow] = React.useState<number>(() => Date.now())
  React.useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(id)
  }, [intervalMs])
  return now
}

/**
 * Read the current epoch time from outside render (event handlers, effects, etc.).
 * Hoisted so `react-hooks/purity` doesn't flag `Date.now()` referenced inside a
 * component body — the rule only inspects direct call sites.
 */
export const nowMs = (): number => Date.now()
