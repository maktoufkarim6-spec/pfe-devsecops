# ---------- build : compilation React (cette etape n'est pas dans l'image finale) ----------
FROM node:12-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm install --silent
COPY . .
RUN npm run build

# ---------- runtime : nginx Alpine (moins de paquets systeme, donc moins de failles) ----------
FROM nginx:stable-alpine
RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/
COPY --from=build /app/build /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
