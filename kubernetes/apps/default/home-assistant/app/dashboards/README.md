# Home Assistant dashboards

YAML-mode Lovelace dashboards, managed in Git.

- `kustomization.yaml` packs each file into the `home-assistant-dashboards`
  ConfigMap, which is mounted read-only at `/config/dashboards/`.
- Each file is registered once in `/config/configuration.yaml` on the PVC.
  This step needs an HA restart:

  ```yaml
  lovelace:
    dashboards:
      dashboard-home:
        mode: yaml
        title: Home
        icon: mdi:home-variant
        show_in_sidebar: true
        filename: dashboards/home.yaml
  ```

- Edits need no restart. HA reloads the file when its mtime changes, so a
  Flux sync followed by a browser refresh is enough. The ConfigMap carries
  `reloader.stakater.com/ignore` so Reloader doesn't bounce the pod.
- To draft a change, copy a view into a scratch storage-mode dashboard in the
  UI, then paste the raw config back here.

The repo is public, so keep these out of the YAML: credentials, `rtsp://user:pass@` URLs, and
entity IDs derived from the street address. Rename such entities in HA first.
