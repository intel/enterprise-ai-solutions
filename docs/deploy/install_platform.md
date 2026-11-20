# Deployment Guide - Install On-Premises

[← Docs Index](../README.md)

This guide walks you through installing Toolkit Manager on your own machines, step by step, from plain servers to a running self-hosted AI stack. No Kubernetes experience needed.

---

## What Gets Installed

The installer sets up these layers automatically, each on top of the one before:

```
1. Infrastructure                — Kubernetes (built with Kubespray), storage (local-path / NFS / Ceph / NetApp)
2. Platform                      — cert-manager, Istio, MetalLB, Envoy Gateway, PostgreSQL, Keycloak, SeaweedFS, Observability
                                   (Grafana, Prometheus, Loki, Tempo), NRI CPU Balloons, Node Feature Discovery, metrics-server;
                                   optional: Headlamp, KEDA, Velero
3. Intel® Inference Toolkit     — from the inference repository: KServe, the Envoy AI Gateway and the model services
                                   (LiteLLM and Langfuse too in LiteLLM mode)
4. Intel® Chatbot Toolkit   — opt-in (vector DB, ingestion, chat, UI) — requires init erag + install erag
```

You run **one command** and it installs every layer the target needs. `./toolkit_manager.sh show` lists every layer and component.

---

## Before You Start

**Your machine needs:**

- Ubuntu 22.04 or 24.04, or a RHEL-family system (RHEL, Rocky). `configure` refuses other Ubuntu versions.
- Internet access (a proxy is fine — see [Step 2](#step-2-edit-your-settings))
- `git`, and `sudo` rights (passwordless is easiest; otherwise you are asked for the password)
- No Docker on the machines that become the cluster: Kubespray removes it ([Prerequisites](../quickstart/prerequisites.md#operating-system))

**Tooling installed by `configure`.** `./toolkit_manager.sh configure` lists what it will change, asks before going ahead, and installs these pinned versions (it skips any tool already on your `PATH`). It also creates the installer's Python environment in `.venv/`. You can install the tools yourself instead:

| Tool | Version | Notes |
|------|---------|-------|
| Python | ≥ 3.11 | plus its `venv` package (Ubuntu 22.04 gets it from the deadsnakes PPA) |
| yq | v4.53.2 | [mikefarah/yq](https://github.com/mikefarah/yq) |
| kubectl | v1.35.4 | |
| helm | v3.20.2 | |
| git | any | must already be installed; used to clone the external repos |

> **Multi-node / remote setups:** run an NTP service (`chrony` or
> `systemd-timesyncd`) on **every** node before installing. Kubernetes,
> etcd, TLS certificates, and token auth all depend on synchronized clocks —
> clock skew between nodes shows up as intermittent, hard-to-diagnose certificate and
> etcd errors. Not required for a single localhost node.

**Minimum hardware requirements:**

| Deployment | RAM | Disk |
|------------|-----|------|
| Platform + Observability only (no models) | 32 GB | 200 GB |
| Inference with 3B model | 64 GB | 300 GB |
| Inference with 8B model | 128 GB | 400 GB |

> More CPU cores = faster model inference. Intel® Xeon® recommended for CPU-based vLLM workloads. CPU core counts are in [Prerequisites](../quickstart/prerequisites.md#hardware-minimums).

---

## Step 1: Get the Code and Configure

```bash
git clone https://github.com/intel/toolkit-manager.git
cd toolkit-manager

# Prepare your machine, once (installs Python 3.11+, yq, kubectl, helm and the .venv; asks first)
./toolkit_manager.sh configure

# Create an environment named "local" for the inference layer (add --env <name> for another name)
./toolkit_manager.sh init inference
```

An **environment** is a folder under `env/` that holds the settings for one cluster. `init` also downloads the inference repository into `ext/`, and creates `env/local/` with:
- `global_config.yaml` — your settings for the whole platform (domain, TLS, proxy, storage)
- `nodes.yaml` — which machines form the cluster; left as is, the cluster is this machine
- `inventory/hosts.yaml` — the machine list in Kubespray format, generated from `nodes.yaml` (or written by hand)
- `config.inference.yaml` — settings for the inference layer, such as `auth_type`
- `models.yaml` — the model catalog `model-manager` deploys from
- `.solutions.yaml` — the record of which layers this environment was set up for; do not edit
- `logs/` — installer logs

`kubeconfig.yaml` (cluster credentials) appears in `env/local/` during the install.

---

## Step 2: Edit Your Settings

Open your environment's config:

```bash
nano env/local/global_config.yaml
```

**If you're behind a corporate proxy**, uncomment and fill in these lines in section 2, *Networking and proxy*:

```yaml
http_proxy: "http://your-proxy:8080"
https_proxy: "http://your-proxy:8080"
no_proxy: "localhost,127.0.0.1,.svc,.cluster.local,.monitoring,10.233.0.0/18,10.233.64.0/18,<node-subnet>"
```

Keep the two cluster address ranges shown in `no_proxy` and add your machines' subnet (shown by `ip route`), or traffic inside the cluster goes to the proxy.

**If you have your own TLS certificates and domain**, update section 3, *Domain and access*:

```yaml
gateway_tls_mode: "custom"
base_domain_name: "your-domain.com"
gateway_tls_cert_file: "/path/to/your/cert.pem"
gateway_tls_key_file: "/path/to/your/key.pem"
```

Otherwise, leave the defaults — the domain `inteltoolkits.ai` and a self-signed certificate the installer creates.

**Everything else can stay as-is for now.** Save and close the file. Every setting is described in the [Configuration Reference](../customize/configuration.md).

---

## Step 3: Choose Your Deployment Mode

The installer reads the machines from `env/local/nodes.yaml` and writes `env/local/inventory/hosts.yaml` from it on every run. Pick one of the three options below based on how many machines you have.

---

### Option A: Single Machine (Simplest)

**Best for:** Getting started, dev/test, single Intel® Xeon® servers.

Everything runs on one machine. No SSH setup needed.

**Nothing to configure** — the files `init` created already describe this machine (localhost deployment).

**Install:**

```bash
./toolkit_manager.sh install inference --env local
```

A first install usually takes 30 to 60 minutes, mostly for downloading container images. It ends with an **INSTALLATION COMPLETE** box that names the log file. Then check:

```bash
export KUBECONFIG=$(pwd)/env/local/kubeconfig.yaml
kubectl get nodes
```

You should see one node with `STATUS` `Ready` and `ROLES` `control-plane`:

```
NAME       STATUS   ROLES           AGE   VERSION
<node>     Ready    control-plane   10m   v1.35.4
```

Done. Skip to [Step 4](#step-4-verify-everything-works).

---

### Option B: Multiple Machines

**Best for:** Production deployments with separate control-plane and worker machines.

**What you need:**
- 2 or more machines that can reach each other over the network
- You'll run the installer from one of them (usually the first control-plane machine)
- Shared storage for model weights: set `storage_backend` to `nfs` or `ceph` in `global_config.yaml` (the default, `local-path`, is for one machine only)

#### B.1 — Set up SSH access

Pick one machine to run the installer from. On that machine, run:

```bash
# Create an SSH key (press Enter for all prompts)
ssh-keygen -t ed25519 -f ~/.ssh/cluster_key -N ""

# Copy it to every other machine (replace user and IPs with yours)
ssh-copy-id -i ~/.ssh/cluster_key ubuntu@10.0.1.10
ssh-copy-id -i ~/.ssh/cluster_key ubuntu@10.0.1.20
ssh-copy-id -i ~/.ssh/cluster_key ubuntu@10.0.1.21

# Test — each should print the machine's name
ssh -i ~/.ssh/cluster_key ubuntu@10.0.1.10 "hostname"
ssh -i ~/.ssh/cluster_key ubuntu@10.0.1.20 "hostname"
ssh -i ~/.ssh/cluster_key ubuntu@10.0.1.21 "hostname"
```

On every target machine, make sure the user can run commands as root without a password:

```bash
echo "ubuntu ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/ubuntu
```

#### B.2 — Edit your inventory

Open `env/local/nodes.yaml`. The file has commented examples. Uncomment and customize the **multi-node** example:

```yaml
nodes_control_plane:
  - ip: 10.0.1.10
    hostname: cp1

nodes_workers:
  - ip: 10.0.1.20
    hostname: worker1
  - ip: 10.0.1.21
    hostname: worker2

nodes_ssh_user: "ubuntu"
nodes_ssh_key: "~/.ssh/cluster_key"
```

The installer generates `inventory/hosts.yaml` (Kubespray's format) from this file on every run, so do not edit `hosts.yaml` while `nodes.yaml` lists machines.

**What do the settings mean?**

| Setting | What it does | How many do I need? |
|------|-------------|---------------------|
| `nodes_control_plane` | Machines that manage the cluster; they also run etcd (the cluster's database) | At least 1. Use 3 for high-availability. |
| `nodes_workers` | Machines that run your actual workloads | Optional. Leave it out and the control-plane machines run the workloads. |
| `hostname` | The node's name in Kubernetes | One per machine, in lower case |
| `nodes_ssh_user` / `nodes_ssh_key` | How the installer logs in to every machine | Once; a machine can override them with its own `ssh_user` / `ssh_key` |

**Example: just 2 machines (smallest multi-node)**

The control-plane machine also runs workloads, so it is listed under both, with identical entries:

```yaml
nodes_control_plane:
  - ip: 10.0.1.10
    hostname: cp1

nodes_workers:
  - ip: 10.0.1.10       # cp1 also runs workloads
    hostname: cp1
  - ip: 10.0.1.20
    hostname: worker1

nodes_ssh_user: "ubuntu"
nodes_ssh_key: "~/.ssh/cluster_key"
```

**Example: 3 control-plane machines + 3 workers (production HA)**

```yaml
nodes_control_plane:
  - ip: 10.0.1.10
    hostname: cp1
  - ip: 10.0.1.11
    hostname: cp2
  - ip: 10.0.1.12
    hostname: cp3

nodes_workers:
  - ip: 10.0.1.20
    hostname: worker1
  - ip: 10.0.1.21
    hostname: worker2
  - ip: 10.0.1.22
    hostname: worker3

nodes_ssh_user: "ubuntu"
nodes_ssh_key: "~/.ssh/cluster_key"
```

More layouts, including a hand-written inventory, are in [Multi-Node & BYO Cluster](topologies.md).

#### B.3 — Install

```bash
./toolkit_manager.sh install inference --env local
```

Wait for the **INSTALLATION COMPLETE** box. When done:

```bash
export KUBECONFIG=$(pwd)/env/local/kubeconfig.yaml
kubectl get nodes
```

You should see all your machines listed as `Ready`.

Done. Skip to [Step 4](#step-4-verify-everything-works).

---

### Option C: Bastion (Jump Host)

**Best for:** When your cluster machines are in a private network and you can only reach them through a gateway machine.

This is the same as Option B, except you run the installer on the bastion (gateway) machine — which does NOT become part of the cluster.

```
 You → SSH → [Bastion] → SSH → [cp1, worker1, worker2, ...]
              (runs installer)    (become the cluster)
```

#### C.1 — On the bastion: get the code and set up SSH

```bash
# Get the code
git clone https://github.com/intel/toolkit-manager.git
cd toolkit-manager

# Prepare the machine and create the environment
./toolkit_manager.sh configure
./toolkit_manager.sh init inference

# Create an SSH key and copy it to all cluster machines
ssh-keygen -t ed25519 -f ~/.ssh/cluster_key -N ""
ssh-copy-id -i ~/.ssh/cluster_key ubuntu@10.0.1.10
ssh-copy-id -i ~/.ssh/cluster_key ubuntu@10.0.1.20
ssh-copy-id -i ~/.ssh/cluster_key ubuntu@10.0.1.21
```

#### C.2 — Configure

Edit `env/local/nodes.yaml` — same as Option B. For example:

```yaml
nodes_control_plane:
  - ip: 10.0.1.10
    hostname: cp1

nodes_workers:
  - ip: 10.0.1.10
    hostname: cp1
  - ip: 10.0.1.20
    hostname: worker1
  - ip: 10.0.1.21
    hostname: worker2

nodes_ssh_user: "ubuntu"
nodes_ssh_key: "~/.ssh/cluster_key"
```

**If there's another jump host between your bastion and the nodes**, `nodes.yaml` cannot express it. Leave `nodes_control_plane` empty in `nodes.yaml`, write `env/local/inventory/hosts.yaml` yourself (Kubespray format, see [Multi-Node & BYO Cluster](topologies.md)), and add the extra hop to its `vars` section:

```yaml
  vars:
    ansible_user: ubuntu
    ansible_ssh_private_key_file: ~/.ssh/cluster_key
    ansible_ssh_common_args: '-o ProxyJump=jumpuser@10.0.0.5'
```

Most of the time you won't need this — only if there's a double-hop.

**With a double hop, the bastion must still reach the API server** at `https://<control-plane-ip>:6443`: after Kubespray, the installer checks the cluster and applies the node labels from the bastion. If it reaches the nodes only through the extra jump host, Kubespray succeeds over SSH, that check times out, and a re-run sees a live cluster and skips the post-install phase. Forward the API port through the jump host, point `env/local/kubeconfig.yaml` at the forwarded port, and run the post-install phase on its own:

```bash
./toolkit_manager.sh install --env local kubernetes --only -- -e _kubespray_skip_cluster_yml=true
```

#### C.3 — Install

```bash
./toolkit_manager.sh install inference --env local
```

#### C.4 — Get kubectl working on the bastion

After install, the kubeconfig is already created at `env/local/kubeconfig.yaml`:

```bash
export KUBECONFIG=$(pwd)/env/local/kubeconfig.yaml
kubectl get nodes
```

Done. Continue to Step 4.

---

## Step 4: Verify Everything Works

Set the kubeconfig and check the cluster:

```bash
export KUBECONFIG=$(pwd)/env/local/kubeconfig.yaml

# All nodes should say "Ready"
kubectl get nodes

# All pods should say "Running" (or "Completed" for one-off jobs)
kubectl get pods -A
```

Or use the built-in commands:

```bash
./toolkit_manager.sh validate inference --env local   # each component checks itself and reports what is wrong
./toolkit_manager.sh status --env local               # what is installed, namespace health, service URLs
./toolkit_manager.sh status --env local --health      # just a pass/fail verdict
```

The web UIs live under your domain, for example `https://grafana.inteltoolkits.ai`. The domain is usually not in your DNS: point the host names at the gateway IP in your DNS or `/etc/hosts` (finding the gateway IP and trusting the self-signed certificate: [Common Setup](#1-common-setup)).

---

## Scaling: Adding More Machines Later

Already have a running cluster and want to add workers? Edit `nodes.yaml` and run `add-node` (not `install`):

1. Set up SSH to the new machine (same as before):

   ```bash
   ssh-copy-id -i ~/.ssh/cluster_key ubuntu@10.0.1.22
   ```

2. Add the new machine under `nodes_workers` in `env/local/nodes.yaml`:

   ```yaml
   nodes_control_plane:
     - ip: 10.0.1.10
       hostname: cp1

   nodes_workers:
     - ip: 10.0.1.20
       hostname: worker1
     - ip: 10.0.1.22            # ← add this
       hostname: worker2
   ```

3. Add it to the cluster — `--dry-run` first shows what would change:

   ```bash
   ./toolkit_manager.sh add-node --env local --dry-run
   ./toolkit_manager.sh add-node --env local
   ```

Existing nodes are untouched; only the new machines are set up. `remove-node` works the same way for a worker you delete from `nodes.yaml`. Only workers can be added or removed, and the cluster must have been installed from `nodes.yaml`; all the rules are in [Node Scaling](../customize/node_scaling.md).

---

## Teardown: Removing Everything

`teardown <target>` removes the target **and everything installed on top of it**. It asks before it starts (`--force` skips the question). Your config files in `env/<name>/` are kept.

```bash
# Remove the full stack (inference + platform, then the Kubernetes cluster itself)
./toolkit_manager.sh teardown infrastructure --env local

# Remove just one component (and whatever depends on it)
./toolkit_manager.sh teardown llm_services --env local
./toolkit_manager.sh teardown kserve --env local
```

---

## Troubleshooting

### "Connection refused" or SSH errors

Test SSH manually first:

```bash
ssh -i ~/.ssh/cluster_key ubuntu@10.0.1.10 "hostname"
```

If this doesn't work, the installer won't work either. Fix SSH access first.

### Install fails partway through

Check the newest log (the error, or the **INSTALLATION COMPLETE** box, names the file):

```bash
ls -t env/local/logs/ | head -3
tail -50 env/local/logs/install-inference-<timestamp>.log
```

Then fix the cause and re-run — the installer is safe to run multiple times; what is already installed is left as it is:

```bash
./toolkit_manager.sh install inference --env local
```

### Pods stuck in "ImagePullBackOff"

The node cannot download images. This usually means the proxy settings are wrong. Double-check `http_proxy`, `https_proxy` and `no_proxy` in `env/local/global_config.yaml`.

### Skip dependency auto-inclusion (expert mode)

If you want to install only a specific component without pulling its entire layer dependency chain:

```bash
./toolkit_manager.sh install metallb --env local --only
```

This runs only the named target without traversing the dependency tree.

### Pass Ansible flags for more details

```bash
./toolkit_manager.sh install inference --env local -- -vvv
```

The `--` separates installer flags from Ansible flags. For more installer output, set `TKM_LOG_LEVEL=debug` (adds Ansible's `-vvv`) or `trace`:

```bash
TKM_LOG_LEVEL=debug ./toolkit_manager.sh install inference --env local
```

---

## Common Issues

### Wrong kubeconfig

Make sure you're using the right one:

```bash
export KUBECONFIG=$(pwd)/env/local/kubeconfig.yaml
kubectl get nodes
```

### Multiple environments getting mixed up

Each environment is isolated. Always specify `--env <name>` and use the correct kubeconfig:

```bash
# For environment "prod"
export KUBECONFIG=$(pwd)/env/prod/kubeconfig.yaml
./toolkit_manager.sh install inference --env prod
```

---

## Logs

Each run writes one log under `env/<name>/logs/`, named with the action, the target (`cluster` for `status`, `add-node` and `remove-node`) and the time. The 30 newest are kept:

```
env/<name>/logs/install-inference-20260817-143022.log
env/<name>/logs/teardown-infrastructure-20260817-150100.log
env/<name>/logs/validate-inference-20260817-151500.log
env/<name>/logs/status-cluster-20260817-152000.log
```

---

## Quick Reference

```bash
# Environment setup
./toolkit_manager.sh configure                          # one-time machine prep
./toolkit_manager.sh init <layer> --env <name>          # create new environment (layer: inference, erag, agent-toolkit)
./toolkit_manager.sh show                               # list layers/components

# Installation
./toolkit_manager.sh install inference --env <name>           # install infrastructure + platform + inference
./toolkit_manager.sh install kubernetes --env <name>          # install just Kubernetes
./toolkit_manager.sh install metallb --env <name> --only      # single component, skip deps
./toolkit_manager.sh install inference --env <name> -- -vvv   # pass Ansible flags

# Operations
./toolkit_manager.sh validate inference --env <name>       # check health
./toolkit_manager.sh status --env <name>                   # what is installed
./toolkit_manager.sh add-node --env <name>                 # add workers listed in nodes.yaml
./toolkit_manager.sh teardown infrastructure --env <name>  # remove everything

# Kubeconfig
export KUBECONFIG=$(pwd)/env/<name>/kubeconfig.yaml
kubectl get nodes
```

Every command and flag: [CLI Reference](../customize/cli.md).

---

## Deploying and Accessing Models

Once the platform is installed, deploy models and access them through the gateway. The access pattern depends on the **`auth_type`** set in `env/<env>/config.inference.yaml`. Keycloak is deployed in both modes and handles sign-in to the web UIs; `auth_type` only selects the credential API clients send.

### Auth Modes at a Glance

| | **Keycloak** (`auth_type: jwt`, default) | **LiteLLM** (`auth_type: litellm_vkey`) |
|---|---|---|
| **What's deployed** | Keycloak + Envoy AI Gateway (LiteLLM + Langfuse optional, `litellm_enabled: true`) | Keycloak + LiteLLM proxy + Langfuse + Valkey |
| **Auth mechanism** | Short-lived token (JWT) from the client credentials flow | LiteLLM virtual API keys (or the master key) |
| **Request path** | Client → Envoy Gateway (checks the JWT) → Envoy AI Gateway → model pod | Client → Envoy Gateway → LiteLLM (checks the key) → model pod |
| **Model endpoint** (subdomain mode) | `https://inference.<domain>/v1/chat/completions` | `https://litellm.<domain>/v1/chat/completions` |
| **Model endpoint** (path mode) | `https://<domain>/inference/v1/chat/completions` | `https://<domain>/litellm/v1/chat/completions` |
| **Model selection** | Via `"model"` field in request body (AI Gateway routes) | Via `"model"` field in request body (LiteLLM routes) |
| **Best for** | Production SSO, per-user access rules | Multi-tenant, per-key budgets and spend tracking |

The examples below use subdomain mode, the default.

### 1. Common Setup

Set your kubeconfig, and get the gateway IP and your domain (replace `<env>` with your environment name, e.g. `local`):

```bash
export KUBECONFIG=$(pwd)/env/<env>/kubeconfig.yaml

GATEWAY_IP=$(kubectl get svc -n envoy-gateway-system \
  -l gateway.envoyproxy.io/owning-gateway-name=eg-gateway \
  -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}')

DOMAIN=$(yq '.base_domain_name' env/<env>/global_config.yaml)
```

The domain (default `inteltoolkits.ai`) is usually not in your DNS. That is why the `curl` commands below use `--resolve <host>:443:<gateway IP>`: it sends the request to the gateway while keeping the right host name.

**TLS certificate:** When using self-signed TLS (the default), the installer exports a CA certificate to:

```
env/<name>/logs/ai-solutions-ca.crt
```

Import this into your browser or OS trust store to open the web UIs (Keycloak, Grafana, Langfuse) without certificate warnings. The `curl` commands below pass it with `--cacert`; for Python/SDK clients, set `SSL_CERT_FILE` to it:

```bash
CA=$(pwd)/env/<env>/logs/ai-solutions-ca.crt
```

### 2. Deploy a Model

Deploy a model — this works the same regardless of auth mode:

```bash
# Deploy from the model catalog (downloads weights + creates serving pod)
./model-manager deploy qwen3-0-6b --wait

# Gated models (Llama, some Mistral models) need a Hugging Face token:
export HF_TOKEN="hf_your_token_here"
./model-manager deploy llama3-8b-awq --wait

# Verify
./model-manager list --deployed
kubectl get pods -n llm-inference
```

The `--wait` flag blocks until the model is ready to serve requests, or until `--wait-timeout` (default 900 seconds) runs out. The first deploy of a model also downloads its weights. More options: [Deploy a Model](deploy_models.md).

### 3. Authenticate and Access

Choose the section matching your `auth_type`.

#### Option A — Keycloak JWT (`auth_type: jwt`, default)

When `auth_type=jwt` (the default), API clients authenticate with Keycloak tokens (JWTs). Models are accessed through the Envoy AI Gateway at a single inference endpoint. Authentication uses the **client credentials** flow (no user passwords needed).

**Quick method — use the helper script:**

```bash
source ./ext/enterprise.ai-inference/model_manager/scripts/get-keycloak-token.sh
# Exports: TOKEN, GATEWAY_IP, GATEWAY_DOMAIN, GATEWAY_URL
# Finds the gateway IP, Keycloak domain, and client credentials in the cluster
```

The token is valid for 15 minutes. For longer sessions, pass `--lifespan 3600` (1 hour; this changes the Keycloak client's token lifetime).

**Manual method — step by step:**

```bash
# 1. Get the gateway IP and CA file (see Common Setup)

# 2. Get the Keycloak domain
KEYCLOAK_DOMAIN=$(kubectl get keycloaks -n keycloak \
  -o jsonpath='{.items[0].spec.hostname.hostname}')

# 3. Read client credentials
CLIENT_ID=$(kubectl get secret keycloak-client-secret -n keycloak \
  -o jsonpath='{.data.client-id}' | base64 -d)
CLIENT_SECRET=$(kubectl get secret keycloak-client-secret -n keycloak \
  -o jsonpath='{.data.client-secret}' | base64 -d)

# 4. Request a JWT token
TOKEN=$(curl -s --cacert "$CA" --noproxy '*' \
  --resolve "${KEYCLOAK_DOMAIN}:443:${GATEWAY_IP}" \
  "https://${KEYCLOAK_DOMAIN}/realms/inference/protocol/openid-connect/token" \
  -d "grant_type=client_credentials" \
  -d "client_id=${CLIENT_ID}" \
  -d "client_secret=${CLIENT_SECRET}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

echo "Token: ${TOKEN:0:20}..."
```

**Call the model:**

```bash
GATEWAY_DOMAIN="inference.${DOMAIN}"

curl -s --cacert "$CA" --noproxy '*' \
  --resolve "${GATEWAY_DOMAIN}:443:${GATEWAY_IP}" \
  "https://${GATEWAY_DOMAIN}/v1/chat/completions" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3-0-6b","messages":[{"role":"user","content":"Hello!"}],"max_tokens":64}'
```

A JSON answer with `"choices"` means it works. List available models:

```bash
curl -s --cacert "$CA" --noproxy '*' \
  --resolve "${GATEWAY_DOMAIN}:443:${GATEWAY_IP}" \
  "https://${GATEWAY_DOMAIN}/v1/models" \
  -H "Authorization: Bearer ${TOKEN}"
```

A request with a missing, malformed, or expired JWT is rejected with `401` at the gateway before it reaches a model.

#### Option B — LiteLLM virtual keys (`auth_type: litellm_vkey`)

When `auth_type=litellm_vkey`, API clients use LiteLLM API keys; Keycloak still handles sign-in to the web UIs. LiteLLM checks the key itself and acts as the model proxy. `model-manager deploy` registers every model with LiteLLM — you access them through a single endpoint and name the model in the request body.

**1. Get the master key** (full admin rights; keep it secret):

```bash
MASTER_KEY=$(kubectl get secret litellm-master-key -n litellm \
  -o jsonpath='{.data.master_key}' | base64 -d)

LITELLM_DOMAIN="litellm.${DOMAIN}"
```

**2. Get a token** — use the master key directly, or mint a scoped virtual key (recommended for applications). The key API is not on the public `litellm.<domain>` address, so reach LiteLLM through a port-forward:

```bash
kubectl port-forward -n litellm svc/litellm 4000:4000 &

# Create a virtual key with a model allow-list, budget cap, and expiry
VIRTUAL_KEY=$(curl -s http://localhost:4000/key/generate \
  -H "Authorization: Bearer ${MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"models":["qwen3-0-6b"],"key_alias":"team-a","max_budget":5,"duration":"24h"}' \
  | jq -r '.key')

TOKEN="${VIRTUAL_KEY}"   # or TOKEN="${MASTER_KEY}" to skip key creation
```

**3. Call the model:**

```bash
curl -s --cacert "$CA" --noproxy '*' \
  --resolve "${LITELLM_DOMAIN}:443:${GATEWAY_IP}" \
  "https://${LITELLM_DOMAIN}/v1/chat/completions" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3-0-6b","messages":[{"role":"user","content":"Hello!"}],"max_tokens":64}'
```

A missing or revoked key is rejected with `401` before anything reaches the model. A key used outside its model allow-list is also rejected. The public `litellm.<domain>` address only answers `/v1/chat/completions`, `/v1/completions`, `/v1/embeddings`, `/v1/models` and `/health/liveliness`.

**4. Manage keys** (through the same port-forward):

```bash
# List (alias, allow-list, budget, spend to date)
curl -s "http://localhost:4000/key/list?return_full_object=true" \
  -H "Authorization: Bearer ${MASTER_KEY}"

# Update (e.g. extend budget)
curl -s http://localhost:4000/key/update \
  -H "Authorization: Bearer ${MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"key":"sk-...","max_budget":20}'

# Revoke
curl -s http://localhost:4000/key/delete \
  -H "Authorization: Bearer ${MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"keys":["sk-..."]}'
```

Keys can also be managed in the LiteLLM admin UI at `https://litellm-ui.<domain>/ui/` (path mode: `https://<domain>/litellm/ui/`; the trailing slash is required). You sign in with Keycloak; only the groups in `litellm_ui_groups` get in (default: the `platform-admins` group), and username/password login is off. Request traces land in Langfuse at `https://langfuse.<domain>`.

<p align="center">
  <img src="../assets/screenshots/litellm-langfuse.png" alt="LiteLLM virtual keys and Langfuse traces" />
</p>

More views of both UIs are in [docs/assets/screenshots/](../assets/screenshots/) —
`litellm-models.png`, `litellm-virtual-keys.png`, `litellm-usage.png`,
`litellm-logs.png`, `langfuse-dashboard.png`, and `langfuse-traces.png`.

### 4. Access the Model — curl

#### With `auth_type: jwt` (default)

All models are served through a single inference gateway endpoint. Select the model via the `"model"` field in the request body:

```bash
# If you used the helper script, GATEWAY_IP and GATEWAY_DOMAIN are already set.
# Otherwise, derive the inference domain:
GATEWAY_DOMAIN="inference.$(yq '.base_domain_name' env/<env>/global_config.yaml)"

curl -s --cacert "$CA" --noproxy '*' \
  --resolve "${GATEWAY_DOMAIN}:443:${GATEWAY_IP}" \
  "https://${GATEWAY_DOMAIN}/v1/chat/completions" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-0-6b",
    "messages": [
      {"role": "user", "content": "What is Kubernetes in one sentence?"}
    ],
    "max_tokens": 100
  }'
```

**List available models:**

```bash
curl -s --cacert "$CA" --noproxy '*' \
  --resolve "${GATEWAY_DOMAIN}:443:${GATEWAY_IP}" \
  "https://${GATEWAY_DOMAIN}/v1/models" \
  -H "Authorization: Bearer ${TOKEN}"
```

**Or use the built-in helper to get token + gateway details in one step:**

```bash
source ./ext/enterprise.ai-inference/model_manager/scripts/get-keycloak-token.sh
# Exports: TOKEN, GATEWAY_IP, GATEWAY_DOMAIN, GATEWAY_URL
```

In path mode, call `${GATEWAY_URL}/v1/...` instead (`GATEWAY_URL` ends in `/inference` there).

#### With `auth_type: litellm_vkey`

Single endpoint, model selected via request body:

```bash
LITELLM_DOMAIN="litellm.$(yq '.base_domain_name' env/<env>/global_config.yaml)"

curl -s --cacert "$CA" --noproxy '*' \
  --resolve "${LITELLM_DOMAIN}:443:${GATEWAY_IP}" \
  "https://${LITELLM_DOMAIN}/v1/chat/completions" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-0-6b",
    "messages": [
      {"role": "user", "content": "What is Kubernetes in one sentence?"}
    ],
    "max_tokens": 100
  }'
```

Add `"stream": true` to the request body for server-sent events. A missing or invalid
key is rejected with `401` by LiteLLM before anything reaches the model.

**List models registered in LiteLLM** — every model registered by `model-manager deploy`
appears here:

```bash
curl -s --cacert "$CA" --noproxy '*' \
  --resolve "${LITELLM_DOMAIN}:443:${GATEWAY_IP}" \
  "https://${LITELLM_DOMAIN}/v1/models" \
  -H "Authorization: Bearer ${TOKEN}"
```

### 5. Access the Model — Python (OpenAI Client)

Run these with `SSL_CERT_FILE=env/<env>/logs/ai-solutions-ca.crt` so Python trusts the self-signed certificate. Your machine must also resolve the host name to the gateway IP (DNS or `/etc/hosts`); Python has no `--resolve`.

#### With `auth_type: jwt` (default)

```python
import os
from openai import OpenAI

# GATEWAY_URL and TOKEN are set by the helper script
# source ./ext/enterprise.ai-inference/model_manager/scripts/get-keycloak-token.sh
client = OpenAI(
    base_url=os.environ["GATEWAY_URL"] + "/v1",   # https://inference.<domain>/v1
    api_key=os.environ["TOKEN"],
)

response = client.chat.completions.create(
    model="qwen3-0-6b",
    messages=[{"role": "user", "content": "What is Kubernetes in one sentence?"}],
    max_tokens=100,
)
print(response.choices[0].message.content)

# List models
for model in client.models.list():
    print(model.id)
```

#### With `auth_type: litellm_vkey`

```python
import os
from openai import OpenAI

# TOKEN = master key or virtual key
client = OpenAI(
    base_url=f"https://{os.environ['LITELLM_DOMAIN']}/v1",
    api_key=os.environ["TOKEN"],
)

response = client.chat.completions.create(
    model="qwen3-0-6b",
    messages=[{"role": "user", "content": "What is Kubernetes in one sentence?"}],
    max_tokens=100,
)
print(response.choices[0].message.content)
```

### Token Refresh

| `auth_type` | Token Lifetime | Refresh |
|-----------|---------------|---------|
| `litellm_vkey` | A virtual key lasts until its `duration` runs out (none set: no expiry) | Create a new virtual key |
| `jwt` | 15 minutes (change with `--lifespan`) | Re-run the helper script |

**Keycloak — re-source the helper to get a fresh token:**

```bash
source ./ext/enterprise.ai-inference/model_manager/scripts/get-keycloak-token.sh
```

**For longer sessions** (e.g. 1 hour):

```bash
source ./ext/enterprise.ai-inference/model_manager/scripts/get-keycloak-token.sh --lifespan 3600
```

---

## Deploying Intel® Chatbot Toolkit

Intel® Chatbot Toolkit adds document ingestion, vector search, RAG pipelines, and a web UI on top of the inference layer. It is opt-in and requires a separate initialization and installation.

**Quick path:**

```bash
./toolkit_manager.sh init erag --env <name>
./toolkit_manager.sh install erag --env <name>
```

The `init erag` command downloads the inference and Intel® Chatbot Toolkit repositories (dependencies first), creates `env/<name>/config.erag.yaml` from the selected pipeline flavour (default: `chatqna`; pick another with `--flavour`), and records the layer in `env/<name>/.solutions.yaml`. The `install erag` command first installs whatever it needs that is missing (infrastructure → platform → inference), then installs the RAG application layer.

**See the full guide:** [Getting Started with Intel® Chatbot Toolkit](../quickstart/getting_started_rag.md) covers prerequisites, flavour selection, per-layer install and teardown, configuration, and troubleshooting.

---

## Related Docs

| If you want to… | Go to |
|---|---|
| Deploy across multiple machines or onto a cluster you already run | [Multi-Node & BYO Cluster](topologies.md) |
| Change TLS, storage, auth type, or any other setting used above | [Configuration Reference](../customize/configuration.md) |
| Deploy your first model once the platform is up | [Deploy an LLM](deploy_models.md) |
| Understand exactly how a request reaches a model after install | [Network Architecture](networking.md) |
