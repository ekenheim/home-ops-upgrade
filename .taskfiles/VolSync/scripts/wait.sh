#!/usr/bin/env bash
# Blocks until the Job's pod exists, so a following `kubectl wait job/...`
# has something to wait on. Any phase counts: a fast Job can be Running or
# already Succeeded the first time we look.

JOB=$1
NAMESPACE="${2:-default}"

[[ -z "${JOB}" ]] && echo "Job name not specified" && exit 1
while true; do
    STATUS="$(kubectl -n "${NAMESPACE}" get pod -l job-name="${JOB}" -o jsonpath='{.items[*].status.phase}')"
    if [ -n "${STATUS}" ]; then
        break
    fi
    sleep 1
done
