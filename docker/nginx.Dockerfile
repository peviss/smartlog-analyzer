# ---- build the web UI (src/frontend) --------------------------------------------
FROM node:24-alpine AS ui
WORKDIR /ui
COPY src/frontend/package.json src/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY src/frontend/ ./
# docs/openapi.json is only needed to regenerate types; the generated file is committed.
RUN npm run build

# ---- nginx: serves the UI and proxies the API ------------------------------------
FROM nginx:1.27-alpine
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=ui /ui/dist /usr/share/nginx/html
