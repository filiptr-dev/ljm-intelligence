/**
 * Notifications — bell feed wrappers over the typed openapi-fetch client.
 *
 * One module per domain (bb-unikoop pattern). Polls `GET /inbox/notifications`
 * every 45s from the client (short of the 15-min incremental tick so new
 * mail surfaces within one incremental cycle + one poll).
 */
import { api, ApiRequestError } from "./client"
import type { components } from "./schema"

export type NotificationItem = components["schemas"]["NotificationItemOut"]
export type NotificationsList = components["schemas"]["NotificationsListOut"]

function unwrap<T>(res: { data?: T; error?: unknown; response: Response }, path: string): T {
  if (res.data !== undefined) return res.data
  throw new ApiRequestError(res.response.status, path, res.error)
}

export async function listNotifications(limit = 50, signal?: AbortSignal): Promise<NotificationsList> {
  const res = await api.GET("/inbox/notifications", { params: { query: { limit } }, signal })
  return unwrap(res, "/inbox/notifications")
}

export async function markNotificationRead(id: number): Promise<void> {
  await api.POST("/inbox/notifications/{notification_id}/read", {
    params: { path: { notification_id: id } },
  })
}

export async function markAllNotificationsRead(): Promise<void> {
  await api.POST("/inbox/notifications/read-all", {})
}
