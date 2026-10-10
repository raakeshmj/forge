# Control-plane UI (ADR 0012): build once, run the self-contained Next.js server.
FROM node:22.20.0-alpine AS build
WORKDIR /ui
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM node:22.20.0-alpine
WORKDIR /ui
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build /ui/.next/standalone ./
COPY --from=build /ui/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
