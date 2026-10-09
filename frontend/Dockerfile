# DMIS command center (Next.js). DIP_API_URL is baked into the rewrite at build time.
FROM node:20-slim AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN mkdir -p public            # static assets folder (the app has none yet; next start expects it)
ARG DIP_API_URL=http://api:8000
ARG NEXT_PUBLIC_MAPBOX_TOKEN=
ENV DIP_API_URL=$DIP_API_URL NEXT_PUBLIC_MAPBOX_TOKEN=$NEXT_PUBLIC_MAPBOX_TOKEN NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:20-slim
WORKDIR /web
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build /web/package.json /web/package-lock.json ./
COPY --from=build /web/node_modules ./node_modules
COPY --from=build /web/.next ./.next
COPY --from=build /web/public ./public
COPY --from=build /web/next.config.ts ./
EXPOSE 3000
CMD ["npx", "next", "start", "-p", "3000"]
