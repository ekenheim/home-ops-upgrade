#!/bin/bash
set -e

APP_DIR="/srv/app"

# Ensure cwd is /srv so `create-strapi-app app` writes to /srv/app (the PVC mount).
cd /srv

echo "Working directory: $(pwd)"
echo "Mount contents: $(ls -la /srv/)"

if [ ! -f "$APP_DIR/package.json" ]; then
  echo "No Strapi project found at $APP_DIR. Creating a new Strapi v5 project..."
  npx create-strapi-app@latest app \
    --no-run \
    --skip-cloud \
    --dbclient=postgres \
    --dbhost="${DATABASE_HOST}" \
    --dbport="${DATABASE_PORT:-5432}" \
    --dbname="${DATABASE_NAME}" \
    --dbusername="${DATABASE_USERNAME}" \
    --dbpassword="${DATABASE_PASSWORD}" \
    --dbssl="${DATABASE_SSL:-false}"
  echo "Project created. Contents: $(ls "$APP_DIR" | head -10)"
fi

cd "$APP_DIR"
echo "Starting Strapi (NODE_ENV=${NODE_ENV:-development}) from $(pwd)..."
exec npm run develop
