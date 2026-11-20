# Prerequisites

[← Docs Index](../README.md)

Everything needed before running the Toolkit Manager installer, whether you're deploying on-premises, air-gapped, or on a single bare-metal box.

---

## Operating system

| OS | Status |
|---|---|
| Ubuntu 22.04 | ✅ Supported |
| Ubuntu 24.04 | ✅ Supported |
| RHEL 8 / Rocky Linux 8 (x86_64) | ✅ Supported |

**No Docker on cluster nodes.** Kubespray uses containerd and, while installing it, deletes Docker and all its data (`/var/lib/docker`) on any node where Docker is installed. The installer refuses to install onto a node with a `docker.service` unit (for a single-node install, the installer host itself). Back up and uninstall Docker first, or use a different node. See [Node Scaling](../customize/node_scaling.md#built-into-add-nodes-pre-flight).

---

## Hardware minimums

| Deployment | CPU cores | RAM | Disk |
|---|---|---|---|
| Platform + Observability only | 16 | 32 GB | 200 GB |
| Inference — 3B parameter model | 32 | 64 GB | 300 GB |
| Inference — 8B parameter model | 64 | 128 GB | 400 GB |

Intel® Xeon® processors are recommended for CPU-based inference. NUMA-aware pinning (NRI Balloons) and AMX acceleration are built into the platform for Xeon workloads.

---

## Access

| Requirement | Single-node | Multi-node |
|---|---|---|
| Passwordless sudo on installer host | Required | Required |
| Passwordless SSH from installer host to all nodes | — | Required |
| Passwordless sudo on all target nodes | — | Required |
| Installer host reaches the API server at `https://<control-plane-ip>:6443` (not only SSH to the nodes); see [Bastion (Jump Host)](../deploy/install_platform.md#option-c-bastion-jump-host) | — | Required |

---

## Network

- **Internet access** is required during install (package downloads, container images, model weights).
- **Corporate proxy:** set `http_proxy`, `https_proxy`, and `no_proxy` in `env/<name>/global_config.yaml` before installing. See [Configuration](../customize/configuration.md#proxy).
- **Time synchronization (multi-node only):** an NTP server (`chrony` or `systemd-timesyncd`) must be running and synced on every node before install. Clock skew causes intermittent etcd failures, TLS errors, and token authentication issues that are hard to diagnose.

---

## Node checks before install

On a fresh install, the installer checks every control-plane and worker node before Kubespray starts. The checks are read-only. Every node is checked first; the install then stops once, listing each failed check with the node and a fix.

**Stops the install:**

| ID | Check |
|---|---|
| N1, N2 | Multi-node: clock synchronised, and within 60 s of the deploy node (a warning above 5 s) |
| N3, N4 | At least 20 GiB free (a warning below 50 GiB), and at most 90 % used, under `/var/lib/containerd`; at most 90 % used on `/` |
| N8 | HTTPS to github.com and registry.k8s.io, through the proxy if one is set |
| N16 | Ceph only (`storage_backend: ceph`): each OSD disk exists, is a whole disk, is not mounted or partitioned, and has no filesystem or old Ceph signature |
| N19 | etcd members: no etcd binary left without its service by an unfinished reset (Kubespray would fail at "Stop etcd") |

**Warnings:**
- N11: leftovers from an earlier install on this node, including a kubeconfig in `/etc/kubernetes` and the API server it points at.
- N14: firewall active.

To skip a check, list its ID in `global_config.yaml`. For example, nodes that download packages and images from a mirror instead of the internet skip the internet check (the deploy node still needs internet access):

```yaml
kubernetes_node_preflight_skip: [N8]
```

To turn all the checks off, set `kubernetes_node_preflight: false`. Do that for one run when you re-install onto a running cluster whose kubeconfig was lost: the checks then run as for a fresh install, and N16 stops on the disks Ceph is using (or skip just N16).

### Reusing a disk that held an OSD

Rook skips a disk that has partitions, a filesystem or an old Ceph label, and the install ends with 0 OSDs. Check N16 stops the install before Kubespray when a listed disk isn't clean.

Teardown keeps the OSD data unless `rook_ceph_teardown_wipe_devices: true` is set in `global_config.yaml`, so a re-install on the same disks needs them wiped. Wipe a former OSD disk on its node first. Ceph keeps copies of its BlueStore label at 1, 10, 100 and 1000 GiB, not only at the start:

```console
DEV=/dev/nvme1n1          # the OSD disk, never the OS disk
sudo wipefs -a "$DEV"
sudo sgdisk --zap-all "$DEV"
for gib in 0 1 10 100 1000; do
  sudo dd if=/dev/zero of="$DEV" bs=1M count=1 seek=$((gib * 1024)) conv=notrunc status=none 2>/dev/null || true
done
```

---

## Tooling

`./toolkit_manager.sh configure` installs the following pinned versions into `/usr/local/bin`. Run it once per machine before anything else. If these tools are already installed at compatible versions, `configure` skips them.

| Tool | Version | Notes |
|---|---|---|
| Python | ≥ 3.11 | Also installs the matching `python<ver>-venv` package |
| yq | v4.53.2 | [mikefarah/yq](https://github.com/mikefarah/yq) — YAML processor used by the installer |
| kubectl | v1.35.4 | |
| helm | v3.20.2 | |

> **`git` must already be installed** before running `configure`. It is preinstalled on most Ubuntu and RHEL images. The installer uses it to clone external repos on first run.

---

## Credentials

- **Hugging Face token** — required only for gated models (Llama, Mistral, Gemma, etc.). Get one at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) (free account). Export as `HF_TOKEN=hf_...` before deploying those models.
- **Keycloak admin password** — auto-generated if not set. Override by exporting `KEYCLOAK_ADMIN_PASSWORD` before install.
- **Grafana admin password** — auto-generated if not set. Override by exporting `GRAFANA_ADMIN_PASSWORD` before install.

---

## What `configure` does not do

- Install `git` — must already be present
- Configure proxy settings — edit `env/<name>/global_config.yaml` yourself
- Set up SSH keys — you must distribute them to target nodes for multi-node installs

---

## Related Docs

| If you want to… | Go to |
|---|---|
| Start the actual install now that prerequisites are met | [Getting Started](quickstart.md) |
| Understand what you're installing and why, before running anything | [Meet Toolkit Manager](../meet/meet.md) |
| Plan hardware for a multi-node or bring-your-own-cluster setup | [Multi-Node & BYO Cluster](../deploy/topologies.md) |
| Set proxy, TLS, or other options `configure` doesn't handle | [Configuration Reference](../customize/configuration.md) |
