#!/bin/bash
# Reconcile the Strapi project on the PVC with the template baked into this image:
#   1. seed it on first run,
#   2. upgrade it when the image ships a newer @strapi/strapi, or refresh its
#      node_modules when the image was rebuilt at the same version with a
#      different dependency tree (a security rebuild),
#   3. sync the config files that are owned by the image.
#
# The project (src/, public/uploads, package.json "strapi" ids) lives on the PVC
# because Content-Type Builder writes schemas there in develop mode, and Strapi
# runs from the PVC's node_modules, not the image's. Without step 2 the PVC
# stays on whatever it was seeded with forever, no matter how often the image
# is rebuilt: Trivy scans the image, so a rebuild looked fixed while the pod
# kept running the old dependencies.
set -euo pipefail

APP_DIR="${APP_DIR:-/srv/app}"
TEMPLATE_DIR="${TEMPLATE_DIR:-/srv/template}"
MARKER="$APP_DIR/.upgrade-in-progress"
# sha256 of the template package-lock.json the PVC's node_modules came from.
# Missing on PVCs seeded before 2026-10-19, which makes them refresh once.
STAMP="$APP_DIR/.template-lock.sha256"

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

TEMPLATE_LOCK="$(sha256sum "$TEMPLATE_DIR/package-lock.json" | cut -d' ' -f1)"

if [ ! -f "$APP_DIR/package.json" ]; then
  echo "Seeding Strapi project from image template to PVC..."
  cp -a "$TEMPLATE_DIR/." "$APP_DIR/"
  echo "$TEMPLATE_LOCK" > "$STAMP"
fi

TEMPLATE_VERSION="$(strapi_version "$TEMPLATE_DIR")"
APP_VERSION="$(strapi_version "$APP_DIR")"
APP_LOCK="$(cat "$STAMP" 2>/dev/null || true)"

# Never downgrade: Strapi's database migrations are one-way. A refresh at the
# same version runs no migrations; it only swaps node_modules. The marker makes
# a sync that was killed halfway (node_modules already removed) start over.
REASON=""
if [ -f "$MARKER" ]; then
  REASON="resuming an interrupted sync to $TEMPLATE_VERSION"
elif [ "$(is_newer "$TEMPLATE_VERSION" "$APP_VERSION")" = "yes" ]; then
  REASON="upgrading Strapi $APP_VERSION -> $TEMPLATE_VERSION"
elif [ "$TEMPLATE_VERSION" = "$APP_VERSION" ] && [ "$TEMPLATE_LOCK" != "$APP_LOCK" ]; then
  REASON="image rebuilt at $TEMPLATE_VERSION with a different dependency tree, refreshing node_modules"
fi

if [ -n "$REASON" ]; then
  echo "Syncing Strapi on PVC: $REASON"
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

  echo "$TEMPLATE_LOCK" > "$STAMP"
  rm -f "$MARKER"
  echo "Sync complete: now on $(strapi_version "$APP_DIR")"
else
  echo "Strapi on PVC is $APP_VERSION, image template is $TEMPLATE_VERSION, node_modules current: nothing to sync."
fi

# Config owned by the image. plugins.ts is opt-in because it enables the DeepL
# translate plugin, which needs DEEPL_API_KEY.
cp "$TEMPLATE_DIR/config/server.ts" "$APP_DIR/config/server.ts"
if [ "${SYNC_PLUGINS_CONFIG:-false}" = "true" ]; then
  cp "$TEMPLATE_DIR/config/plugins.ts" "$APP_DIR/config/plugins.ts"
fi
