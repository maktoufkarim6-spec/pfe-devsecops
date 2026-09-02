FROM node:18-alpine AS build
WORKDIR /app
COPY . /app/
RUN npm install
RUN npm run build

FROM node:18-alpine
WORKDIR /app
COPY --from=build /app/build /app/
COPY package.json /app/package.json
COPY ormconfig.json /app/ormconfig.json
COPY wait-for-it.sh /app/wait-for-it.sh
RUN npm install
RUN apk add --no-cache bash
EXPOSE 5500
