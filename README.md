# LJM Intelligence

First-meeting sales artefact for **LJM International** — a Lincoln Park, NJ dry-van
carrier running the eastern US. Broker intelligence, a live lead finder, and an
outreach engine, all in one dashboard.

| Folder | What | Runs on |
|---|---|---|
| `frontend/` | Next.js 16 dashboard (React 19, Tailwind, shadcn) | Vercel (Root Directory `frontend`) |
| `backend/` | FastAPI service — the real FMCSA crawler lands here | Render (planned) |

The frontend today reads seeded, in-memory data (fixed seed) so the demo is
self-contained. The backend folder is a placeholder until the crawler task
(`fastapi-backend-render`) lands.

## Run the frontend locally

```bash
cd frontend
pnpm install
pnpm dev -p 3100   # http://localhost:3100
```

More detail (branding, mock vs Gemini AI, data layout) in
[`frontend/README.md`](frontend/README.md).
