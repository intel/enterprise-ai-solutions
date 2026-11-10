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

---

## Network

- **Internet access** is required during install (package downloads, container images, model weights).
- **Corporate proxy:** set `http_proxy`, `https_proxy`, and `no_proxy` in `env/<name>/global_config.yaml` before installing. See [Configuration](../customize/configuration.md#proxy).
- **Time synchronization (multi-node only):** an NTP server (`chrony` or `systemd-timesyncd`) must be running and synced on every node before install. Clock skew causes intermittent etcd failures, TLS errors, and token authentication issues that are hard to diagnose.

---

## Node checks before install

On a fresh install, the installer checks every node before Kubespray starts. The checks are read-only. Every node is checked first; the install then stops once, listing each failed check with the node and a fix. Results are also saved to `env/<name>/logs/node-preflight-<time>.json`. On a first install with the nodes in `nodes.yaml`, the inventory doesn't list them yet, so the checks are skipped; a re-run before the cluster exists checks them.

**Stops the install:**

| ID | Check |
|---|---|
| N1, N2 | Clock synchronised, and within 60 s of the deploy node |
| N3, N4 | At least 20 GiB free, and at most 90 % used, under `/var/lib/containerd`; at most 90 % used on `/` |
| N6, N7, N8 | Default gateway answers ping, TCP or ARP (a confirmed neighbour entry); DNS resolves (a warning behind a proxy, or with N8 skipped); HTTPS to github.com and registry.k8s.io (through the proxy, if one is set; a 403 is a warning) |
| N9 | Passwordless sudo |
| N10 | No configuration from another cluster: `/etc/kubernetes/kubelet.conf` or `admin.conf` pointing at another API server, or at a local one (Kubespray's `127.0.0.1` or `localhost`) with a CA that this run's control-plane nodes don't have, or none. The control-plane nodes must also share one cluster CA |
| N12 | No other program on the Kubernetes ports: 10250 on every node, 6443, 10257 and 10259 on control-plane nodes, 2379-2380 on etcd nodes, 179 with BGP |
| N13 | Package manager not broken (`dpkg --audit`) |
| N15 | Rack-scale only: Ubuntu 22.04 or 24.04 |
| N16 | Ceph only (`storage_backend: ceph`): each OSD disk exists, is a whole disk, is not mounted or partitioned, and has no filesystem or old Ceph signature |
| N19 | etcd nodes: no etcd binary left without its service by an unfinished reset (Kubespray would fail at "Stop etcd") |

**Warnings:**
- N5: etcd disk space.
- N11: leftovers from an earlier install on this node.
- N13: apt busy.
- N14: firewall active.

To skip a check, list its ID in `global_config.yaml`. For example, nodes that download packages and images from a mirror instead of the internet skip the internet check, and list the mirror's names for the DNS check (the deploy node still needs internet access). With N8 skipped, a name that doesn't resolve is only a warning:

```yaml
kubernetes_node_preflight_skip: [N8]
kubernetes_node_preflight_dns_names: [mirror.example.com]
```

To turn all the checks off, set `kubernetes_node_preflight: false`. Do that for one run when you re-install onto a running cluster whose kubeconfig was lost: the checks then run as for a fresh install, and N16 stops on the disks Ceph is using (or skip just N16). See [Configuration](../customize/configuration.md#node-preflight) for the thresholds.

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

`./es_auto_installer.sh configure` installs the following pinned versions into `/usr/local/bin`. Run it once per machine before anything else. If these tools are already installed at compatible versions, `configure` skips them.

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
