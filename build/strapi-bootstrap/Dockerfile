# Stage 1: scaffold a vanilla Strapi v5 project and install its dependencies.
# This runs during CI/Docker build where there is no TTY — create-strapi-app
# handles non-interactive environments with CI=true.
FROM node:24-alpine AS builder

RUN apk add --no-cache build-base gcc autoconf automake zlib-dev libpng-dev vips-dev git bash

WORKDIR /tmp

ENV CI=true

# Required so create-strapi-app's `git commit` step doesn't fail with exit 128.
RUN git config --global user.email "build@docker.local" && \
    git config --global user.name "Docker Build"

# create-strapi-app v5 ignores CI=true for its two interactive prompts. We can't
# use a finite pipe (printf/echo) because Node v24 readline fires 'close' on EOF
# before inquirer finishes processing, causing ERR_USE_AFTER_CLOSE.
# `yes ""` keeps the pipe open (no EOF) and sends empty newlines that accept each
# prompt's default: sqlite=Y (what we want) and example=N (what we want).
RUN yes "" | npx --yes create-strapi-app@latest template --no-run --skip-cloud

# Install deps and pre-build the admin panel as separate layers.
# This avoids paying the Vite compilation cost on every pod restart.
WORKDIR /tmp/template
RUN npm install
# Install the PostgreSQL driver — the scaffold defaults to SQLite so pg is not
# included in the generated package.json, but the deployment uses Postgres.
RUN npm install pg
# Translate plugin with DeepL provider — auto-translates content between i18n locales.
# Using v5-compatible pre-release until stable v2.0 ships.
RUN npm install strapi-plugin-translate@2.0.0-next.4 strapi-provider-translate-deepl@1.3.0-next.4
# Allow non-localhost hosts in Vite's dev server so the admin panel loads
# when accessed via the ingress hostname instead of localhost.
RUN mkdir -p src/admin && printf 'import { mergeConfig, type UserConfig } from "vite";\n\nexport default (config: UserConfig) => {\n  return mergeConfig(config, {\n    server: { allowedHosts: true },\n  });\n};\n' > src/admin/vite.config.ts
# CORS middleware — reads allowed origins from CORS_ORIGIN env var (comma-separated).
# Falls back to * so dev mode works without configuration.
RUN printf 'export default [\n  "strapi::logger",\n  "strapi::errors",\n  "strapi::security",\n  {\n    name: "strapi::cors",\n    config: {\n      origin: process.env.CORS_ORIGIN ? process.env.CORS_ORIGIN.split(",") : ["*"],\n      methods: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],\n      headers: ["Content-Type", "Authorization", "Origin", "Accept"],\n      keepHeaderOnError: true,\n    },\n  },\n  "strapi::poweredBy",\n  "strapi::query",\n  "strapi::body",\n  "strapi::session",\n  "strapi::favicon",\n  "strapi::public",\n];\n' > config/middlewares.js
# Translate plugin configuration — uses DeepL provider with API key from env var.
RUN printf 'export default ({ env }) => ({\n  translate: {\n    enabled: true,\n    config: {\n      provider: "deepl",\n      providerOptions: {\n        apiKey: env("DEEPL_API_KEY"),\n        freeApi: true,\n      },\n      translatedFieldTypes: [\n        "string",\n        { type: "text", format: "plain" },\n        { type: "richtext", format: "markdown" },\n        "component",\n        "dynamiczone",\n      ],\n      translateRelations: true,\n    },\n  },\n});\n' > config/plugins.ts
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
