#!/bin/bash
set -e

APP_DIR="/srv/app"

# On first run (empty PVC), seed from the template baked into the image.
if [ ! -f "$APP_DIR/package.json" ]; then
  echo "Seeding Strapi project from image template to PVC..."
  cp -a /srv/template/. "$APP_DIR/"
  echo "Seed complete. Files: $(ls "$APP_DIR" | head -10)"
fi

cd "$APP_DIR"
echo "Starting Strapi (NODE_ENV=${NODE_ENV:-development})..."
if [ "${NODE_ENV}" = "production" ]; then
  exec npm run start
else
  exec npm run develop
fi
