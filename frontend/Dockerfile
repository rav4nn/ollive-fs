# --- build stage ---
FROM node:20-alpine AS builder

WORKDIR /app

# Public env var must be baked in at build time for Next.js.
ARG NEXT_PUBLIC_API_BASE_URL
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL

COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

COPY tsconfig.json next.config.mjs next-env.d.ts ./
COPY src ./src
COPY public ./public

RUN npm run build

# --- runtime stage ---
FROM node:20-alpine AS runner

WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000

# Standalone output bundles the minimal node_modules subset Next needs.
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000

CMD ["node", "server.js"]
