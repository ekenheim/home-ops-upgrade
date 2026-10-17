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
# tuppr's TalosUpgrade/KubernetesUpgrade are skipped: the datree
# catalog schema lags the deployed CRD (no spec.drain).
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

echo "Validating $(echo "$files" | wc -l) files"
echo "$files" | xargs kubeconform \
  -strict \
  -ignore-missing-schemas \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  -skip 'HelmRelease,Kustomization,GitRepository,HelmRepository,OCIRepository,Bucket,Receiver,Alert,Provider,ImagePolicy,ImageRepository,ImageUpdateAutomation,Plan,TalosUpgrade,KubernetesUpgrade' \
  -summary \
  -output pretty
