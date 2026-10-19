# etcd-backup

Daily `talosctl etcd snapshot` at 03:15, gzipped onto the NAS. The files are in
the Unraid share `ceph2`, under `etcd-snapshots/etcd-YYYYMMDD-HHMMSS.snapshot.gz`.
Snapshots are kept for 14 days.

## Restore (whole control plane lost)

From the Talos docs, "Disaster Recovery":

1. Copy the newest snapshot off the NAS and `gunzip` it.
2. Install Talos on the new control-plane node and apply its machine config,
   but do not run `talosctl bootstrap`.
3. Wait for etcd to reach `Preparing`: `talosctl -n <ip> service etcd`.
4. Run `talosctl -n <ip> bootstrap --recover-from=./etcd-YYYYMMDD-HHMMSS.snapshot`.
5. Join the other control-plane nodes normally.

Secrets in the snapshot are encrypted with the cluster's secretbox key, which is
in `kubernetes/bootstrap/talos/talsecret.sops.yaml`. The node you restore onto
must be configured from that same secrets file, or Secrets will not decrypt.
