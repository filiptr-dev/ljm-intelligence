# FreightRadar: broker intelligence demo

Client demo for a US + EU trucking company (Ironline Transport, 48 trucks). It shows three things:

1. **Broker Intelligence**: semantic analysis of ~2,700 broker emails. Shows who they work with most, who rejects them and why, reply-speed impact, best send times, lanes, rates and k-means broker segments.
2. **Lead Finder**: a crawler that is "always on", streams new broker leads and scores each one against the client's best brokers (lookalike score). Includes one-click outreach.
3. **New campaign** (`/outreach`): name and goal (replies, interested replies, new customers, loads), audience (new leads, existing brokers or both, with search), an AI-written message with plain-English personal fields, automatic follow-ups to non-responders, design, and send now / at the best time / at a picked date. **Reuse** copies any past campaign.
4. **Campaigns** (`/campaigns`): every campaign with goal progress, funnel, follow-up step results and each reply tagged by AI sentiment (interested, asked for rates, not now, out of office, not interested, unsubscribe). The "what works" analysis ranks campaign types, audiences, tones, launch days and subject lines. It includes 6 months of dummy history (`src/lib/campaigns/history.ts`).

All data is dummy data, generated with a fixed seed.

## Run

```bash
pnpm install
pnpm dev          # http://localhost:3000
```

Tip for the meeting: move between pages with the sidebar (client-side navigation). A full browser reload restarts the live crawler session. Campaigns survive reloads (localStorage).

## How it works

| Layer | File | Notes |
|---|---|---|
| Dummy data | `src/lib/data/generate.ts` | 150 brokers (90 US / 60 EU), 18 months of load threads: offers, quotes, bookings, rejections, invoices, complaints. Hidden "persona" drives behaviour. |
| AI (mock) | `src/lib/ai/mock.ts` | Reads the email **text** (patterns and a sentiment lexicon) and extracts intent, sentiment, rate, lane, equipment and rejection reason. The analysis never sees the hidden labels. |
| AI (Gemini) | `src/lib/ai/gemini.ts` | Same interface. Set `AI_PROVIDER=gemini GEMINI_API_KEY=…` to switch. Not tested yet. |
| Data science | `src/lib/analytics/` | Thread reconstruction, per-broker stats, health score, k-means (k-means++ init, z-scored features), cosine-similarity lookalike scoring, reply-time and send-time analysis. |
| Live engine | `src/components/app/engine.tsx` | Client-side crawler stream, auto-outreach, scheduled sends, follow-ups, delivery/open/reply/won simulation. |
| Reply AI (mock) | `src/lib/ai/replies.ts` | Reads each reply and returns category + sentiment. |
| Campaign analysis | `src/lib/campaigns/metrics.ts` | Per-campaign summary, goal progress, and the cross-campaign "what works" insights. |

## Branding

The client is `CLIENT` in `src/lib/data/types.ts`. Change the name, dispatcher, phone and fleet there. The product name "FreightRadar" is in `src/components/brand/marks.tsx`.
