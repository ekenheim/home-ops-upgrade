#!/bin/bash
set -e

APP_DIR="/srv/app"

if [ ! -f "$APP_DIR/package.json" ]; then
  echo "No Strapi project found at $APP_DIR. Creating a new Strapi v5 project..."
  npx create-strapi-app@latest app \
    --no-run \
    --dbclient=postgres \
    --dbhost="${DATABASE_HOST}" \
    --dbport="${DATABASE_PORT:-5432}" \
    --dbname="${DATABASE_NAME}" \
    --dbusername="${DATABASE_USERNAME}" \
    --dbpassword="${DATABASE_PASSWORD}" \
    --dbssl="${DATABASE_SSL:-false}"
  echo "Project created successfully."
fi

cd "$APP_DIR"
echo "Starting Strapi (NODE_ENV=${NODE_ENV:-development})..."
exec npm run develop
