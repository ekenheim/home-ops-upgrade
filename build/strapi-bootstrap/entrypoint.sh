#!/bin/bash
set -e

APP_DIR="/srv/app"

# Seed the PVC on first run, upgrade it when this image ships a newer Strapi.
/usr/local/bin/strapi-sync.sh

cd "$APP_DIR"
echo "Starting Strapi (NODE_ENV=${NODE_ENV:-development})..."
if [ "${NODE_ENV}" = "production" ]; then
  exec npm run start
else
  exec npm run develop
fi
