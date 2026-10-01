/**
 * Client-safe type re-exports from the generated OpenAPI schema.
 *
 * `lib/api/client.ts` is annotated `import "server-only"` so the typed
 * runtime client never ships to the browser. Islands that still need the
 * backend's response/request shapes import from this module instead — it
 * contains types only, zero runtime, and is safe anywhere.
 */

export type { paths, components, operations } from "./schema"
