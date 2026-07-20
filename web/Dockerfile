# Web edge — builds the static Next.js export and serves it with Caddy, which also
# reverse-proxies /api/* to the backend container (same-origin => no CORS in prod).
# Build context = web/.

# ---- build the static export ----
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci || npm install
COPY . .
# Same-origin API: the browser calls /api/... which Caddy strips + proxies to the backend.
ENV NEXT_PUBLIC_API_URL=/api
# Turnstile site key is public and baked at build time (empty = widget disabled in dev).
ARG NEXT_PUBLIC_TURNSTILE_SITE_KEY=""
ENV NEXT_PUBLIC_TURNSTILE_SITE_KEY=${NEXT_PUBLIC_TURNSTILE_SITE_KEY}
# Cloudflare Web Analytics beacon. Empty = no beacon tag is emitted at all, so a build
# without the token makes zero third-party requests.
ARG NEXT_PUBLIC_CF_BEACON_TOKEN=""
ENV NEXT_PUBLIC_CF_BEACON_TOKEN=${NEXT_PUBLIC_CF_BEACON_TOKEN}
RUN npm run build

# ---- serve ----
FROM caddy:2-alpine
COPY --from=build /app/out /srv
COPY Caddyfile /etc/caddy/Caddyfile
EXPOSE 80
