# Stage 1: scaffold a vanilla Strapi v5 project and install its dependencies.
# This runs during CI/Docker build where there is no TTY — create-strapi-app
# handles non-interactive environments with CI=true.
FROM node:24-alpine AS builder

RUN apk add --no-cache build-base gcc autoconf automake zlib-dev libpng-dev vips-dev git bash

WORKDIR /tmp

ENV CI=true

# CI=true suppresses all interactive prompts in create-strapi-app v5.
# --skip-cloud and --no-run keep it fully non-interactive without needing `yes`.
RUN npx --yes create-strapi-app@latest template --no-run --skip-cloud

# Install deps and pre-build the admin panel as separate layers.
# This avoids paying the Vite compilation cost on every pod restart.
WORKDIR /tmp/template
RUN npm install
RUN npm run build

# Stage 2: lean runtime image that ships the pre-built project as a seed template.
FROM node:24-alpine

RUN apk add --no-cache build-base vips-dev bash

# Bake the complete project (including node_modules) into the image.
# On first pod start the entrypoint copies this to the PVC mount at /srv/app.
COPY --from=builder /tmp/template /srv/template

WORKDIR /srv

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 1337
ENTRYPOINT ["/entrypoint.sh"]
