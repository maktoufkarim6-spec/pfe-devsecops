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
# apk upgrade : correctifs de securite du systeme (ex. libcrypto3), l'image node:18 n'etant plus mise a jour
RUN apk upgrade --no-cache && apk add --no-cache bash
COPY package.json package-lock.json ./
# Dependances de production, puis suppression de npm, npx, corepack et yarn :
# inutiles a l'execution et sources de failles (ex. tar embarque dans npm)
RUN npm ci --omit=dev --no-audit --no-fund \
 && npm cache clean --force \
 && rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
           /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack \
           /opt/yarn-* /usr/local/bin/yarn /usr/local/bin/yarnpkg
COPY --from=build /app/build ./
# ormconfig.json ne sert qu'en local (docker-compose) : en Kubernetes, les variables TYPEORM_* le remplacent.
COPY ormconfig.json wait-for-it.sh ./
USER node
EXPOSE 5500
