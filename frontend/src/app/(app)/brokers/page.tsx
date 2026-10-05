/**
 * Brokers — /brokers  (real-data v2, overview restore)
 *
 * Dense overview table with segment pills, click-to-sort headers (user's
 * explicit extra), 12-month sparkline, and the deterministic next-action
 * chip from v1 preserved as a column. The two columns deliberately hidden
 * (Revenue, Payment-issues chip) are documented in the inventory.
 */

import { PageHeader } from "@/components/app/ui"
import { BrokersTable } from "@/components/app/brokers-table"

export default function BrokersPage() {
  return (
    <>
      <PageHeader
        eyebrow="Brokers"
        title="Broker database"
        description="Every broker on real data. Health combines recency, win rate, tone and volume (30/30/20/20)."
      />
      <BrokersTable />
    </>
  )
}
