# Prerequisites

[← Docs Index](../README.md)

Everything needed before running the Intel® AI for Enterprise Solutions installer, whether you're deploying on-premises, air-gapped, or on a single bare-metal box.

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

## Tooling

`./es_auto_installer.sh configure` installs the following pinned versions into `/usr/local/bin`. Run it once per machine before anything else. If these tools are already installed at compatible versions, `configure` skips them.

| Tool | Version | Notes |
|---|---|---|
| Python | ≥ 3.11 | Also installs the matching `python<ver>-venv` package |
| yq | v4.53.2 | [mikefarah/yq](https://github.com/mikefarah/yq) — YAML processor used by the installer |
| kubectl | v1.34.3 | |
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
| Understand what you're installing and why, before running anything | [Meet Intel® AI for Enterprise Solutions](../meet/meet.md) |
| Plan hardware for a multi-node or bring-your-own-cluster setup | [Multi-Node & BYO Cluster](../deploy/topologies.md) |
| Set proxy, TLS, or other options `configure` doesn't handle | [Configuration Reference](../customize/configuration.md) |
