#!/usr/bin/env bash
# Validates the manifests under kubernetes/ with kubeconform. Shared by
# `task kubernetes:kubeconform` and .github/workflows/kubeconform.yaml.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../../.."

# Only validate files that declare a top-level `kind:` — skips
# helm-values, kustomizeconfig patches, app configs, etc. naturally.
# Also exclude SOPS-encrypted secrets (their `sops:` stanza breaks
# strict Secret schema) and Talos bootstrap files.
# system-upgrade-controller plans use YAML merge-key anchors
# (<<: *prepare) which Go YAML flags as duplicate keys — runtime
# fine, strict parser not, so exclude.
files=$(grep -rlE '^kind:[[:space:]]+[A-Z]' kubernetes \
  --include='*.yaml' --include='*.yml' \
  | grep -vE '\.sops\.|^kubernetes/bootstrap/|^kubernetes/flux/vars/|^kubernetes/templates/|system-upgrade-controller/plans/|/chart/.*/templates/' \
  || true)

# Flux substitutes ${VAR} after the build, so raw manifests hold
# placeholders that typed fields reject (HTTPRoute hostnames, CIDRs).
# Validate a copy with stand-in values: ${VAR:-default} becomes its
# default, then domains, CIDRs and addresses by naming convention.
# Anything else is left alone; it validates fine as a string.
work=$(mktemp -d)
trap 'rm -rf "${work}"' EXIT
echo "$files" | xargs cp --parents -t "$work"
cd "$work"
echo "$files" | xargs sed -i -E \
  -e 's/\$\{[A-Za-z_][A-Za-z0-9_]*:[-=]([^}]*)\}/\1/g' \
  -e 's/\$\{[A-Z0-9_]*DOMAIN[A-Z0-9_]*\}/example.com/g' \
  -e 's#\$\{[A-Z0-9_]*CIDR[A-Z0-9_]*\}#10.0.0.0/8#g' \
  -e 's/\$\{[A-Z0-9_]*_(ADDR|IP)\}/10.0.0.1/g'

# CRD schemas come from this cluster first: schemas.yaml extracts the
# installed CRDs every night and publishes them to lds-schemas-5hg.pages.dev
# (the same host the `$schema` headers use). A CRD upgrade reaches it the
# night after it lands; run the Schemas workflow by hand to refresh sooner.
# The datree catalog covers anything not installed here.
schema_host=https://lds-schemas-5hg.pages.dev

# The host has to answer before anything is validated. kubeconform moves on
# to the next -schema-location on a 404 or a body it cannot compile, so a
# host that serves nothing usable looks exactly like "CRD not installed":
# every kind drops to the datree catalog or to Skipped, without an error.
# Two CI runs on 2026-10-23 did that (1556 valid / 88 skipped instead of
# 1613 / 32, reproduced locally by leaving the host out) and then failed on
# tuppr's healthChecks labelSelector, which datree's older schema lacks.
# tuppr's schema is the canary because datree has it but lags it. Each
# failed attempt is logged, which tells a blip from a runner the host
# never answers.
canary="${schema_host}/tuppr.home-operations.com/talosupgrade_v1alpha1.json"
ok=
for attempt in 1 2 3 4 5; do
  code=$(curl -sS -D canary.headers -o canary.json -w '%{http_code}' "$canary") || code=none
  if [[ $code == 200 && $(head -c 1 canary.json 2>/dev/null) == "{" ]]; then
    ok=1
    break
  fi
  echo "Schema host, attempt ${attempt}/5: HTTP ${code}"
  sleep 3
done
if [[ -z $ok ]]; then
  echo "::error::Schema host did not serve ${canary}; refusing to validate against the datree fallback."
  cat canary.headers 2>/dev/null || true
  head -c 300 canary.json 2>/dev/null || true
  echo
  exit 1
fi
rm -f canary.headers canary.json

# KUBECONFORM_DEBUG (set by the workflow) logs, per kind, which location
# served the schema and which answered 404.
echo "Validating $(echo "$files" | wc -l) files"
echo "$files" | xargs kubeconform \
  -strict \
  -ignore-missing-schemas \
  ${KUBECONFORM_DEBUG:+-debug} \
  -schema-location default \
  -schema-location "${schema_host}/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json" \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  -skip 'Plan' \
  -summary \
  -output pretty
