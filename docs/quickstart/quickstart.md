# Getting Started

[← Docs Index](../README.md)

Three ways to deploy Toolkit Manager — single node, multi-node, or an existing (bring-your-own) Kubernetes cluster — depending on your setup. Pick one and follow it end to end.

---

## Before you begin

Check [Prerequisites](prerequisites.md). At minimum you need:
- Ubuntu 22.04/24.04 or RHEL/Rocky Linux
- `git`, and `sudo` rights on the machine running the installer (passwordless is easiest)
- Internet access

---

## Step 1 — Get the code

```bash
git clone https://github.com/intel/toolkit-manager.git
cd toolkit-manager

./toolkit_manager.sh configure    # one-time machine prep — installs Python 3.11+, yq, kubectl, helm
```

`configure` lists the changes it will make with `sudo` and asks you to confirm: type `y`. It ends with `configure: done.` Skip it if Python 3.11+, `yq`, `kubectl` and `helm` are already installed. Run every command in this guide from the `toolkit-manager` folder.

---

## Step 2 — Create an environment

Every deployment lives in a named environment under `env/<name>/`. The name is arbitrary — use `local` for a single-node dev setup, `prod` for production, or anything you like.

```bash
./toolkit_manager.sh init inference
```

It clones the Intel® Inference Toolkit into `ext/enterprise.ai-inference/` and creates `env/local/` with:
- `global_config.yaml` — platform-wide settings
- `config.inference.yaml` — the inference layer's settings, including the API auth type (`auth_type`)
- `nodes.yaml` — the machines to install on (leave it as it is for a single node)
- `inventory/hosts.yaml` — Kubespray-compatible inventory; a localhost one to start with, regenerated from `nodes.yaml` once that lists nodes
- `models.yaml` — your model catalog (copied from the inference repository)
- `.solutions.yaml` — the record of which layers this environment was set up for (do not edit)

It ends with `init: done.` and the next command to run. Pass `--env <name>` to use a name other than `local`.

---

## Step 3 — Configure

For a first try nothing needs changing. Open `env/local/global_config.yaml` if you want to:

```yaml
# Optional — base domain for all service URLs and TLS certificates (default shown)
base_domain_name: "inteltoolkits.ai"

# Optional — uncomment and set if behind a corporate proxy
# http_proxy:  "http://proxy.example.com:8080"
# https_proxy: "http://proxy.example.com:8080"
# no_proxy: "localhost,127.0.0.1,.svc,.cluster.local,.monitoring,10.233.0.0/18,10.233.64.0/18,<node-subnet>"
```

Everything else uses safe defaults. Full option reference → [Configuration](../customize/configuration.md).

---

## Step 4 — Choose your deployment mode

### Option A — Single node (simplest)

Everything runs on the machine you're sitting on. No SSH configuration needed.

The default inventory created by `init` already targets localhost, so just install:

```bash
./toolkit_manager.sh install inference --env local
```

Allow up to an hour on a first install, mostly for downloading container images. A successful run ends with an `INSTALLATION COMPLETE` banner that names its log file. Then:

```bash
export KUBECONFIG=$(pwd)/env/local/kubeconfig.yaml
kubectl get nodes
# NAME        STATUS   ROLES           AGE   VERSION
# <your-node> Ready    control-plane   10m   v1.35.4
```

→ Continue to [Deploy a Model](../deploy/deploy_models.md).

---

### Option B — Multi-node

Provisions Kubernetes across multiple machines via SSH. Best for production and scale-out.

See the full guide → [Multi-Node & BYO Cluster](../deploy/topologies.md).

Quick overview:

1. Set up passwordless (key-based) SSH from the installer host to every node.
2. List the nodes in `env/local/nodes.yaml` under `nodes_control_plane` and `nodes_workers` (an `ip` and a `hostname` each), and set `nodes_ssh_user` and `nodes_ssh_key`. The installer generates `inventory/hosts.yaml` from this file.
3. Set `storage_backend: nfs` (or `ceph`, or `netapp-trident`) in `global_config.yaml` — `local-path` only works for single-node.
4. Run `./toolkit_manager.sh install inference --env local`.

---

### Option C — Bring your own Kubernetes

Deploy the platform on a cluster you already manage. Kubespray is skipped entirely.

In `env/local/global_config.yaml`:

```yaml
existing_kubernetes: "/absolute/path/to/your/kubeconfig"
```

Optionally disable components you already run:

```yaml
cert_manager_enabled: false     # already have cert-manager
metallb_enabled: false          # using a cloud load balancer
istio_enabled: false            # bring your own service mesh
```

Then install:

```bash
./toolkit_manager.sh install inference --env local
```

On a BYO cluster the installer uses your kubeconfig and writes none of its own, so in the steps below set `KUBECONFIG` to the path you gave in `existing_kubernetes`.

---

## Verify the install

```bash
export KUBECONFIG=$(pwd)/env/local/kubeconfig.yaml

kubectl get nodes                  # all nodes Ready
kubectl get pods -A                # all pods Running or Completed

# Or use the built-in checks:
./toolkit_manager.sh status --health --env local   # green/red cluster verdict
./toolkit_manager.sh validate inference --env local
```

---

## Access the services

Every service has its own hostname under your `base_domain_name` (for example `grafana.inteltoolkits.ai`), and all of them point at one gateway IP. Unless your DNS already resolves those names, add them to `/etc/hosts` on each machine that needs access:

```bash
GATEWAY_IP=$(kubectl get svc -n envoy-gateway-system \
  -l gateway.envoyproxy.io/owning-gateway-name=eg-gateway \
  -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}')

DOMAIN=$(yq '.base_domain_name' env/local/global_config.yaml)

# Prints the line to add to /etc/hosts on each client machine
echo "${GATEWAY_IP}  ${DOMAIN} inference.${DOMAIN} keycloak.${DOMAIN} grafana.${DOMAIN}"
```

Add `litellm.${DOMAIN}` and `langfuse.${DOMAIN}` when LiteLLM is on (`auth_type: litellm_vkey`), and `headlamp.${DOMAIN}` with `headlamp_enabled: true`. `./toolkit_manager.sh status` prints the service URLs. With `routing_mode: path` the services sit under the one name `${DOMAIN}` (Langfuse and the vLLM Semantic Router dashboard keep their own hostnames).

> [!IMPORTANT]
> Every subdomain must be listed explicitly. Wildcards do not work in `/etc/hosts`, so a missing entry looks like a connection failure in the browser rather than an error from the cluster.

For self-signed TLS (the default), import the CA certificate to remove browser warnings:

```bash
# The installer writes the CA certificate here during install
cat env/local/logs/ai-solutions-ca.crt
```

| Browser / OS | Import path |
|---|---|
| Firefox | Settings → Privacy & Security → Certificates → View Certificates → Authorities → Import |
| Chrome / Edge | Settings → Privacy → Security → Manage certificates → Authorities → Import |
| Linux system-wide | `sudo cp env/local/logs/ai-solutions-ca.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates` |
| macOS | `sudo security add-trusted-cert -d -k /Library/Keychains/System.keychain env/local/logs/ai-solutions-ca.crt` |

---

## Next steps

- [Deploy a Model](../deploy/deploy_models.md) — serve your first LLM
- [Integration Guide](../customize/integration.md) — connect Python, LangChain, Cursor, n8n, and more
- [Configuration Reference](../customize/configuration.md) — all available settings
- [Architecture & Design Document](../reference/architecture.md) — how the layers you just installed fit together
- [Multi-Node & BYO Cluster](../deploy/topologies.md) — scale beyond the single-node path above
- [Teardown](../customize/cli.md#teardown) — remove everything cleanly

---

## Multiple environments

You can manage multiple isolated environments from one machine:

```bash
./toolkit_manager.sh init inference --env dev
./toolkit_manager.sh init inference --env staging
./toolkit_manager.sh init inference --env prod

# Each has its own config, inventory, kubeconfig
./toolkit_manager.sh install inference --env dev
./toolkit_manager.sh install inference --env prod

# Use the matching kubeconfig per environment
export KUBECONFIG=$(pwd)/env/prod/kubeconfig.yaml
```
