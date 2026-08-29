#!/usr/bin/env bash
# One-shot helper for the datasci -> llm namespace move. See README.md.
#
#   ./migrate.sh retain    # BEFORE pushing: flip PVs to Retain, record the map
#   ./migrate.sh status    # any time: where is everything
#   ./migrate.sh rebind    # AFTER flux prunes datasci: re-point PVs at llm
#
# Not referenced by any kustomization; Flux ignores it.
set -euo pipefail

OLD_NS=datasci
NEW_NS=llm
MAP="${MAP_FILE:-$(dirname "$0")/.migrate-pvmap}"

# PVCs that rebind directly. Their manifests declare a plain PVC, so pointing
# the retained PV at the new claim is all that is needed.
REBIND=(
  litellm-chatgpt-auth
  llmkube-model-cache
  memory-mcp
  ollama-igpu
)

# PVCs that CANNOT rebind. templates/volsync/claim.yaml gives these a
# dataSourceRef on ReplicationDestination <app>-bootstrap, and a PVC with a
# dataSourceRef is handled by the volume-populator controller -- it provisions a
# fresh volume from the restic snapshot and will not adopt a pre-existing PV.
# They are still flipped to Retain, as a manual fallback if a restore comes back
# empty or stale: the old volume survives and can be mounted by a debug pod.
RESTORE=(
  agentmemory
  hermes
  open-webui
)

retain() {
  : > "$MAP"
  for p in "${REBIND[@]}" "${RESTORE[@]}"; do
    pv=$(kubectl get pvc -n "$OLD_NS" "$p" -o jsonpath='{.spec.volumeName}' 2>/dev/null || true)
    if [[ -z "$pv" ]]; then
      echo "SKIP  $p (no PVC in $OLD_NS)"
      continue
    fi
    kubectl patch pv "$pv" -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}' >/dev/null
    echo "$p $pv" >> "$MAP"
    echo "RETAIN $p -> $pv"
  done
  echo
  echo "PV map written to $MAP -- do not delete it, rebind reads it."
}

status() {
  printf '%-24s %-42s %-10s %s\n' PVC PV RECLAIM STATUS
  while read -r p pv; do
    printf '%-24s %-42s %-10s %s\n' "$p" "$pv" \
      "$(kubectl get pv "$pv" -o jsonpath='{.spec.persistentVolumeReclaimPolicy}' 2>/dev/null)" \
      "$(kubectl get pv "$pv" -o jsonpath='{.status.phase}' 2>/dev/null)"
  done < "$MAP"
}

rebind() {
  for p in "${REBIND[@]}"; do
    pv=$(awk -v k="$p" '$1==k{print $2}' "$MAP")
    [[ -n "$pv" ]] || { echo "SKIP  $p (not in $MAP)"; continue; }
    # Drop the uid/resourceVersion of the deleted claim and re-point at the new
    # namespace. Setting claimRef (rather than clearing it) reserves the volume
    # for exactly this PVC, so no other pending claim can take it.
    kubectl patch pv "$pv" --type=merge -p \
      "{\"spec\":{\"claimRef\":{\"apiVersion\":\"v1\",\"kind\":\"PersistentVolumeClaim\",\"namespace\":\"$NEW_NS\",\"name\":\"$p\",\"uid\":null,\"resourceVersion\":null}}}" >/dev/null
    echo "REBIND $p -> $pv (reserved for $NEW_NS/$p)"
  done
  echo
  echo "Volsync-backed claims are NOT rebound and restore from restic instead:"
  printf '  %s\n' "${RESTORE[@]}"
  echo "Their old PVs are retained as a fallback; see README.md."
}

case "${1:-}" in
  retain) retain ;;
  status) status ;;
  rebind) rebind ;;
  *) sed -n '2,9p' "$0"; exit 2 ;;
esac
