# Stage 1: scaffold a vanilla Strapi v5 project and install its dependencies.
# This runs during CI/Docker build where there is no TTY — create-strapi-app
# handles non-interactive environments with CI=true.
FROM node:24-alpine@sha256:44fce696f38ba8c987c4db89954051000bbd05ffd8d8659ba15ed4a8602e937f AS builder

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
#
# The version is pinned so Renovate can see it: a bump here rebuilds the image,
# and strapi-sync.sh then upgrades the project on each PVC to match.
# renovate: datasource=npm depName=@strapi/strapi
ARG STRAPI_VERSION=5.58.0
RUN yes "" | npx --yes create-strapi-app@${STRAPI_VERSION} template --no-run --skip-cloud

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
# Config owned by the image (strapi-sync.sh copies it onto the PVC). These are
# type-checked by `strapi build`, which is strict since the 5.4x scaffold.
COPY server.ts plugins.ts config/
RUN npm run build
# Drop the plugin-authoring toolchain once the admin panel is built.
# strapi-plugin-translate declares @strapi/sdk-plugin as a peerDependency, so
# npm installs it (5.4.0) and with it @strapi/pack-up 5.1.1, which pins
# vite 5.4.21 and esbuild 0.20.2 / 0.21.5 exactly. Those esbuild binaries are
# built with Go 1.20.12, which was 4 of the image's 5 Critical and 53 of
# its High findings in Trivy on 2026-10-19. Nothing else in node_modules
# requires either package (grep of the 5.58.0 image), and neither `strapi build`
# nor `strapi develop` runs them: they build plugins for publishing to npm.
RUN rm -rf node_modules/@strapi/sdk-plugin node_modules/@strapi/pack-up

# Stage 2: lean runtime image that ships the pre-built project as a seed template.
FROM node:24-alpine@sha256:44fce696f38ba8c987c4db89954051000bbd05ffd8d8659ba15ed4a8602e937f

# `apk upgrade` takes Alpine's security fixes at build time instead of waiting
# for the next node:24-alpine digest (the 5.58.0 image of 2026-10-14 still had
# expat 2.8.5, High CVE-2026-77214, until #7799 moved the base).
RUN apk upgrade --no-cache && apk add --no-cache build-base vips-dev bash

# The npm that node:24-alpine bundles (11.19.1 in 24.22.0) ships brace-expansion
# 5.0.9, undici 6.28.0 and http-cache-semantics 4.2.0, four High findings.
# 11.21.1 bundles 5.0.12, 6.29.0 and 4.3.0. Stay on npm 11 (the line Node 24
# ships); npm 12 is a major and Renovate will not auto-merge it.
# renovate: datasource=npm depName=npm
ARG NPM_VERSION=11.21.1
RUN npm install --global --no-audit --no-fund npm@${NPM_VERSION} && npm cache clean --force

# Bake the complete project (including node_modules) into the image.
# On first pod start the entrypoint copies this to the PVC mount at /srv/app.
COPY --from=builder /tmp/template /srv/template

WORKDIR /srv

COPY strapi-sync.sh /usr/local/bin/strapi-sync.sh
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /usr/local/bin/strapi-sync.sh /entrypoint.sh

EXPOSE 1337
ENTRYPOINT ["/entrypoint.sh"]
