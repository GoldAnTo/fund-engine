FROM node:24-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
COPY server/localGatewayProxy.ts ./server/localGatewayProxy.ts
RUN npm run build

FROM node:24-bookworm-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/dist ./dist
COPY server/gatewayServer.mjs ./server/gatewayServer.mjs
USER node
EXPOSE 8080
CMD ["node", "server/gatewayServer.mjs"]
