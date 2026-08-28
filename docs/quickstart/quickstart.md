# Getting Started with Intel® AI for Enterprise Solutions

[← Docs Index](../README.md)

Three ways to deploy Intel® AI for Enterprise Solutions — single node, multi-node, or an existing (bring-your-own) Kubernetes cluster — depending on your setup. Pick one and follow it end to end.

---

## Before you begin

Check [Prerequisites](prerequisites.md). At minimum you need:
- Ubuntu 22.04/24.04 or RHEL/Rocky x86_64
- Passwordless sudo on the machine running the installer
- Internet access

---

## Step 1 — Get the code

```bash
git clone https://github.com/intel/enterprise-ai-solutions.git
cd enterprise-ai-solutions

./es_auto_installer.sh configure    # one-time machine prep — installs Python 3.11+, yq, kubectl, helm
```

---

## Step 2 — Create an environment

Every deployment lives in a named environment under `env/<name>/`. The name is arbitrary — use `local` for a single-node dev setup, `prod` for production, or anything you like.

```bash
./es_auto_installer.sh init local
```

This creates `env/local/` with:
- `global_config.yaml` — all platform settings
- `nodes.yaml` — node IPs and SSH credentials (edit for multi-node)
- `inventory/hosts.yaml` — Kubespray-compatible inventory (auto-generated from nodes.yaml)
- `models.yaml` — your model catalog (pre-seeded with defaults)

---

## Step 3 — Configure

Open `env/local/global_config.yaml` and set:

```yaml
# Required — base domain for all service URLs and TLS certificates
base_domain_name: "solutions.ai"

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
./es_auto_installer.sh install --all --env local
```

Takes 15–20 minutes. When it finishes:

```bash
export KUBECONFIG=$(pwd)/env/local/kubeconfig.yaml
kubectl get nodes
# NAME      STATUS   ROLES           AGE   VERSION
# master1   Ready    control-plane   10m   v1.34.3
```

→ Continue to [Deploy a Model](../deploy/deploy_models.md).

---

### Option B — Multi-node

Provisions Kubernetes across multiple machines via SSH. Best for production and scale-out.

See the full guide → [Multi-Node & BYO Cluster](../deploy/topologies.md).

Quick overview:

1. Set up passwordless SSH from the installer host to every node.
2. Edit `env/local/inventory/hosts.yaml` with your node IPs.
3. Set `storage_backend: nfs` (or `ceph`) in `global_config.yaml` — `local-path` only works for single-node.
4. Run `./es_auto_installer.sh install --all --env local`.

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
./es_auto_installer.sh install --all --env local
```

---

## Verify the install

```bash
export KUBECONFIG=$(pwd)/env/local/kubeconfig.yaml

kubectl get nodes                  # all nodes Ready
kubectl get pods -A                # all pods Running or Completed

# Or use the built-in health check:
./es_auto_installer.sh validate --all --env local
```

---

## Access the services

After install, the gateway IP and per-service hostnames are derived from your `base_domain_name`. Add the gateway IP to `/etc/hosts` on any machine that needs access:

```bash
GATEWAY_IP=$(kubectl get svc -n envoy-gateway-system \
  -l gateway.envoyproxy.io/owning-gateway-name=eg-gateway \
  -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}')

DOMAIN=$(yq '.base_domain_name' env/local/global_config.yaml)

# Add to /etc/hosts on any client machine
echo "${GATEWAY_IP}  ${DOMAIN} grafana.${DOMAIN} keycloak.${DOMAIN} litellm.${DOMAIN}"
```

For self-signed TLS (the default), import the CA certificate to remove browser warnings:

```bash
# The CA cert is written here after install
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
./es_auto_installer.sh init dev
./es_auto_installer.sh init staging
./es_auto_installer.sh init prod

# Each has its own config, inventory, kubeconfig
./es_auto_installer.sh install --all --env dev
./es_auto_installer.sh install --all --env prod

# Use the matching kubeconfig per environment
export KUBECONFIG=$(pwd)/env/prod/kubeconfig.yaml
```
