# ---------- build : compilation TypeScript avec les dependances de developpement ----------
FROM node:18-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# ---------- runtime : uniquement les dependances de production (moins de failles, image plus legere) ----------
FROM node:18-alpine
WORKDIR /app
ENV NODE_ENV=production
RUN apk add --no-cache bash
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force
COPY --from=build /app/build ./
# ormconfig.json ne sert qu'en local (docker-compose) : en Kubernetes, les variables TYPEORM_* le remplacent.
COPY ormconfig.json wait-for-it.sh ./
USER node
EXPOSE 5500
