#!/usr/bin/env bash
# Installs the CLIs the Taskfiles need that no devcontainer feature provides.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCH="$(dpkg --print-architecture)" # amd64 | arm64

# talosctl follows the cluster's Talos version, which Renovate bumps in the
# Talos Taskfile, so it is read from there rather than pinned twice.
TALOS_VERSION="$(sed -nE 's/^\s*TALOS_VERSION:\s*(\S+).*/\1/p' "${ROOT_DIR}/.taskfiles/Talos/Taskfile.yaml")"
# renovate: datasource=github-releases depName=budimanjojo/talhelper
TALHELPER_VERSION=v3.1.17
# renovate: datasource=github-releases depName=helmfile/helmfile
HELMFILE_VERSION=v1.8.2
# renovate: datasource=github-releases depName=yannh/kubeconform
KUBECONFORM_VERSION=v0.6.7
# renovate: datasource=github-releases depName=databus23/helm-diff
HELM_DIFF_VERSION=v3.15.15
# bws releases are tagged bws-vX.Y.Z next to the SDK's own tags, which the
# annotation above cannot express. Bump by hand.
BWS_VERSION=2.1.0

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends age gettext-base unzip

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
gh_release() { curl -fsSL "https://github.com/$1/releases/download/$2/$3"; }

gh_release siderolabs/talos "${TALOS_VERSION}" "talosctl-linux-${ARCH}" > "${tmp}/talosctl"
gh_release budimanjojo/talhelper "${TALHELPER_VERSION}" "talhelper_linux_${ARCH}.tar.gz" | tar -xz -C "${tmp}" talhelper
gh_release helmfile/helmfile "${HELMFILE_VERSION}" "helmfile_${HELMFILE_VERSION#v}_linux_${ARCH}.tar.gz" | tar -xz -C "${tmp}" helmfile
gh_release yannh/kubeconform "${KUBECONFORM_VERSION}" "kubeconform-linux-${ARCH}.tar.gz" | tar -xz -C "${tmp}" kubeconform
case "${ARCH}" in amd64) bws_arch=x86_64 ;; arm64) bws_arch=aarch64 ;; esac
gh_release bitwarden/sdk-sm "bws-v${BWS_VERSION}" "bws-${bws_arch}-unknown-linux-gnu-${BWS_VERSION}.zip" > "${tmp}/bws.zip"
unzip -qo "${tmp}/bws.zip" bws -d "${tmp}"
sudo install -m 0755 "${tmp}"/{talosctl,talhelper,helmfile,kubeconform,bws} /usr/local/bin/

# helmfile apply (task talos:bootstrap-apps) diffs through the helm-diff plugin.
if ! helm plugin list | grep -q '^diff\b'; then
  helm plugin install https://github.com/databus23/helm-diff --version "${HELM_DIFF_VERSION}" --verify=false
fi
