# ---------- build : compilation React (cette etape n'est pas dans l'image finale) ----------
FROM node:12-alpine AS build
WORKDIR /app
RUN chown node:node /app
USER node
COPY --chown=node:node package.json package-lock.json ./
RUN npm install --silent
# Copie explicite (pas de "COPY . .") : aucun fichier inattendu n'entre dans l'image
COPY --chown=node:node tsconfig.json ./
COPY --chown=node:node public ./public
COPY --chown=node:node src ./src
RUN npm run build

# ---------- runtime : nginx sans droits root (utilisateur 101, port 8080) ----------
FROM nginxinc/nginx-unprivileged:stable-alpine
USER root
RUN apk upgrade --no-cache && rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/app.conf
COPY --from=build /app/build /usr/share/nginx/html
USER 101
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
