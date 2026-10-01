import "server-only"

/**
 * Server-only entry — re-exports the typed `api` + helpers with a build-
 * time guard that prevents accidental browser imports.
 *
 * Pages that want to prove "this is server-first, this file can never
 * leak into a client bundle" import from here instead of `./client`.
 * Islands that need shared types continue to import from `./types`.
 */

export { api, apiConfigured, ApiRequestError } from "./client"
