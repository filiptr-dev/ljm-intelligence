# LJM Intelligence frontend — Next.js 16 / pnpm 11
# Runs `next dev` for local development (hot reload, no build step needed).
# For production, swap CMD to: pnpm build && pnpm start

FROM node:24-alpine

# Install pnpm via corepack (matches packageManager in package.json)
RUN corepack enable && corepack prepare pnpm@11.22.0 --activate

WORKDIR /app

# Copy dependency manifests first so layer is cached unless lockfile changes
COPY pnpm-workspace.yaml package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# Copy rest of source (bind-mount in dev overrides this at runtime)
COPY . .

EXPOSE 3000

# API_URL is server-only; NEXT_PUBLIC_API_URL is browser-visible.
# Both are set via docker-compose env so the frontend talks to the api container.
ENV NEXT_TELEMETRY_DISABLED=1

CMD ["pnpm", "dev", "-p", "3000"]
