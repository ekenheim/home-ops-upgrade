#!/bin/sh
# Restores VolSync backups into this namespace and reads them back.
#
# Every day it restores one app from apps.txt (by epoch day) from its MinIO
# repo. On the 1st of the month it also restores one app from R2_APPS (by
# month) from its R2 repo: R2 bills per request, and a small sample is
# enough to prove the offsite repos and credentials work.
# For each target it restores the latest
# restic snapshot into a fresh PVC (copyMethod Direct), starts a check pod
# (check-pod.yaml, check.py) on it, and passes only if that pod printed
# "VERIFY OK app=<app> backend=<backend>". Everything is deleted afterwards,
# pass or fail.
#
# VERIFY_TARGETS="wizarr:r2 kavita:minio" overrides the rotation for a
# manual run.
#
# Runs in alpine/kubectl, whose date is busybox: only `date +%s` style
# formatting is used here, no date parsing (see nfs-stale-watchdog).
# shellcheck disable=SC3040 # busybox ash has pipefail
set -euo pipefail

NS=backup-verify
CONFIG=/config
R2_APPS=${R2_APPS:-wizarr actual}
RESTORE_TIMEOUT=${RESTORE_TIMEOUT:-2400}
CHECK_TIMEOUT=${CHECK_TIMEOUT:-960}
RUN_ID=$(date -u +%Y%m%d%H%M%S)

apps=$(grep -v -e '^#' -e '^[[:space:]]*$' "$CONFIG/apps.txt" | awk '{print $1}')
count=$(echo "$apps" | wc -l)
pick() { echo "$apps" | sed -n "$(( $1 % count + 1 ))p"; }

if [ -n "${VERIFY_TARGETS:-}" ]; then
  targets=$VERIFY_TARGETS
else
  day=$(( $(date -u +%s) / 86400 ))
  targets="$(pick "$day"):minio"
  if [ "$(date -u +%d)" = "01" ] && [ -n "$R2_APPS" ]; then
    # %m is zero-padded and $(( 08 )) is an octal error: strip the zero.
    month=$(date -u +%m)
    month=$(( $(date -u +%Y) * 12 + ${month#0} ))
    # shellcheck disable=SC2086 # split the list into words
    set -- $R2_APPS
    shift $(( month % $# ))
    targets="$targets $1:r2"
  fi
fi

log() { echo "[volsync-verify] $*"; }

current=""
cleanup() {
  [ -n "$current" ] || return 0
  kubectl -n "$NS" delete pod "$current" --ignore-not-found --wait=true --timeout=5m || true
  kubectl -n "$NS" delete replicationdestination "$current" --ignore-not-found --wait=true --timeout=5m || true
  kubectl -n "$NS" delete pvc "$current" --ignore-not-found --wait=true --timeout=5m || true
  current=""
}
trap cleanup EXIT
trap 'cleanup; exit 1' INT TERM

verify_one() {
  app=$1
  backend=$2
  line=$(awk -v a="$app" '$1 == a' "$CONFIG/apps.txt")
  if [ -z "$line" ]; then
    log "FAIL $app: not in apps.txt"
    return 1
  fi
  # shellcheck disable=SC2086
  set -- $line
  capacity=$2
  uid=$3
  gid=$4
  case "$backend" in
    minio) secret="$app-volsync" ;;
    r2) secret="$app-volsync-r2" ;;
    *) log "FAIL $app: unknown backend $backend"; return 1 ;;
  esac
  name="verify-$app-$backend"

  ready=$(kubectl -n "$NS" get externalsecret "$secret" --ignore-not-found \
    -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')
  if [ "$ready" != "True" ]; then
    log "FAIL $app/$backend: ExternalSecret $secret not Ready (${ready:-missing})"
    return 1
  fi

  current=$name
  cleanup
  current=$name

  # verify_one runs under `if !`, where set -e is off: check each step.
  log "restoring $app from $backend into $name ($capacity)"
  kubectl -n "$NS" apply -f - <<EOF || { log "FAIL $app/$backend: could not create restore"; return 1; }
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: $name
  labels:
    app.kubernetes.io/name: volsync-verify
    app.kubernetes.io/component: restore
spec:
  accessModes: ["ReadWriteOnce"]
  storageClassName: ceph-block
  resources:
    requests:
      storage: $capacity
---
apiVersion: volsync.backube/v1alpha1
kind: ReplicationDestination
metadata:
  name: $name
  labels:
    app.kubernetes.io/name: volsync-verify
    app.kubernetes.io/component: restore
spec:
  trigger:
    manual: "$RUN_ID"
  restic:
    repository: $secret
    destinationPVC: $name
    copyMethod: Direct
    cacheStorageClassName: ceph-block
    cacheAccessModes: ["ReadWriteOnce"]
    cacheCapacity: 4Gi
    cleanupCachePVC: true
    enableFileDeletion: true
    moverSecurityContext:
      runAsUser: $uid
      runAsGroup: $gid
      fsGroup: $gid
EOF

  deadline=$(( $(date -u +%s) + RESTORE_TIMEOUT ))
  while :; do
    status=$(kubectl -n "$NS" get replicationdestination "$name" \
      -o jsonpath='{.status.lastManualSync}|{.status.latestMoverStatus.result}')
    case "$status" in
      "$RUN_ID|Successful") break ;;
      *"|Failed")
        log "FAIL $app/$backend: restore failed"
        kubectl -n "$NS" get replicationdestination "$name" \
          -o jsonpath='{.status.latestMoverStatus.logs}' || true
        echo
        return 1 ;;
    esac
    if [ "$(date -u +%s)" -ge "$deadline" ]; then
      log "FAIL $app/$backend: restore not done after ${RESTORE_TIMEOUT}s"
      kubectl -n "$NS" describe replicationdestination "$name" || true
      return 1
    fi
    sleep 15
  done
  log "restore of $app from $backend done; mover log:"
  kubectl -n "$NS" get replicationdestination "$name" \
    -o jsonpath='{.status.latestMoverStatus.logs}' | tail -n 5 || true
  echo

  sed -e "s/__NAME__/$name/g" -e "s/__APP__/$app/g" -e "s/__BACKEND__/$backend/g" \
    "$CONFIG/check-pod.yaml" | kubectl -n "$NS" apply -f -     || { log "FAIL $app/$backend: could not create check pod"; return 1; }

  deadline=$(( $(date -u +%s) + CHECK_TIMEOUT ))
  while :; do
    phase=$(kubectl -n "$NS" get pod "$name" -o jsonpath='{.status.phase}')
    case "$phase" in
      Succeeded|Failed) break ;;
    esac
    if [ "$(date -u +%s)" -ge "$deadline" ]; then
      log "FAIL $app/$backend: check pod still $phase after ${CHECK_TIMEOUT}s"
      kubectl -n "$NS" describe pod "$name" || true
      return 1
    fi
    sleep 5
  done

  output=$(kubectl -n "$NS" logs "$name" -c check || true)
  echo "$output"
  # Success needs the positive line, not just a clean exit. grep -c reads
  # all input; grep -q could exit early and SIGPIPE printf under pipefail.
  ok=$(printf '%s
' "$output" | grep -c "^VERIFY OK app=$app backend=$backend " || true)
  if [ "$phase" = "Succeeded" ] && [ "${ok:-0}" -ge 1 ]; then
    log "PASS $app/$backend"
    cleanup
    return 0
  fi
  log "FAIL $app/$backend: check pod $phase without a VERIFY OK line"
  return 1
}

log "run $RUN_ID, targets: $targets"
# A run killed mid-cleanup (activeDeadlineSeconds) can leave another app's
# restore behind; clear anything a previous run created.
kubectl -n "$NS" delete pod,replicationdestination,pvc \
  -l 'app.kubernetes.io/name=volsync-verify,app.kubernetes.io/component in (restore,check)' \
  --ignore-not-found --wait=true --timeout=5m || true
failed=""
for target in $targets; do
  if ! verify_one "${target%%:*}" "${target##*:}"; then
    failed="$failed $target"
  fi
  cleanup
done

if [ -n "$failed" ]; then
  log "FAILED:$failed"
  exit 1
fi
log "all passed: $targets"
