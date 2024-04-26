#!/usr/bin/env bash

NODE="${2}"
TALOS_STANZA="${3}"
ROLLOUT="${4:-false}"

echo "Waiting for all jobs to complete before upgrading Talos ..."
until kubectl wait --timeout=5m \
    --for=condition=Complete jobs --all --all-namespaces;
do
    echo "Waiting for jobs to complete ..."
    sleep 10
done

if [ "${ROLLOUT}" != "true" ]; then
    echo "Suspending Flux Kustomizations ..."
    flux  suspend kustomization --all
fi

echo "Upgrading Talos on node ${NODE} in cluster ${CLUSTER} ..."
talosctl --nodes "${NODE}" upgrade \
    --image="factory.talos.dev/installer/${TALOS_STANZA}" \
        --wait=true --timeout=10m --preserve=true

echo "Waiting for Talos to be healthy ..."
talosctl --nodes "${NODE}" health \
    --wait-timeout=10m --server=false

echo "Waiting for Ceph health to be OK ..."
until kubectl  wait --timeout=5m \
    --for=jsonpath=.status.ceph.health=HEALTH_OK cephcluster \
        --all --all-namespaces;
do
    echo "Waiting for Ceph health to be OK ..."
    sleep 10
done

if [ "${ROLLOUT}" != "true" ]; then
    echo "Resuming Flux Kustomizations ... ..."
    flux resume kustomization --all
fi
