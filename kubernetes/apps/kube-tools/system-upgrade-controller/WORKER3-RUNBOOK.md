# worker3 — Talos Upgrade Runbook (automated, with manual fallback)

**Why this node is special:** `worker3` uses an AMD CPU/GPU. The rest of
the cluster (master1–3) runs Intel and relies on
`intel-device-plugins-gpu` for iGPU scheduling. The control-plane
`system-upgrade-controller` Plan at
`kubernetes/apps/kube-tools/system-upgrade-controller/plans/talos.yaml`
explicitly excludes `worker3` via nodeSelector so a generic Intel-focused
upgrade can't apply the wrong schematic.

worker3 is now upgraded automatically by a dedicated
[`talos-worker3`](./plans/talos-worker3.yaml) Plan that pins
`TALOS_SCHEMATIC_WORKER3` (the AMD schematic ID lives in
[`ks.yaml`](./ks.yaml)). The manual steps below are kept as a fallback
for when the Plan fails (e.g. Job DeadlineExceeded).

## When to upgrade

Normally: nothing — the `talos-worker3` Plan picks the new version up
automatically when Renovate bumps `TALOS_VERSION`. Watch
`kubectl get nodes -o wide` and the Plan status:

```bash
kubectl get plan -n kube-tools talos-worker3
```

Fall back to the manual procedure only if the Plan is wedged. Common
failure modes:

- Plan condition `Complete=False`, message `DeadlineExceeded` — the
  upgrade Job ran past `spec.jobActiveDeadlineSecs`. The Plan now sets
  this to 2700s and `drain.disableEviction: true`; if you still hit it,
  inspect the failed Job logs before bumping further.
- Node stuck `SchedulingDisabled` with no Job running — SUC cordoned it
  but the Job aged out. Uncordon and let SUC retry, or proceed manually
  below.

## Prerequisites

- `talosctl` installed locally, pointing at your cluster
- `~/.talos/config` with admin access
- The new Talos version in `${TALOS_VERSION}` (same one the controller
  just applied to the other nodes — grep `talconfig.yaml` for it)
- The AMD schematic image (not the generic installer the other nodes use)

## Procedure

1. **Cordon and drain worker3** so workloads move off cleanly:

   ```bash
   kubectl cordon worker3
   kubectl drain worker3 --ignore-daemonsets --delete-emptydir-data
   ```

2. **Verify everything rescheduled.** Some workloads may be pinned
   elsewhere (e.g. anything with `nodeSelector: workload=media` stays on
   master3). That's fine — as long as worker3 has only DaemonSets left:

   ```bash
   kubectl get pods --all-namespaces --field-selector spec.nodeName=worker3
   ```

3. **Upgrade Talos on worker3** — use the AMD-specific schematic image:

   ```bash
   talosctl --nodes 192.168.50.37 upgrade \
     --image factory.talos.dev/metal-installer/<AMD_SCHEMATIC>:<TALOS_VERSION> \
     --preserve \
     --reboot-mode powercycle
   ```

   Replace `<AMD_SCHEMATIC>` with the schematic ID from
   `kubernetes/bootstrap/talos/talconfig.yaml` (search for worker3 →
   `talosImageURL`). Replace `<TALOS_VERSION>` with the target version.

4. **Wait for the node to come back** — monitor with:

   ```bash
   kubectl get nodes -w
   watch 'talosctl --nodes 192.168.50.37 health --wait-timeout 10m'
   ```

5. **Uncordon** once it's Ready and at the new version:

   ```bash
   kubectl uncordon worker3
   ```

6. **Verify** — confirm it's back in the cluster and workloads are
   scheduling to it again:

   ```bash
   kubectl get node worker3 -o jsonpath='{.status.nodeInfo.osImage}{"\n"}'
   kubectl get pods -A --field-selector spec.nodeName=worker3 | wc -l
   ```

## Rollback

If the upgrade fails:

```bash
talosctl --nodes 192.168.50.37 rollback
```

This reverts to the previous Talos boot entry. If that doesn't work,
PXE-boot with the old installer image.

## Future improvements

- **Persistent node labels via Talos machineconfig.** The `workload=media`
  label on master3 currently lives only in etcd. If you rebuild the
  cluster, re-apply with `kubectl label node master3 workload=media`. For
  permanence, add it to the Talos machine patch:

  ```yaml
  machine:
    nodeLabels:
      workload: media
  ```

- Consider adding a second system-upgrade-controller Plan for AMD nodes
  that uses the AMD schematic image, so worker3 can eventually rejoin
  automation with its own Plan selector.
