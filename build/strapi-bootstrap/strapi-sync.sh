#!/bin/bash
# Reconcile the Strapi project on the PVC with the template baked into this image:
#   1. seed it on first run,
#   2. upgrade it when the image ships a newer @strapi/strapi,
#   3. sync the config files that are owned by the image.
#
# The project (src/, public/uploads, package.json "strapi" ids) lives on the PVC
# because Content-Type Builder writes schemas there in develop mode. Without
# step 2 the PVC stays on whatever version it was seeded with forever, no
# matter how often the image is rebuilt.
set -euo pipefail

APP_DIR="${APP_DIR:-/srv/app}"
TEMPLATE_DIR="${TEMPLATE_DIR:-/srv/template}"
MARKER="$APP_DIR/.upgrade-in-progress"

strapi_version() {
  node -p "require('$1/node_modules/@strapi/strapi/package.json').version" 2>/dev/null || echo "0.0.0"
}

# Prints "yes" when $1 is a strictly newer x.y.z than $2.
is_newer() {
  node -e '
    const [a, b] = process.argv.slice(1).map((v) => v.split(".").map((n) => parseInt(n, 10) || 0));
    for (let i = 0; i < 3; i++) {
      if (a[i] !== b[i]) { console.log(a[i] > b[i] ? "yes" : "no"); process.exit(0); }
    }
    console.log("no");
  ' "$1" "$2"
}

if [ ! -f "$APP_DIR/package.json" ]; then
  echo "Seeding Strapi project from image template to PVC..."
  cp -a "$TEMPLATE_DIR/." "$APP_DIR/"
fi

TEMPLATE_VERSION="$(strapi_version "$TEMPLATE_DIR")"
APP_VERSION="$(strapi_version "$APP_DIR")"

# Never downgrade: Strapi's database migrations are one-way. The marker makes an
# upgrade that was killed halfway (node_modules already removed) start over.
if [ -f "$MARKER" ] || [ "$(is_newer "$TEMPLATE_VERSION" "$APP_VERSION")" = "yes" ]; then
  echo "Upgrading Strapi on PVC: $APP_VERSION -> $TEMPLATE_VERSION"
  touch "$MARKER"
  cd "$APP_DIR"

  # Take the template's dependency versions, keep everything else (the "strapi"
  # uuid/installId and any dependency that was added on the PVC by hand).
  EXTRAS="$(node -e '
    const fs = require("fs");
    const [appFile, tplFile] = process.argv.slice(1);
    const app = JSON.parse(fs.readFileSync(appFile));
    const tpl = JSON.parse(fs.readFileSync(tplFile));
    const extras = [];
    for (const key of ["dependencies", "devDependencies"]) {
      for (const name of Object.keys(app[key] || {})) {
        if (!(tpl[key] || {})[name]) extras.push(name);
      }
      app[key] = { ...(app[key] || {}), ...(tpl[key] || {}) };
    }
    app.engines = tpl.engines || app.engines;
    fs.writeFileSync(appFile, JSON.stringify(app, null, 2) + "\n");
    console.log(extras.join(" "));
  ' "$APP_DIR/package.json" "$TEMPLATE_DIR/package.json")"

  # Reuse the node_modules that CI already installed and built against, rather
  # than resolving and compiling native modules again inside the pod.
  rm -rf node_modules .strapi dist build .cache
  cp -a "$TEMPLATE_DIR/node_modules" node_modules
  cp "$TEMPLATE_DIR/package-lock.json" package-lock.json

  if [ -n "$EXTRAS" ]; then
    echo "Reinstalling dependencies that only exist on the PVC: $EXTRAS"
    npm install --no-audit --no-fund
  fi

  rm -f "$MARKER"
  echo "Upgrade complete: now on $(strapi_version "$APP_DIR")"
else
  echo "Strapi on PVC is $APP_VERSION, image template is $TEMPLATE_VERSION: nothing to upgrade."
fi

# Config owned by the image. plugins.ts is opt-in because it enables the DeepL
# translate plugin, which needs DEEPL_API_KEY.
cp "$TEMPLATE_DIR/config/server.ts" "$APP_DIR/config/server.ts"
if [ "${SYNC_PLUGINS_CONFIG:-false}" = "true" ]; then
  cp "$TEMPLATE_DIR/config/plugins.ts" "$APP_DIR/config/plugins.ts"
fi
