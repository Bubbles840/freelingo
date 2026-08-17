FROM node:25-alpine AS deps
WORKDIR /app
RUN npm install -g npm@11
COPY frontend/package.json frontend/package-lock.json ./
COPY frontend/scripts/ ./scripts/
RUN npm ci

FROM node:25-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ .
COPY messages/ ./messages/
# Re-run postinstall to ensure all ORT WASM + .mjs loaders are present in public/vad/
# (deps stage generates them but COPY frontend/ . above overwrites public/vad from source)
RUN node scripts/copy-vad-models.js
ENV NEXT_TELEMETRY_DISABLED=1
# Fork: next/font downloads Google Fonts during build; transient fetch
# failures abort it. Prefer IPv4 and retry once before giving up.
ENV NODE_OPTIONS=--dns-result-order=ipv4first
RUN --mount=type=cache,target=/app/.next/cache \
    npm run build || (echo "retrying build (flaky font fetch)" && npm run build)

FROM node:25-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME=0.0.0.0
CMD ["node", "server.js"]