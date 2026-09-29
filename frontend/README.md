# LJM Intelligence: broker intelligence demo

First-meeting sales artefact for **LJM International** — a Lincoln Park, NJ dry-van
carrier running the eastern US. It shows four things:

1. **Broker Intelligence**: semantic analysis of ~2,700 broker emails. Shows who they work with most, who rejects them and why, reply-speed impact, best send times, lanes, rates and k-means broker segments.
2. **Lead Finder**: a crawler that is "always on", streams new broker / shipper / forwarder leads and scores each one against LJM's best partners (lookalike score). Includes one-click outreach.
3. **New campaign** (`/outreach`): name and goal (replies, interested replies, new customers, loads), audience (new leads, existing brokers or both, with search), an AI-written message with plain-English personal fields, automatic follow-ups to non-responders, design, and send now / at the best time / at a picked date. **Reuse** copies any past campaign.
4. **Campaigns** (`/campaigns`): every campaign with goal progress, funnel, follow-up step results and each reply tagged by AI sentiment (interested, asked for rates, not now, out of office, not interested, unsubscribe). The "what works" analysis ranks campaign types, audiences, tones, launch days and subject lines. It includes 6 months of dummy history (`src/lib/campaigns/history.ts`).

All data in this rev is dummy data, generated with a fixed seed. Later revs will
stream real registrations from the FMCSA Company Census SODA API into the Lead
Finder alongside the seeded pool.

## Run

```bash
pnpm install
pnpm dev -p 3100  # http://localhost:3100
```

Tip for the meeting: move between pages with the sidebar (client-side navigation). A full browser reload restarts the live crawler session. Campaigns survive reloads (localStorage; key `ljm.campaigns.v1`).

## How it works

| Layer | File | Notes |
|---|---|---|
| Dummy data | `src/lib/data/generate.ts` | 150 brokers, 18 months of load threads: offers, quotes, bookings, rejections, invoices, complaints. Hidden "persona" drives behaviour. |
| AI (mock) | `src/lib/ai/mock.ts` | Reads the email **text** (patterns and a sentiment lexicon) and extracts intent, sentiment, rate, lane, equipment and rejection reason. The analysis never sees the hidden labels. |
| AI (Gemini) | `src/lib/ai/gemini.ts` | Same interface. Set `AI_PROVIDER=gemini GEMINI_API_KEY=…` to switch. Not tested yet. |
| Data science | `src/lib/analytics/` | Thread reconstruction, per-broker stats, health score, k-means (k-means++ init, z-scored features), cosine-similarity lookalike scoring, reply-time and send-time analysis. |
| Live engine | `src/components/app/engine.tsx` | Client-side crawler stream, auto-outreach, scheduled sends, follow-ups, delivery/open/reply/won simulation. |
| Reply AI (mock) | `src/lib/ai/replies.ts` | Reads each reply and returns category + sentiment. |
| Campaign analysis | `src/lib/campaigns/metrics.ts` | Per-campaign summary, goal progress, and the cross-campaign "what works" insights. |

## Branding

The client is `CLIENT` in `src/lib/data/types.ts`. The product mark and lockup
live in `src/components/brand/marks.tsx` (`LogoMark`, `LogoMarkCompact`,
`Wordmark`). The full-lockup SVG is also mirrored at `public/ljm-intelligence.svg`
and the favicon at `src/app/icon.svg` — all `currentColor`, so one knob (a
Tailwind `text-*` class) drives the mark's colour on any surface.
