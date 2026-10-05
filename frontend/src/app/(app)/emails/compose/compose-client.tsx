"use client"

/**
 * Client wrapper that mounts `SingleEmailBuilder` with the real-send
 * adapter. The adapter is a function value — functions can't be passed
 * from Server Components to Client Components, so the SC renders this
 * tiny bridge and the bridge injects the adapter in-browser.
 */

import type { ContactOption } from "@/components/app/email-composer"
import { SingleEmailBuilder } from "@/components/app/single-email-builder"
import { singleSendAdapter } from "@/lib/api/email-send"

export function ComposeClient({
  initialRecipient,
  initialSubject,
  initialBody,
}: {
  initialRecipient?: ContactOption
  initialSubject?: string
  initialBody?: string
}) {
  return (
    <SingleEmailBuilder
      contacts={[]}
      backHref="/emails"
      backLabel="Back to messages"
      initialToId={initialRecipient?.id}
      initialRecipient={initialRecipient}
      initialSubject={initialSubject}
      initialBody={initialBody}
      onSend={singleSendAdapter}
    />
  )
}
